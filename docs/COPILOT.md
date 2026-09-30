# Desktop copilot release

Thursday now has a task execution layer, a native Hypruse MCP connection, grounded personal
memory, and an OpenAI Responses transport. GPT-6 Luna is the default with low reasoning effort;
the assistant never automatically escalates to a more expensive model. This release implements
the approved core-and-Hypruse scope. It does not promise perfect autonomy or a universal
intelligence ranking.

## Install and run

```bash
.venv/bin/python -m pip install -e '.[hypruse]'
.venv/bin/python -m assistant.main --web
```

Run from the graphical Hyprland session so the bridge inherits the display and session bus.
Hypruse 0.11.0 and MCP 1.x are pinned to tested compatible versions. The existing `.env` key is
reused; no global Codex plugin or MCP configuration is modified. Restart Thursday to load changed
configuration and the rebuilt web client.

The active configuration has 24 tool steps, a 300-second task budget, a 24,000-token request
budget, up to 8,000 tokens of recent history, and 4,096 output tokens. The safe example retains
local inference, restricted paths, and disabled Hypruse. Configuration takes environment overrides:

| Setting | Purpose |
| --- | --- |
| `LLM_MODEL=gpt-6-luna` | Default Luna; choose `gpt-5.6-luna` manually for comparison |
| `LLM_API_MODE=auto` | OpenAI uses Responses; other providers retain Chat Completions |
| `LLM_API_MODE=responses` | Explicit Responses endpoint for a compatible provider |
| `LLM_REASONING_EFFORT=low` | Reasoning effort, independently of output budget |
| `THURSDAY_HYPRUSE=1` | Enable the lazy desktop bridge |
| `THURSDAY_TASK_TIMEOUT=300` | Time budget checked between operations |
| `THURSDAY_MAX_TOOL_STEPS=24` | Tool-loop budget before a resumable pause |

## Desktop workflow

The first `hypruse__desktop` observation discovers the live tool schemas. All 14 supported tools
become available during the task. `discover_tools` can add other capabilities as the task expands,
including explicit file/calendar/desktop combinations.

Window operations use Hyprland IPC; app controls use AT-SPI accessible names. Keyboard actions
require a current window address. Screenshots and zoom preserve logical geometry, pixel size, and
scale, and send inline images to the configured model. They are used during tasks rather than
continuous monitoring. Captures stay out of SQLite and model logs; preview IDs live in memory
for up to five minutes, capped at eight images. Hypruse also manages its own bounded capture files
in the session runtime directory.

Desktop actions run serially. Strict seat and authentication guards remain enabled; authentication
overrides and clipboard access are withheld from the model. Pointer actions, compositor keybinds,
window closure, Enter/Delete keys, and named consequential submissions request approval. Named
reversible controls can run within the task. A refusal or uncertain delivery is evidence of a
failure, not evidence of completion. Seat interference and transport failures pause the task.
Terminal content goes through the guarded terminal tool, and launch only accepts trusted apps,
paths/URLs, and basic window flags.

After an approval, Thursday re-reads the desktop to acknowledge the mouse/focus movement needed
to click Approve. It rechecks the original target and then delivers the approved action once,
without asking for that approval again. A closed/replaced target or changed pointer geometry
pauses before delivery. Strict seat guards remain enabled for unrelated interference.

Apps differ in accessibility support. Use `ui(actionable=false)` when a GTK text field is omitted
from the actionable-only view. When an app has no tree, use cropped captures and zoom, followed by
a confirmed pointer action. The wrapper's guards are conservative; they are not a general-purpose
security sandbox for every existing integration or shell command.

## Tasks and memory

The web task panel shows progress, Stop/Resume, observed tool outcomes, token counts, and estimated
cost. Outcomes are saved immediately after delivery, so stopping does not erase completed actions.
Resume creates a child run in the original conversation with prior outcomes and invalidates cached
desktop targets. A restart marks unfinished runs interrupted. Repeated identical outcomes and step
or time exhaustion pause for review. A finished response is distinct from verified task success.

The memory dialog reviews preferences, personal facts, and named notes. New personal values need
literal user evidence and source message IDs; assistant claims never supply extraction evidence.
Existing values are labelled as having unavailable original provenance. Corrections supersede prior
values; forgetting removes the canonical value and its review correction chain. Deletion invalidates
queued extraction so it cannot restore stale values. This deletes personal memory, not the separate
conversation transcript; conversation deletion remains a separate control.

The extraction queue holds at most 16 exchanges. Raw captures are never personal memories. Request
context retains complete tool-call exchanges, bounded excerpts from older user/tool messages, and
recent task outcomes. The excerpts are working context, not a durable fact source.
The request estimator reserves 4,096 tokens per inline image without counting its base64 bytes
as text. Image use is model-dependent, so this remains an approximate context budget.

## Luna comparison

Measured on 2026-09-30 using the same prompt, permissions, low reasoning effort, 4,096 output tokens,
12 tool steps, and synthetic tools. The 24 cases comprise eight routine tasks, eight contextual
follow-ups, and eight cross-domain tasks. Scoring checks delivered tools and fixture state, rather
than trusting the model's completion text. These are fixture workflows, not a live-desktop success
rate or a general intelligence benchmark.

