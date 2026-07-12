from __future__ import annotations

import hmac
import os
import secrets
from functools import lru_cache
from ipaddress import ip_address
from typing import Any


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
        raise RuntimeError(
            f"Refusing to bind Thursday to {host!r}. "
            "Set THURSDAY_ALLOW_REMOTE=1 only if you understand the risk "
            "(tools can control this machine). Prefer 127.0.0.1 and a tunnel."
        )


@lru_cache(maxsize=1)
def api_token() -> str | None:
    """Optional shared secret for mutating HTTP APIs.

    If THURSDAY_API_TOKEN is set, clients must send:
      Authorization: Bearer <token>
      or header X-Thursday-Token: <token>
      or query ?token=
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
    if auth.lower().startswith("bearer "):
        provided = auth[7:].strip()
        if hmac.compare_digest(provided, expected):
            return True
    header_token = headers.get("X-Thursday-Token") or headers.get("x-thursday-token")
    if header_token and hmac.compare_digest(header_token.strip(), expected):
        return True
    if query_token and hmac.compare_digest(query_token.strip(), expected):
        return True
    return False


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
