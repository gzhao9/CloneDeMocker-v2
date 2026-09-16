#!/usr/bin/env bash
# macOS/Linux 版启动脚本，和 start-ui.ps1 做同样的事。
# macOS/Linux launcher, mirrors start-ui.ps1.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export UV_CACHE_DIR="$SCRIPT_DIR/.uv-cache"
if [ -d "$HOME/.local/share/jdk-17" ]; then
    export JAVA_HOME="${JAVA_HOME:-$HOME/.local/share/jdk-17}"
    export PATH="$JAVA_HOME/bin:$PATH"
fi
if [ -d "$HOME/.local/share/maven" ]; then
    export MAVEN_HOME="${MAVEN_HOME:-$HOME/.local/share/maven}"
    export PATH="$MAVEN_HOME/bin:$PATH"
fi
cd "$SCRIPT_DIR"
uv run python -m app.server --host 127.0.0.1 --port 8765
