# Unofficial DeepSeek website TTS → local OpenAI-style speech endpoint

A **single-account, personal research prototype**, not affiliated with or endorsed by DeepSeek. It uses undocumented website endpoints that may change or cease working; check the website's applicable terms before use. No account pooling, credential sharing, resale, or public service is intended. Do not put your website login token in issues, commits, screenshots, or logs.

**僅供個人興趣與技術學習。** 本專案是非官方研究原型，不提供商業服務、不代表獲得 DeepSeek 授權，也不保證符合網站所有使用條款；請自行確認適用規則並合理使用自己的帳號。聲明本身不構成免責或授權。

**WASM 來源：** This source snapshot does **not** redistribute the website's PoW WASM binary or browser bundles. When arbitrary-text synthesis first needs PoW, the Node.js bridge downloads a pinned version of the WASM **directly from DeepSeek's official static host**, checks its SHA-256, and caches it in ignored `cache/` (0600). Subsequent runs use the local cache, including offline; if the official file is unavailable or changes, the solver fails rather than executing an unverified binary. You can supply an identical verified local copy with `DS_TTS_POW_WASM_PATH=/path/to/file`; never commit it. Downloading and using the website binary is not a legal-rights guarantee. The existing-message TTS path does not require the PoW bridge. The bundled Python fallback is unverified; website compatibility may change at any time.

## Files

- `single_account_tts.py` — local-only, Bearer-protected `/v1/audio/speech` and model/voice endpoints.
- `deepseek_native_tts.py` — ticket issuance and message-bound TTS WebSocket client.
- `pow_native.py`, `pow_solver.js` — PoW integration; JS fetches the hash-pinned official WASM into ignored `cache/` on first use. **No third-party WASM is redistributed here.**
- `test_native_tts.py`, `test_auth.py` — offline tests; `requirements-single.txt` — Python dependencies.

## Local setup (Python 3.11+, Node.js for the WASM route)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-single.txt
# Use a private file outside the repository; do NOT commit it.
export DEEPSEEK_TOKEN_FILE=/secure/private/path/deepseek-token
export DS_TTS_API_KEY='replace-with-a-long-random-local-key'
.venv/bin/python single_account_tts.py
```

By default listens **only on `127.0.0.1:8769`**. Missing `DS_TTS_API_KEY` returns 401 for all endpoints. Optionally use `DEEPSEEK_COOKIE_FILE` if your own account needs it; `DS_TTS_CACHE` selects local cache directory, default `cache/`. Avoid public exposure even with Bearer authentication. Account voice selection is global; this server serializes generation. The web token is **not** an official API key.

```bash
curl -X POST http://127.0.0.1:8769/v1/audio/speech \
  -H "Authorization: Bearer $DS_TTS_API_KEY" -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-web-tts","input":"Hello, world!","voice":"echo","response_format":"mp3"}' \
  --output speech.mp3
```

Supported voices: `mira`, `echo`, `stella`, `tide`, `default`; output: `mp3`, `wav`, `pcm`. Arbitrary text creates a temporary session, requests a verbatim assistant reply, validates it byte-for-byte as Unicode text, synthesizes audio, then tries to delete the session; upstream consumption and failures remain possible. Speech of an existing assistant message can be requested with `chat_session_id` and `message_id` instead. No compatibility promise is made.

## Publication and rights

This directory contains only project-authored source and no private deployment scripts, samples, keys, website bundles or copied application UI source. The original website WASM and bundles are excluded pending an explicit redistribution-rights check. **Our project-authored source code is licensed under [MPL-2.0](LICENSE).** Modified project source files redistributed to others remain subject to the MPL; it permits commercial use and does not, by itself, require releasing the source of a service merely because it is run over a network. The DeepSeek website/WASM and any downloaded third-party code are **not covered by our license**. Before publishing, review every file staged for commit and rotate credentials already exposed elsewhere.
