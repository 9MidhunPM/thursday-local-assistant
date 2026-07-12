"""Gmail IMAP tool – reads a user's Gmail inbox.

Credentials are preferred from environment variables so they never pass through
the model tool-call channel:

  GMAIL_USER / GMAIL_USERNAME
  GMAIL_APP_PASSWORD / GMAIL_PASSWORD

Arguments still work as a fallback (and are redacted in logs).
"""

from __future__ import annotations

import email
import imaplib
import os
from dataclasses import dataclass
from typing import Any

from assistant.agent.context import ExecutionContext
from assistant.tools.base import BaseTool, ToolMetadata


@dataclass
class GmailReadTool(BaseTool):
    """Read recent emails from a Gmail account via IMAP."""

    metadata: ToolMetadata = ToolMetadata(
        name="gmail_read",
        description=(
            "Read the latest emails from a Gmail inbox using IMAP. "
            "Prefer configuring GMAIL_USER and GMAIL_APP_PASSWORD in .env; "
            "username/password args are optional fallbacks."
        ),
        parameters={
            "type": "object",
            "properties": {
                "username": {
                    "type": "string",
                    "description": "Optional Gmail address (defaults to GMAIL_USER env)",
                },
                "password": {
                    "type": "string",
                    "description": "Optional app password (defaults to GMAIL_APP_PASSWORD env)",
                },
                "max_messages": {
                    "type": "integer",
                    "description": "Maximum number of recent messages to fetch (default 5)",
                    "default": 5,
                },
            },
            "required": [],
        },
    )

    _IMAP_HOST = "imap.gmail.com"
    _IMAP_PORT = 993

    def _fetch_latest(self, conn: imaplib.IMAP4_SSL, max_messages: int) -> list[dict[str, Any]]:
        typ, data = conn.search(None, "ALL")
        if typ != "OK":
            raise RuntimeError("IMAP search failed")
        uids = data[0].split()
        if not uids:
            return []
        recent_uids = uids[-max_messages:]
        messages: list[dict[str, Any]] = []
        for uid in reversed(recent_uids):
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
            snippet = ""
            if msg.is_multipart():
                for part in msg.walk():
                    ctype = part.get_content_type()
                    if ctype == "text/plain" and not part.get("Content-Disposition"):
                        payload = part.get_payload(decode=True)
                        if payload:
                            snippet = payload.decode(
                                part.get_content_charset("utf-8"), errors="replace"
                            )
                            break
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    snippet = payload.decode(msg.get_content_charset("utf-8"), errors="replace")
            snippet = snippet.strip().replace("\r", " ").replace("\n", " ")
            if len(snippet) > 200:
                snippet = snippet[:200] + "…"
            messages.append(
                {
                    "uid": uid.decode() if isinstance(uid, bytes) else str(uid),
                    "subject": subject_str,
                    "from": from_,
                    "date": date,
                    "snippet": snippet,
                }
            )
        return messages

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        username = (
            str(arguments.get("username") or "").strip()
            or os.getenv("GMAIL_USER")
            or os.getenv("GMAIL_USERNAME")
            or ""
        ).strip()
        password = (
            str(arguments.get("password") or "")
            or os.getenv("GMAIL_APP_PASSWORD")
            or os.getenv("GMAIL_PASSWORD")
            or ""
        )
        max_messages = int(arguments.get("max_messages", 5))
        if not username or not password:
            return {
                "success": False,
                "error": (
                    "Gmail credentials missing. Set GMAIL_USER and GMAIL_APP_PASSWORD "
                    "in .env (recommended), or pass username/password."
                ),
            }
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
            return {"success": False, "error": str(e)}


def get_tools() -> list[BaseTool]:
    return [GmailReadTool()]
