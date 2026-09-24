# DSTTS2API

把 DeepSeek 网页版的原生 TTS 转成兼容 OpenAI Speech API 的本地接口。

支持任意文本、四种音色、MP3/WAV/PCM 输出和本地缓存，适合接入 RikkaHub 等支持 OpenAI TTS 的客户端。

## 功能

- OpenAI 风格的 `POST /v1/audio/speech`
- 音色：`mira`、`echo`、`stella`、`tide`
- 格式：`mp3`、`wav`、`pcm`
- 相同文本和音色自动命中本地缓存
- 默认仅监听 `127.0.0.1`
- 单账号串行工作，不包含账号池或管理后台

## 环境要求

- Python 3.11+
- Node.js 18+
- 一个已登录的 DeepSeek 网页账号及其 `userToken`

## 安装

```bash
git clone https://github.com/Photalia/DSTTS2API.git
cd DSTTS2API

python3 -m venv .venv
.venv/bin/pip install -r requirements-single.txt
```

将自己的 DeepSeek 网页 `userToken` 保存到仓库外的私有文件：

```bash
printf '%s' '你的 userToken' > /path/to/deepseek-token
chmod 600 /path/to/deepseek-token

export DEEPSEEK_TOKEN_FILE=/path/to/deepseek-token
export DS_TTS_API_KEY='给本地接口设置一个密码'
.venv/bin/python single_account_tts.py
```

服务默认启动在：

```text
http://127.0.0.1:8769
```

如账号环境还需要 Cookie，可额外设置 `DEEPSEEK_COOKIE_FILE`。不要把 token、Cookie 或 API key 提交到仓库。

## 使用

```bash
curl http://127.0.0.1:8769/v1/audio/speech \
  -H "Authorization: Bearer $DS_TTS_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "deepseek-web-tts",
    "input": "你好，世界！",
    "voice": "echo",
    "response_format": "mp3"
  }' \
  --output speech.mp3
```

### 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/healthz` | 服务状态 |
| `GET` | `/v1/models` | 模型列表 |
| `GET` | `/v1/audio/voices` | 音色列表 |
| `POST` | `/v1/audio/speech` | 生成语音 |

`model` 填 `deepseek-web-tts`；`voice` 可填 `mira`、`echo`、`stella`、`tide` 或 `default`；`response_format` 支持 `mp3`、`wav`、`pcm`。

也可以传入已有消息的 `chat_session_id` 和 `message_id`，直接朗读该 assistant 消息。

## 工作方式

DeepSeek 网页 TTS 只能朗读已有的 assistant 消息。对于任意文本，本项目会临时创建会话，让模型逐字返回原文，核对内容一致后生成语音，最后删除临时会话。

首次生成任意文本时，程序会从 DeepSeek 官方静态资源地址下载 PoW 所需的 WASM，校验 SHA-256 后缓存在本地 `cache/`；该文件不会包含在本仓库中。

## 注意事项

- 使用的是网页内部接口，网站更新后可能需要跟进适配。
- 每段未缓存的新文本都会产生一次网页模型请求。
- 音色是账号级状态，因此单账号请求会串行处理。
- 建议仅在本机或可信私网使用，不要公开暴露网页凭证。

## License

项目自有代码采用 [MPL-2.0](LICENSE)。DeepSeek 网站及运行时下载的 WASM 不属于本项目的授权范围。

非官方项目，仅供个人兴趣与技术学习，请合理使用自己的账号。
