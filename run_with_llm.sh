#!/usr/bin/env bash
# Thursday desktop app launcher (invoked by thursday.desktop).
#
# Behavior:
#   1. Uses the configured cloud model; llama.cpp starts only for an explicit
#      legacy local-provider configuration.
#   2. Starts the Thursday server (no auto-browser) if not already running.
#   3. Opens exactly one app window at http://127.0.0.1:5005
#      (focuses the existing window instead of opening a duplicate).
#   4. Watches the window: when the last Thursday window is closed, the
#      Thursday server is shut down. A legacy local llama.cpp process started
#      here is also stopped — nothing keeps running in the background.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Load .env if present
if [[ -f "$PROJECT_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$PROJECT_DIR/.env"
    set +a
fi

LLM_PROVIDER="${LLM_PROVIDER:-local}"
LLAMA_HOST="${LLAMA_HOST:-127.0.0.1}"
LLAMA_PORT="${LLAMA_PORT:-8080}"
THURSDAY_URL="http://${THURSDAY_HOST:-127.0.0.1}:${THURSDAY_PORT:-5005}"
TITLE_RE="Thursday - AI Assistant"

LLAMA_PID=""    # set only if WE started llama.cpp
SERVER_PID=""   # set only if WE started the Thursday server
APP_OPEN=0      # set once a Thursday window exists (user actually used the app)

log() { echo "[thursday-app] $*"; }

if [[ "$LLM_PROVIDER" == "openai" && -z "${OPENAI_API_KEY:-${LLM_API_KEY:-}}" ]]; then
    log "OpenAI is selected but no API key is configured. Set OPENAI_API_KEY in $PROJECT_DIR/.env."
    exit 1
fi

# ---------------------------------------------------------------------------
# Cleanup: runs when the window closes or this script is terminated.
# ---------------------------------------------------------------------------
# Stop every llama.cpp server bound to our port — the one we started, an
# adopted pre-existing one, or an orphan — so nothing outlives the window.
stop_llama() {
    local pids
    pids=$(
        {
            if [[ -n "$LLAMA_PID" ]] && kill -0 "$LLAMA_PID" 2>/dev/null; then
                echo "$LLAMA_PID"
            fi
            pgrep -f "llama-server.*--port +${LLAMA_PORT}([^0-9]|$)" 2>/dev/null
        } | sort -u
    )
    [[ -z "$pids" ]] && return 0
    log "Stopping llama.cpp server..."
    echo "$pids" | xargs -r kill 2>/dev/null || true
    local i
    for i in 1 2 3 4 5; do
        if ! echo "$pids" | xargs -r kill -0 2>/dev/null; then
            return 0
        fi
        sleep 1
    done
    echo "$pids" | xargs -r kill -9 2>/dev/null || true
}

cleanup() {
    trap - EXIT INT TERM
    # Shut down the Thursday server if the app was used, or if we started it
    # (avoids leaking our own child on early-exit errors).
    if [[ "$APP_OPEN" == "1" || -n "$SERVER_PID" ]]; then
        curl -sf -m 3 -X POST "$THURSDAY_URL/api/shutdown" >/dev/null 2>&1 || true
    fi
    if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
        for _ in 1 2 3 4 5; do
            kill -0 "$SERVER_PID" 2>/dev/null || break
            sleep 1
        done
        kill -- -"$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
        log "Thursday server stopped."
    fi
    if [[ "$APP_OPEN" == "1" || -n "$LLAMA_PID" ]]; then
        stop_llama
    fi
}
trap cleanup EXIT INT TERM

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
port_listening() {
    (ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null) | grep -q ":$1\b"
}

thursday_healthy() {
    curl -sf -m 2 "$THURSDAY_URL/health" >/dev/null 2>&1
}

thursday_window_count() {
    hyprctl clients -j 2>/dev/null | jq --arg re "$TITLE_RE" '[.[] | select(.title | test($re))] | length'
}

focus_thursday_window() {
    local addr
    addr=$(hyprctl clients -j 2>/dev/null | jq -r --arg re "$TITLE_RE" \
        '[.[] | select(.title | test($re))][0].address // empty')
    [[ -z "$addr" ]] && return 1
    # Legacy dispatcher (stock Hyprland); fall back to Lua dispatch (0.55+).
    if ! hyprctl dispatch focuswindow "address:$addr" 2>&1 | grep -qi error; then
        return 0
    fi
    hyprctl dispatch "hl.dsp.focus({ window = \"address:$addr\" })" >/dev/null 2>&1
}

open_app_window() {
    local brave app_dir
    brave=$(command -v brave || command -v brave-browser || true)
    if [[ -n "$brave" ]]; then
        app_dir="$HOME/.config/brave-thursday-app"
        mkdir -p "$app_dir"
        setsid "$brave" --app="$THURSDAY_URL" --user-data-dir="$app_dir" >/dev/null 2>&1 < /dev/null &
    else
        setsid xdg-open "$THURSDAY_URL" >/dev/null 2>&1 < /dev/null &
    fi
}

wait_for() { # wait_for <description> <seconds> <check-command...>
    local desc="$1" tries="$2"; shift 2
    log "Waiting for $desc ..."
    while (( tries > 0 )); do
        if "$@" >/dev/null 2>&1; then
            log "$desc is up."
            return 0
        fi
        sleep 1
        tries=$((tries - 1))
    done
    log "Warning: $desc did not come up in time." >&2
    return 1
}

# ---------------------------------------------------------------------------
# 1. Local LLM server (only for the local provider)
# ---------------------------------------------------------------------------
if [[ "$LLM_PROVIDER" == "local" ]] && ! port_listening "$LLAMA_PORT"; then
    LLAMA_SERVER="${LLAMA_SERVER_BIN:-$HOME/llama.cpp/build-vulkan/bin/llama-server}"
    LLAMA_SERVER="${LLAMA_SERVER/#\~/$HOME}"
    MODEL="${MODEL_PATH:-$HOME/Models/model.gguf}"
    MODEL="${MODEL/#\~/$HOME}"

    if [[ ! -f "$LLAMA_SERVER" ]]; then
        log "Warning: llama-server not found at $LLAMA_SERVER — LLM calls will fail." >&2
    elif [[ ! -f "$MODEL" ]]; then
        log "Warning: model not found at $MODEL — LLM calls will fail." >&2
    else
        log "Starting llama.cpp server..."
        # Serialize with model.sh / thursday-server.sh llama starts.
        exec 8> /tmp/thursday-llama.lock
        flock -w 150 8
        if ! port_listening "$LLAMA_PORT"; then
            setsid bash -c 'exec "$0" "$@"' "$PROJECT_DIR/run_vulkan_server.sh" \
                > /tmp/thursday-llama.log 2>&1 < /dev/null 8>&- &
            LLAMA_PID=$!
            wait_for "llama.cpp server" 90 port_listening "$LLAMA_PORT" || true
        fi
    fi
fi

# ---------------------------------------------------------------------------
# 2. Thursday server (without auto-opening a browser)
# ---------------------------------------------------------------------------
if thursday_healthy; then
    log "Thursday server already running."
else
    log "Starting Thursday server..."
    setsid "$PROJECT_DIR/run.sh" --web --no-browser > /tmp/thursday-server.log 2>&1 < /dev/null &
    SERVER_PID=$!
    wait_for "Thursday server" 60 thursday_healthy || true
fi

# ---------------------------------------------------------------------------
# 3. Open exactly one window on :5005 (or focus the existing one)
# ---------------------------------------------------------------------------
if ! command -v hyprctl >/dev/null 2>&1 || ! command -v jq >/dev/null 2>&1; then
    # No window tracking available: open the UI and just keep the server
    # alive in the foreground until interrupted.
    log "hyprctl/jq not found — cannot watch windows. Close with Ctrl+C."
    open_app_window
    APP_OPEN=1
    if [[ -n "$SERVER_PID" ]]; then
        wait "$SERVER_PID" || true
    else
        while true; do sleep 3600; done
    fi
    exit 0
fi

if (( $(thursday_window_count) > 0 )); then
    log "Thursday window already open — focusing it."
    focus_thursday_window || true
else
    open_app_window
fi
APP_OPEN=1

# Give the window a moment to appear (fresh profile/server can be slow).
tries=45
while (( tries > 0 )) && (( $(thursday_window_count) == 0 )); do
    sleep 1
    tries=$((tries - 1))
done
if (( $(thursday_window_count) == 0 )); then
    log "Error: Thursday window never appeared." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# 4. Watch: last Thursday window closed -> shut everything down
# ---------------------------------------------------------------------------
log "Thursday app is open. Closing its window will stop the server."
while (( $(thursday_window_count) > 0 )); do
    sleep 2
done
log "Thursday window closed — shutting down."
exit 0
