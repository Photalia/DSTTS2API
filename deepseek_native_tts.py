#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""DeepSeek web native TTS client.

Reverse engineered from the 2026-09-22 web bundle. It deliberately operates on
an existing DeepSeek chat message: the upstream websocket accepts
(chat_session_id, message_id), not arbitrary text.

Protocol:
  POST /api/v0/auth/ticket {"scope":"tts"}
  WS /api/v0/chat/tts/?chat_session_id=...&message_id=...&ticket=...
       &mode=manual&format=pcm|opus
  text:   {"event":"ready","audio_id":"...","format":"pcm"}
  binary: uint32_be sequence + payload
  text:   {"event":"finish","code":0,...}

For format=pcm, payload is signed 16-bit little-endian PCM, 24 kHz, mono.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import struct
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from dataclasses import dataclass
from typing import Any, Callable

import websocket

BASE_HTTPS = "https://chat.deepseek.com"
BASE_WSS = "wss://chat.deepseek.com"
SAMPLE_RATE = 24_000
CHANNELS = 1
SAMPLE_WIDTH = 2


class DeepSeekTTSError(RuntimeError):
    pass


class DeepSeekTTSRejected(DeepSeekTTSError):
    def __init__(self, code: int, message: str):
        self.code = code
        super().__init__(f"DeepSeek TTS rejected request: code={code}, message={message}")


@dataclass(frozen=True)
class Voice:
    voice_id: str
    name_i18n: dict[str, str]
    description_i18n: dict[str, str]
    gender: str | None
    languages: list[str]
    demo_urls: Any
    is_default: bool


