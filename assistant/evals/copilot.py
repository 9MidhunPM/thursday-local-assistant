"""Reproducible Luna comparison on synthetic state, never the user's desktop.

Run: python -m assistant.evals.copilot --live --output docs/luna-evaluation.json
The --live flag makes paid API calls using the existing .env credential.
Fixtures score observed state and tool delivery, not the model's self-report.
"""

from __future__ import annotations

# CLI errors describe the unavailable fixture or credential requirement.
# ruff: noqa: TRY003
import argparse
import base64
import json
import logging
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv

from assistant.agent.agent import Agent, AgentConfig
from assistant.agent.tasks import TaskStore
from assistant.config.loader import load_config
from assistant.integrations.hypruse import HypruseTool
from assistant.llm.client import ChatMessage
from assistant.llm.responses import ResponsesClient
from assistant.memory.long_term import LongTermMemory
from assistant.memory.short_term import Message, ShortTermMemory
from assistant.tools.base import BaseTool, ToolMetadata
from assistant.tools.registry import ToolRegistry


def schema(properties, required=()):
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


STR = {"type": "string"}


class FixtureTool(BaseTool):
    def __init__(self, name, description, parameters, fixture, read=False):
        self.fixture = fixture
        self.metadata = ToolMetadata(
            name, description, parameters, effect="read" if read else "mutation", parallel_safe=read
        )

    def execute(self, args, context):
        self.fixture.calls.append((self.name, args))
        if self.name == "write_file":
            self.fixture.files[args["path"]] = args["content"]
            return {"success": True, "verification": "fixture file contains supplied content"}
        if self.name == "read_file":
            return {
                "success": True,
                "content": self.fixture.files.get(args["path"], "File missing"),
            }
        if self.name == "current_time":
            return {"success": True, "time": "14:30", "timezone": "Asia/Kolkata"}
        if self.name == "calculate":
            # Fixed expressions; never eval arbitrary model code.
            values = {"17*23": 391, "17 * 23": 391, "4+5": 9, "4 + 5": 9}
            return {
                "success": args["expression"] in values,
                "result": values.get(args["expression"]),
            }
        if self.name == "calendar_agenda":
            return {"success": True, "events": [{"title": "Design review", "start": "15:00"}]}
        if self.name == "gmail_read":
            return {
                "success": True,
                "messages": [
                    {
                        "sender": "fixture@example.test",
                        "subject": "Demo",
                        "body": "The demo is Friday.",
                    }
                ],
            }
        if self.name == "search_and_fetch":
            return {
                "success": True,
                "sources": [
                    {"url": "https://example.test/guide", "text": "Fixture version is 42."}
                ],
            }
        if self.name == "store_preference":
            context.memory.set_preference(args["key"], args["value"], context.now().isoformat())
            return {"success": True}
        if self.name == "get_preference":
            item = context.memory.get_preference(args["key"])
            return {"success": True, "value": item.value if item else None}
        raise ValueError("Unknown fixture tool")


