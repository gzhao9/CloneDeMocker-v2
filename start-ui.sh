#!/usr/bin/env bash
# macOS/Linux 版启动脚本，和 start-ui.ps1 做同样的事。
# macOS/Linux launcher, mirrors start-ui.ps1.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export UV_CACHE_DIR="$SCRIPT_DIR/.uv-cache"
export UV_PYTHON_INSTALL_DIR="$SCRIPT_DIR/.uv-python"
# Read a local .env without overriding values explicitly supplied by the shell.
for ENV_FILE in "$SCRIPT_DIR/.env" "$(dirname "$SCRIPT_DIR")/.env"; do
    [ -f "$ENV_FILE" ] || continue
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in ''|'#'*) continue ;; esac
        key=${line%%=*}
        value=${line#*=}
        case "$key" in *[!A-Za-z0-9_]*|'') continue ;; esac
        if [ -z "${!key+x}" ]; then
            export "$key=$value"
        fi
    done < "$ENV_FILE"
done
if [ -d "$HOME/.local/share/jdk-17" ]; then
    export JAVA_HOME="${JAVA_HOME:-$HOME/.local/share/jdk-17}"
    export PATH="$JAVA_HOME/bin:$PATH"
fi
if [ -d "$HOME/.local/share/maven" ]; then
    export MAVEN_HOME="${MAVEN_HOME:-$HOME/.local/share/maven}"
    export PATH="$MAVEN_HOME/bin:$PATH"
fi
cd "$SCRIPT_DIR"
if ! command -v uv >/dev/null 2>&1; then
    echo "uv was not found. Run: bash setup.sh" >&2
    exit 1
fi
uv sync --locked --python 3.11
uv run --locked --python 3.11 python -m studio.server --host 127.0.0.1 --port 8765
