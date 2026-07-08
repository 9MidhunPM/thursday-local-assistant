from __future__ import annotations

import http.server
import json
import os
import queue
import socketserver
import subprocess
import tempfile
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Any

from assistant.runtime import AssistantRuntime

# --- Static frontend (built React app) ---
WEB_DIST = Path(__file__).parent / "web" / "dist"
FALLBACK_HTML = Path(__file__).parent / "gui" / "index.html"

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".webp": "image/webp",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".wasm": "application/wasm",
    ".txt": "text/plain; charset=utf-8",
    ".map": "application/json; charset=utf-8",
}

# Global thread-safe event broadcaster for Server-Sent Events (SSE)
class EventBroadcaster:
    def __init__(self) -> None:
        self.clients: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.shutdown_timer = None
        self._shutting_down = False

    def add_client(self, client_type: str = "web") -> queue.Queue[dict[str, Any]]:
        q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=100)
        with self.lock:
            self.clients.append({"queue": q, "type": client_type})
            if self.shutdown_timer:
                self.shutdown_timer.cancel()
                self.shutdown_timer = None
        return q

    def remove_client(self, q: queue.Queue[dict[str, Any]]) -> None:
        with self.lock:
            self.clients = [c for c in self.clients if c["queue"] != q]
            if len(self.clients) == 0:
                self.shutdown_timer = threading.Timer(10.0, self._dummy_shutdown)
                self.shutdown_timer.daemon = True
                self.shutdown_timer.start()

    def _dummy_shutdown(self):
        # We don't shut down llama-server anymore, but we can do backend cleanup here if needed
        pass

    def broadcast(self, event_type: str, data: Any) -> None:
        event = {"type": event_type, "data": data, "timestamp": time.time()}
        with self.lock:
            has_web = any(c["type"] == "web" for c in self.clients)
            for c in self.clients:
                if event_type == "tts_audio" and c["type"] == "quickshell" and has_web:
                    continue
                try:
                    c["queue"].put_nowait(event)
                except queue.Full:
                    # Evict oldest if queue is full
                    try:
                        c["queue"].get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        c["queue"].put_nowait(event)
                    except queue.Full:
                        pass

    def shutdown_all_clients(self) -> None:
        """Gracefully close all SSE client connections."""
        with self.lock:
            self._shutting_down = True
            for q in self.clients:
                try:
                    q.put_nowait({"type": "shutdown", "data": {"reason": "server_shutdown"}, "timestamp": time.time()})
                except queue.Full:
                    pass
            self.clients.clear()
            if self.shutdown_timer:
                self.shutdown_timer.cancel()
                self.shutdown_timer = None

    def is_shutting_down(self) -> bool:
        return self._shutting_down

    def remove_all_clients(self) -> None:
        """Remove all clients and clear the client list."""
        with self.lock:
            for q in self.clients:
                try:
                    q.put_nowait({"type": "shutdown", "data": {"reason": "server_shutdown"}, "timestamp": time.time()})
                except queue.Full:
                    pass
            self.clients.clear()
            if self.shutdown_timer:
                self.shutdown_timer.cancel()
                self.shutdown_timer = None


broadcaster = EventBroadcaster()
server_runtime: AssistantRuntime | None = None
is_busy = False
busy_lock = threading.Lock()
running_port = 5005
tts_active = False
is_model_ready = False
model_log_buffer = []
active_conversation_id: int | None = None
http_server: ThreadingHTTPServer | None = None
server_thread: threading.Thread | None = None
http_server: ThreadingHTTPServer | None = None
server_thread: threading.Thread | None = None


