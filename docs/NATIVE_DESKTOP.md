# Native desktop companion

Thursday's native interface is a single Quickshell component at the shell root. It stays connected
when chat is closed, chooses the active monitor when opened or recording begins, and leaves the
bar's visibility and unrelated desktop modules under their existing controls.

## Prerequisites

Use an existing Hyprland graphical session with Quickshell, Qt 6 Multimedia, systemd user services,
a working microphone, and the system libraries needed to build PyAudio against PortAudio. Install
Python dependencies deliberately from the checkout:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[hypruse,hotkeys,voice,desktop]'
```

Skip environment creation when `.venv` already exists. Hypruse and MCP must be installed into this
same environment; a global Codex plugin does not make them available to Thursday. The integration
uses the pinned Hypruse 0.11.0 adapter and its live MCP schemas.

The hotkey service reads evdev keyboard devices. Check `id -nG` and your device permissions first.
On systems using the `input` group, add your account only if it is not already a member:

```bash
sudo usermod -aG input "$USER"
```

New group membership requires a fresh login. Existing members do not need another group change.
This grants broad input-device access; the daemon itself only forwards the configured shortcut.

Copy `.env.example` to `.env` only when creating a new configuration, then set the model key and
personal preferences. Keep it private with `chmod 600 .env`:

```bash
THURSDAY_USER_NAME="Your name"
THURSDAY_TTS_ENGINE=online
THURSDAY_TTS_LOCAL_VOICE=bm_george
THURSDAY_TTS_SPEED=1.0
THURSDAY_TTS_VOICE=en-GB-RyanNeural
THURSDAY_TTS_RATE=+0%
```

The model, STT, and TTS choices have separate network requirements. A successful import or `/health`
response does not verify microphone hardware, provider authentication, or audio playback.

## Natural voice

The default engine uses Edge's British male `en-GB-RyanNeural`. Edge TTS 7.3.1 or newer is required:
its [upstream handshake fix](https://github.com/rany2/edge-tts/pull/492) resolves the service's
October 2026 HTTP 403 rejection. Upgrade through the checkout's dependency installation command,
then restart the backend. Speech clips are loudness-normalized with a bounded true peak; this
does not change system or per-application volume. `THURSDAY_TTS_RATE=+8%` gives a slightly quicker
online delivery, while `+0%` retains the voice's original pace.

Optional local Kokoro synthesis provides offline speech and an announced fallback when the
online provider fails. Its setup requires `uv` on `PATH` and a Python 3.13 interpreter available
to it. Install deliberately:

```bash
.venv/bin/python -m assistant.voice.neural_setup
```

The neural runtime uses a separate Python 3.13 environment, so the main application can use
Python 3.14. A persistent CPU worker reuses the loaded model across sentences; interruption
terminates an active worker and discards its unfinished audio. Speech travels through stdin,
without model API credentials in the worker environment or text in command arguments.

The default environment is `$XDG_DATA_HOME/thursday/neural-venv`
(`~/.local/share/thursday/neural-venv`), and models live in
`$XDG_CACHE_HOME/thursday/voices/kokoro` (`~/.cache/thursday/voices/kokoro`). To choose an existing
interpreter, pass `--python /path/to/python3.13`. Setup refuses to modify an unmarked existing
runtime directory, installs only hash-verified wheels, and verifies model size and SHA256.

Setup verifies fixed SHA256 hashes for the FP16 model and voice table, about 192 MB combined.
Model downloads are a setup operation, not a side effect of speaking. Keep its environment,
models, generated audio, and local installation metadata out of Git. The
[Kokoro model card](https://huggingface.co/hexgrad/Kokoro-82M) documents the Apache 2.0 model;
the [ONNX wrapper](https://github.com/thewh1teagle/kokoro-onnx) is MIT licensed and uses eSpeak NG
for phonemization, whose GPL terms remain separate.

`ffmpeg` converts generated PCM into the shared playback format and supplies measured
visualization levels. `pw-dump` observes the native output route. Missing neural files produce
an actionable setup error; the robotic voice is not an automatic replacement.

| Setting | Behavior |
| --- | --- |
| `THURSDAY_TTS_ENGINE=neural` | Local neural synthesis, with no online-provider wait |
| `THURSDAY_TTS_LOCAL_VOICE=bm_george` | British male voice; `bm_lewis` and American male `am_michael` are also supported |
| `THURSDAY_TTS_SPEED=1.0` | Normal pace; supported range 0.8–1.2 |
| `THURSDAY_TTS_ENGINE=online` | Default: Edge with `THURSDAY_TTS_VOICE` and `THURSDAY_TTS_RATE`; installed local neural fallback is announced |
| `THURSDAY_TTS_EMERGENCY_FALLBACK=false` | Keep the robotic emergency voice disabled unless deliberately enabled |
| `THURSDAY_NEURAL_PYTHON`, `THURSDAY_NEURAL_MODELS` | Override the private environment/model locations |

Restart `thursday.service` after changing these settings. The online provider can reject requests
before producing audio even when its package imports successfully. Selecting the local engine
avoids that dependency; do not mistake the emergency voice for neural synthesis.

## Install and inspect

Run these commands from the checkout inside the graphical session:

```bash
.venv/bin/python -m assistant.desktop_install install --dry-run
.venv/bin/python -m assistant.desktop_install install
.venv/bin/python -m assistant.desktop_install doctor
```

The supported existing layout contains `quickshell/shell.qml`, `Bar.qml`, and `ThursdayButton.qml`,
plus `hypr/config/autostart.lua` and `hypr/config/keybinds.lua` under `XDG_CONFIG_HOME` (normally
`~/.config`). The installer recognizes the existing `Scope`/`ShellRoot`, `Super+C`, and
`Alt+Space` Lua binding forms. Unsupported or ambiguous layouts fail before installation; this is
not a general migration tool for arbitrary Hyprland or Quickshell configurations.

The managed changes are narrow:

- Install `thursday.service` and `thursday-hotkeys.service` as user services.
- Link `quickshell/ThursdayNative` to the repository module and instantiate it once outside the
  per-monitor bar variants. Replace the old bar button with a small IPC launcher.
- Remove the old per-bar voice overlay instance and direct the chat bindings to the native IPC
  target. The voice daemon owns Super+Alt; Right Shift continues to serve the existing bar binding.
- Add a session-start wrapper that imports graphical session variables into the user manager,
  starts the supervised services, and stops them when the compositor session ends.
- Install the native and **Thursday Web** application launchers and update recognized
  Thursday-specific wrapper scripts.

The installer retains private backups and a manifest in
`$XDG_STATE_HOME/thursday/desktop-install` (default `~/.local/state/thursday/desktop-install`).
It tightens `.env`, database, and log permissions. Repeat installation is guarded by managed-file
hashes; local changes are not silently overwritten. `--no-start` prepares the files and enables
the units without starting/restarting them.

Quickshell normally reloads changed configuration. Inspect `qs ipc show` for the `thursday` target
and open it with `qs ipc call thursday open`. If file watching is disabled, reload Quickshell using
your usual shell lifecycle. The module does not start an unrelated replacement shell.

## Daily use and lifecycle

| Input | Result |
| --- | --- |
| Super + C | Toggle chat |
| Alt + Space | Toggle chat; focus its composer when opening |
| Hold Super + Alt | Start recording; releasing either modifier ends the recording |
| Microphone button | Start or finish a recording without holding a shortcut |
| Cancel voice | Cancel recording or transcription |
| Stop task | Cooperatively cancel further work; completed actions remain recorded |
| Resume | Create a child task from an interrupted, paused, cancelled, or failed checkpoint |
| Allow / Deny | Resolve the displayed action request; expired approvals cannot authorize work |

Recording is bounded to 55 seconds. A new voice hold interrupts the prior owned work and speech.
The HUD reports listening, transcription, work, approvals, and replies without taking keyboard
focus. Use **Open conversation** to inspect a longer response or approve an action. Dismissing the
HUD only hides it; use the explicit Stop/Cancel controls to cancel work.

Typed native replies are silent by default. **Speak typed replies** enables their playback;
**Voice off** mutes native playback, and **Motion off** reduces animation. These preferences live
in the ignored local `$XDG_CONFIG_HOME/thursday/native.json`. The header's **Web app** button,
the settings menu's **Full app**, the **Thursday Web** application launcher, and `./run_web.sh`
open detailed history, memory review, task evidence, and Codex project controls. They wait for the
supervised backend and reuse the existing dedicated Brave window for that local server.

The speech visualizer uses decoded audio levels at the current playback position. Silence and
stopped playback leave it flat; reduced motion uses a static level bar. The helper observes
Quickshell's own PipeWire stream and linked output while speech files are retained. A muted,
zero-volume, or disconnected route shows a playback notice. This read-only check never changes
volume and does not measure sound from the physical speaker.

Closing chat or the web tab keeps the backend available. `run_with_llm.sh` starts the installed
backend and toggles chat; `run_hotkeys.sh` starts its installed user service. `run.sh` is a foreground
CLI/web launcher that checks dependencies and never installs them or desktop configuration.

Useful service controls:

```bash
systemctl --user status thursday.service thursday-hotkeys.service --no-pager
systemctl --user restart thursday.service
systemctl --user restart thursday-hotkeys.service
systemctl --user stop thursday-hotkeys.service thursday.service
```

Restarting the backend also affects its dependent hotkey unit. The graphical-session wrapper
starts both explicitly. After changing a token used by the native helper, reload Quickshell as
well, so the helper reads the updated environment. Model and voice changes require a backend
restart; changing UI preferences applies immediately.

## Diagnosis

Start with `python -m assistant.desktop_install doctor` from the project's activated environment,
or the explicit `.venv/bin/python` command above. It reports dependency presence, session-variable
presence, service status, managed-file drift, and private-storage permissions without printing
credentials or configuration contents.

| Symptom | Check |
| --- | --- |
| Shortcut does nothing | Hotkey service state, evdev import, readable keyboard devices, input-group membership |
| Repeated dependency failure | Install the missing extra into this checkout's `.venv`; restart the failed service |
| UI says it cannot authorize | Match the local `THURSDAY_API_TOKEN`; restart the backend and reload the native helper |
| “Microphone unavailable” | Default audio input, hardware permissions, PyAudio and PortAudio availability |
| Transcription fails | STT provider connectivity/configuration; recording and key detection are separate stages |
| Text works but speech fails | TTS provider, native mute setting, Qt Multimedia playback, per-app PipeWire channel levels, default output device |
| Web interface is hard to find | Header **Web app**, **Thursday Web** in the application menu, `./run_web.sh`, or `http://127.0.0.1:5005` |
| Hypruse unavailable | Same-venv packages, actual Hyprland socket/display/session bus, sanitized desktop status in `/health` |
| Changed managed configuration | Inspect the named path and installer dry-run; do not remove the linked module piecemeal |

