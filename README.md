# Thursday

**A desktop AI that does the work where you can see it.**

Thursday is a local-first, JARVIS-style assistant for Linux. It connects an OpenAI-compatible
model to real desktop capabilities: searching and opening files, controlling apps and media,
researching the web, working with Gmail and Calendar, remembering useful context, responding by
voice, and now launching contained Codex project sessions from the same interface.

The important distinction is simple: Thursday does not stop at telling you what command to run.
For action requests, it selects a purpose-built tool, shows the call in the interface, executes it,
and reports the result. Local llama.cpp and cloud providers share the same agent, memory, safety,
and UI layers.

## Why Thursday exists

Thursday started as a deliberately **local-only** experiment: could an assistant feel genuinely
useful without becoming another cloud tab that asks you to copy commands, switch windows, and do
the work yourself? The first version ran against llama.cpp on my own hardware. Privacy, ownership,
and the freedom to work offline were the point—not features bolted on afterward.

That beginning shaped the project. I wanted an assistant that could live where my work actually
happens: in the terminal, file manager, music player, browser, notifications, and keyboard flow. A
chat response is only half useful when the real task is to find a file, open the right app, draft an
email, check a calendar, or adjust the system while staying focused.

As the project grew, local inference remained the foundation, but capability became a practical
choice rather than an ideology. Thursday now supports cloud OpenAI-compatible models when a faster
or stronger model is worth using, while keeping the same local-first architecture: one agent loop,
the same visible tools, local memory, explicit safety controls, and the option to return to a fully
local llama.cpp setup at any time.

It is also intentionally more than a web wrapper. The web interface is the control surface, but
Thursday is wired into Linux through desktop entries, Hyprland hotkeys, a Quickshell voice overlay,
native system and media tools, and a loopback-only local server. The goal is a personal operating
layer that stays observable: every action has a tool behind it, a visible result, and a boundary you
can understand.

## Watch Thursday in action

These are real desktop demos, not mocked product tours. They show the assistant operating through
its visible tool layer, with the same guard rails and native integrations described in this README.

