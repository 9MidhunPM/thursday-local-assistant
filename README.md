# Thursday

**A desktop AI that does the work where you can see it.**

Thursday is a local-first, JARVIS-style assistant for Linux. It connects an OpenAI-compatible
model to real desktop capabilities: searching and opening files, controlling apps and media,
researching the web, working with Gmail and Calendar, remembering useful context, responding by
voice, and now launching contained Codex project sessions from the same interface.

The desktop copilot now adds native Hypruse controls, screenshot understanding, durable task
checkpoints, and personal-memory review. The checked-in desktop profile uses GPT-6 Luna through
the Responses API; local llama.cpp and other compatible providers remain configurable.

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
- 💾 **Grounded memory** — SQLite history and personal facts extracted from literal user evidence, with source references and correction history
- 🎯 **Tool discovery** — relevant tools each turn, with additional capabilities available as a task crosses domains
- 🖥️ **Hypruse copilot** — discovers live MCP schemas, targets windows explicitly, uses accessible controls, and sends transient captures to the model during tasks
- ⏯️ **Task checkpoints** — progress, Stop/Resume, action evidence, and an estimated token cost in the web UI
- 🔎 **Memory review** — inspect sources, correct values, and forget personal facts or saved notes
- 🎙️ **Voice** — Edge TTS + SpeechRecognition (optional)
- ⌨️ **Global hotkeys** — Super+C opens Thursday; hold Super+Alt to push-to-talk with a live transcript overlay
- 🔒 **Explicit guard rails** — path boundaries, shell policy, confirmation gates, secret redaction, optional API token, and a loopback bind guard
- 🌐 **Web UI** — streaming, tool cards, conversations, voice visualizer, action confirmations

## Desktop copilot upgrade

- **Tasks keep their progress.** Outcomes are saved after each delivered operation. Stop cancels
  the model stream and owned desktop bridge; Resume creates a child run from the checkpoint.
  Restarting marks unfinished runs interrupted. Already delivered actions are retained.
- **Desktop controls use observed targets.** Hypruse discovers 14 live MCP tools, uses window
  addresses and accessible control names, and supplies inline screenshots with geometry metadata.
  Captures are transient and excluded from conversation storage and model logs.
- **Approval clicks work with the shared desktop.** After Approve, Thursday re-reads the desktop,
  rechecks the original target, and delivers the approved action once. Moving the mouse or changing
  focus to approve is acknowledged. A closed/replaced target or changed pointer geometry pauses
  before delivery; strict seat and authentication guards stay enabled.
- **Memory can be reviewed and corrected.** Open **Memory** to inspect user evidence, correct a
  value, or forget it and its correction chain. Legacy values are labelled when their original
  source is unavailable. Forgetting memory does not delete the separate conversation transcript.
- **Tool details are readable.** Nested arguments and arrays of results display as structured
  text, including after conversation history is reloaded.

GPT-6 Luna and GPT-5.6 Luna both completed **23/24** workflows in the latest controlled evaluation;
GPT-6 Luna's estimated token cost was **about 55% lower**. These synthetic workflows support the
default cost choice, rather than a universal intelligence ranking. The assistant does not
automatically escalate to a more expensive model. See the [measured comparison](docs/luna-evaluation.json)
and [setup, evidence, limitations, rollback, and roadmap](docs/COPILOT.md).

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

The desktop copilot release uses GPT-6 Luna with the Responses API by default; GPT-5.6 Luna
and local llama.cpp remain configurable. Desktop automation requires a running Hyprland graphical
session and the optional `hypruse` extra. App accessibility support varies; the React web UI is
the control surface for task and memory review.

Example requests:

| Request | What to expect |
| --- | --- |
| “Look at my desktop and tell me what's open. Don't change anything.” | A current desktop observation and a summary. |
| “Focus Brave, maximize its window, and verify the result.” | Window operations followed by an observation. |
| “Read the error visible in my active window and explain it.” | Accessible text or a cropped screenshot used as evidence. |
| “Check today's calendar and suggest a work plan.” | Calendar reads and a suggested schedule; writes still require confirmation. |
| “Remember that I prefer short replies.” | A personal preference grounded in that statement, available for review. |

During a longer task, use **Stop task**, then **Resume task**. Expand the task status to inspect
tool outcomes, token counts, and an estimated cost. That estimate excludes background memory
extraction and nested integration requests. It is not an invoice or a guarantee of task success.

---

## Quick start

### 1. Clone & install

```bash
git clone https://github.com/9MidhunPM/thursday-local-assistant.git Thursday
cd Thursday
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[hypruse]'
cp .env.example .env
```

Choose a provider and fill in `.env` below before the first launch. `run.sh` can also create a
missing `.venv` and install the base package, but it does not install the Hypruse extra into an
existing environment. On a desktop without Hyprland, install the base package with
`.venv/bin/python -m pip install -e .` and set `THURSDAY_HYPRUSE=0` in `.env`.

