"""Owned stdio MCP connection; no global Codex configuration is changed."""

from __future__ import annotations

# Policy refusals are shown directly in the task's evidence.
# ruff: noqa: TRY003
import asyncio
import base64
import concurrent.futures
import contextlib
import copy
import importlib.util
import json
import os
import re
import shlex
import sys
import threading
import time
import uuid
from collections import OrderedDict

from assistant.tools.base import BaseTool, ToolMetadata

OBSERVATIONS = {"desktop", "screenshot", "zoom", "ui", "marks", "binds", "wait_for"}
ALLOWED = OBSERVATIONS | {
    "hypr",
    "pointer",
    "keyboard",
    "click_ui",
    "launch",
    "sequence",
    "use_bind",
}
COMMIT = re.compile(
    r"\b(send|submit|publish|purchase|buy|pay|delete|remove|checkout|confirm order)\b",
    re.IGNORECASE,
)
EXECUTABLES = {
    "brave",
    "brave-browser",
    "firefox",
    "thunar",
    "kitty",
    "code",
    "spotify",
    "gedit",
    "kate",
    "mousepad",
}


class CaptureCache:
    def __init__(self):
        self._items = OrderedDict()
        self._lock = threading.Lock()

    def add(self, data, mime):
        decoded = base64.b64decode(data, validate=True)
        if len(decoded) > 5_000_000 or mime not in {"image/png", "image/jpeg"}:
            raise ValueError("Unsupported or oversized desktop capture.")
        key = uuid.uuid4().hex
        with self._lock:
            self._items[key] = (time.monotonic(), decoded, mime)
            while len(self._items) > 8:
                self._items.popitem(last=False)
        return f"/api/captures/{key}"

    def get(self, key):
        with self._lock:
            item = self._items.get(key)
            if item and time.monotonic() - item[0] < 300:
                return item[1:]
            self._items.pop(key, None)
        return None

    def clear(self):
        with self._lock:
            self._items.clear()


captures = CaptureCache()


_ERROR_MESSAGES = {
    "missing_dependencies": (
        "Desktop control needs Thursday's Hypruse extra. Run the desktop installer or install "
        ".[hypruse] into Thursday's virtual environment, then restart."
    ),
    "missing_session": (
        "Desktop control needs a live Hyprland session. "
        "Start Thursday through its graphical-session service."
    ),
    "startup_timeout": (
        "Desktop control did not start in time. Check the desktop service, "
        "then request a fresh desktop observation."
    ),
    "startup_failed": (
        "Desktop control could not start. Check its installation and graphical-session service, "
        "then request a fresh desktop observation."
    ),
    "connection_lost": (
        "The desktop connection closed. Request a fresh desktop observation before continuing."
    ),
    "call_timeout": "The desktop request timed out. Observe the desktop before retrying an action.",
    "observation_required": (
        "Read hypruse__desktop to reconnect before requesting another desktop action."
    ),
    "closed": "Desktop control was stopped. Request a fresh desktop observation to reconnect.",
}


class HypruseConnectionError(RuntimeError):
    """A safe public error; raw transport errors may contain private desktop data."""

    def __init__(self, code, *, delivered=False):
        self.error_code = code
        self.delivery = "uncertain_delivery" if delivered else "not_delivered"
        super().__init__(_ERROR_MESSAGES[code])


