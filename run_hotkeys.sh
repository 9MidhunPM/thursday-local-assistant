#!/usr/bin/env bash
# Run the Thursday global hotkey daemon (Super+Alt push-to-talk).
# Autostarted from hyprland.conf via exec-once; safe to run multiple times
# (a flock singleton guard keeps only one instance).
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python3"

# Singleton: exit silently if another instance is already running.
exec 9> /tmp/thursday-hotkeys.lock
flock -n 9 || exit 0

# Load .env if present (THURSDAY_HOST / THURSDAY_PORT / THURSDAY_API_TOKEN)
if [[ -f "$PROJECT_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$PROJECT_DIR/.env"
    set +a
fi

export PYTHONPATH="$PROJECT_DIR:${PYTHONPATH:-}"
exec "$VENV_PYTHON" -m assistant.hotkeys "$@"