- [Desktop workflows: file discovery, Gmail drafts, and Calendar automation](https://drive.google.com/file/d/1VBo7DjoN34oAEptECPhUGLXbTuoWGxJp/view?usp=drive_link) — Thursday finds the right files, uses the signed-in browser workflow for Gmail, and prepares Calendar actions through its confirmation flow.
- [System awareness and media control: live vitals and Spotify](https://drive.google.com/file/d/13Sw3TUYcaSJo1eIMX1XZZqWVLdZNHmjq/view?usp=drive_link) — Thursday reads machine health and controls the dedicated Spotify integration without treating another media player as a fallback.
- [Native desktop integration: Thursday inside the OS](https://drive.google.com/file/d/1Wd_5lowulWIXl_skxhNKPAqPBthTayDT/view?usp=drive_link) — a look at the launcher, Hyprland controls, and Quickshell voice overlay that make Thursday a Linux desktop companion rather than a browser-only wrapper.

[Browse the complete demo collection in Google Drive](https://drive.google.com/drive/folders/1s2c5p86JadB3azm1t-a7LQFfNALwEmIG?usp=sharing).

| Interface | How |
|-----------|-----|
| **CLI** | Fast terminal chat (default) |
| **Web UI** | React app over HTTP + SSE at `http://127.0.0.1:5005` |

[Read the architecture](docs/ARCHITECTURE.md) · [Run the complete project demo](docs/DEMO.md) ·
[Explore the Codex workspace](codex_workspace/README.md)

> Secrets stay in `.env`. Inference stays on your machine unless you opt into a cloud provider.

---

## Features

- 🧠 **Local or cloud LLM** — llama.cpp *or* API keys (OpenAI / OpenRouter / Groq / …)
- 🛠️ **Tool-using agent** — indexed whole-PC file search, Thunar reveal, guarded shell, source-backed web research, visual website reviews, Gmail drafts, Spotify, memory, voice, and more
- 🧑‍💻 **Codex project studio** — refine a brief, choose a Codex model, and open an isolated project session in Kitty
- 💾 **Memory** — SQLite long-term + session history + automatic fact extraction
- 🎯 **Smart tool filtering** — sends only relevant tools each turn (saves context)
- 🎙️ **Voice** — Edge TTS + SpeechRecognition (optional)
- ⌨️ **Global hotkeys** — Super+C opens Thursday; hold Super+Alt to push-to-talk with a live transcript overlay
- 🔒 **Explicit guard rails** — path boundaries, shell policy, confirmation gates, secret redaction, optional API token, and a loopback bind guard
- 🌐 **Web UI** — streaming, tool cards, conversations, voice visualizer, action confirmations

## What Thursday can do

| Capability | What it looks like in practice |
|-----------|--------------------------------|
| **Desktop control** | Open or focus applications, manage windows, adjust volume and brightness, send notifications, inspect system health, and work with the clipboard. |
| **Files and terminal** | Search indexed filenames across the PC, inspect content, reveal results in Thunar, write within configured roots, and run guarded terminal commands. |
| **Live research** | Search the web, extract readable pages, produce source-backed answers, review websites with Playwright, and open visible Google or YouTube results when asked. |
| **Personal workflows** | Draft Gmail messages without sending, summarize the newest inbox rows, read or change Calendar events with confirmation, and use the signed Brave Helper with existing sessions. |
| **Media and focus** | Find and control Spotify specifically, play YouTube only when requested, run timers, translate text, and optionally manage focused Instagram Reels viewing. |
| **Memory and conversation** | Keep named preferences, facts, entity profiles, conversation history, and short-term context in a local SQLite store with explicit delete controls. |
| **Voice and presence** | Accept microphone input, stream responses, speak through Edge TTS, and expose push-to-talk through a non-focus-stealing Quickshell overlay. |
| **Software projects** | Turn a brief into a contained Codex CLI session, select Terra/Luna/Sol or a custom model, and keep each generated project in its own workspace. |

Thursday discovers tool modules at runtime and uses intent-based filtering, so the model receives a
small relevant toolset instead of the entire catalog on every turn. Tool calls, streaming progress,
results, and confirmation requests remain visible in the interface.

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
./run_with_llm.sh     # Desktop app mode: one window on :5005, closing it stops everything
python -m assistant.main --config path/to/config.json
```

The **desktop entry** (`thursday.desktop`) uses `run_with_llm.sh`: it opens a single app
window at `http://127.0.0.1:5005` (focusing instead of duplicating), and when the last
Thursday window closes it shuts down the Thursday server. With the optional local provider,
it also stops the llama.cpp server it manages. The default desktop configuration uses OpenAI
`gpt-5.6-luna` and requires `OPENAI_API_KEY` in `.env`.

| Setting | Where | Notes |
|---------|--------|--------|
| LLM provider / model / keys | `.env` | See `.env.example` |
| Local llama host/port | `.env` → `LLAMA_*` | Used only when `LLM_PROVIDER=local` |
| Web host/port | `.env` → `THURSDAY_HOST` / `THURSDAY_PORT` | Default `127.0.0.1:5005` |
| API token | `.env` → `THURSDAY_API_TOKEN` | Optional; required for remote binds |
| Tools / prompt / voice | `assistant/config/config.json` | Behavior |
| User name | `.env` → `THURSDAY_USER_NAME` | Injected into system prompt |
| Model selection | `./model.sh` | Test/switch GGUFs without editing `.env` |

## Building projects with Codex

The **Codex Project** button turns Thursday into a project launchpad without mixing generated work
into the assistant source tree.

1. Open Thursday and select **Codex Project**.
2. Choose a workspace name such as `portfolio-dashboard`.
3. Choose the Codex default or an explicit Terra, Luna, Sol, or custom model identifier.
4. Use **Refine with Thursday** when the product, stack, or design needs clarification, or choose
   **Open Codex in Kitty** when the brief is ready.
5. Thursday validates the request and opens Codex inside `codex_workspace/<project-name>`.

Project names accept lowercase letters, numbers, and hyphens. Path-like names are rejected. The
child Codex process runs with a workspace-write sandbox, without Thursday's OpenAI provider key,
and cannot receive approval to expand its filesystem access. Thursday reports that the interactive
session has started; the visible Kitty terminal remains the source of truth for progress and
completion.

The repository includes [Ritual](codex_workspace/todo-app/README.md), a polished local habit tracker,
as a concrete demo artifact produced through this workflow.

---

## Switching local models

`./model.sh` swaps the llama.cpp model on the fly — test first, apply permanently only
when you're happy:

```bash
./model.sh            # rofi picker — applies permanently, reopens open windows
./model.sh list       # all ~/Models/*.gguf with RUNNING / CONFIGURED markers
./model.sh use 8b     # switch now (runtime only — .env untouched)
./model.sh test 4b    # switch + raw llama ping: reply + tokens/sec
./model.sh apply 4b   # switch + write MODEL_PATH to .env (permanent)
./model.sh revert     # back to the model configured in .env
```

- Names are fuzzy: `8b`, `gemma`, `qwen3-4b`, or the full filename all work.
- Per-model flags live in `model_args_for()` at the top of `model.sh` — Qwen3 models
  automatically get thinking disabled (`--jinja --chat-template-kwargs {"enable_thinking":false}`),
  so answers never contain `<think>` spam.
- llama starts are serialized via `/tmp/thursday-llama.lock` across the desktop launcher,
  quickshell button and `model.sh` — no port races.
- Thursday's `/health` model *label* may lag until Thursday restarts (cosmetic only —
  inference always uses the RUNNING model).

---

## Global hotkeys (Linux / Hyprland)

| Hotkey | Action |
|--------|--------|
| **Super+C** | Open Thursday: focuses the existing window, starts the server if down, or opens the Web UI |
| **Super+Alt** (hold) | Push-to-talk: records while held with a live transcript, then shows the streamed answer in a centered **quickshell** overlay — without stealing window focus (spoken reply via TTS) |

Voice overlay integration (quickshell):
- `ThursdayVoice.qml` reads `/tmp/thursday_voice_overlay.json` (state/transcript/answer, updated atomically by the daemon).
- `/tmp/thursday_voice_active` turns the bar's Thursday button red while you're being heard.
- The button's notification popup is suppressed while the voice HUD shows the answer; without quickshell the daemon falls back to eww, then dunst.

Setup:

```bash
pip install -e ".[hotkeys]"   # evdev for global key listening
sudo usermod -aG input $USER  # read /dev/input (re-login after)
./run_hotkeys.sh              # singleton daemon; autostarted via hyprland exec-once
```

- The overlay uses **eww** (`~/.config/eww/thursday.yuck`) and falls back to **dunst** notifications.
- The Super+C bind lives in `hyprland.conf` → `~/.config/hypr/scripts/thursday-open.sh`.
- Hyprland ≥0.55 routes `hyprctl dispatch` through Lua; both integrations auto-fallback between legacy and `hl.dsp` syntax.

---

## Architecture

```
assistant/
├── main.py          # Entry (CLI + web)
├── hotkeys.py       # Global hotkey daemon (Super+Alt push-to-talk)
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

At runtime, the request path is:

```text
CLI / Web UI / Voice
        ↓
HTTP + SSE server and conversation state
        ↓
Agent loop → smart tool selection → confirmation policy
        ↓                    ↓
OpenAI-compatible model      Built-in tools / Brave Helper / Codex CLI
        ↓                    ↓
Streamed answer, visible tool result, and persisted conversation
```

See [Architecture and internals](docs/ARCHITECTURE.md) for component boundaries, data flow,
security controls, integrations, and extension points.

---

## Safety notes

Thursday can run shell commands and touch files on **your** machine.

- Default bind is **loopback only**. Remote bind needs `THURSDAY_ALLOW_REMOTE=1`.
- Set `THURSDAY_API_TOKEN` if you expose the port beyond localhost.
- Path tools use `read_roots` / `write_roots` (override with `THURSDAY_*_ROOTS`).
- Shell policy: denylist + optional whitelist; dangerous actions can require **web confirmation**.
- Power user: `THURSDAY_ALLOW_SHELL=true`, `THURSDAY_UNRESTRICTED_PATHS=1`.

The checked-in `assistant/config/config.json` reflects the author's power-user desktop setup and
allows shell access plus broad read roots. It is not the recommended shared-machine profile. Start
from `assistant/config/config.safe.example.json`, then expand permissions intentionally for the
machine where Thursday will run.

### Desktop integration packages (Arch Linux)

Thursday uses `wtype` to drive Spotify's focused Wayland search UI and `plocate` for fast
whole-PC filename searches:

```bash
sudo pacman -S --needed wtype plocate
sudo systemctl enable --now plocate-updatedb.timer
sudo systemctl start plocate-updatedb.service
```

Spotify commands only target an MPRIS player whose name contains `spotify`; another active
player such as YouTube or mpv is never used as a fallback. File-search results are numbered,
so follow-ups such as “open the second one” can reveal that file in Thunar.

Website reviews use Python Playwright with the already-installed Brave binary. Gmail, Google
Calendar, and Instagram use the signed Thursday Brave Helper in the normal Brave profile, so they
reuse the accounts already signed in there. Drafting opens a populated unsent Gmail compose window
and never activates Send. `summarize_inbox` reads the newest 20 inbox rows without asking for a
Gmail password, `watch_reels` advances only while Instagram is visible and focused, and Calendar
writes always show a confirmation first.

Install Thursday's user-level `mailto:` handler to open populated Gmail drafts in normal Brave:

```bash
python -m assistant.integrations.mailto_handler --install
```

Install or repair the persistent managed Brave helper:

```bash
python -m assistant.integrations.brave_helper --install
```

The Thursday desktop launcher performs the same idempotent check and requests one-time system
authorization when the helper is missing or outdated. Brave then loads it on every normal launch;
no custom Brave desktop entry or `--load-extension` flag is needed. Check the installation with
`python -m assistant.integrations.brave_helper --status`. Repeated summary requests are serialized,
so they do not create duplicate Gmail tabs or race while reading the inbox. Brave may display
"Managed by your organization" because the helper is installed through its Linux managed policy.
Remove only Thursday's helper and policy with
`python -m assistant.integrations.brave_helper --uninstall`.

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

For the release/demo gate, also build the web client and run the focused Codex tests:

```bash
cd assistant/web && npm ci && npm run build
cd ../..
python -m pytest assistant/tests/test_codex_orchestrator_tool.py \
  assistant/tests/test_codex_routing.py assistant/tests/test_tool_groups.py
```

## Demo

The strongest demo tells one connected story: Thursday understands a request, chooses the right
capability, asks before consequential actions, remembers context, and can hand a complete software
brief to Codex without hiding the work. The full presenter script, prompts, reset steps, fallback
paths, timing, and recording checklist live in [docs/DEMO.md](docs/DEMO.md).

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