@dataclass
class SynthesisResult:
    pcm_s16le: bytes
    audio_id: str
    upstream_format: str
    packet_count: int
    first_packet_ms: int | None
    finish: dict[str, Any]

    def to_wav(self) -> bytes:
        import io
        out = io.BytesIO()
        with wave.open(out, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(SAMPLE_WIDTH)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(self.pcm_s16le)
        return out.getvalue()


class DeepSeekNativeTTS:
    def __init__(
        self,
        token: str,
        cookie: str = "",
        user_agent: str | None = None,
        timeout: float = 30.0,
        debug: Callable[[str], None] | None = None,
    ):
        token = token.strip()
        if not token:
            raise ValueError("token is required")
        self.authorization = token if token.lower().startswith("bearer ") else f"Bearer {token}"
        self.cookie = cookie.strip()
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 "
            "Safari/537.36 Edg/153.0.0.0"
        )
        self.timeout = timeout
        self.debug = debug or (lambda _msg: None)
        self._voice_lock = threading.RLock()

    def _headers(self, *, json_body: bool = False) -> dict[str, str]:
        h = {
            "Accept": "application/json",
            "Authorization": self.authorization,
            "Origin": BASE_HTTPS,
            "Referer": BASE_HTTPS + "/",
            "User-Agent": self.user_agent,
            "x-client-platform": "web",
        }
        if self.cookie:
            h["Cookie"] = self.cookie
        if json_body:
            h["Content-Type"] = "application/json"
        return h

    def _json_request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            BASE_HTTPS + path,
            data=data,
            headers=self._headers(json_body=body is not None),
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            raise DeepSeekTTSError(f"HTTP {exc.code} from {path}: {raw[:300]!r}") from exc
        payload = json.loads(raw)
        if payload.get("code") != 0:
            raise DeepSeekTTSError(
                f"DeepSeek API error from {path}: code={payload.get('code')} msg={payload.get('msg')}"
            )
        data_block = payload.get("data") or {}
        if data_block.get("biz_code", 0) != 0:
            raise DeepSeekTTSError(
                f"DeepSeek business error from {path}: "
                f"code={data_block.get('biz_code')} msg={data_block.get('biz_msg')}"
            )
        return payload

    def issue_ticket(self) -> str:
        payload = self._json_request("POST", "/api/v0/auth/ticket", {"scope": "tts"})
        ticket = (((payload.get("data") or {}).get("biz_data") or {}).get("ticket"))
        if not ticket:
            raise DeepSeekTTSError("ticket response did not contain data.biz_data.ticket")
        return ticket

    def list_voices(self) -> tuple[list[Voice], str | None, str | None]:
        payload = self._json_request("GET", "/api/v0/chat/tts/voices")
        data = ((payload.get("data") or {}).get("biz_data") or {})
        voices = [
            Voice(
                voice_id=v["voice_id"],
                name_i18n=v.get("name_i18n") or {},
                description_i18n=v.get("description_i18n") or {},
                gender=v.get("gender"),
                languages=v.get("languages") or [],
                demo_urls=v.get("demo_urls"),
                is_default=bool(v.get("is_default")),
            )
            for v in data.get("voices") or []
        ]
        return voices, data.get("default_voice_id"), data.get("current_voice_id")

    def resolve_voice(self, requested: str, voices: list[Voice] | None = None) -> str:
        if voices is None:
            voices, default_id, current_id = self.list_voices()
        else:
            default_id = current_id = None
        key = requested.strip().casefold()
        if key in ("", "default"):
            if current_id or default_id:
                return current_id or default_id  # type: ignore[return-value]
            raise DeepSeekTTSError("no current/default voice returned by upstream")
        for v in voices:
            aliases = {v.voice_id.casefold()}
            aliases.update(str(x).casefold() for x in v.name_i18n.values())
            aliases.update(str(x).casefold() for x in v.description_i18n.values())
            if key in aliases:
                return v.voice_id
        raise DeepSeekTTSError(f"unknown DeepSeek voice: {requested!r}")

    def set_voice(self, voice_id: str) -> None:
        payload = self._json_request("POST", "/api/v0/chat/tts/voice", {"voice_id": voice_id})
        code = ((payload.get("data") or {}).get("biz_code", 0))
        if code != 0:
            raise DeepSeekTTSError(f"voice switch failed: biz_code={code}")

    @staticmethod
    def _ws_url(
        session_id: str,
        message_id: str | int,
        ticket: str,
        fmt: str,
        resume: dict[str, str | int] | None = None,
    ) -> str:
        params: dict[str, str | int] = {
            "chat_session_id": session_id,
            "message_id": str(message_id),
            "ticket": ticket,
            "mode": "manual",
            "format": fmt,
        }
        if resume:
            params.update(resume)
        query = urllib.parse.urlencode(params)
        return f"{BASE_WSS}/api/v0/chat/tts/?{query}"

    def synthesize_message(
        self,
        chat_session_id: str,
        message_id: str | int,
        *,
        upstream_format: str = "pcm",
        voice: str | None = None,
    ) -> SynthesisResult:
        if upstream_format not in ("pcm", "opus"):
            raise ValueError("upstream_format must be pcm or opus")
        # Voice is account-global upstream. Serialize voice switch + synthesis to
        # prevent concurrent requests speaking with each other's voice.
        with self._voice_lock:
            if voice and voice != "default":
                self.set_voice(self.resolve_voice(voice))
            return self._stream(chat_session_id, message_id, upstream_format)

    def _stream(self, session_id: str, message_id: str | int, fmt: str) -> SynthesisResult:
        """Receive a complete stream, resuming with a fresh ticket after drops.

        This mirrors the web client: each reconnect obtains a new auth ticket and
        sends audio_id/received_seq/played_seq so the server continues at the
        next packet instead of restarting the utterance.
        """
        headers = [f"User-Agent: {self.user_agent}", "Pragma: no-cache", "Cache-Control: no-cache"]
        started = time.monotonic()
        pcm_parts: list[bytes] = []
        opus_packets: list[bytes] = []
        audio_id = ""
        packet_count = 0
        first_packet_ms: int | None = None
        finish: dict[str, Any] = {}
        highest_seq = -1
        resume_attempts = 0
        max_resume_attempts = 3

        while True:
            ticket = self.issue_ticket()
            resume = None
            if audio_id:
                received_count = highest_seq + 1
                resume = {
                    "audio_id": audio_id,
                    "received_seq": received_count,
                    "played_seq": received_count,
                }
            url = self._ws_url(session_id, message_id, ticket, fmt, resume)
            ws = None
            got_ready_this_round = False
            last_ack = time.monotonic()
            try:
                ws = websocket.create_connection(
                    url,
                    cookie=self.cookie or None,
                    origin=BASE_HTTPS,
                    header=headers,
                    timeout=self.timeout,
                    suppress_origin=False,
                )
                while True:
                    opcode, frame = ws.recv_data()
                    if opcode == websocket.ABNF.OPCODE_TEXT:
                        text = frame.decode("utf-8") if isinstance(frame, bytes) else frame
                        event = json.loads(text)
                        kind = event.get("event")
                        if kind == "ready":
                            got_ready_this_round = True
                            returned_audio_id = str(event.get("audio_id") or "")
                            if audio_id and returned_audio_id and returned_audio_id != audio_id:
                                raise DeepSeekTTSError("resume returned a different audio_id")
                            audio_id = returned_audio_id or audio_id
                            actual = event.get("format")
                            if actual and actual != fmt:
                                raise DeepSeekTTSError(f"upstream changed format from {fmt} to {actual}")
                        elif kind == "finish":
                            finish = event
                            code = int(event.get("code", -1))
                            try:
                                ws.send(json.dumps({"event": "finish"}))
                            except Exception:
                                pass
                            if code != 0:
                                raise DeepSeekTTSRejected(code, str(event.get("msg") or code))
                            if fmt == "opus":
                                pcm = decode_opus_packets(opus_packets)
                            else:
                                pcm = b"".join(pcm_parts)
                            return SynthesisResult(
                                pcm_s16le=pcm,
                                audio_id=audio_id,
                                upstream_format=fmt,
                                packet_count=packet_count,
                                first_packet_ms=first_packet_ms,
                                finish=finish,
                            )
                    elif opcode == websocket.ABNF.OPCODE_BINARY:
                        if len(frame) < 4:
                            raise DeepSeekTTSError("short binary TTS frame")
                        seq = struct.unpack(">I", frame[:4])[0]
                        payload = bytes(frame[4:])
                        if seq <= highest_seq:
                            continue
                        highest_seq = seq
                        packet_count += 1
                        if first_packet_ms is None:
                            first_packet_ms = round((time.monotonic() - started) * 1000)
                        if fmt == "pcm":
                            pcm_parts.append(payload)
                        else:
                            opus_packets.append(payload)
                        now = time.monotonic()
                        if now - last_ack >= 0.8:
                            received_count = highest_seq + 1
                            ws.send(json.dumps({
                                "event": "ack",
                                "received_seq": received_count,
                                "played_seq": received_count,
                            }))
                            last_ack = now
                    elif opcode == websocket.ABNF.OPCODE_CLOSE:
                        raise websocket.WebSocketConnectionClosedException(
                            "websocket closed before finish event"
                        )
            except DeepSeekTTSRejected:
                raise
            except DeepSeekTTSError:
                raise
            except Exception as exc:
                if resume_attempts >= max_resume_attempts:
                    raise DeepSeekTTSError(
                        f"TTS websocket resume exhausted after {resume_attempts} retries: {exc}"
                    ) from exc
                resume_attempts += 1
                self.debug(
                    f"websocket dropped; resume {resume_attempts}/{max_resume_attempts} "
                    f"audio_id={bool(audio_id)} received={highest_seq + 1}"
                )
                time.sleep(min(0.25 * (2 ** (resume_attempts - 1)), 1.0))
            finally:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass


