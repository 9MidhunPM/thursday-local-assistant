# Thursday

A **local-first, JARVIS-style AI assistant**. Thursday runs against a local
[llama.cpp](https://github.com/ggerganov/llama.cpp) server, keeps its own
long- and short-term memory, and can act on your machine through a rich set of
tools (files, apps, web search, Spotify, system info, voice, and more).

It ships with two interfaces:

- **CLI** — a fast terminal chat (default).
- **Web UI** — a React single-page app served locally over HTTP + SSE.

> Thursday is a personal, self-hosted assistant. It talks only to the local
> model you run and the optional APIs you configure yourself.

---

## Features

- 🧠 **Local LLM** via any OpenAI-compatible llama.cpp server — bring your own GGUF model.
- 🛠️ **Tool-using agent** — files, applications, browser/web search, Spotify,
  system monitoring, clipboard, media, translation, dictionary, news, and more.
- 💾 **Memory** — SQLite-backed long-term memory plus session/short-term context
  and automatic fact extraction.
- 🎙️ **Voice** — optional TTS (Edge TTS) and STT (SpeechRecognition).
- 🌐 **Web UI** — streaming responses, tool cards, conversation history, and a
  voice visualizer.
- 🔒 **Local-first** — no cloud dependency for inference; secrets stay in `.env`.

---

## Architecture

```
assistant/
├── main.py          # Entry point (CLI + web)
├── runtime.py       # Wires config, LLM client, tools, memory, voice together
├── server.py        # HTTP + SSE server for the web UI
├── agent/           # Agent loop, context building, safety
├── llm/             # llama.cpp client + backends
├── tools/           # Built-in tools (files, apps, web, spotify, system, …)
├── memory/          # Long-term (SQLite), short-term, session, auto-extract
├── voice/           # TTS / STT
├── config/          # Config loader + config.json
├── gui/             # Minimal fallback HTML
└── web/             # React + Vite frontend (built to web/dist)
```

---

## Prerequisites

- **Python 3.11+**
- A **llama.cpp server** and a **GGUF model**. Build llama.cpp (CPU, CUDA,
  Vulkan, Metal — your choice) and download a model such as
  `Qwen2.5-7B-Instruct` in GGUF format.
- **Node.js 18+** (only if you want to rebuild the web UI).

---

## Setup

```bash
# 1. Clone and enter the project
git clone <your-repo-url> Thursday && cd Thursday

# 2. Create a virtual environment and install
python3 -m venv .venv
source .venv/bin/activate
pip install -e .            # add ".[dev]" for tests/linting

# 3. Configure environment
cp .env.example .env
# then edit .env — set MODEL_PATH, LLAMA_SERVER_BIN, and any API keys
```

All runtime configuration lives in two places:

- **`.env`** — secrets and machine-specific settings (model path, ports, API
  keys). Gitignored; never committed.
- **`assistant/config/config.json`** — app behavior (tools, voice, agent
  prompt, memory/log locations). Paths here are relative to the project root.

---

## Running

### 1. Start the local model server

```bash
./run_vulkan_server.sh          # reads MODEL_PATH / LLAMA_* from .env
```

Or start any llama.cpp server yourself on the host/port in `.env`
(`LLAMA_HOST` / `LLAMA_PORT`, default `127.0.0.1:8080`).

### 2. Start Thursday

```bash
./run.sh                        # CLI (default)
./run.sh --web                  # Web UI at http://127.0.0.1:5005
```

`run.sh` creates the venv on first use and launches `python -m assistant.main`.
You can also run it directly:

```bash
python -m assistant.main            # CLI
python -m assistant.main --web      # Web UI
python -m assistant.main --config path/to/config.json
```

---

## Web UI development

The prebuilt frontend is served from `assistant/web/dist`. To develop or rebuild it:

```bash
cd assistant/web
npm install
npm run dev        # Vite dev server (proxies to THURSDAY_BACKEND)
npm run build      # Produces assistant/web/dist served by the Python server
```

The dev server proxies API calls to the backend at `THURSDAY_BACKEND`
(default `http://127.0.0.1:5005`).

---

## Configuration reference

| Setting | Where | Notes |
|---|---|---|
| Model host/port | `.env` → `LLAMA_HOST` / `LLAMA_PORT` | Overrides `model.base_url` in config.json |
| Model & server paths | `.env` → `MODEL_PATH` / `LLAMA_SERVER_BIN` | Used by `run_vulkan_server.sh` |
| Web server host/port | `.env` → `THURSDAY_HOST` / `THURSDAY_PORT` | Default `127.0.0.1:5005` |
| Web search keys | `.env` → `GOOGLE_CSE_*`, `SEARCH_API_*` | Optional; DuckDuckGo fallback otherwise |
| Tools / prompt / voice | `assistant/config/config.json` | App behavior |

---

## Testing

```bash
pip install -e ".[dev]"
python -m pytest assistant/tests/
```

---

## License

Personal project — add a license of your choice before publishing.