class DesktopFixture:
    def __init__(self):
        self.calls, self.files, self.text, self.clicked = (
            [],
            {"/fixture/brief.txt": "Launch date is Friday."},
            "",
            False,
        )
        self.snapshot_at = 0
        self.snapshot = {
            "active_window": "0x2",
            "windows": [
                {"address": "0x1", "class": "notes", "title": "Notes", "workspace": 1},
                {"address": "0x2", "class": "browser", "title": "Browser", "workspace": 1},
            ],
        }

    def call(self, name, args):
        self.calls.append(("hypruse__" + name, args))
        if name == "keyboard":
            if args.get("action") == "type":
                self.text += args.get("text", "")
            elif args.get("action") == "key":
                if args.get("keys", "").lower() == "ctrl+a":
                    self.text = ""
            else:
                return {
                    "isError": True,
                    "content": [
                        {"type": "text", "text": "Unknown keyboard action; use type or key."}
                    ],
                }
        elif name == "click_ui":
            if args.get("name", "").casefold() == "preview":
                self.clicked = True
        elif name == "hypr":
            self.snapshot["active_window"] = args.get("target", self.snapshot["active_window"])
        elif name == "launch":
            self.snapshot["windows"].append(
                {"address": "0x3", "class": args["command"], "title": "New app", "workspace": 1}
            )
        if name in {"ui", "keyboard", "click_ui"}:
            result = {
                "elements": [
                    {"name": "Note text", "role": "entry", "clickable": True, "value": self.text},
                    {
                        "name": "Preview",
                        "role": "button",
                        "clickable": True,
                        "pressed": self.clicked,
                    },
                ]
            }
        elif name == "screenshot":
            result = {
                "geometry": [0, 0, 1280, 720],
                "scale": 1,
                "image": [1280, 720],
                "fixture": True,
            }
        else:
            result = self.snapshot
        return {"structuredContent": result, "content": [], "isError": False}

    def registry(self):
        registry = ToolRegistry()
        desktop = {
            "desktop": ("Read windows and active window before any desktop action.", schema({})),
            "ui": (
                "Read accessible controls and their current values in a window.",
                schema({"window": STR}),
            ),
            "keyboard": (
                "Keyboard action='type' takes text; action='key' takes keys (e.g. ctrl+a). "
                "Specify window. Verify using then='ui'.",
                schema(
                    {"action": STR, "text": STR, "keys": STR, "window": STR, "then": STR},
                    ["action"],
                ),
            ),
            "click_ui": (
                "Click a named accessible control in a window. Prefer this to coordinates.",
                schema({"name": STR, "window": STR, "then": STR}, ["name"]),
            ),
            "hypr": (
                "Focus a window using action='focus_window' and target address.",
                schema({"action": STR, "target": STR, "workspace": STR, "then": STR}, ["action"]),
            ),
            "launch": (
                "Launch a trusted app by command.",
                schema({"command": STR, "workspace": STR}, ["command"]),
            ),
            "screenshot": (
                "Observe visual desktop evidence. Fixture reports capture geometry.",
                schema({"window": STR}),
            ),
        }
        for name, (description, parameters) in desktop.items():
            registry.register(HypruseTool(self, name, description, parameters))
        for name, description, params, read in [
            ("current_time", "Get current time.", schema({}), True),
            (
                "calculate",
                "Calculate an arithmetic expression.",
                schema({"expression": STR}, ["expression"]),
                True,
            ),
            ("read_file", "Read a file's content.", schema({"path": STR}, ["path"]), True),
            (
                "write_file",
                "Write a local file.",
                schema({"path": STR, "content": STR}, ["path", "content"]),
                False,
            ),
            ("calendar_agenda", "Read calendar events.", schema({"query": STR}), True),
            ("gmail_read", "Read inbox messages.", schema({}), True),
            (
                "search_and_fetch",
                "Research a topic using cited web sources.",
                schema({"query": STR}, ["query"]),
                True,
            ),
            (
                "store_preference",
                "Store a personal preference quoted from the user.",
                schema({"key": STR, "value": STR}, ["key", "value"]),
                False,
            ),
            (
                "get_preference",
                "Read a saved personal preference.",
                schema({"key": STR}, ["key"]),
                True,
            ),
        ]:
            registry.register(FixtureTool(name, description, params, self, read))
        return registry


