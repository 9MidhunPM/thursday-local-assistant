#!/usr/bin/env bash
# Run Thursday AI Assistant using the project virtual environment
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python3"
if [[ ! -x "$VENV_PYTHON" ]]; then
    echo "Thursday's virtual environment is missing. Run the desktop installer or create .venv and install .[hypruse,hotkeys,voice,desktop]." >&2
    exit 1
fi

# Load .env if present (export for child processes)
if [[ -f "$PROJECT_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$PROJECT_DIR/.env"
    set +a
fi

export PYTHONPATH="$PROJECT_DIR:${PYTHONPATH:-}"
cd "$PROJECT_DIR"
if ! "$VENV_PYTHON" -c "import assistant, dotenv, httpx, jsonschema" 2>/dev/null; then
    echo "Thursday's dependencies are incomplete. Run the desktop installer or .venv/bin/python -m pip install -e '.[hypruse,hotkeys,voice,desktop]' from this checkout." >&2
    exit 1
fi
if [[ "${THURSDAY_HYPRUSE:-1}" != "0" ]] && ! "$VENV_PYTHON" -c "import mcp, hypruse" 2>/dev/null; then
    echo "Thursday desktop control is unavailable: install .[hypruse] in this checkout's .venv and restart. Starting with the remaining capabilities." >&2
fi
exec "$VENV_PYTHON" -m assistant.main "$@"
