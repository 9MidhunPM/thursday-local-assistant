#!/usr/bin/env bash
set -euo pipefail
session_vars=()
for session_var in DISPLAY WAYLAND_DISPLAY XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS HYPRLAND_INSTANCE_SIGNATURE XDG_CURRENT_DESKTOP; do
    if [[ -v "$session_var" ]]; then
        session_vars+=("$session_var")
    fi
done
if ((${#session_vars[@]})); then
    timeout 10s systemctl --user import-environment "${session_vars[@]}"
fi
timeout 40s systemctl --user restart thursday.service
timeout 30s systemctl --user restart thursday-hotkeys.service