class HypruseBridge:
    def __init__(self, registry, *, startup_timeout=15, call_timeout=30):
        self.registry = registry
        self.startup_timeout = startup_timeout
        self.call_timeout = call_timeout
        self._thread = None
        self._ready = threading.Event()
        self._closing = threading.Event()
        self._error = None
        self._error_code = None
        self._abort_code = None
        self._state = "idle"
        self._state_lock = threading.Lock()
        self._schemas = []
        self._start_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._pending = {}
        self._pending_lock = threading.Lock()
        self.snapshot = None
        self.snapshot_at = 0

    @staticmethod
    def _dependencies():
        return {name: importlib.util.find_spec(name) is not None for name in ("mcp", "hypruse")}

    @staticmethod
    def _session_present():
        return all(os.environ.get(key) for key in (
            "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
            "HYPRLAND_INSTANCE_SIGNATURE",
        ))

    def _set_state(self, state, code=None):
        with self._state_lock:
            self._state = state
            self._error_code = code
            self._error = _ERROR_MESSAGES.get(code)

    def status(self):
        """Inspect readiness without connecting or exposing private session data."""
        dependencies = self._dependencies()
        session_present = self._session_present()
        with self._state_lock:
            state, code = self._state, self._error_code
        if state == "idle":
            if not all(dependencies.values()):
                state, code = "unavailable", "missing_dependencies"
            elif not session_present:
                state, code = "unavailable", "missing_session"
        return {
            "state": state,
            "error_code": code,
            "message": _ERROR_MESSAGES.get(code, "Desktop control is ready." if state == "ready"
                                            else "Desktop control connects when requested."),
            "dependencies": dependencies,
            "session_present": session_present,
        }

    def _preflight(self):
        if not all(self._dependencies().values()):
            raise HypruseConnectionError("missing_dependencies")
        if not self._session_present():
            raise HypruseConnectionError("missing_session")

    def _finish(self, future, *, result=None, error=None):
        with self._pending_lock:
            if future not in self._pending:
                return
            self._pending.pop(future, None)
        if not future.done():
            if error:
                future.set_exception(error)
            else:
                future.set_result(result)

    def _fail_pending(self, code):
        with self._pending_lock:
            pending, self._pending = self._pending, {}
        for future, delivered in pending.items():
            if not future.done():
                future.set_exception(HypruseConnectionError(code, delivered=delivered))

    async def _serve(self):
        self._loop = asyncio.get_running_loop()
        self._runner = asyncio.current_task()
        self._queue = asyncio.Queue()
        self._transport_closed = asyncio.Event()
        if self._closing.is_set():
            raise HypruseConnectionError("closed")
        from mcp import ClientSession  # noqa: PLC0415 - optional dependency, lazy connection

        # Keep only graphical-session variables; the MCP child never needs
        # Thursday's provider, email, cloud, or account credentials.
        env = {
            key: value
            for key, value in os.environ.items()
            if key
            in {"PATH", "HOME", "USER", "LOGNAME", "LANG", "DISPLAY", "WAYLAND_DISPLAY", "TMPDIR"}
            or key.startswith(("XDG_", "DBUS_", "LC_", "HYPRLAND_", "HYPRUSE_", "GTK_", "QT_"))
        }
        env.update(
            HYPRUSE_SCREENSHOT_MODE="image",
            HYPRUSE_STRICT="1",
            HYPRUSE_AUTH_GUARD="strict",
            HYPRUSE_CLIPBOARD="0",
        )
        async with contextlib.AsyncExitStack() as stack:
            try:
                async with asyncio.timeout(self.startup_timeout):
                    streams = await stack.enter_async_context(self._stdio(env))
                    session = await stack.enter_async_context(ClientSession(*streams))
                    await session.initialize()
                    listing = await session.list_tools()
            except TimeoutError:
                raise HypruseConnectionError("startup_timeout") from None
            if self._closing.is_set():
                raise HypruseConnectionError("closed")
            self._schemas = [tool for tool in listing.tools if tool.name in ALLOWED]
            self._set_state("ready")
            self._ready.set()
            disconnected = asyncio.create_task(self._transport_closed.wait())
            try:
                while not self._closing.is_set():
                    request = asyncio.create_task(self._queue.get())
                    try:
                        done, _ = await asyncio.wait(
                            (request, disconnected), return_when=asyncio.FIRST_COMPLETED
                        )
                        if disconnected in done:
                            raise HypruseConnectionError("connection_lost")
                        name, arguments, future = request.result()
                    finally:
                        request.cancel()
                        await asyncio.gather(request, return_exceptions=True)
                    with self._pending_lock:
                        if future not in self._pending:
                            continue
                        # Once submitted, a failed mutation must never be replayed.
                        self._pending[future] = name not in OBSERVATIONS
                    operation = asyncio.create_task(session.call_tool(name, arguments))
                    try:
                        done, _ = await asyncio.wait(
                            (operation, disconnected), timeout=self.call_timeout,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if disconnected in done:
                            raise HypruseConnectionError("connection_lost")
                        if operation not in done:
                            raise HypruseConnectionError("call_timeout")
                        result = operation.result()
                        self._finish(future, result=result.model_dump(by_alias=True))
                    finally:
                        operation.cancel()
                        await asyncio.gather(operation, return_exceptions=True)
            finally:
                disconnected.cancel()
                await asyncio.gather(disconnected, return_exceptions=True)

    @contextlib.asynccontextmanager
    async def _stdio(self, env):
        """Same MCP framing as SDK stdio, with an owned process handle for Stop."""
        import anyio  # noqa: PLC0415 - optional MCP dependencies
        from mcp.shared.message import SessionMessage  # noqa: PLC0415
        from mcp.types import JSONRPCMessage  # noqa: PLC0415

        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "from hypruse.cli import main; main()",
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            # Inline MCP images exceed asyncio's default 64 KiB line limit.
            limit=8_000_000,
        )
        self._process = process
        read_send, read_receive = anyio.create_memory_object_stream(0)
        write_send, write_receive = anyio.create_memory_object_stream(0)

        async def reader():
            try:
                async with read_send:
                    while line := await process.stdout.readline():
                        try:
                            message = SessionMessage(JSONRPCMessage.model_validate_json(line))
                        except ValueError as exc:
                            message = exc
                        await read_send.send(message)
            finally:
                self._transport_closed.set()

        async def writer():
            try:
                async with write_receive:
                    async for item in write_receive:
                        encoded = item.message.model_dump_json(by_alias=True, exclude_none=True)
                        process.stdin.write((encoded + "\n").encode())
                        await process.stdin.drain()
            finally:
                self._transport_closed.set()

        tasks = [asyncio.create_task(reader()), asyncio.create_task(writer())]
        try:
            yield read_receive, write_send
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 2)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            await read_receive.aclose()
            await write_send.aclose()

    def _failure_code(self, exc):
        if isinstance(exc, HypruseConnectionError):
            return exc.error_code
        if isinstance(exc, BaseExceptionGroup):
            for nested in exc.exceptions:
                code = self._failure_code(nested)
                if code not in {"startup_failed", "connection_lost"}:
                    return code
        return "connection_lost" if self._state == "ready" else "startup_failed"

    def _worker(self):
        code = "closed"
        try:
            asyncio.run(self._serve())
        except (Exception, asyncio.CancelledError) as exc:
            code = "closed" if self._closing.is_set() else (
                self._abort_code or self._failure_code(exc)
            )
        finally:
            self.snapshot, self.snapshot_at = None, 0
            self._set_state("closed" if self._closing.is_set() else "unavailable", code)
            self._fail_pending(code)
            self._ready.set()

    def connect(self):
        with self._start_lock:
            if self._thread and self._state in {"unavailable", "closed"}:
                self._thread.join(3)
            if self._thread is None or not self._thread.is_alive():
                with self._lifecycle_lock:
                    self.snapshot, self.snapshot_at = None, 0
                    try:
                        self._preflight()
                    except HypruseConnectionError as exc:
                        self._set_state("unavailable", exc.error_code)
                        raise
                    self._schemas = []
                    self._abort_code = None
                    self._closing.clear()
                    self._set_state("connecting")
                    self._ready.clear()
                    self._thread = threading.Thread(
                        target=self._worker, daemon=True, name="thursday-hypruse"
                    )
                    self._thread.start()
            if not self._ready.wait(self.startup_timeout + 4):
                self._cancel_worker("startup_timeout")
                self._set_state("unavailable", "startup_timeout")
                raise HypruseConnectionError("startup_timeout")
            if self._state != "ready" or not self._thread.is_alive():
                raise HypruseConnectionError(self._error_code or "startup_failed")
            for schema in self._schemas:
                self.registry.register(
                    HypruseTool(self, schema.name, schema.description or "", schema.inputSchema)
                )

    def call(self, name, arguments):
        if name not in OBSERVATIONS and self._state != "ready":
            raise HypruseConnectionError("observation_required")
        self.connect()
        future = concurrent.futures.Future()
        with self._pending_lock:
            self._pending[future] = False

        def enqueue():
            if self._state != "ready" or self._closing.is_set():
                self._finish(future, error=HypruseConnectionError("connection_lost"))
            else:
                self._queue.put_nowait((name, arguments, future))

        try:
            self._loop.call_soon_threadsafe(enqueue)
        except RuntimeError:
            self._finish(future, error=HypruseConnectionError("connection_lost"))
        try:
            return future.result(self.call_timeout + 5)
        except TimeoutError:
            self._cancel_worker("call_timeout")
            self._fail_pending("call_timeout")
            return future.result()

    def _cancel_worker(self, code="closed"):
        self._abort_code = code
        loop, runner = getattr(self, "_loop", None), getattr(self, "_runner", None)
        if loop and runner and not loop.is_closed():
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(runner.cancel)

    def close(self, *, wait=True):
        """Signal cancellation now; optionally wait for the owned process to exit."""
        with self._lifecycle_lock:
            self.snapshot, self.snapshot_at = None, 0
            self._closing.set()
            self._set_state("closed", "closed")
            self._fail_pending("closed")
            self._cancel_worker()
            thread = self._thread
        if wait and thread and thread is not threading.current_thread():
            thread.join(3)
        captures.clear()