`/health` reports model readiness, separate voice readiness, and desktop bridge state. Package
presence is not a successful live desktop observation. Do not paste full logs, screenshots, session
state, or personal configuration into public issues. Share the sanitized doctor result and a
minimal reproduction instead.

For audio diagnosis, `qs ipc call thursday audioStatus` reports bounded playback/device/level
state without speech text or credentials. Qt can report that a clip is playing while
WirePlumber has restored zero channel levels for the application. Check the application's
stream in your volume mixer as well as the system output. Do not reset the entire audio state
or raise unrelated applications to repair one stream. `ffmpeg` supplies decoded visualization
levels; `pw-dump` supplies the PipeWire route observation.

The optional API token is sent in headers by the native helper and hotkeys. It never appears in
QML, process arguments, or event URLs. When configured, the web app asks for the local access token
and exchanges it for a one-hour HttpOnly, SameSite=Strict cookie scoped to `/api`. The operator
token is not saved in URLs, localStorage, or sessionStorage. Expiry requires entering it again;
changing the token invalidates existing browser sessions after restarting the backend.

## Privacy and data flow

| Data | Handling |
| --- | --- |
| Model prompts, relevant memories, tool results, requested captures | Sent to the configured model endpoint; the default is cloud-hosted |
| Microphone audio | Captured in memory by the backend; the default Google recognizer receives the audio |
| Spoken response text | Sent to Edge TTS by default; optional Kokoro synthesis stays local. Speech text is not placed in subprocess arguments |
| Neural model and voices | Downloaded explicitly with pinned checksums into private local storage; excluded from Git |
| Native event stream | Authenticated by the private Python helper; QML receives normalized events, not the API token |
| Generated audio | Private files in the session runtime directory; native copies expire or are deleted after playback |
| Desktop captures | Transient previews with bounded retention; excluded from persistent conversation/image logs |
| Conversations, memories, checkpoints | Persistent local SQLite data; deleting a memory does not delete its conversation |
| Configuration, preferences, logs, installer backups | Local private state; not publication artifacts |