# category, request, expected tool(s), expected literal in fixture state
CASES = [
    ("routine", "List the open windows on my desktop.", ["hypruse__desktop"], None),
    ("routine", "Focus the Notes window.", ["hypruse__hypr"], None),
    (
        "routine",
        "Type 'Hello Thursday' into the Note text field in Notes.",
        ["hypruse__keyboard"],
        "Hello Thursday",
    ),
    (
        "routine",
        "Click Preview in Notes and verify its pressed state.",
        ["hypruse__click_ui"],
        None,
    ),
    ("routine", "Capture the Notes window.", ["hypruse__screenshot"], None),
    ("routine", "What time is it right now?", ["current_time"], None),
    ("routine", "Calculate 17*23.", ["calculate"], None),
    ("routine", "I use Kate. Remember my editor as Kate.", ["store_preference"], None),
    ("followup", "Type 'Friday launch' into it.", ["hypruse__keyboard"], "Friday launch"),
    ("followup", "Now focus that window.", ["hypruse__hypr"], None),
    ("followup", "Click Preview there and verify it.", ["hypruse__click_ui"], None),
    ("followup", "Read the text I entered there.", ["hypruse__ui"], None),
    ("followup", "Capture that window.", ["hypruse__screenshot"], None),
    (
        "followup",
        "Write the words 'Design review' into that note.",
        ["hypruse__keyboard"],
        "Design review",
    ),
    (
        "followup",
        "Read /fixture/brief.txt and put its contents in that note.",
        ["read_file", "hypruse__keyboard"],
        "Friday",
    ),
    ("followup", "Show the currently open windows again.", ["hypruse__desktop"], None),
    (
        "cross_domain",
        "Read /fixture/brief.txt and type its contents into Notes.",
        ["read_file", "hypruse__keyboard"],
        "Friday",
    ),
    (
        "cross_domain",
        "Read my calendar and type the next meeting title and time into Notes.",
        ["calendar_agenda", "hypruse__keyboard"],
        "Design review",
    ),
    (
        "cross_domain",
        "Read my inbox and write the demo date to /fixture/demo.txt.",
        ["gmail_read", "write_file"],
        "Friday",
    ),
    (
        "cross_domain",
        "Research fixture version and save the answer and source URL to /fixture/version.txt.",
        ["search_and_fetch", "write_file"],
        "42",
    ),
    (
        "cross_domain",
        "Calculate 4+5 and type the result into Notes.",
        ["calculate", "hypruse__keyboard"],
        "9",
    ),
    (
        "cross_domain",
        "Get the current time and write it to /fixture/time.txt.",
        ["current_time", "write_file"],
        "14:30",
    ),
    (
        "cross_domain",
        "Read /fixture/brief.txt and my calendar; save a combined brief to /fixture/combined.txt.",
        ["read_file", "calendar_agenda", "write_file"],
        "Design review",
    ),
    (
        "cross_domain",
        "Read my inbox; type its demo date into Notes and verify the field value.",
        ["gmail_read", "hypruse__keyboard"],
        "Friday",
    ),
]


