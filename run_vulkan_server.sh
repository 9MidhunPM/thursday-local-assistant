#!/usr/bin/env bash
# Start llama.cpp server with Vulkan backend.
# Configuration is read from .env (see .env.example).

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Load .env if present (export all variables defined in it).
if [[ -f "$PROJECT_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$PROJECT_DIR/.env"
    set +a
fi

# Expand ~ in configured paths and fall back to sensible defaults.
LLAMA_SERVER="${LLAMA_SERVER_BIN:-$HOME/llama-vulkan/build-vulkan/bin/llama-server}"
LLAMA_SERVER="${LLAMA_SERVER/#\~/$HOME}"
MODEL="${MODEL_PATH:-$HOME/.models/model.gguf}"
MODEL="${MODEL/#\~/$HOME}"

LLAMA_HOST="${LLAMA_HOST:-127.0.0.1}"
LLAMA_PORT="${LLAMA_PORT:-8080}"
LLAMA_NGL="${LLAMA_NGL:-70}"
LLAMA_CTX="${LLAMA_CTX:-8192}"
LLAMA_THREADS="${LLAMA_THREADS:-6}"

if [[ ! -f "$LLAMA_SERVER" ]]; then
    echo "Error: llama-server not found at $LLAMA_SERVER"
    echo "Build llama.cpp (with Vulkan support) and set LLAMA_SERVER_BIN in .env"
    exit 1
fi

if [[ ! -f "$MODEL" ]]; then
    echo "Error: Model not found at $MODEL"
    echo "Download a GGUF model and set MODEL_PATH in .env"
    exit 1
fi

echo "Starting llama.cpp server with Vulkan backend on $LLAMA_HOST:$LLAMA_PORT..."
exec "$LLAMA_SERVER" \
    -m "$MODEL" \
    -ngl "$LLAMA_NGL" \
    -c "$LLAMA_CTX" \
    -t "$LLAMA_THREADS" \
    --host "$LLAMA_HOST" \
    --port "$LLAMA_PORT"