`assistant.paths` creates Thursday runtime directories with mode `0700` and private files with
mode `0600`, using `XDG_RUNTIME_DIR` or a user-specific temporary fallback. The old shared `/tmp`
transcript/HUD files and eww/dunst fallback are no longer the native control protocol. The recorder
and hotkey daemon do not persist raw recordings or transcript text to their logs. Conversation
persistence is a separate, intentional store.

Git publication must use explicit paths and a staged secret/content review. Keep `.env`, databases,
logs, local JSON preferences, raw audio/video, desktop screenshots, and installer backups out of
commits. Captures for native QA stay local; do not attach them to a public PR by default. Ignore
rules cannot remove sensitive material that was already committed.

## Rollback

Preview rollback first:

```bash
.venv/bin/python -m assistant.desktop_install uninstall --dry-run
.venv/bin/python -m assistant.desktop_install uninstall
```

An unchanged installation restores the saved configuration and prior service enable/active state,
and removes files that installation created. User data, conversation databases, local preferences,
and the checkout remain. Tightened private-data permissions are not relaxed.

If any managed file changed after installation, uninstall reports the conflict and leaves the
coherent installation in place. It does not remove the module while another managed file still
references it. Review those changes and the private backups before resolving the conflict, then
repeat the dry-run. `--no-start` suppresses restarting the previously active services during rollback.