Or manually:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[hypruse,voice,desktop]"   # choose the extras needed on this machine
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
LLM_MODEL=gpt-6-luna
LLM_API_MODE=auto            # OpenAI uses Responses; compatible providers use Chat
LLM_REASONING_EFFORT=low
OPENAI_API_KEY=sk-...        # or OPENROUTER_API_KEY / GROQ_API_KEY / …
# LLM_API_KEY=...            # generic fallback
```

Then:

```bash
./run.sh --web
```

No llama.cpp process required. Cloud providers are marked ready as soon as a key is present.
That readiness flag does not verify account access or billing. For a manual Luna comparison,
set `LLM_MODEL=gpt-5.6-luna` and restart Thursday.

#### Option C — Custom OpenAI-compatible endpoint

```bash
LLM_PROVIDER=custom
LLM_BASE_URL=https://your-proxy.example/v1
LLM_MODEL=your-model-name
LLM_API_KEY=your-key
```

### 3. Personalize

```bash
THURSDAY_USER_NAME="Midhun P M"
```

Quote names containing spaces: the shell launchers also read `.env`.

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
`gpt-6-luna` and requires `OPENAI_API_KEY` in `.env`. Run with `./run.sh --web --no-browser`
when the server should stay available independently of the desktop app window.

| Setting | Where | Notes |
|---------|--------|--------|
| LLM provider / model / keys | `.env` | See `.env.example` |
| Local llama host/port | `.env` → `LLAMA_*` | Used only when `LLM_PROVIDER=local` |
| Web host/port | `.env` → `THURSDAY_HOST` / `THURSDAY_PORT` | Default `127.0.0.1:5005` |
| API token | `.env` → `THURSDAY_API_TOKEN` | Optional; required for remote binds |
| Tools / prompt / voice | `assistant/config/config.json` | Behavior |
| User name | `.env` → `THURSDAY_USER_NAME` | Injected into system prompt |
| Model selection | `./model.sh` | Test/switch GGUFs without editing `.env` |
| Desktop bridge | `.env` → `THURSDAY_HYPRUSE` | Requires the optional extra and a Hyprland session |
| Task budget | `.env` → `THURSDAY_MAX_TOOL_STEPS` / `THURSDAY_TASK_TIMEOUT` | Checked between operations; defaults are 24 steps / 300 seconds |
| API transport / effort | `.env` → `LLM_API_MODE` / `LLM_REASONING_EFFORT` | Native Responses for OpenAI by default; low reasoning effort |

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
./run_hotkeys.sh              # start the singleton daemon manually
```

Keep one autostart owner. The author's desktop uses a supervised `thursday-hotkeys.service`
under `graphical-session.target`; that service configuration lives outside this repository.

- Without Quickshell, the overlay falls back to **eww** (`~/.config/eww/thursday.yuck`), then **dunst** notifications.
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
├── agent/           # Agent loop, checkpoints, safety, web confirmations
├── llm/             # Responses + compatible Chat transports
├── integrations/    # Hypruse MCP, Brave Helper, desktop integrations
├── evals/           # Repeatable controlled copilot/model evaluation
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
Responses / Chat model       Built-in tools / Hypruse / Brave Helper / Codex CLI
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

The current Python suite passes **146 tests**. Copilot regressions cover completed Responses
output, cancellation, checkpoint recovery, grounded memory, approval handoff, large inline MCP
images, and protected task/memory HTTP routes. The production web build and controlled
desktop/mobile UI flows passed. Live Hypruse checks used temporary owned GTK windows; they do
not establish unattended reliability across all apps or production voice/account integrations.

List evaluation cases without making model requests:

```bash
.venv/bin/python -m assistant.evals.copilot
```

The `--live` option deliberately makes paid API requests. See [the evaluation instructions and
retained reports](docs/COPILOT.md#luna-comparison) before running it. Existing repository-wide
Ruff style debt remains; the new modules pass their full rules and focused Python error checks
pass across the repository.

## Demo

The strongest demo tells one connected story: Thursday understands a request, chooses the right
capability, asks before consequential actions, remembers context, and can hand a complete software
brief to Codex without hiding the work. The full presenter script, prompts, reset steps, fallback
paths, timing, and recording checklist live in [docs/DEMO.md](docs/DEMO.md).

---

## Configuration tips for a stronger assistant

| Goal | Setting |
|------|---------|
| Longer multi-step work | `THURSDAY_MAX_TOOL_STEPS=24` and `THURSDAY_TASK_TIMEOUT=300`; exhaustion pauses for review |
| Request context | `LLM_CONTEXT_BUDGET=24000`; the estimator also reserves space for inline images |
| Faster local turns | Keep `smart_tool_filter` on (default) |
| Your name / style | `THURSDAY_USER_NAME` + `agent.system_prompt` in config |

Document ingestion with citations, durable reminders, reviewed reusable routines, Codex job
completion tracking, and voice interruption improvements remain on the later roadmap. The
Quickshell voice overlay and fallback HTML do not yet include the React task/memory panels.

---

## License

Personal project — add a license of your choice before publishing.