class HypruseTool(BaseTool):
    def __init__(
        self,
        bridge,
        name="desktop",
        description="Inspect the live desktop and discover Hypruse tools.",
        schema=None,
    ):
        self.bridge, self.operation = bridge, name
        parameters = copy.deepcopy(schema or {"type": "object", "properties": {}})
        # Authentication overrides belong to the human, never to the model.
        parameters.get("properties", {}).pop("allow_auth", None)
        parameters["additionalProperties"] = False
        self.metadata = ToolMetadata(
            "hypruse__" + name,
            description,
            parameters,
            effect="read" if name in OBSERVATIONS else "desktop",
            resource="desktop",
        )

    def _validate(self, name, args, context, *, request_approval=True):
        if name not in ALLOWED or args.get("allow_auth"):
            raise ValueError("This desktop action is not permitted.")
        if name == "sequence":
            steps = args.get("steps", [])
            if not isinstance(steps, list) or not 1 <= len(steps) <= 12:
                raise ValueError("A desktop sequence requires 1-12 steps.")
            approved = False
            for step in steps:
                if not isinstance(step, dict):
                    raise TypeError("Each sequence step must be an object.")
                if step.get("op") == "sequence":
                    raise ValueError("Nested desktop sequences are not supported.")
                approved = (
                    self._validate(
                        step.get("op"),
                        {k: v for k, v in step.items() if k != "op"},
                        context,
                        request_approval=request_approval,
                    )
                    or approved
                )
            return approved
        if name == "launch":
            command = args.get("command", "")
            # Pinned Hypruse accepts a shell command; expose trusted app launches only.
            parts = shlex.split(command) if isinstance(command, str) else []
            if (
                not parts
                or parts[0] not in EXECUTABLES
                or any(char in command for char in ";&|`$\n<>\\")
            ):
                raise ValueError(
                    "Launch supports trusted application commands without shell expressions."
                )
            if parts[0] == "kitty" and len(parts) > 1:
                raise ValueError("Use the guarded terminal tool for terminal commands.")
            if any(part.startswith(("--command", "--execute", "-e")) for part in parts[1:]):
                raise ValueError(
                    "Application command execution must use the guarded terminal tool."
                )
            if any(
                part.startswith("-")
                and part not in {"--new-window", "--private-window", "--incognito"}
                for part in parts[1:]
            ):
                raise ValueError(
                    "Launch accepts paths, URLs, and basic window flags; "
                    "use guarded tools for app configuration."
                )
        if name in {"keyboard", "click_ui", "pointer", "hypr", "use_bind"}:
            snapshot = self.bridge.snapshot
            if not snapshot or time.monotonic() - self.bridge.snapshot_at > 30:
                raise ValueError(
                    "Read hypruse__desktop before acting; the snapshot is missing or stale."
                )
            if name == "keyboard" and not args.get("window"):
                raise ValueError("Keyboard actions require an explicit target window.")
            target = args.get("window") or args.get("target")
            addresses = {item.get("address") for item in snapshot.get("windows", [])}
            if target and target not in addresses:
                candidates = [
                    {"address": item.get("address"), "title": item.get("title")}
                    for item in snapshot.get("windows", [])
                ][:12]
                raise ValueError(
                    "Use a current window address, not a title or app name. "
                    "Observed targets: " + json.dumps(candidates)
                )
            window = next(
                (item for item in snapshot.get("windows", []) if item.get("address") == target), {}
            )
            if (
                name == "keyboard"
                and args.get("action") == "type"
                and re.search(
                    r"kitty|alacritty|foot|konsole|terminal|xterm",
                    str(window.get("class", "")),
                    re.IGNORECASE,
                )
            ):
                raise ValueError(
                    "Use the guarded terminal tool for typing executable terminal content."
                )
        consequential = (
            name in {"pointer", "use_bind"}
            or (name == "hypr" and args.get("action") == "close_window")
            or (
                name == "click_ui"
                and (bool(args.get("mark")) or COMMIT.search(str(args.get("name", ""))))
            )
            or (
                name == "keyboard"
                and args.get("action") == "key"
                and any(
                    key in str(args.get("keys", "")).lower()
                    for key in ("enter", "return", "delete")
                )
            )
        )
        action_summary = json.dumps(
            {k: v for k, v in args.items() if k != "text"}, ensure_ascii=False
        )[:500]
        if consequential and request_approval:
            if not context.confirm(f"Approve desktop action {name}: {action_summary}?"):
                raise PermissionError("Desktop action was declined.")
            return True
        return False

    @staticmethod
    def _steps(name, args):
        if name == "sequence":
            return [(step.get("op"), step) for step in args.get("steps", [])]
        return [(name, args)]

    def _pin_targets(self, args, snapshot):
        active = (snapshot or {}).get("active_window") or (snapshot or {}).get("active")
        if isinstance(active, dict):
            active = active.get("address")
        for name, step in self._steps(self.operation, args):
            if active and name == "click_ui" and not step.get("window"):
                step["window"] = active
            if (
                active
                and name == "hypr"
                and step.get("action") == "close_window"
                and not step.get("target")
            ):
                step["target"] = active
            if name == "pointer" and step.get("action") in {"click", "scroll"}:
                cursor = (snapshot or {}).get("cursor")
                if isinstance(cursor, (list, tuple)) and len(cursor) == 2:
                    if step.get("x") is None:
                        step["x"] = cursor[0]
                    if step.get("y") is None:
                        step["y"] = cursor[1]

    def _after_approval(self, args, before, context):
        task = getattr(context, "task", None)
        if task:
            task.check()
        observed = HypruseTool(self.bridge).execute({}, context)
        if not observed.get("success") or self.bridge.snapshot is None:
            raise RuntimeError(
                "Could not refresh the desktop after approval; the action was not delivered."
            )
        old_windows = {
            window.get("address"): window for window in (before or {}).get("windows", [])
        }
        new_windows = {
            window.get("address"): window for window in self.bridge.snapshot.get("windows", [])
        }
        for name, step in self._steps(self.operation, args):
            target = step.get("window") or step.get("target")
            if target:
                previous, current = old_windows.get(target), new_windows.get(target)
                if (
                    previous is None
                    or current is None
                    or any(
                        previous.get(key) is not None and previous[key] != current.get(key)
                        for key in ("class", "pid")
                    )
                ):
                    raise RuntimeError(
                        "The approved target changed or closed; the action was not delivered."
                    )
            if name == "pointer" and any(
                address not in new_windows
                or any(previous.get(key) != new_windows[address].get(key) for key in ("at", "size"))
                for address, previous in old_windows.items()
            ):
                raise RuntimeError(
                    "Window geometry changed during approval; "
                    "observe before choosing new coordinates."
                )
        self._validate(self.operation, args, context, request_approval=False)
        if any(name == "use_bind" for name, _step in self._steps(self.operation, args)):
            active = (before or {}).get("active_window") or (before or {}).get("active")
            if isinstance(active, dict):
                active = active.get("address")
            if active:
                if active not in new_windows:
                    raise RuntimeError(
                        "The original focused window closed; the binding was not delivered."
                    )
                focused = HypruseTool(self.bridge, "hypr").execute(
                    {"action": "focus_window", "target": active},
                    context,
                )
                if not focused.get("success"):
                    raise RuntimeError(
                        "Could not restore the approved binding's focus; "
                        "the binding was not delivered."
                    )
        if task:
            task.check()

    def execute(self, arguments, context):
        args = copy.deepcopy(arguments)
        before = copy.deepcopy(self.bridge.snapshot)
        self._pin_targets(args, before)
        approved = self._validate(self.operation, args, context)
        if approved:
            try:
                self._after_approval(args, before, context)
            except (RuntimeError, ValueError) as exc:
                return {
                    "success": False,
                    "error": str(exc),
                    "pause": True,
                    "verification": "Approval handoff failed; "
                    "the requested action was not delivered.",
                }
        if self.operation not in OBSERVATIONS and self.operation != "launch":
            args.setdefault(
                "then", "ui" if self.operation in {"keyboard", "click_ui"} else "desktop"
            )
        try:
            raw = self.bridge.call(self.operation, args)
        except HypruseConnectionError as exc:
            uncertain = exc.delivery == "uncertain_delivery"
            return {
                "success": False,
                "error": str(exc),
                "error_code": exc.error_code,
                "delivery": exc.delivery,
                "pause": True,
                "verification": "Delivery is uncertain; re-observe before retrying."
                if uncertain else "The requested desktop operation was not delivered.",
            }
        except Exception:
            return {
                "success": False,
                "error": _ERROR_MESSAGES["connection_lost"],
                "error_code": "connection_lost",
                "delivery": "not_delivered" if self.operation in OBSERVATIONS
                else "uncertain_delivery",
                "pause": True,
                "verification": "The desktop observation failed."
                if self.operation in OBSERVATIONS
                else "Delivery is uncertain; re-observe before retrying.",
            }
        if raw.get("isError"):
            error = " ".join(p.get("text", "") for p in raw.get("content", []))
            pause = bool(
                re.search(
                    r"seat|took.*control|focus.*changed|cursor.*moved|timed out",
                    error,
                    re.IGNORECASE,
                )
            )
            return {"success": False, "error": error, "pause": pause}
        images, text = [], []
        preview = None
        for block in raw.get("content", []):
            if block.get("type") == "image":
                preview = captures.add(block["data"], block["mimeType"])
                images.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{block['mimeType']};base64,{block['data']}",
                            "detail": "high",
                        },
                    }
                )
            elif block.get("type") == "text":
                text.append(block.get("text", ""))

        def without_images(value):
            if isinstance(value, dict):
                if value.get("type") == "image":
                    return {
                        "type": "image",
                        "mimeType": value.get("mimeType"),
                        "note": "Binary capture is transient and supplied separately.",
                    }
                return {key: without_images(item) for key, item in value.items()}
            if isinstance(value, list):
                return [without_images(item) for item in value]
            return value

        output = without_images(raw.get("structuredContent") or {"text": text})

        def find_snapshot(value):
            if isinstance(value, dict):
                if "windows" in value:
                    return value
                for nested in value.values():
                    found = find_snapshot(nested)
                    if found is not None:
                        return found
            elif isinstance(value, list):
                for nested in value:
                    found = find_snapshot(nested)
                    if found is not None:
                        return found
            return None

        snapshot = find_snapshot(output)
        if snapshot is None:
            for value in text:
                try:
                    candidate = json.loads(value)
                    snapshot = find_snapshot(candidate)
                    if snapshot is not None:
                        if self.operation == "desktop":
                            output = candidate
                        break
                except json.JSONDecodeError:
                    pass
        if snapshot is not None:
            self.bridge.snapshot, self.bridge.snapshot_at = snapshot, time.monotonic()
        return {
            "success": True,
            "output": output,
            "_images": images,
            "preview": preview,
            "verification": "observed"
            if self.operation in OBSERVATIONS
            else "action delivered; inspect returned state",
        }
