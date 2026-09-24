# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import json, struct, tempfile, unittest
from unittest.mock import patch
import websocket

from deepseek_native_tts import DeepSeekNativeTTS, DeepSeekTTSRejected


class FakeWS:
    def __init__(self, frames):
        self.frames = iter(frames)
        self.sent = []
        self.closed = False
    def recv_data(self):
        return next(self.frames)
    def send(self, data):
        self.sent.append(json.loads(data))
    def close(self):
        self.closed = True


class NativeTTSTest(unittest.TestCase):
    def test_pcm_protocol_and_wav(self):
        pcm1 = b"\x01\x00\x02\x00"
        pcm2 = b"\x03\x00\x04\x00"
        fake = FakeWS([
            (websocket.ABNF.OPCODE_TEXT, json.dumps({"event":"ready","audio_id":"a1","format":"pcm"})),
            (websocket.ABNF.OPCODE_BINARY, struct.pack(">I", 0) + pcm1),
            (websocket.ABNF.OPCODE_BINARY, struct.pack(">I", 1) + pcm2),
            (websocket.ABNF.OPCODE_TEXT, json.dumps({"event":"finish","code":0})),
        ])
        c = DeepSeekNativeTTS("dummy")
        with patch.object(c, "issue_ticket", return_value="ticket"), \
             patch("deepseek_native_tts.websocket.create_connection", return_value=fake):
            result = c._stream("s", 5, "pcm")
        self.assertEqual(result.pcm_s16le, pcm1 + pcm2)
        self.assertEqual(result.audio_id, "a1")
        self.assertEqual(result.packet_count, 2)
        self.assertTrue(fake.closed)
        self.assertEqual(fake.sent[-1], {"event":"finish"})
        wav = result.to_wav()
        self.assertEqual(wav[:4], b"RIFF")
        self.assertEqual(wav[8:12], b"WAVE")

    def test_duplicate_packet_is_dropped(self):
        fake = FakeWS([
            (websocket.ABNF.OPCODE_TEXT, json.dumps({"event":"ready","audio_id":"a","format":"pcm"})),
            (websocket.ABNF.OPCODE_BINARY, struct.pack(">I", 0) + b"aa"),
            (websocket.ABNF.OPCODE_BINARY, struct.pack(">I", 0) + b"bb"),
            (websocket.ABNF.OPCODE_TEXT, json.dumps({"event":"finish","code":0})),
        ])
        c = DeepSeekNativeTTS("dummy")
        with patch.object(c, "issue_ticket", return_value="t"), \
             patch("deepseek_native_tts.websocket.create_connection", return_value=fake):
            result = c._stream("s", 1, "pcm")
        self.assertEqual(result.pcm_s16le, b"aa")
        self.assertEqual(result.packet_count, 1)

    def test_resume_after_drop(self):
        first = FakeWS([
            (websocket.ABNF.OPCODE_TEXT, json.dumps({"event":"ready","audio_id":"resume-a","format":"pcm"})),
            (websocket.ABNF.OPCODE_BINARY, struct.pack(">I", 0) + b"aa"),
            (websocket.ABNF.OPCODE_CLOSE, b""),
        ])
        second = FakeWS([
            (websocket.ABNF.OPCODE_TEXT, json.dumps({"event":"ready","audio_id":"resume-a","format":"pcm"})),
            # Server may replay the boundary packet; client must deduplicate it.
            (websocket.ABNF.OPCODE_BINARY, struct.pack(">I", 0) + b"duplicate"),
            (websocket.ABNF.OPCODE_BINARY, struct.pack(">I", 1) + b"bb"),
            (websocket.ABNF.OPCODE_TEXT, json.dumps({"event":"finish","code":0})),
        ])
        c = DeepSeekNativeTTS("dummy")
        tickets = iter(["t1", "t2"])
        with patch.object(c, "issue_ticket", side_effect=lambda: next(tickets)), \
             patch("deepseek_native_tts.websocket.create_connection", side_effect=[first, second]) as connect:
            result = c._stream("s", 1, "pcm")
        self.assertEqual(result.pcm_s16le, b"aabb")
        self.assertEqual(connect.call_count, 2)
        resumed_url = connect.call_args_list[1].args[0]
        self.assertIn("audio_id=resume-a", resumed_url)
        self.assertIn("received_seq=1", resumed_url)
        self.assertIn("played_seq=1", resumed_url)

    def test_rejection(self):
        fake = FakeWS([
            (websocket.ABNF.OPCODE_TEXT, json.dumps({"event":"ready","audio_id":"a","format":"pcm"})),
            (websocket.ABNF.OPCODE_TEXT, json.dumps({"event":"finish","code":7,"msg":"unsupported"})),
        ])
        c = DeepSeekNativeTTS("dummy")
        with patch.object(c, "issue_ticket", return_value="t"), \
             patch("deepseek_native_tts.websocket.create_connection", return_value=fake):
            with self.assertRaises(DeepSeekTTSRejected) as ctx:
                c._stream("s", 1, "pcm")
        self.assertEqual(ctx.exception.code, 7)


if __name__ == "__main__":
    unittest.main()