To disable desktop automation while retaining chat, set `THURSDAY_HYPRUSE=0` and restart the backend.
A model/voice change does not require deleting databases or uninstalling the companion.

## Acceptance scenarios

The checked development baseline has 422 passing Python tests, one known dependency `aifc`
deprecation warning, a passing Vite 7.3.7 build, and zero findings in the checked npm audit.
Controlled tests cover fragmented SSE, token headers, speech ordering/interruption, expired
approvals, microphone state ownership, dependency failures, installer conflicts, and rollback.

A separate live Quickshell fixture exercised Allow, Deny, Stop, and Resume through the rendered
controls and real HTTP backend, with one request per action and the expected checkpoint lineage.
Generated silent clips exercised native and browser playback queues; the browser check also
covered token rejection, cookie login, serialized audio fetches, and discarded late audio after
Stop. This measures the playback path, not acoustic output from physical speakers.

An installation check observed both user services active and enabled with zero restarts, a
successful sanitized doctor result, and the `thursday` IPC target. The user separately confirmed
that a physical voice request produced both a transcript and a spoken reply. A real backend task
completed with successful capability discovery and an observed `hypruse__desktop` result.
These are host-specific checks; the voice result is human confirmation rather than automated
microphone or acoustic playback measurement.

The subsequent browser repair reproduced five search-driven tabs in the original task trace.
A real channel-open task then completed with one read-only `youtube_search` and one `open_url`
dispatch. After bounding the slow preferred provider, a separate public search returned matching
channel results in 2.98 seconds without browser navigation; this is search timing, not total task
latency.

An additional live native fixture passed eight output-gate cases: usable PipeWire route, actual
Qt playback while muted, no false speaking feedback on a fresh muted clip, recovery with one
playing report, measured waveform recovery, real silence, and Stop. Its targeted fixture mute
state was restored and its processes were stopped. A saved zero per-application channel level
was separately repaired on the host, after which the user confirmed audible speech. Neural
inference produced non-silent 24 kHz audio with the persistent worker reused between requests.

Edge TTS 7.3.1 subsequently restored Ryan synthesis in direct and service-environment checks.
Speech normalization measured approximately -16.5 LUFS on a Ryan sample. A live app replay
still hit a transient online failure and used the local fallback; its diagnosis remains a
separate acceptance check. Safe failure classifications and short transient cooldowns make
that state visible without recording speech text or locking the app onto fallback for five
minutes. Human voice-quality acceptance must be confirmed after an observed online replay.

Use these scenarios when validating another installation or changing the integration:

1. Open and focus chat with both shortcuts while the bar is hidden. Close it and confirm services
   remain active. Repeat on each monitor and check that only one companion owns playback.
2. Hold Super+Alt, speak a short request, and release. Verify the transcript, accepted task,
   completed answer, audible response, and return to idle. Test the microphone button separately.
3. Cancel during recording and transcription. Start another hold during speech and verify that
   late chunks from the prior generation never resume. Check too-short and silent recordings.
4. Open the web app alongside native chat. Typed requests remain silent by default; explicitly
   enabled speech plays once on the requesting surface, including with native chat closed.
5. Approve and deny an action, reconnect during an approval, and let one expire. Stop/Resume a
   harmless task and verify the checkpoint. Switch conversations without losing an unsent draft.
6. Request a read-only Hypruse desktop observation, then a reversible action in an owned test
   window. Inspect the resulting observation and confirmation boundary, not only `/health`.
7. Exercise offline provider, missing dependency, stale session, audio playback failure, long
   replies, keyboard focus, reduced motion, and narrow-screen layouts. Keep screenshots private.