def decode_opus_packets(packets: list[bytes]) -> bytes:
    """Decode raw 24 kHz mono Opus packets through libopus using ctypes."""
    import ctypes
    import ctypes.util

    libname = ctypes.util.find_library("opus")
    if not libname:
        raise DeepSeekTTSError("libopus was not found; request upstream_format=pcm instead")
    lib = ctypes.CDLL(libname)
    lib.opus_decoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
    lib.opus_decoder_create.restype = ctypes.c_void_p
    lib.opus_decode.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ubyte),
        ctypes.c_int32,
        ctypes.POINTER(ctypes.c_int16),
        ctypes.c_int,
        ctypes.c_int,
    ]
    lib.opus_decode.restype = ctypes.c_int
    lib.opus_decoder_destroy.argtypes = [ctypes.c_void_p]

    err = ctypes.c_int()
    decoder = lib.opus_decoder_create(SAMPLE_RATE, CHANNELS, ctypes.byref(err))
    if not decoder or err.value != 0:
        raise DeepSeekTTSError(f"opus_decoder_create failed: {err.value}")
    out: list[bytes] = []
    # Maximum legal Opus frame duration is 120 ms => 2880 samples at 24 kHz.
    max_samples = 2880
    pcm_buf = (ctypes.c_int16 * max_samples)()
    try:
        for packet in packets:
            encoded = (ctypes.c_ubyte * len(packet)).from_buffer_copy(packet)
            samples = lib.opus_decode(decoder, encoded, len(packet), pcm_buf, max_samples, 0)
            if samples < 0:
                raise DeepSeekTTSError(f"opus_decode failed: {samples}")
            out.append(bytes(memoryview(pcm_buf).cast("B")[: samples * SAMPLE_WIDTH]))
    finally:
        lib.opus_decoder_destroy(decoder)
    return b"".join(out)


def cache_key(session_id: str, message_id: str | int, voice: str, fmt: str = "wav") -> str:
    raw = f"{session_id}\0{message_id}\0{voice}\0{fmt}".encode()
    return hashlib.sha256(raw).hexdigest()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Download one DeepSeek web TTS message as WAV")
    parser.add_argument("session_id")
    parser.add_argument("message_id")
    parser.add_argument("-o", "--output", default="deepseek-tts.wav")
    parser.add_argument("--voice", default=None)
    parser.add_argument("--format", choices=("pcm", "opus"), default="pcm")
    args = parser.parse_args()

    token = os.environ.get("DEEPSEEK_TOKEN", "")
    cookie = os.environ.get("DEEPSEEK_COOKIE", "")
    client = DeepSeekNativeTTS(token, cookie, debug=print)
    result = client.synthesize_message(
        args.session_id, args.message_id, upstream_format=args.format, voice=args.voice
    )
    pathlib.Path(args.output).write_bytes(result.to_wav())
    print(json.dumps({
        "output": args.output,
        "audio_id": result.audio_id,
        "packet_count": result.packet_count,
        "first_packet_ms": result.first_packet_ms,
        "wav_bytes": pathlib.Path(args.output).stat().st_size,
    }, ensure_ascii=False))
