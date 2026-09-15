#!/usr/bin/env bash
# macOS/Linux 版启动脚本，和 start-ui.ps1 做同样的事。
# macOS/Linux launcher, mirrors start-ui.ps1.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export UV_CACHE_DIR="$SCRIPT_DIR/.uv-cache"
cd "$SCRIPT_DIR"
uv run python -m app.server --host 127.0.0.1 --port 8765
