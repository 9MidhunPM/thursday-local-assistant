# Thursday

A **local-first, JARVIS-style AI assistant** that can also use **cloud API keys** when you want smarter models without a GPU.

Thursday talks to any **OpenAI-compatible** endpoint (llama.cpp, OpenAI, OpenRouter, Groq, Together, DeepSeek, Mistral, or custom), keeps long- and short-term memory, and can act on your machine through a rich toolset (files, apps, web, Spotify, system, voice, and more).

| Interface | How |
|-----------|-----|
| **CLI** | Fast terminal chat (default) |
| **Web UI** | React app over HTTP + SSE at `http://127.0.0.1:5005` |

> Secrets stay in `.env`. Inference stays on your machine unless you opt into a cloud provider.

---

## Features

- 🧠 **Local or cloud LLM** — llama.cpp *or* API keys (OpenAI / OpenRouter / Groq / …)
- 🛠️ **Tool-using agent** — files, apps, shell (guarded), web, Spotify, memory, voice, …
- 💾 **Memory** — SQLite long-term + session history + automatic fact extraction
- 🎯 **Smart tool filtering** — sends only relevant tools each turn (saves context)
- 🎙️ **Voice** — Edge TTS + SpeechRecognition (optional)
- 🔒 **Safer defaults** — path sandboxes, shell policy, optional API token, loopback bind guard
- 🌐 **Web UI** — streaming, tool cards, conversations, voice visualizer, action confirmations

---

## Quick start (5 minutes)

### 1. Clone & install

```bash
git clone <your-repo-url> Thursday && cd Thursday
cp .env.example .env
./run.sh --web
```

`run.sh` creates `.venv`, installs the package, and launches Thursday.

Or manually:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[voice,desktop]"   # voice/desktop extras optional
python -m assistant.main --web
```

### 2. Choose your brain

#### Option A — Local (private, free after setup)

1. Build [llama.cpp](https://github.com/ggerganov/llama.cpp) and download a GGUF (e.g. Qwen2.5-7B-Instruct).
2. Set in `.env`:

```bash
LLM_PROVIDER=local
LLAMA_HOST=127.0.0.1
LLAMA_PORT=8080
MODEL_PATH=~/.models/your-model.gguf
LLAMA_SERVER_BIN=~/llama.cpp/build/bin/llama-server
```

3. Start the model server: `./run_vulkan_server.sh` (or any OpenAI-compatible llama-server).
4. Start Thursday: `./run.sh --web`

#### Option B — Cloud API (easiest, no GPU)

Edit `.env`:

```bash
LLM_PROVIDER=openai          # or: openrouter | groq | together | deepseek | mistral
LLM_MODEL=gpt-4o-mini
OPENAI_API_KEY=sk-...        # or OPENROUTER_API_KEY / GROQ_API_KEY / …
# LLM_API_KEY=...            # generic fallback
```

Then:

```bash
./run.sh --web
```

No llama.cpp process required. Cloud providers are marked ready as soon as a key is present.

#### Option C — Custom OpenAI-compatible endpoint

```bash
LLM_PROVIDER=custom
LLM_BASE_URL=https://your-proxy.example/v1
LLM_MODEL=your-model-name
LLM_API_KEY=your-key
```

### 3. Personalize

```bash
THURSDAY_USER_NAME=Alex
```

Optional safer config for shared machines:

```bash
cp assistant/config/config.safe.example.json assistant/config/config.json
```

---

## Running

```bash
./run.sh              # CLI (+ web server in background)
./run.sh --web        # Web UI, opens browser
python -m assistant.main --config path/to/config.json
```

| Setting | Where | Notes |
|---------|--------|--------|
| LLM provider / model / keys | `.env` | See `.env.example` |
| Local llama host/port | `.env` → `LLAMA_*` | Overrides config base URL |
| Web host/port | `.env` → `THURSDAY_HOST` / `THURSDAY_PORT` | Default `127.0.0.1:5005` |
| API token | `.env` → `THURSDAY_API_TOKEN` | Optional; required for remote binds |
| Tools / prompt / voice | `assistant/config/config.json` | Behavior |
| User name | `.env` → `THURSDAY_USER_NAME` | Injected into system prompt |

---

## Architecture

```
assistant/
├── main.py          # Entry (CLI + web)
├── runtime.py       # Wires config, LLM, tools, memory, voice
├── server.py        # HTTP + SSE
├── security.py      # Bind guard, API token, secret redaction
├── agent/           # Agent loop, safety, web confirmations
├── llm/             # OpenAI-compatible client (local + cloud)
├── tools/           # Built-in tools + smart groups
├── memory/          # SQLite long-term + conversations
├── voice/           # TTS / STT
├── config/          # config.json + loader
└── web/             # React + Vite UI
```

---

## Safety notes

Thursday can run shell commands and touch files on **your** machine.

- Default bind is **loopback only**. Remote bind needs `THURSDAY_ALLOW_REMOTE=1`.
- Set `THURSDAY_API_TOKEN` if you expose the port beyond localhost.
- Path tools use `read_roots` / `write_roots` (override with `THURSDAY_*_ROOTS`).
- Shell policy: denylist + optional whitelist; dangerous actions can require **web confirmation**.
- Power user: `THURSDAY_ALLOW_SHELL=true`, `THURSDAY_UNRESTRICTED_PATHS=1`.

---

## Web UI development

```bash
cd assistant/web
npm install
npm run dev        # Vite; proxies to THURSDAY_BACKEND (default :5005)
npm run build      # → assistant/web/dist
```

---

## Testing

```bash
pip install -e ".[dev]"
python -m pytest assistant/tests/
```

---

## Configuration tips for a stronger assistant

| Goal | Setting |
|------|---------|
| Smarter multi-step work | `THURSDAY_MAX_TOOL_STEPS=8` (cloud default is already higher) |
| Bigger context | `LLM_CONTEXT_BUDGET=100000` (cloud) or raise llama `-c` |
| Faster local turns | Keep `smart_tool_filter` on (default) |
| Your name / style | `THURSDAY_USER_NAME` + `agent.system_prompt` in config |

---

## License

Personal project — add a license of your choice before publishing.