def _serialize_message(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert a stored conversation message into the shape the UI expects."""
    item: dict[str, Any] = {"role": raw["role"], "content": raw.get("content")}
    tool_calls = raw.get("tool_calls")
    if tool_calls:
        item["tool_calls"] = [
            {"name": tc.get("name"), "arguments": tc.get("arguments")} for tc in tool_calls
        ]
    if raw["role"] == "tool" and raw.get("tool_name"):
        item["tool_name"] = raw["tool_name"]
    return item


def _history_for_active_conversation() -> list[dict[str, Any]]:
    """Full message history for the active conversation (UI-facing)."""
    global active_conversation_id
    if not server_runtime or active_conversation_id is None:
        return []
    try:
        messages = server_runtime.agent.get_conversation_messages(active_conversation_id)
    except Exception:
        return []
    return [_serialize_message(m) for m in messages if m.get("role") != "system"]


def _ensure_active_conversation() -> int:
    """Create+activate a conversation if none is active. Returns its id."""
    global active_conversation_id
    if active_conversation_id is not None:
        return active_conversation_id
    assert server_runtime is not None
    cid = server_runtime.agent.start_conversation("New Chat")
    assert cid is not None
    active_conversation_id = cid
    return cid


def _activate_conversation(conversation_id: int) -> None:
    """Switch the agent's working memory to a conversation."""
    global active_conversation_id
    active_conversation_id = conversation_id
    if server_runtime:
        server_runtime.agent.set_conversation(conversation_id)



class ThursdayHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        # Suppress logging HTTP requests to stdout to keep CLI clean!
        pass

    def do_GET(self) -> None:
        from urllib.parse import urlparse
        parsed_path = urlparse(self.path).path
        if parsed_path == "/" or parsed_path == "/index.html":
            self._serve_index()
            return

        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            health_data = {
                "status": "ok" if is_model_ready else "starting",
                "model_ready": is_model_ready,
                "busy": is_busy,
                "tts_active": tts_active,
                "clients": len(broadcaster.clients),
                "llama_server_running": True,
            }
            self.wfile.write(json.dumps(health_data).encode())
            return

        if self.path == "/api/tools":
            self.handle_tools_request()
            return

        if parsed_path == "/api/events":
            from urllib.parse import parse_qs
            params = parse_qs(urlparse(self.path).query)
            client_type = params.get("client", ["web"])[0]
            if client_type not in ("web", "quickshell"):
                client_type = "web"

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            q = broadcaster.add_client(client_type)
            try:
                # Send initial state including buffered logs and the FULL history
                # of the active conversation (not the pruned model context window).
                history_data = _history_for_active_conversation()

                self.wfile.write(
                    f"data: {json.dumps({'type': 'init', 'data': {'busy': is_busy, 'model_ready': is_model_ready, 'logs': model_log_buffer, 'history': history_data, 'conversation_id': active_conversation_id}})}\n\n".encode()
                )
                self.wfile.flush()

                while True:
                    try:
                        # Periodically timeout to check if client socket has closed
                        event = q.get(timeout=2.0)
                        self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                        self.wfile.flush()
                    except queue.Empty:
                        # Keep-alive ping
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
            except (ConnectionError, BrokenPipeError):
                pass
            finally:
                broadcaster.remove_client(q)
            return

        if parsed_path == "/api/conversations":
            self.handle_list_conversations()
            return

        if parsed_path.startswith("/api/conversations/"):
            self.handle_get_conversation(parsed_path)
            return

        if self.path.startswith("/api/audio/"):
            filename = self.path.split("/")[-1]
            filepath = Path("/tmp/thursday_tts") / filename
            if filepath.exists() and filepath.is_file():
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Access-Control-Allow-Origin", "*")
                
                # Cache control for audio
                self.send_header("Cache-Control", "no-cache")
                
                try:
                    data = filepath.read_bytes()
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except Exception as e:
                    self.send_error(500, f"Error reading file: {e}")
                
                # Try to clean up after serving
                try:
                    filepath.unlink(missing_ok=True)
                except Exception:
                    pass
                return
            self.send_error(404, "Audio file not found")
            return

        # Unknown /api/* paths are real 404s (do not fall through to SPA).
        if parsed_path.startswith("/api/"):
            self.send_error(404, "Not Found")
            return

        # Serve a built asset, or fall back to index.html for unknown routes (SPA).
        self._serve_static(parsed_path)

    def _serve_index(self) -> None:
        html_path = WEB_DIST / "index.html"
        if not html_path.exists():
            html_path = FALLBACK_HTML
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        if html_path.exists():
            self.wfile.write(html_path.read_bytes())
        else:
            self.wfile.write(
                b"<h1>Frontend not built.</h1>"
                b"<p>Run <code>npm install &amp;&amp; npm run build</code> in <code>assistant/web</code>.</p>"
            )

    def _serve_static(self, rel_path: str) -> None:
        safe = rel_path.lstrip("/")
        try:
            file_path = (WEB_DIST / safe).resolve()
            file_path.relative_to(WEB_DIST.resolve())
        except (ValueError, OSError):
            self.send_error(403, "Forbidden")
            return
        if file_path.is_file():
            ext = file_path.suffix.lower()
            ctype = MIME_TYPES.get(ext, "application/octet-stream")
            data = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(data)))
            if ext == ".html":
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            else:
                self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(data)
        else:
            # SPA fallback: unknown non-file routes render the app shell.
            self._serve_index()

    def handle_tools_request(self) -> None:
        """Handle GET request for available tools."""
        try:
            # Get the runtime to access the agent and its tools
            if not server_runtime or not server_runtime.agent:
                self.send_error(503, "Agent not initialized")
                return
            
            tools = []
            if server_runtime and server_runtime.agent:
                agent_tools = server_runtime.agent._tool_registry
                for tool_instance in agent_tools.tools():
                    tool_info = {
                        "name": tool_instance.name,
                        "description": getattr(tool_instance, 'description', 'No description available'),
                        "parameters": getattr(tool_instance, 'parameters', {}),
                        "example": self._get_tool_example(tool_instance.name)
                    }
                    tools.append(tool_info)
            
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(tools).encode())
            
        except Exception as e:
            self.send_error(500, f"Error retrieving tools: {str(e)}")

    def _get_tool_example(self, tool_name: str) -> str:
        """Get an example usage for a tool."""
        examples = {
            "web_search": "Search for 'latest AI news'",
            "file_read": "Read file '/home/user/documents/notes.txt'",
            "file_write": "Write 'Hello world' to '/tmp/test.txt'",
            "calculate": "Calculate 'sqrt(144) + 10'",
            "convert": "Convert '100 fahrenheit to celsius'",
            "system_monitor": "Get system information with full details",
            "news": "Get latest technology news",
            "spotify_search_play": "Play 'Bohemian Rhapsody' on Spotify",
            "volume_control": "Set volume to 70%",
            "brightness_control": "Set screen brightness to 80%",
            "open_app": "Open Firefox browser",
            "run_terminal_command": "List files in current directory with 'ls -la'",
        }
        return examples.get(tool_name, f"Use {tool_name} tool")

    def _send_json(self, status: int, payload: Any) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def handle_list_conversations(self) -> None:
        if not server_runtime:
            self._send_json(200, [])
            return
        convs = server_runtime.conversation_store.list_conversations()
        self._send_json(
            200,
            [
                {
                    "id": c.id,
                    "title": c.title,
                    "created_at": c.created_at,
                    "updated_at": c.updated_at,
                }
                for c in convs
            ],
        )

    def handle_get_conversation(self, parsed_path: str) -> None:
        if not server_runtime:
            self.send_error(503, "Agent not initialized")
            return
        try:
            cid = int(parsed_path.rstrip("/").split("/")[-1])
        except ValueError:
            self.send_error(400, "Invalid conversation id")
            return
        conv = server_runtime.conversation_store.get_conversation(cid)
        if not conv:
            self.send_error(404, "Conversation not found")
            return
        messages = [_serialize_message(m) for m in server_runtime.conversation_store.get_messages(cid)]
        self._send_json(
            200,
            {
                "id": conv.id,
                "title": conv.title,
                "created_at": conv.created_at,
                "updated_at": conv.updated_at,
                "messages": messages,
            },
        )

    def handle_create_conversation(self) -> None:
        if not server_runtime:
            self.send_error(503, "Agent not initialized")
            return
        title = "New Chat"
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length:
            try:
                data = json.loads(self.rfile.read(content_length))
                if isinstance(data.get("title"), str) and data["title"].strip():
                    title = data["title"].strip()[:100]
            except Exception:
                pass
        cid = server_runtime.conversation_store.create_conversation(title)
        conv = server_runtime.conversation_store.get_conversation(cid)
        assert conv is not None
        self._send_json(
            201,
            {
                "id": conv.id,
                "title": conv.title,
                "created_at": conv.created_at,
                "updated_at": conv.updated_at,
            },
        )

    def handle_modify_conversation(self, parsed_path: str) -> None:
        if not server_runtime:
            self.send_error(503, "Agent not initialized")
            return
        try:
            cid = int(parsed_path.rstrip("/").split("/")[-1])
        except ValueError:
            self.send_error(400, "Invalid conversation id")
            return
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else b"{}"
        try:
            data = json.loads(body) if body else {}
        except Exception:
            self.send_error(400, "Invalid JSON")
            return
        title = data.get("title")
        if not isinstance(title, str) or not title.strip():
            self.send_error(400, "title is required")
            return
        ok = server_runtime.conversation_store.rename_conversation(cid, title.strip()[:100])
        if not ok:
            self.send_error(404, "Conversation not found")
            return
        broadcaster.broadcast("conversation_updated", {"id": cid, "title": title.strip()[:100]})
        self._send_json(200, {"id": cid, "title": title.strip()[:100]})

    def do_DELETE(self) -> None:
        from urllib.parse import urlparse
        parsed_path = urlparse(self.path).path
        if parsed_path.startswith("/api/conversations/"):
            if not server_runtime:
                self.send_error(503, "Agent not initialized")
                return
            try:
                cid = int(parsed_path.rstrip("/").split("/")[-1])
            except ValueError:
                self.send_error(400, "Invalid conversation id")
                return
            ok = server_runtime.conversation_store.delete_conversation(cid)
            if not ok:
                self.send_error(404, "Conversation not found")
                return
            global active_conversation_id
            if active_conversation_id == cid:
                active_conversation_id = None
            broadcaster.broadcast("conversation_deleted", {"id": cid})
            self._send_json(200, {"id": cid, "deleted": True})
            return
        self.send_error(404, "Not Found")

    def do_PATCH(self) -> None:
        from urllib.parse import urlparse
        parsed_path = urlparse(self.path).path
        if parsed_path.startswith("/api/conversations/"):
            self.handle_modify_conversation(parsed_path)
            return
        self.send_error(404, "Not Found")

    def do_POST(self) -> None:
        from urllib.parse import urlparse
        parsed_path = urlparse(self.path).path
        if parsed_path == "/api/message":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body)
                prompt = data.get("prompt", "").strip()
                use_tts = data.get("tts", False)
                conversation_id = data.get("conversation_id")
            except Exception:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Invalid JSON")
                return

            if not prompt:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Prompt cannot be empty")
                return

            # Activate the requested conversation (or create one), so the agent's
            # working memory and persistence target the right session.
            try:
                if conversation_id is not None and server_runtime:
                    _activate_conversation(int(conversation_id))
                else:
                    cid = _ensure_active_conversation()
                    conversation_id = cid
            except Exception:
                conversation_id = active_conversation_id

            global is_busy
            with busy_lock:
                if is_busy:
                    self.send_response(409)
                    self.end_headers()
                    self.wfile.write(b"Agent is busy")
                    return
                is_busy = True

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {"status": "processing", "conversation_id": conversation_id}
                ).encode()
            )

            # Stop any ongoing TTS from previous response
            if server_runtime and server_runtime.tts:
                server_runtime.tts.stop()

            # Start agent reasoning in a separate thread
            threading.Thread(
                target=self.run_agent,
                args=(prompt, use_tts, conversation_id),
                daemon=True,
            ).start()
            return

        if parsed_path == "/api/conversations":
            self.handle_create_conversation()
            return

        if parsed_path.startswith("/api/conversations/"):
            self.handle_modify_conversation(parsed_path)
            return

        if parsed_path == "/api/transcribe":
            content_length = int(self.headers.get("Content-Length", 0))
            audio_data = self.rfile.read(content_length)
            if not audio_data:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"No audio data received")
                return

            result = self._transcribe_audio(audio_data)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())
            return

        if parsed_path == "/api/shutdown":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "shutting_down"}).encode())
            # Trigger shutdown in background to allow response to be sent
            threading.Thread(target=shutdown_server, daemon=True).start()
            return

        self.send_error(404, "Not Found")

    def run_agent(self, prompt: str, use_tts: bool = False, conversation_id: int | None = None) -> None:
        import re as _re
        import time
        global is_busy
        tts = server_runtime.tts if server_runtime and use_tts else None
        tts_buffer: list[str] = []
        tts_spoken_index = 0
        _tts_debounce_timer: threading.Timer | None = None

        # Auto-title the conversation from the first user message.
        if conversation_id is not None and server_runtime:
            try:
                conv = server_runtime.conversation_store.get_conversation(conversation_id)
                if conv and conv.title == "New Chat":
                    title = prompt.strip().splitlines()[0][:50].strip() or "New Chat"
                    server_runtime.conversation_store.rename_conversation(conversation_id, title)
                    broadcaster.broadcast(
                        "conversation_updated", {"id": conversation_id, "title": title}
                    )
            except Exception:
                pass

        # Common abbreviations that should NOT trigger a sentence break
        _ABBREV_PATTERN = _re.compile(
            r'\b(?:e\.g|i\.e|Dr|Mr|Mrs|Ms|vs|etc|approx|dept|govt|Jr|Sr|Prof|Inc|Ltd|Corp|Fig|Vol|No|Ch)\s*$',
            _re.IGNORECASE,
        )
        # Real sentence boundary: sentence-ending punct followed by space, newline, or end-of-string
        _SENTENCE_END = _re.compile(r'[.!?](?:\s|$|\n)')

        # Wire TTS audio callbacks to SSE
        if tts:
            def _on_tts_audio(filename: str, text: str) -> None:
                global tts_active
                tts_active = True
                broadcaster.broadcast("tts_audio", {"url": f"/api/audio/{filename}", "text": text})
            def _on_tts_speak_end() -> None:
                global tts_active
                tts_active = False
                broadcaster.broadcast("tts_stop", {})
            tts._on_audio_ready = _on_tts_audio
            tts._on_speak_end = _on_tts_speak_end

        def _flush_tts(force: bool = False) -> None:
            nonlocal tts_spoken_index, _tts_debounce_timer
            if _tts_debounce_timer:
                _tts_debounce_timer.cancel()
                _tts_debounce_timer = None
            if not tts or tts_spoken_index >= len(tts_buffer):
                return
            text = "".join(tts_buffer[tts_spoken_index:])
            if not text.strip():
                return
            clean = _re.sub(r"[*_`#\[\]]", "", text)
            clean = _re.sub(r"\[.*?\]\(.*?\)", "", clean)
            if clean.strip():
                tts.speak_async(clean.strip())
            tts_spoken_index = len(tts_buffer)

        def _schedule_debounce() -> None:
            nonlocal _tts_debounce_timer
            if _tts_debounce_timer:
                _tts_debounce_timer.cancel()
            _tts_debounce_timer = threading.Timer(0.25, _flush_tts, kwargs={"force": True})
            _tts_debounce_timer.daemon = True
            _tts_debounce_timer.start()

        def on_stream(chunk: str) -> None:
            broadcaster.broadcast("token", {"chunk": chunk})
            if tts:
                tts_buffer.append(chunk)
                acc = "".join(tts_buffer[tts_spoken_index:])

                # Flush on real sentence boundary with enough text buffered
                # (queue-based TTS handles sequential playback, so flush often)
                has_boundary = _SENTENCE_END.search(acc)
                is_long_enough = len(acc) >= 15
                ends_with_newline = acc.rstrip().endswith('\n')

                if has_boundary and is_long_enough and not _ABBREV_PATTERN.search(acc):
                    _flush_tts()
                elif ends_with_newline and len(acc.strip()) > 10:
                    _flush_tts()
                else:
                    # Debounce: auto-flush if tokens stop arriving
                    _schedule_debounce()

        def on_tool_call(tool: str, arguments: dict[str, Any]) -> None:
            broadcaster.broadcast("tool_call", {"tool": tool, "arguments": arguments})

        def on_tool_chunk(tool: str, chunk: str) -> None:
            broadcaster.broadcast("tool_chunk", {"tool": tool, "chunk": chunk})

        def on_tool_result(result: dict[str, Any]) -> None:
            broadcaster.broadcast("tool_result", result)

        try:
            if server_runtime and server_runtime.agent:
                final_response = server_runtime.agent.handle_message(
                    prompt,
                    on_stream=on_stream,
                    on_tool_call=on_tool_call,
                    on_tool_chunk=on_tool_chunk,
                    on_tool_result=on_tool_result,
                )
                
                broadcaster.broadcast("final_response", {"content": final_response})
                
                # Speak any remaining text not yet spoken
                if tts:
                    _flush_tts(force=True)
            else:
                broadcaster.broadcast("error", {"content": "Agent runtime not initialized."})
        except Exception as exc:
            broadcaster.broadcast("error", {"content": str(exc)})
        finally:
            if _tts_debounce_timer:
                _tts_debounce_timer.cancel()
            with busy_lock:
                is_busy = False
            broadcaster.broadcast("status", {"busy": False})
    def _transcribe_audio(self, audio_data: bytes) -> dict[str, Any]:
        rt = server_runtime
        if not rt or not rt.stt:
            return {"error": "STT not available"}
        webm_path: str | None = None
        wav_path: str | None = None
        try:
            tmp = tempfile.NamedTemporaryFile(suffix=".webm", delete=False)
            tmp.write(audio_data)
            tmp.flush()
            webm_path = tmp.name
            tmp.close()

            wav_path = webm_path + ".wav"
            ffmpeg_cmd = [
                "ffmpeg", "-y", "-i", webm_path,
                "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
                wav_path,
            ]
            convert = subprocess.run(ffmpeg_cmd, capture_output=True, timeout=30)
            if convert.returncode != 0:
                raise RuntimeError(f"Audio conversion failed: {convert.stderr.decode(errors='ignore')[:200]}")

            text = rt.stt.transcribe(wav_path)
            return {"text": text}
        except Exception as exc:
            print(f"STT Error: {exc}")
            return {"error": str(exc)}
        finally:
            for p in (webm_path, wav_path):
                if p and os.path.exists(p):
                    try:
                        os.unlink(p)
                    except OSError:
                        pass


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    # Enable socket re-use to avoid port-binding issues on quick restarts
    allow_reuse_address = True


