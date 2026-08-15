# Thursday architecture and internals

Thursday is a single-user Linux desktop agent built around one practical rule: reasoning and action
should be separate, observable layers. The model decides what capability is needed; deterministic
Python tools perform the work; the interface shows what happened.

## System map

```text
┌──────────────────────────────── Interfaces ────────────────────────────────┐
│ CLI chat · Web UI · Super+C launcher · Super+Alt voice overlay            │
└────────────────────────────────────┬────────────────────────────────────────┘
                                     │ HTTP / SSE / direct CLI calls
┌────────────────────────────────────▼────────────────────────────────────────┐
│ Server and runtime                                                          │
│ lifecycle · auth · conversations · event broadcast · voice · health         │
└────────────────────────────────────┬────────────────────────────────────────┘
                                     │ normalized user turn
┌────────────────────────────────────▼────────────────────────────────────────┐
│ Agent                                                                         │
│ short-term context · prompt budget · smart tool groups · tool loop · memory  │
└───────────────────┬────────────────────────────────────┬─────────────────────┘
                    │                                    │
        ┌───────────▼────────────┐          ┌────────────▼───────────────────┐
        │ OpenAI-compatible LLM  │          │ Deterministic capability layer │
        │ local or cloud         │          │ files · desktop · browser ·    │
        │ streamed responses     │          │ Gmail · Calendar · Codex · ... │
        └───────────┬────────────┘          └────────────┬───────────────────┘
                    └────────────────────┬───────────────┘
                                         │
                         visible result + persisted history
```

## Request lifecycle

1. The CLI, browser, or voice layer submits a user turn.
2. The server activates a conversation and broadcasts lifecycle events over Server-Sent Events.
3. The agent builds a bounded context from the current conversation, relevant memories, and the
   system contract.
4. Keyword groups and follow-up routing reduce the tool catalog to the capabilities relevant to
   this turn.
5. The configured OpenAI-compatible model either answers or requests one or more tools.
6. Safety checks validate paths, shell policy, authentication, and confirmation requirements before
   a tool runs.
7. Tool calls, chunks, results, errors, and confirmations are broadcast to the UI. The model can use
   those results for the next step, up to the configured tool-step budget.
8. The final answer and conversation state are persisted locally. Automatic fact extraction can
   separately update long-term memory.

## Major components

### Interfaces and server

- `assistant/main.py` selects CLI or web mode and owns process startup.
- `assistant/server.py` serves the UI, JSON APIs, audio, conversation routes, health data, the Brave
  bridge, and the SSE stream used for live tokens and tool cards.
- The React client in `assistant/web` is the primary maintained UI. The self-contained fallback in
  `assistant/gui/index.html` allows clones to run even when a fresh frontend build is unavailable.
- The desktop launcher keeps one Thursday window alive, focuses an existing instance, and shuts the
  backend down when the final app window closes.

### Model and agent

- `assistant/llm` normalizes local llama.cpp and cloud OpenAI-compatible endpoints behind one chat
  client.
- Provider-specific payload rules handle GPT-5-style token and reasoning fields without breaking
  local models.
- `assistant/agent` owns the iterative tool loop, conversation context, safety integration, retries,
  streaming, and memory extraction.
- Smart tool grouping reduces prompt size and tool-choice noise while retaining explicit follow-up
  behaviors such as “open the second one.”

### Tools and integrations

- `assistant/tools/registry.py` discovers built-in modules dynamically. A module can export
  `get_tools()` or a single `TOOL_CLASS`.
- Tools declare an OpenAI function schema through `ToolMetadata`, then implement deterministic
  execution in Python.
- Desktop tools use native Linux interfaces such as MPRIS, Hyprland, Thunar, `plocate`, and system
  utilities.
- The managed Brave Helper communicates through a loopback bridge and the user's normal signed-in
  browser profile. Gmail drafting never presses Send; Calendar writes require confirmation.

### Memory

- Session context, conversation history, preferences, named memories, and knowledge facts live in a
  local SQLite database under `assistant/database`.
- Memory has explicit read, list, delete, entity-delete, and full-forget operations.
- The database is runtime state and is excluded from Git.

### Codex orchestration

- The UI emits a private `[codex-launch]` envelope containing the workspace, brief, and optional
  model. The agent parses this locally instead of asking the chat model to relay UI state.
- `codex_orchestrate` validates the project and model identifiers, creates a directory only beneath
  `codex_workspace`, removes Thursday's provider credentials from the child environment, and opens
  a visible Kitty terminal.
- Codex runs with `workspace-write` and approval expansion disabled. The terminal is interactive and
  remains the authority for task status; Thursday only confirms that it launched successfully.
- The included `codex_workspace/todo-app` project is a demonstration artifact. New personal Codex
  projects are ignored by Git unless intentionally allowlisted.

## Security model

Thursday is powerful because it can affect a real desktop. Its controls are layered:

- The HTTP server binds to loopback by default. Remote binds require an explicit opt-in and a token.
- Mutating API routes honor the configured bearer token.
- File tools resolve paths against configured read and write roots.
- Shell execution applies blacklist, whitelist, timeout, and confirmation policies.
- Consequential browser actions expose a confirmation step in the web UI.
- Provider and request errors are translated into public-safe messages; technical details stay in
  redacted logs.
- Secrets belong in `.env`, which is excluded from Git. The repository ships only `.env.example`.

The default checked-in configuration is tailored to the author's trusted workstation. For another
machine—especially a shared one—start from `assistant/config/config.safe.example.json` and grant
capabilities incrementally.

## Adding a capability

1. Add a module in `assistant/tools` with precise metadata and a narrow input schema.
2. Keep side effects inside the tool; never make the model pretend an action occurred.
3. Add the tool to a group and relevant intent keywords in `assistant/tools/groups.py`.
4. Apply path, confirmation, authentication, or focus checks appropriate to the action.
5. Add isolated tests for success, rejection, and failure paths.
6. Exercise the real integration when static tests cannot prove desktop behavior.

## Operational checks

```bash
python -m pytest assistant/tests/
python -m ruff check assistant
cd assistant/web && npm ci && npm run build
```

For an installed desktop release, also validate the project and installed `.desktop` entries,
launch the real application, inspect `/health`, perform a real `/api/message` turn, and confirm that
closing the last window shuts the managed process down.
