#!/usr/bin/env bash
# Use the single supervised owner for push-to-talk.
set -euo pipefail
if ! systemctl --user cat thursday-hotkeys.service >/dev/null 2>&1; then
    echo "Thursday's voice shortcut is not installed. Install the native desktop integration first." >&2
    exit 78
fi
exec timeout 30s systemctl --user start thursday-hotkeys.service
