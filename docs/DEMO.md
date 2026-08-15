# Thursday demo playbook

This demo is designed as a story, not a feature dump. The audience should leave with one clear
idea: Thursday is a personal desktop operating layer that turns natural language into visible,
controlled action—and can hand larger build work to Codex without hiding the process.

## Recommended format

- **Length:** 8–10 minutes for the main demo, plus 2 minutes for architecture and questions.
- **Audience:** developers, recruiters, technical judges, and people curious about practical agents.
- **Format:** live desktop recording with a quiet backup clip for browser integrations.
- **Narrative:** understand → act → verify → remember → build.

## The opening line

> “Most assistants end with instructions. Thursday starts there: it connects model reasoning to the
> tools on my Linux desktop, shows every action, and keeps the risky parts behind explicit gates.”

Avoid calling it fully autonomous or universally cross-platform. It is a Linux-first personal
assistant with configurable local/cloud inference and intentionally visible automation.

## Before recording

1. Start from a clean desktop and close unrelated notifications, terminals, email, and private tabs.
2. Use a demo-only `.env`; never show API keys, browser tokens, personal email contents, or logs that
   may contain private prompts.
3. Run `./run_with_llm.sh`, then confirm `http://127.0.0.1:5005/health` reports `model_ready: true`.
4. Confirm the Brave Helper status, microphone, TTS voice, Spotify MPRIS player, Kitty, and Codex CLI.
5. Put a harmless demo file in an allowed directory and choose a non-sensitive Calendar event.
6. Reset Ritual with **Reset demo data** and clear Thursday's demo conversation.
7. Increase the UI/browser zoom enough for tool cards and confirmation banners to be readable.
8. Record a silent 20–30 second backup clip of Gmail/Calendar and Codex launch in case network or
   browser state changes during the live presentation.

## Main demo flow

### 1. Establish the product — 45 seconds

Show the Thursday window, conversation sidebar, model/health status, voice toggle, logs button, and
the new **Codex Project** button.

Say:

> “The same interface can talk to a local llama.cpp model or a cloud OpenAI-compatible model. The
> agent, memory, safety policy, and tools do not change when I swap the brain.”

Point out that tool calls appear as expandable cards. This makes the demo's core promise—observable
action—visible before any automation begins.

### 2. Live factual research — 60 seconds

Prompt:

> “Find the latest official Python release information and give me the two details that matter most,
> with sources.”

Show the web search/fetch tool card and cited response. Explain that Thursday routes factual and
current questions through live sources rather than silently trusting model memory.

Fallback: use a stable official-documentation question if the news/search provider is slow.

### 3. Desktop action with conversational follow-up — 75 seconds

Prompt:

> “Find files on my computer named Thursday demo.”

After the numbered results appear, say:

> “Open the second one.”

Show that Thursday preserves the search-result context and reveals the selected file in Thunar.
Then ask:

> “Set the volume to 35 percent and send me a notification saying Demo ready.”

This demonstrates contextual follow-ups, native desktop action, and multiple deterministic tools.

### 4. Safety and browser integration — 90 seconds

Use one read action and one write action:

> “Summarize the newest messages in my demo inbox.”

Then:

> “Create a calendar event called Thursday demo review tomorrow at 4 PM for 30 minutes.”

Show the normal signed-in Brave session, the Calendar preview, and the confirmation banner. Approve
only after explaining it:

> “Reading and drafting are separated from consequential writes. Thursday prepares the action, but
> Calendar changes stay behind a human confirmation.”

If using Gmail compose, emphasize that Thursday opens a populated draft and never presses Send.

### 5. Memory that remains controllable — 60 seconds

Prompt:

> “Remember that I prefer demos under ten minutes and call this project Thursday.”

Start a new conversation, then ask:

> “What do you remember about my demo preferences?”

Show the answer, then mention that preferences, facts, and entity profiles live in local SQLite and
have explicit list/delete/forget tools. Do not use “perfect memory”; describe it as controlled,
inspectable local persistence.

### 6. Voice presence — 45 seconds

Hold **Super+Alt** and say:

> “Thursday, what is my system status?”

Show the live transcript overlay, streamed response, and spoken answer. Mention that the overlay does
not steal focus, which is useful while coding or presenting another window.

If venue audio is unreliable, show the waveform and transcript but keep TTS muted.

### 7. Codex project handoff — 2 minutes

