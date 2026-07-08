"""Gmail IMAP tool – reads a user's Gmail inbox.

Provides a function `gmail_read` which logs into Gmail via IMAP using a username and password (or app password), fetches the most recent messages, and returns their metadata.

**Security note:** Credentials are passed as arguments; the tool does not store them anywhere. In a real deployment you would prefer OAuth2 tokens and secure storage.
"""

from __future__ import annotations

import imaplib
import email
from dataclasses import dataclass
from typing import Any, List, Dict

from assistant.agent.context import ExecutionContext
from assistant.tools.base import BaseTool, ToolMetadata


@dataclass
class GmailReadTool(BaseTool):
    """Read recent emails from a Gmail account via IMAP.

    The tool expects a Gmail username (full email address) and the corresponding password or app‑specific password.
    It returns a list of messages with subject, sender, date, and a short snippet of the plain‑text body.
    """

    metadata: ToolMetadata = ToolMetadata(
        name="gmail_read",
        description="Read the latest emails from a Gmail inbox using IMAP.",
        parameters={
            "type": "object",
            "properties": {
                "username": {
                    "type": "string",
                    "description": "Full Gmail address (e.g., user@example.com)"
                },
                "password": {
                    "type": "string",
                    "description": "Gmail password or app‑specific password (will not be stored)"
                },
                "max_messages": {
                    "type": "integer",
                    "description": "Maximum number of recent messages to fetch (default 5)",
                    "default": 5
                }
            },
            "required": ["username", "password"]
        }
    )

    _IMAP_HOST = "imap.gmail.com"
    _IMAP_PORT = 993

    def _fetch_latest(self, conn: imaplib.IMAP4_SSL, max_messages: int) -> List[Dict[str, Any]]:
        # Search for all messages and get their UIDs
        typ, data = conn.search(None, "ALL")
        if typ != "OK":
            raise RuntimeError("IMAP search failed")
        uids = data[0].split()
        if not uids:
            return []
        # Get the most recent UIDs (IMAP stores them in chronological order)
        recent_uids = uids[-max_messages:]
        messages: List[Dict[str, Any]] = []
        for uid in reversed(recent_uids):  # newest first
            typ, msg_data = conn.fetch(uid, "(RFC822)")
            if typ != "OK":
                continue
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)
            subject = email.header.decode_header(msg.get("Subject", ""))
            subject_parts = []
            for part, enc in subject:
                if isinstance(part, bytes):
                    part = part.decode(enc or "utf-8", errors="replace")
                subject_parts.append(part)
            subject_str = "".join(subject_parts)
            from_ = msg.get("From", "")
            date = msg.get("Date", "")
            # Extract a plain‑text snippet (first 200 chars)
            snippet = ""
            if msg.is_multipart():
                for part in msg.walk():
                    ctype = part.get_content_type()
                    if ctype == "text/plain" and not part.get("Content-Disposition"):
                        payload = part.get_payload(decode=True)
                        if payload:
                            snippet = payload.decode(part.get_content_charset("utf-8"), errors="replace")
                            break
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    snippet = payload.decode(msg.get_content_charset("utf-8"), errors="replace")
            snippet = snippet.strip().replace("\r", " ").replace("\n", " ")
            if len(snippet) > 200:
                snippet = snippet[:200] + "…"
            messages.append({
                "uid": uid.decode() if isinstance(uid, bytes) else str(uid),
                "subject": subject_str,
                "from": from_,
                "date": date,
                "snippet": snippet,
            })
        return messages

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        username = arguments.get("username", "").strip()
        password = arguments.get("password", "")
        max_messages = int(arguments.get("max_messages", 5))
        if not username or not password:
            return {"success": False, "error": "Both username and password are required"}
        try:
            conn = imaplib.IMAP4_SSL(self._IMAP_HOST, self._IMAP_PORT)
            conn.login(username, password)
            conn.select("INBOX")
            messages = self._fetch_latest(conn, max_messages)
            conn.logout()
            return {"success": True, "messages": messages, "count": len(messages)}
        except imaplib.IMAP4.error as e:
            return {"success": False, "error": f"IMAP authentication error: {e}"}
        except Exception as e:
            return {"success": False, "error": f"Failed to read Gmail: {e}"}


def get_tools(config: Any | None = None) -> List[BaseTool]:
    return [GmailReadTool()]
