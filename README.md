# Thursday

**A desktop AI that does the work where you can see it.**

Thursday connects a configurable AI model to real tools for files, apps, research, Gmail,
Calendar, Spotify, personal memory, and contained Codex project sessions. It runs a local
Python server with a native Quickshell companion, a React web app, and a terminal interface.
The default model is GPT-6 Luna through the Responses API; local llama.cpp and other
OpenAI-compatible providers remain configurable.

The native companion handles chat, push-to-talk, spoken replies, approvals, and task controls
without depending on whether the desktop bar or a browser window is open. Thursday reports
observed results and keeps durable checkpoints for interrupted work.

**Local process and storage do not mean offline processing.** The default model uses a cloud
API, Google speech recognition receives recorded audio, and the default Edge TTS voice receives
response text. The optional local neural voice runs on the machine.
Desktop captures and relevant context can reach the configured model during requested tasks.
See [privacy and data flow](docs/NATIVE_DESKTOP.md#privacy-and-data-flow).

## Why Thursday exists

Thursday started as a local-only, JARVIS-style experiment with llama.cpp on personal hardware.
The aim was an assistant that could help inside the terminal, file manager, music player,
browser, and keyboard flow: find the file, open the app, or prepare the draft while keeping
the work visible and under the user's control.

That origin still shapes the project. Local inference remains an option alongside cloud models;
both use the same agent, visible tools, local memory, and explicit action boundaries. The native
companion brings those controls into the desktop, while the web app provides the detailed view.

## Interfaces and capabilities

| Surface | Purpose |
| --- | --- |
| Native Quickshell companion | Chat, microphone, voice HUD, approvals, Stop/Resume, local voice settings |
| Web app at `http://127.0.0.1:5005` | Detailed history, structured tool results, task evidence, memory review, Codex project controls |
| CLI | Terminal conversation through the same tools and model configuration |

- **Desktop control:** Hypruse observes the active session, addresses windows explicitly, uses
  accessible controls where available, and returns transient captures when needed.
- **Files and research:** indexed filename search, file operations within configured roots,
  guarded terminal commands, source-backed research, and visual website reviews.
- **Personal workflows:** unsent Gmail drafts, inbox summaries, confirmed Calendar writes,
  Spotify control, and grounded personal memory with correction and deletion controls.
- **Tasks:** saved outcomes, cooperative cancellation, resumable checkpoints, and approval
  before consequential actions. Completed actions remain completed after cancellation.
- **Voice:** one backend microphone controller, bounded recordings, ordered speech playback,
  and interruption when a new voice request starts. Typed native replies are silent by default.
- **Codex projects:** refine a brief and launch a visible Kitty session inside
  `codex_workspace/<project-name>`, without passing Thursday's provider credentials to Codex.

[Native desktop setup](docs/NATIVE_DESKTOP.md) · [Architecture](docs/ARCHITECTURE.md) ·
[Copilot behavior and evaluation](docs/COPILOT.md) · [Demo guide](docs/DEMO.md)

## Quick start

From the checkout, create an environment and install the desktop extras:

```bash
git clone https://github.com/9MidhunPM/thursday-local-assistant.git Thursday
cd Thursday
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[hypruse,hotkeys,voice,desktop]'
cp .env.example .env
chmod 600 .env
```

Run `cp` only for a new configuration; keep an existing `.env`. PyAudio requires the system's
PortAudio development support. The native integration also needs a running Hyprland session,
Quickshell with Qt Multimedia, systemd user services, and microphone access. Install `ffmpeg`
for speech normalization and measured waveforms, and PipeWire's `pw-dump` for output-route feedback.
See the [prerequisites](docs/NATIVE_DESKTOP.md#prerequisites) before installing desktop configuration.

For a CLI/web installation without Hyprland, install the base package instead and set
`THURSDAY_HYPRUSE=0`. `run.sh` checks the existing environment; it never installs dependencies.

### Configure the model and voice

Edit the ignored `.env`:

```bash
LLM_PROVIDER=openai
LLM_MODEL=gpt-6-luna
LLM_API_MODE=auto
LLM_REASONING_EFFORT=low
OPENAI_API_KEY=your-api-key
THURSDAY_USER_NAME="Your name"
THURSDAY_TTS_ENGINE=online
THURSDAY_TTS_LOCAL_VOICE=bm_george
THURSDAY_TTS_SPEED=1.0
THURSDAY_TTS_VOICE=en-GB-RyanNeural
THURSDAY_TTS_RATE=+0%
```

The default speech engine uses Edge's British male `en-GB-RyanNeural`. Edge TTS 7.3.1 or newer
is required for the updated service handshake. An optional local Kokoro voice provides offline
synthesis and fallback when installed. Personal identity, voice, and
provider credentials belong in `.env`; the public configuration uses a generic user name.
The response style favors concise, natural confirmations and clear failures grounded in tool
results. `THURSDAY_SYSTEM_PROMPT` can override the base prompt; the shared expression guidance
is appended by the loader.

Install the optional local neural voice explicitly:

```bash
.venv/bin/python -m assistant.voice.neural_setup
```

This optional setup uses an isolated Python 3.13 environment and checksum-verified model/voice files in
private local storage. It does not require a paid speech API. See the
[voice setup and recovery guide](docs/NATIVE_DESKTOP.md#natural-voice) for dependencies and controls.

For local model inference, replace the provider values with:

```bash
LLM_PROVIDER=local
LLAMA_HOST=127.0.0.1
LLAMA_PORT=8080
MODEL_PATH=~/.models/your-model.gguf
LLAMA_SERVER_BIN=~/llama.cpp/build/bin/llama-server
```

Start the model separately with `./run_vulkan_server.sh` or your existing llama.cpp service.
Choosing a local model does not change the configured STT/TTS providers. For another compatible
API, use `LLM_PROVIDER=custom`, `LLM_BASE_URL`, `LLM_MODEL`, and `LLM_API_KEY`.

### Install the native companion

Run from your graphical session:

```bash
.venv/bin/python -m assistant.desktop_install install --dry-run
.venv/bin/python -m assistant.desktop_install install
.venv/bin/python -m assistant.desktop_install doctor
```

The installer targets the supported existing Hyprland Lua and Quickshell layout. It refuses
unknown or conflicting layouts instead of replacing the desktop configuration. It backs up
managed files privately, installs one root-level companion, and enables `thursday.service`
and `thursday-hotkeys.service`. [Installation, recovery, and rollback](docs/NATIVE_DESKTOP.md)
cover the exact layout, service commands, and safe uninstall procedure.

| Shortcut | Action |
| --- | --- |
| **Super + C** | Toggle native chat |
| **Alt + Space** | Toggle native chat; focus the composer when opening |
| **Super + Alt**, held | Record; release to transcribe and submit |

A recording stops automatically after 55 seconds. The HUD does not take keyboard focus.
Closing chat keeps the server and keyboard service available. Open the settings menu for
**Voice on/off**, **Speak typed replies**, **Motion on/off**, and **Full app**.
The header's **Web app** button and the application menu's **Thursday Web** entry open
the detailed interface. Repeated launches focus its existing dedicated window.

### Other launch modes

```bash
./run.sh                         # CLI with local HTTP server
./run.sh --web                   # Web app
./run.sh --web --no-browser      # Foreground server without opening a browser
./run_web.sh                     # Open/reuse the web app through the installed service
./run_with_llm.sh                # Start the installed service and toggle native chat
./run_hotkeys.sh                 # Start the installed supervised hotkey service
```

The two desktop wrappers require the native installation. They do not create a second daemon
or stop the server when a window closes. For an installed desktop, manage the backend with
`systemctl --user restart thursday.service` rather than running a second foreground server.

## Configuration and boundaries

| Setting | Location / behavior |
| --- | --- |
| Model, endpoint, provider keys | `.env`; see `.env.example` |
| Name and spoken voice | `THURSDAY_USER_NAME`, `THURSDAY_TTS_ENGINE`, `THURSDAY_TTS_LOCAL_VOICE`, `THURSDAY_TTS_SPEED`; Edge voice/rate when selected |
| Native preferences | Local `~/.config/thursday/native.json`, respecting `XDG_CONFIG_HOME` |
| Desktop bridge | `THURSDAY_HYPRUSE=1`; requires the optional packages and live session access |
| Task budget | `THURSDAY_MAX_TOOL_STEPS=24`, `THURSDAY_TASK_TIMEOUT=300` |
| Request context | `LLM_CONTEXT_BUDGET=24000`; image accounting is approximate |
| HTTP listener | `THURSDAY_HOST=127.0.0.1`, `THURSDAY_PORT=5005` |
| API authentication | Optional `THURSDAY_API_TOKEN`; native headers and a browser connection prompt |
| Tool roots, shell policy, STT | `assistant/config/config.json` and supported environment overrides |

The checked-in desktop profile allows shell access and broad reads. For a shared machine,
start from `assistant/config/config.safe.example.json` and explicitly choose permissions.
Read/write roots, shell policy, observed desktop targets, and action confirmations are separate
controls; a tool result must support any completion claim.

To use the safer profile in a foreground session, stop any installed backend first, then run:

```bash
./run.sh --config assistant/config/config.safe.example.json --web
```

The file's defaults disable shell execution and Hypruse, restrict writes to the checkout, and select
local inference. Reads still include your home directory. The checkout's `.env` and exported
environment variables override the selected file, including `LLM_PROVIDER`, `LLM_MODEL`,
`LLM_BASE_URL`, `THURSDAY_ALLOW_SHELL`, `THURSDAY_HYPRUSE`, and the path settings. Review those
values before starting; `--config` does not change the installed service's configuration.

Keep the service on loopback. Non-loopback binding requires `THURSDAY_ALLOW_REMOTE=1`; configure
a token before deliberately exposing it. The native transport only accepts loopback addresses.
When a token is configured, the web app asks for it and exchanges the header credential for a
one-hour HttpOnly, SameSite=Strict session cookie. It does not save the token in URLs or browser
storage. Never put credentials in public configuration.

Memory and conversation history remain on disk until removed through their respective controls.
Generated audio and captures are transient; logs, recordings, local configuration, backups,
screenshots, and personal workspace artifacts must stay out of commits. Git ignore rules are a
convenience, not a substitute for reviewing each staged change.

## Browser and Codex integrations

Gmail, Calendar, and Instagram use the managed Thursday Brave Helper in the normal signed-in
profile. Install and inspect it explicitly:

```bash
.venv/bin/python -m assistant.integrations.brave_helper --install
.venv/bin/python -m assistant.integrations.brave_helper --status
.venv/bin/python -m assistant.integrations.mailto_handler --install
```

The native launcher does not install browser policies. Gmail drafting leaves the message unsent;
Calendar writes show a confirmation. Spotify tools select Spotify's own MPRIS player. File
search uses `plocate`; the installed desktop applications and their accessibility support affect
which integrations are available.

YouTube searches return results without opening tabs. Requests to open a channel use a matching
result and one explicit `open_url` action; repeated dispatches of the same URL within a task
reuse its recorded outcome. Slow preferred search providers have bounded attempts before the
public fallback. A browser launch result confirms dispatch, not that the page finished loading.

In the web app, **Codex Project** accepts a lowercase, hyphenated workspace name, a brief, and an
optional model identifier. **Refine with Thursday** asks for material missing details;
**Open Codex in Kitty** launches the contained interactive session. The visible terminal is the
source of truth for completion. See the [workspace guide](codex_workspace/README.md).

## Development and verification

```bash
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest assistant/tests -q
.venv/bin/python -m ruff check assistant --select E9,F63,F7,F82
cd assistant/web
npm ci
npx tsc --noEmit
npm run build
npm audit
```

Integration checks on **2026-10-20** passed **429 Python tests**, with one known `aifc`
deprecation warning from a dependency. Python error lint, TypeScript checks, the Vite 7.3.7
production build, and Qt6 QML lint passed; the npm audit reported zero vulnerabilities.
The graphical-session doctor also reported both services active and enabled with zero restarts
and no problems. These checks cover controlled fixtures and service readiness;
they do not establish successful microphone transcription or spoken playback on every machine.
The [native acceptance scenarios](docs/NATIVE_DESKTOP.md#acceptance-scenarios) keep service
readiness, rendered interaction, speech, and real desktop execution separate.

List model evaluation cases without making paid requests:

```bash
.venv/bin/python -m assistant.evals.copilot
```

The retained [Luna comparison](docs/COPILOT.md#luna-comparison) describes a dated, synthetic
24-case evaluation. `--live` deliberately makes provider requests. It is not a guarantee of
unattended desktop reliability.

## Watch Thursday in action

- [Files, Gmail drafts, and Calendar](https://drive.google.com/file/d/1VBo7DjoN34oAEptECPhUGLXbTuoWGxJp/view?usp=drive_link)
- [System status and Spotify](https://drive.google.com/file/d/13Sw3TUYcaSJo1eIMX1XZZqWVLdZNHmjq/view?usp=drive_link)
- [Native desktop integration](https://drive.google.com/file/d/1Wd_5lowulWIXl_skxhNKPAqPBthTayDT/view?usp=drive_link)
- [Complete collection](https://drive.google.com/drive/folders/1s2c5p86JadB3azm1t-a7LQFfNALwEmIG?usp=sharing)

These recordings show earlier versions; current native controls and lifecycle are documented
above. The [demo guide](docs/DEMO.md) contains workflow prompts. The repository also includes
[Ritual](codex_workspace/todo-app/README.md), a local habit-tracker demo produced through Codex.

## License

Personal project — add a license of your choice before publishing.
