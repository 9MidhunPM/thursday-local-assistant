#!/usr/bin/env bash
# Thursday model switcher — swap the local llama.cpp model on the fly.
#
#   ./model.sh                 rofi menu (numbered menu if rofi is missing)
#   ./model.sh list            all models + RUNNING / CONFIGURED markers
#   ./model.sh use <name>      switch now (runtime only — .env untouched)
#   ./model.sh apply <name>    switch now + permanent (.env + UI label + Thursday restart)
#   ./model.sh revert          switch back to the model configured in .env
#   ./model.sh test <name> [prompt]   switch + raw llama ping (reply + tok/s)
#
# <name> can be the full filename or any unique substring (e.g. "8b", "gemma").
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Load .env (for LLAMA_* / THURSDAY_* / MODEL_PATH)
if [[ -f "$PROJECT_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$PROJECT_DIR/.env"
    set +a
fi

MODELS_DIR="${MODELS_DIR:-$HOME/Models}"
LLAMA_HOST="${LLAMA_HOST:-127.0.0.1}"
LLAMA_PORT="${LLAMA_PORT:-8080}"
THURSDAY_URL="http://${THURSDAY_HOST:-127.0.0.1}:${THURSDAY_PORT:-5005}"
LLAMA_URL="http://${LLAMA_HOST}:${LLAMA_PORT}"

log() { echo "[model] $*"; }
die() {
    echo "[model] error: $*" >&2
    notify "$*" "error"
    exit 1
}

# Desktop-notification feedback (useful when launched from rofi; silent no-op
# if notify-send is unavailable).
notify() {
    command -v notify-send >/dev/null 2>&1 || return 0
    notify-send -r 2609 -t "${3:-4000}" "Thursday Model${2:+ · $2}" "$1" >/dev/null 2>&1 || true
}

# Per-model extra llama-server args, matched against the filename.
# Qwen3: disable <think> blocks at the template level.
model_args_for() {
    case "$1" in
        Qwen3-*) printf '%s' '--jinja --chat-template-kwargs {"enable_thinking":false}' ;;
        *) printf '%s' "" ;;
    esac
}

# ---------------------------------------------------------------------------
# Model discovery / status
# ---------------------------------------------------------------------------

list_models() {
    find "$MODELS_DIR" -maxdepth 1 -name '*.gguf' -printf '%f\n' | sort
}

