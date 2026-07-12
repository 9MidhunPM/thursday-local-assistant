#!/usr/bin/env bash
# Run Thursday AI Assistant using the project virtual environment
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python3"
VENV_PIP="$PROJECT_DIR/.venv/bin/pip"

if [[ ! -f "$VENV_PYTHON" ]]; then
    echo "Creating virtual environment..."
    python3 -m venv "$PROJECT_DIR/.venv"
    "$VENV_PIP" install --upgrade pip
    echo "Installing Thursday and dependencies..."
    "$VENV_PIP" install -e "$PROJECT_DIR"
    # Optional voice / desktop extras (best-effort)
    "$VENV_PIP" install -e "$PROJECT_DIR[voice,desktop]" 2>/dev/null || true
fi

# Ensure package is installed (handles upgrades / new clones)
if ! "$VENV_PYTHON" -c "import assistant" 2>/dev/null; then
    echo "Installing Thursday package..."
    "$VENV_PIP" install -e "$PROJECT_DIR"
fi

# Load .env if present (export for child processes)
if [[ -f "$PROJECT_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$PROJECT_DIR/.env"
    set +a
fi

export PYTHONPATH="$PROJECT_DIR:${PYTHONPATH:-}"
exec "$VENV_PYTHON" -m assistant.main "$@"
