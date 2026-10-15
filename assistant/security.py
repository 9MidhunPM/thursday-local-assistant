from __future__ import annotations

import hmac
import os
import re
import secrets
import time
from functools import lru_cache
from http.cookies import CookieError, SimpleCookie
from ipaddress import ip_address
from typing import Any

BROWSER_SESSION_COOKIE = "Thursday-Session"
BROWSER_SESSION_TTL = 3600
BROWSER_SESSION_FUTURE_SKEW = 30
_BROWSER_SESSION_PATTERN = re.compile(
    r"v1\.([0-9]{1,16})\.([A-Za-z0-9_-]{32})\.([a-f0-9]{64})"
)
_BROWSER_SESSION_CONTEXT = b"thursday-browser-session-v1\0"


def _session_now() -> float:
    """Separate clock seam for deterministic expiry boundary checks."""
    return time.time()


def _session_signature(payload: str, token: str) -> str:
    return hmac.new(
        token.encode("utf-8"), _BROWSER_SESSION_CONTEXT + payload.encode("ascii"), "sha256"
    ).hexdigest()


def create_browser_session() -> str:
    """Issue an hour-long browser credential signed by the local operator token."""
    token = api_token()
    if token is None:
        message = "Configure THURSDAY_API_TOKEN before creating a browser session."
        raise RuntimeError(message)
    payload = f"v1.{int(_session_now())}.{secrets.token_urlsafe(24)}"
    return f"{payload}.{_session_signature(payload, token)}"


def _valid_browser_cookie(raw_cookie: str, token: str) -> bool:
    if not isinstance(raw_cookie, str) or not raw_cookie or len(raw_cookie) > 4096:
        return False
    try:
        cookies = SimpleCookie()
        cookies.load(raw_cookie)
        cookie = cookies.get(BROWSER_SESSION_COOKIE)
        if cookie is None:
            return False
        value = cookie.value
        matched = _BROWSER_SESSION_PATTERN.fullmatch(value)
        if matched is None:
            return False
        payload, signature = value.rsplit(".", 1)
        if not hmac.compare_digest(signature, _session_signature(payload, token)):
            return False
        age = _session_now() - int(matched.group(1))
    except (CookieError, ValueError, TypeError, UnicodeError):
        return False
    return -BROWSER_SESSION_FUTURE_SKEW <= age < BROWSER_SESSION_TTL


def _matches_operator_token(provided: str, expected: str) -> bool:
    # compare_digest(str, str) rejects non-ASCII input with TypeError. A malformed
    # HTTP header must be an authentication failure, never a request crash.
    return isinstance(provided, str) and hmac.compare_digest(
        provided.strip().encode("utf-8"), expected.encode("utf-8")
    )


def is_loopback_host(host: str) -> bool:
    host = (host or "").strip().lower()
    if host in {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"}:
        return True
    try:
        return ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        return False


def assert_bind_allowed(host: str) -> None:
    """Refuse non-loopback binds unless explicitly enabled."""
    if is_loopback_host(host):
        return
    allow = os.getenv("THURSDAY_ALLOW_REMOTE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not allow:
        message = (
            f"Refusing to bind Thursday to {host!r}. "
            "Set THURSDAY_ALLOW_REMOTE=1 only if you understand the risk "
            "(tools can control this machine). Prefer 127.0.0.1 and a tunnel."
        )
        raise RuntimeError(message)


@lru_cache(maxsize=1)
def api_token() -> str | None:
    """Optional shared secret for mutating HTTP APIs.

    If THURSDAY_API_TOKEN is set, clients must send:
      Authorization: Bearer <token>
      or header X-Thursday-Token: <token>
      or query ?token=
      or an unexpired Thursday-Session browser cookie
    """
    token = (os.getenv("THURSDAY_API_TOKEN") or "").strip()
    return token or None


def require_token_configured_for_remote(host: str) -> None:
    if is_loopback_host(host):
        return
    if not api_token():
        print(
            "WARNING: Binding remotely without THURSDAY_API_TOKEN. "
            "Anyone who can reach the port can run tools on this machine."
        )


def check_request_auth(headers: dict[str, str], query_token: str | None = None) -> bool:
    """Return True if request is authorized (or no token is configured)."""
    expected = api_token()
    if not expected:
        return True
    auth = headers.get("Authorization") or headers.get("authorization") or ""
    if isinstance(auth, str) and auth.lower().startswith("bearer "):
        provided = auth[7:].strip()
        if _matches_operator_token(provided, expected):
            return True
    header_token = headers.get("X-Thursday-Token") or headers.get("x-thursday-token")
    if header_token and _matches_operator_token(header_token, expected):
        return True
    if query_token and _matches_operator_token(query_token, expected):
        return True
    return _valid_browser_cookie(headers.get("Cookie") or headers.get("cookie") or "", expected)


def generate_dev_token() -> str:
    return secrets.token_urlsafe(24)


def redact_secrets(data: Any) -> Any:
    """Recursively redact likely secrets from tool args / logs."""
    secret_keys = {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "auth",
        "access_token",
        "refresh_token",
        "private_key",
        "client_secret",
        # Email drafts are private even though they are not authentication secrets.
        "recipient",
        "subject",
        "body",
        "cc",
        "bcc",
    }
    if isinstance(data, dict):
        out: dict[Any, Any] = {}
        for k, v in data.items():
            key_l = str(k).lower()
            if key_l in secret_keys or any(
                s in key_l for s in ("password", "secret", "token", "api_key")
            ):
                out[k] = "***REDACTED***"
            else:
                out[k] = redact_secrets(v)
        return out
    if isinstance(data, list):
        return [redact_secrets(x) for x in data]
    if isinstance(data, str) and len(data) > 8:
        lower = data.lower()
        if lower.startswith("sk-") or lower.startswith("ghp_") or "bearer " in lower:
            return "***REDACTED***"
    return data