Click **Codex Project** and walk through the modal:

- Workspace: `demo-dashboard`
- Model: the currently available Codex default or your preferred explicit model
- Brief: `Build a responsive one-page launch dashboard with a hero, three live-looking status cards,
  keyboard-friendly controls, and a README. Use plain HTML, CSS, and JavaScript. Do not use a build
  step. Verify it locally.`

First click **Refine with Thursday** and show how the brief becomes a normal planning conversation.
Explain that you can clarify product and design choices before spending a Codex run.

Reopen the modal with the finalized brief and click **Open Codex in Kitty**. Show:

1. Project-name validation.
2. The dedicated `codex_workspace/demo-dashboard` directory.
3. The visible Kitty session and selected model.
4. Codex inspecting, editing, and verifying inside that workspace.

Do not wait for a full build during the main recording. Cut to the included Ritual artifact or a
pre-recorded completed project and say:

> “Thursday's job here is orchestration: validate the workspace, carry the brief and model choice,
> and launch a contained interactive session. Codex remains visible and is the source of truth for
> build progress.”

### 8. Show the artifact — 60 seconds

Run:

```bash
cd codex_workspace/todo-app
python3 -m http.server 4173
```

Open `http://127.0.0.1:4173` and demonstrate Ritual:

1. Choose a mood check-in.
2. Complete a habit and show the percentage, streak, and weekly chart update immediately.
3. Add a new ritual with an icon.
4. Toggle dark mode.
5. Refresh the page to prove local persistence.

Explain that it is deliberately dependency-free: semantic HTML, responsive CSS, vanilla
JavaScript, and localStorage. It is an artifact of the orchestration workflow, not a hidden part of
Thursday's backend.

### 9. Close with the architecture — 45 seconds

Show the architecture diagram in `docs/ARCHITECTURE.md` and say:

> “The model is not the operating system. It chooses from a small relevant capability set. Python
> tools perform the action, policies guard it, SSE makes the work visible, and SQLite keeps the
> context local. That separation is what makes Thursday extensible and debuggable.”

End with:

> “Thursday is my answer to what a personal agent should feel like: fast enough to stay present,
> powerful enough to be useful, and honest about every action it takes.”

## Five-minute cut

For a shorter pitch, keep only:

1. 20-second product framing.
2. Source-backed research.
3. File search plus “open the second one.”
4. Calendar confirmation.
5. Codex Project launch, then cut to Ritual.
6. 20-second architecture close.

## Failure-safe presenter plan

| Risk | Live fallback |
|------|---------------|
| Provider/API latency | Switch to a known local model or use the prepared successful research clip. |
| Browser helper unavailable | Show `/health` helper status and the prerecorded confirmation flow; do not fake live Gmail data. |
| Microphone or venue noise | Type the same prompt and show the saved overlay clip. |
| Spotify has no active session | Use system volume/notification as the desktop-action example. |
| Codex takes longer than the slot | Show the launch and containment live, then cut to Ritual as the completed artifact. |
| Calendar event already exists | Use a uniquely timestamped demo title and delete it after the presentation. |

## Recording and editing notes

- Record at 1440p or higher; export at 1080p so tool-card text remains sharp.
- Keep the pointer movement deliberate and pause briefly over confirmation gates and tool results.
- Use chapter labels: **Research**, **Desktop**, **Safety**, **Memory**, **Voice**, **Build**.
- Add small on-screen callouts for “live source,” “local SQLite,” “confirmation required,” and
  “workspace sandbox.”
- Prefer real interaction sounds and restrained music. The impressive part is the system behavior,
  not cinematic effects.
- Blur account avatars, email addresses, file paths containing private names, and all tokens.
- End on the working artifact and repository URL, with the test command visible in the description.

## Claims you can safely make

- Thursday supports local llama.cpp and multiple OpenAI-compatible cloud providers.
- It exposes CLI, web, desktop-launcher, hotkey, and voice interaction paths.
- It uses deterministic tools, smart filtering, local memory, visible tool results, and configurable
  confirmation/security policies.
- It integrates with the author's Linux/Hyprland desktop and normal Brave profile.
- It can launch contained, interactive Codex project sessions and includes a working demo artifact.

Avoid claiming that every integration works on every Linux distribution, that cloud inference is
local/private, that Codex completed work merely because its terminal launched, or that Thursday can
send Gmail messages automatically.