def start_server(
    runtime: AssistantRuntime,
    host: str | None = None,
    port: int | None = None,
) -> None:
    global server_runtime, running_port, http_server, server_thread
    server_runtime = runtime

    host = host or os.getenv("THURSDAY_HOST", "127.0.0.1")
    port = port if port is not None else int(os.getenv("THURSDAY_PORT", "5005"))

    llama_host = os.getenv("LLAMA_HOST", "127.0.0.1")
    llama_port = os.getenv("LLAMA_PORT", "8080")
    health_url = f"http://{llama_host}:{llama_port}/health"

    def poll_health():
        global is_model_ready
        import urllib.request
        import json
        import time
        while True:
            try:
                resp = urllib.request.urlopen(health_url, timeout=1)
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("status") in ("ok", "ready"):
                    if not is_model_ready:
                        is_model_ready = True
                        broadcaster.broadcast("model_ready", {})
                else:
                    is_model_ready = False
            except Exception:
                is_model_ready = False
            time.sleep(2.0)
    
    threading.Thread(target=poll_health, daemon=True).start()

    current_port = port
    while current_port < port + 50:
        try:
            server = ThreadingHTTPServer((host, current_port), ThursdayHTTPRequestHandler)
            running_port = current_port
            break
        except OSError:
            current_port += 1
    else:
        print(f"Error: Could not bind HTTP server to any port in range {port}-{port+50}")
        return

    http_server = server

    # Start the server thread
    t = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread = t
    t.start()


def shutdown_server() -> None:
    """Gracefully shutdown the HTTP server."""
    global http_server, server_thread, server_runtime
    
    print("Shutting down Thursday server...")
    
    # Stop accepting new connections and close existing ones
    if http_server:
        http_server.shutdown()
        http_server.server_close()
        http_server = None
    
    # Wait for server thread to finish
    if server_thread and server_thread.is_alive():
        server_thread.join(timeout=5.0)
        server_thread = None
    
    # Shutdown runtime (closes LLM client, stops TTS)
    if server_runtime:
        try:
            server_runtime.shutdown()
        except Exception as e:
            print(f"Error shutting down runtime: {e}")
    
    # Notify all SSE clients
    broadcaster.broadcast("shutdown", {})
    
    # Clear all clients
    broadcaster.remove_all_clients()
    
    print("Thursday server shutdown complete.")


def open_browser() -> None:
    # Give the server a small moment to start up
    time.sleep(0.5)
    webbrowser.open(f"http://127.0.0.1:{running_port}")