resolve_model() {
    local q="$1" f
    local matches=()
    mapfile -t all < <(list_models)
    ((${#all[@]} > 0)) || die "no .gguf files in $MODELS_DIR"
    # Exact (with or without .gguf)
    for f in "${all[@]}"; do
        if [[ "$f" == "$q" || "${f%.gguf}" == "${q%.gguf}" ]]; then
            printf '%s' "$f"
            return 0
        fi
    done
    # Unique substring, case-insensitive
    local ql="${q%.gguf}"
    ql="${ql,,}"
    for f in "${all[@]}"; do
        [[ "${f,,}" == *"$ql"* ]] && matches+=("$f")
    done
    if ((${#matches[@]} == 1)); then
        printf '%s' "${matches[0]}"
        return 0
    fi
    ((${#matches[@]} > 1)) && die "'$q' is ambiguous: ${matches[*]}"
    die "no model matching '$q' in $MODELS_DIR"
}

running_model() {
    curl -sf -m 2 "$LLAMA_URL/v1/models" 2>/dev/null \
        | jq -r '.data[0].id // empty' 2>/dev/null \
        | xargs -r basename 2>/dev/null || true
}

configured_model() {
    local p="${MODEL_PATH:-}"
    p="${p/#\~/$HOME}"
    basename "$p"
}

llama_healthy() {
    curl -sf -m 2 "$LLAMA_URL/health" >/dev/null 2>&1
}

# ---------------------------------------------------------------------------
# Switching
# ---------------------------------------------------------------------------

stop_llama() {
    local pids
    pids=$(pgrep -f "llama-server.*--port +${LLAMA_PORT}([^0-9]|$)" 2>/dev/null || true)
    if [[ -n "$pids" ]]; then
        log "Stopping current llama.cpp server..."
        echo "$pids" | xargs -r kill 2>/dev/null || true
        for _ in $(seq 1 10); do
            echo "$pids" | xargs -r kill -0 2>/dev/null || break
            sleep 1
        done
        echo "$pids" | xargs -r kill -9 2>/dev/null || true
    fi
    # Wait for the port (and VRAM) to actually free up.
    for _ in $(seq 1 20); do
        llama_healthy || return 0
        sleep 1
    done
    log "Warning: old llama server may still be shutting down." >&2
}

switch_to() {
    local file="$1"
    local args
    args=$(model_args_for "$file")
    # Serialize llama swaps across all starter scripts (app launcher,
    # quickshell button, model.sh) so two servers never race for the port.
    exec 8> /tmp/thursday-llama.lock
    flock -w 150 8 || die "another llama switch is in progress"
    stop_llama
    log "Starting $file ${args:+($args)}..."
    notify "Switching to $file …" "switching" 15000
    MODEL_PATH="$MODELS_DIR/$file" MODEL_ARGS="$args" \
        setsid "$PROJECT_DIR/run_vulkan_server.sh" \
        > /tmp/thursday-llama.log 2>&1 < /dev/null 8>&- &
    local tries=120
    while (( tries > 0 )); do
        llama_healthy && break
        sleep 1
        tries=$((tries - 1))
    done
    llama_healthy || die "llama did not come up — see /tmp/thursday-llama.log"
    # Best-effort: wait for Thursday to mark the model ready (if it's running).
    for _ in $(seq 1 30); do
        curl -sf -m 2 "$THURSDAY_URL/health" 2>/dev/null | grep -q '"model_ready": true' && break
        sleep 1
    done
    log "Now serving: $(running_model)"
    notify "Now serving: $(running_model)" "ready"
}

cmd_use() {
    local file
    file=$(resolve_model "$1")
    if [[ "$(running_model)" == "$file" ]]; then
        log "$file is already running."
        return 0
    fi
    switch_to "$file"
    log "Runtime switch only — .env unchanged. './model.sh apply $file' to keep it, './model.sh revert' to go back."
}

# Human label for the UIs: strip extension, quant suffix and local fix marker.
# Qwen2.5-7B-Instruct-Q4_K_M_FIXED.gguf -> Qwen2.5-7B-Instruct
label_for() {
    local base="${1%.gguf}"
    base="${base%_FIXED}"
    base="${base%-Q4_K_M}"
    printf '%s' "$base"
}

# Restart Thursday (only if running) so /health — and every UI label built on
# it — picks up the new model name from config.json.
restart_thursday() {
    curl -sf -m 2 "$THURSDAY_URL/health" >/dev/null 2>&1 || return 0
    log "Restarting Thursday to refresh the UI label..."
    curl -sf -m 3 -X POST "$THURSDAY_URL/api/shutdown" >/dev/null 2>&1 || true
    for _ in $(seq 1 15); do
        curl -sf -m 2 "$THURSDAY_URL/health" >/dev/null 2>&1 || break
        sleep 1
    done
    setsid "$PROJECT_DIR/run.sh" --web --no-browser > /tmp/thursday-server.log 2>&1 < /dev/null &
    for _ in $(seq 1 60); do
        curl -sf -m 2 "$THURSDAY_URL/health" 2>/dev/null | grep -q '"model_ready": true' && break
        sleep 1
    done
}

cmd_apply() {
    local file label
    file=$(resolve_model "$1")
    if [[ "$(running_model)" != "$file" ]]; then
        switch_to "$file"
    else
        log "$file is already running."
    fi
    if grep -q '^MODEL_PATH=' "$PROJECT_DIR/.env"; then
        sed -i "s|^MODEL_PATH=.*|MODEL_PATH=~/Models/$file|" "$PROJECT_DIR/.env"
    else
        echo "MODEL_PATH=~/Models/$file" >> "$PROJECT_DIR/.env"
    fi
    # Make the UIs show the right model name (config.json feeds /health).
    label=$(label_for "$file")
    local cfg="$PROJECT_DIR/assistant/config/config.json"
    if jq -e '.model.model' "$cfg" >/dev/null 2>&1; then
        local tmp
        tmp=$(mktemp)
        jq --arg m "$label" '.model.model = $m' "$cfg" > "$tmp" && mv "$tmp" "$cfg"
        log "UI label set to '$label' (config.json)."
    fi
    restart_thursday
    log "Applied permanently: MODEL_PATH=~/Models/$file"
}

cmd_revert() {
    local want
    want=$(configured_model)
    [[ -n "$want" && "$want" != "." ]] || die "no MODEL_PATH configured in .env"
    if [[ "$(running_model)" == "$want" ]]; then
        log "Already running the configured model ($want)."
        return 0
    fi
    log "Reverting to configured model: $want"
    switch_to "$want"
}

cmd_test() {
    local name="$1" prompt="${2:-}"
    local file
    file=$(resolve_model "$name")
    [[ -n "$prompt" ]] || prompt="Say hi and tell me which model you are, in one short sentence."
    switch_to "$file"
    log "Pinging $file ..."
    local body start end reply tok
    start=$(date +%s.%N)
    body=$(jq -n --arg p "$prompt" '{
        model: "local",
        messages: [{role: "user", content: $p}],
        max_tokens: 128, temperature: 0.7, stream: false
    }')
    reply=$(curl -sf -m 120 "$LLAMA_URL/v1/chat/completions" \
        -H "Content-Type: application/json" -d "$body") \
        || die "ping failed — see /tmp/thursday-llama.log"
    end=$(date +%s.%N)
    echo "-----"
    echo "$reply" | jq -r '.choices[0].message.content // "(empty reply)"'
    echo "-----"
    tok=$(echo "$reply" | jq -r '.usage.completion_tokens // 0')
    if [[ "$tok" =~ ^[0-9]+$ ]] && (( tok > 0 )); then
        awk -v t="$tok" -v s="$start" -v e="$end" \
            'BEGIN{printf "%d tokens in %.1fs (%.1f tok/s)\n", t, e-s, t/(e-s)}'
    fi
}

cmd_list() {
    local running configured f size mark_r mark_c
    running=$(running_model)
    configured=$(configured_model)
    printf '%-42s %7s  %s\n' "MODEL" "SIZE" "STATUS"
    while IFS= read -r f; do
        size=$(du -h "$MODELS_DIR/$f" | cut -f1)
        mark_r=""; mark_c=""
        [[ "$f" == "$running" ]] && mark_r="RUNNING"
        [[ "$f" == "$configured" ]] && mark_c="CONFIGURED"
        printf '%-42s %7s  %s\n' "$f" "$size" "$mark_r $mark_c"
    done < <(list_models)
    [[ -n "$running" ]] || echo "(no llama server currently running on :$LLAMA_PORT)"
    # Cosmetic note: Thursday's label is captured at Thursday-server start.
    local label
    label=$(curl -sf -m 2 "$THURSDAY_URL/health" 2>/dev/null | jq -r '.model // empty' 2>/dev/null || true)
    if [[ -n "$label" && -n "$running" && "$label" != "${running%.gguf}"* && "${running}" != *"$label"* ]]; then
        echo "(note: Thursday's /health label says '$label' — cosmetic; inference uses the RUNNING model. Label refreshes when Thursday restarts.)"
    fi
}

cmd_menu() {
    local running configured f line lines=() choice
    running=$(running_model)
    configured=$(configured_model)
    while IFS= read -r f; do
        line="$f"
        [[ "$f" == "$running" ]] && line+="  ● running"
        [[ "$f" == "$configured" ]] && line+="  ✓ configured"
        lines+=("$line")
    done < <(list_models)

    if command -v rofi >/dev/null 2>&1; then
        choice=$(printf '%s\n' "${lines[@]}" | rofi -dmenu -i -p "Thursday model" || true)
    else
        echo "Select a model:"
        select choice in "${lines[@]}"; do break; done
    fi
    [[ -n "${choice:-}" ]] || { log "cancelled."; exit 0; }
    choice="${choice%%  ●*}"; choice="${choice%%  ✓*}"
    cmd_use "$choice"
}

# ---------------------------------------------------------------------------

case "${1:-menu}" in
    list|ls)        cmd_list ;;
    use|switch)     [[ $# -ge 2 ]] || die "usage: ./model.sh use <name>"; cmd_use "$2" ;;
    apply)          [[ $# -ge 2 ]] || die "usage: ./model.sh apply <name>"; cmd_apply "$2" ;;
    revert)         cmd_revert ;;
    test)           [[ $# -ge 2 ]] || die "usage: ./model.sh test <name> [prompt]"; cmd_test "$2" "${3:-}" ;;
    menu|"")        cmd_menu ;;
    -h|--help|help) sed -n '2,12p' "$0" ;;
    *)              die "unknown command '$1' (try: list, use, apply, revert, test)" ;;
esac