| Latest complete run | GPT-5.6 Luna | GPT-6 Luna |
| --- | ---: | ---: |
| Completed workflows | 23/24 | 23/24 |
| Routine workflows | 7/8 | 8/8 |
| Time across the suite | 164.939 s | 154.876 s |
| Estimated standard token cost | $0.028060 | $0.012722 |

GPT-6 Luna cost about 55% less in this run, with the same total completion score. It is the default
for this release. Both models sometimes supplied a window title where a live address was required;
the error now includes observed addresses. Both then passed both targeted regression cases.
Earlier runs varied and are retained below; they also exposed incomplete fixture metadata and a
progress instruction that encouraged re-observation. These results support the cost choice, not
a claim that one model is universally more intelligent.

Standard prices per million input/cached/output tokens are $0.10/$0.01/$0.50 for
[GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna) and $0.20/$0.02/$1.20 for
[GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna). Estimates exclude tool
fees, cache writes, long-context multipliers, regional premiums, and usage unavailable after an
interrupted stream. The UI estimate covers the main task, not asynchronous memory extraction or
nested integration requests. It is not an invoice. Responses is required for GPT-6 Luna function
calling with reasoning; its Chat Completions function calling requires reasoning effort `none`.

Evidence:

- [Latest full comparison](luna-evaluation.json)
- [Address validation regressions](luna-target-validation.json)
- [Cropped live-window image reading](luna-vision.json): GPT-6 Luna read all three expected labels
- [First comparison](luna-evaluation-first.json), [second comparison](luna-evaluation-prior.json),
  and [discovery diagnostics](luna-discovery-regression.json). The first report's token-count fields
  were over-redacted; this was fixed before subsequent runs.

Rerun deliberately: `--live` makes paid requests, and fixtures do not touch the real desktop.

```bash
.venv/bin/python -m assistant.evals.copilot
.venv/bin/python -m assistant.evals.copilot --live --output /tmp/luna-comparison.json
.venv/bin/python -m assistant.evals.copilot --live --cases 4,17 --output /tmp/luna-targets.json
```

## Verification and limits

The Python suite passes 146 tests, including Responses completion/reasoning replay, invalid tool
arguments, mixed-batch ordering, cancellation between mutations, idle-stream cancellation,
checkpoint recovery, memory correction/deletion, a real subprocess sending a large inline MCP
image, and protected local HTTP task/memory routes. New modules pass their full Ruff rules; focused
Python error checks pass across the repository. Existing repository-wide style debt remains.
Approval regression cases cover mouse/focus movement, long approval waits, target replacement,
failed observations, declined actions, fixed pointer coordinates, and sequences without duplicate
approval requests. Nested tool arguments and arrays of results render as structured text instead
of `[object Object]`, including when conversation history is reloaded.

The production web build passes. Rendered QA used installed Chromium through Playwright because
the Browser plugin was unavailable: `http://127.0.0.1:5015/`, 1440x1000 and 390x844. Page identity,
nonblank rendering, no error overlay, no console errors, and no mobile horizontal overflow passed.
The interaction path was send → Stop → Resume → finished → reload checkpoint → memory correction
→ historical value check → Forget. QA used a temporary database and controlled model, so it does
not claim a production voice, Gmail, Spotify, Calendar, or desktop-launcher test.
Rendered evidence is saved outside source at `/tmp/thursday-memory.png` and
`/tmp/thursday-mobile.png`.

Checks run:

```bash
.venv/bin/python -m pytest assistant/tests -q
.venv/bin/ruff check assistant --select E9,F63,F7,F82
cd assistant/web
npx tsc --noEmit
npm run build
```

The session's controlled UI and desktop checks used `/tmp/thursday_ui_qa.py` and
`/tmp/thursday_desktop_qa.py`; these are temporary QA harnesses, not application entry points.

A temporary owned GTK window verified live MCP discovery (14 tools), accessible controls,
window-targeted typing, named clicking, inline capture, and geometry preservation. It was closed
after testing. A separate real GPT-6 Luna image request read the cropped window successfully. This
is controlled integration evidence, not unattended operation of the user's entire desktop.

Stop cancels the model stream and terminates the owned MCP child. Existing synchronous tools may
still take their own timeout to return; an already delivered action is retained and cannot be
rolled back by Stop. Budget checks happen between operations. The loop detector can conservatively
pause a legitimate repeated observation. Memory review currently shows the latest 200 assertions.
The fallback HTML and Quickshell overlay do not have the React task/memory panels.

## Rollback and next work

To disable desktop automation, set `THURSDAY_HYPRUSE=0` and restart. To choose the previous Luna,
set `LLM_MODEL=gpt-5.6-luna`; it works through Responses too. Local inference retains the existing
Chat path. `LLM_API_MODE=chat` is an explicit transport rollback, with model-specific compatibility
constraints above. SQLite changes are additive: no existing conversation or personal-memory table
is dropped. Do not manually erase runtime databases to roll back a model choice.

The later roadmap remains: document ingestion with source citation, durable reminders, reviewed
reusable routines, Codex job completion tracking, and voice interruption/transcript improvements.
Useful next milestones are extending the fixture catalog with real schemas and screenshot cases,
testing additional GTK/Qt/Electron apps, replacing keyword routing with measured capability
selection, and making long-running integration cancellation cooperative.