def evaluate(model, config, selected=None):
    results = []
    client = ResponsesClient(
        config.model.base_url,
        model,
        0.2,
        4096,
        90,
        None,
        config.model.api_key,
        "openai",
        reasoning_effort="low",
    )
    try:
        for number, (category, prompt, expected, literal) in enumerate(CASES, 1):
            if selected and number not in selected:
                continue
            fixture = DesktopFixture()
            with tempfile.TemporaryDirectory(prefix="thursday-eval-") as directory:
                db = Path(directory) / "memory.db"
                memory = LongTermMemory(db)
                history = ShortTermMemory(max_tokens=8000)
                if category == "followup":
                    history.add(
                        Message(
                            "user",
                            "We are working in Notes (0x1), in the Note text field. "
                            "I entered a project note there.",
                        )
                    )
                    history.add(Message("assistant", "The selected project note is in Notes."))
                    fixture.text = "Project note. "
                logs = SimpleNamespace(
                    **{
                        name: logging.getLogger("eval." + name)
                        for name in ["user", "tool", "model", "error"]
                    }
                )
                agent = Agent(
                    client,
                    fixture.registry(),
                    memory,
                    history,
                    logs,
                    AgentConfig(
                        12,
                        config.agent.system_prompt,
                        0,
                        False,
                        auto_extract=False,
                        smart_tool_filter=True,
                        context_budget_tokens=24000,
                    ),
                )
                agent.tasks = TaskStore(db)
                agent._confirm = lambda _prompt: (
                    False
                )  # Consequential actions are never authorized.
                start = time.perf_counter()
                error, answer = None, ""
                try:
                    answer = agent.handle_message(prompt)
                except Exception as exc:
                    # Never write raw request headers or provider bodies to the report.
                    error = type(exc).__name__
                task = agent.tasks.latest()
                delivered = {name for name, _ in fixture.calls}
                state = fixture.text + " " + " ".join(fixture.files.values())
                passed = not error and task["status"] == "completed" and set(expected) <= delivered
                passed = passed and (literal is None or literal.lower() in state.lower())
                if number == 2 or number == 10:
                    passed = passed and fixture.snapshot["active_window"] == "0x1"
                if number == 4 or number == 11:
                    passed = passed and fixture.clicked
                if number == 8:
                    passed = passed and any(
                        item.value == "Kate" for item in memory.list_preferences()
                    )
                results.append(
                    {
                        "case": number,
                        "category": category,
                        "passed": bool(passed),
                        "seconds": round(time.perf_counter() - start, 3),
                        "tools": sorted(delivered),
                        "usage": task["usage"],
                        "error": error,
                        "answer": answer[:1000],
                        "outcomes": task["outcomes"],
                    }
                )
                agent.close()
                print(f"{model} {number}/24 {'PASS' if passed else 'FAIL'}", flush=True)
                if error:
                    # Stop on provider failure; don't repeat 23 inaccessible calls.
                    break
    finally:
        client.close()
    return {
        "model": model,
        "cases_run": len(results),
        "passed": sum(item["passed"] for item in results),
        "estimated_usd": round(sum(item["usage"].get("estimated_usd", 0) for item in results), 6),
        "seconds": round(sum(item["seconds"] for item in results), 3),
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--cases", help="Comma-separated case IDs for targeted regression runs")
    parser.add_argument(
        "--vision",
        type=Path,
        help="Read a cropped, non-private QA capture instead of running the suite",
    )
    parser.add_argument("--output", type=Path, default=Path("docs/luna-evaluation.json"))
    args = parser.parse_args()
    if not args.live:
        print(
            json.dumps(
                {
                    "cases": len(CASES),
                    "categories": {
                        category: sum(c[0] == category for c in CASES)
                        for category in ("routine", "followup", "cross_domain")
                    },
                    "paid_calls": False,
                }
            )
        )
        return
    load_dotenv()
    config = load_config(Path("assistant/config/config.json"))
    if config.model.provider != "openai" or not config.model.api_key:
        raise SystemExit("The benchmark requires the existing configured OpenAI credential.")
    if args.vision:
        encoded = base64.b64encode(args.vision.read_bytes()).decode()
        client = ResponsesClient(
            config.model.base_url,
            "gpt-6-luna",
            0.2,
            1024,
            90,
            None,
            config.model.api_key,
            "openai",
            reasoning_effort="low",
        )
        try:
            result = client.chat(
                [
                    ChatMessage(
                        "user",
                        [
                            {
                                "type": "text",
                                "text": "Read this controlled QA window. State the text in its top "
                                "field, the button label, and the bottom status.",
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": "data:image/jpeg;base64," + encoded,
                                    "detail": "high",
                                },
                            },
                        ],
                    )
                ]
            )
            passed = all(
                value in (result.content or "")
                for value in ("Thursday verified", "Preview", "Previewed")
            )
            report = {
                "model": "gpt-6-luna",
                "passed": passed,
                "answer": result.content,
                "input_tokens": result.stats.prompt_tokens,
                "output_tokens": result.stats.completion_tokens,
                "scope": "Cropped owned GTK QA window only; no private desktop data",
            }
            args.output.write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report))
            return
        finally:
            client.close()
    report = {
        "created_at": time.time(),
        "scope": "Synthetic tool-state evaluation; no real desktop actions, "
        "private data, or general intelligence claims.",
        "reasoning_effort": "low",
        "max_output_tokens": 4096,
        "max_tool_steps": 12,
        "models": [
            evaluate(
                model, config, {int(item) for item in args.cases.split(",")} if args.cases else None
            )
            for model in ("gpt-5.6-luna", "gpt-6-luna")
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            [{k: v for k, v in model.items() if k != "results"} for model in report["models"]]
        )
    )


if __name__ == "__main__":
    main()
