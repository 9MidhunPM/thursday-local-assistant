#!/usr/bin/env bash
set -euo pipefail
thursday_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "$thursday_root/.venv/bin/python" -m assistant.web_launch
