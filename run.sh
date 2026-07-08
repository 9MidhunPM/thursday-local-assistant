#!/usr/bin/env bash
# Run Thursday AI Assistant using the project virtual environment
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python3"

if [[ ! -f "$VENV_PYTHON" ]]; then
    echo "Creating virtual environment..."
    python3 -m venv "$PROJECT_DIR/.venv"
    "$VENV_PYTHON" -m pip install --upgrade pip
    "$VENV_PYTHON" -m pip install pyaudio speechrecognition
fi

export PYTHONPATH="$PROJECT_DIR:${PYTHONPATH:-}"
exec "$VENV_PYTHON" -m assistant.main "$@"