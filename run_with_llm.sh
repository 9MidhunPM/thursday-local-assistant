#!/usr/bin/env bash
# Open the installed native companion. Services own the backend lifecycle.
set -euo pipefail
if ! systemctl --user cat thursday.service >/dev/null 2>&1; then
    echo "Thursday's desktop integration is not installed. Install it with .venv/bin/python -m assistant.desktop_install install." >&2
    exit 78
fi
timeout 40s systemctl --user start thursday.service
exec timeout 5s qs ipc call thursday toggle
