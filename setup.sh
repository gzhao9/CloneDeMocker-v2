#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export UV_CACHE_DIR="$SCRIPT_DIR/.uv-cache"
export UV_PYTHON_INSTALL_DIR="$SCRIPT_DIR/.uv-python"
cd "$SCRIPT_DIR"

SYNC_ARGS=(sync --locked --python 3.11)
if [ "${1:-}" = "--reports" ]; then
    SYNC_ARGS+=(--extra reports)
elif [ "$#" -gt 0 ]; then
    echo "Usage: bash setup.sh [--reports]" >&2
    exit 2
fi

if ! command -v uv >/dev/null 2>&1; then
    cat >&2 <<'EOF'
uv is required but was not found.
Install it from https://docs.astral.sh/uv/getting-started/installation/
Then open a new terminal and run: bash setup.sh
EOF
    exit 1
fi

echo "[1/3] Syncing the locked Python 3.11 environment..."
uv "${SYNC_ARGS[@]}"

echo "[2/3] Verifying Python imports..."
uv run --locked --python 3.11 python -c "import openai, tree_sitter, tree_sitter_java; print('Python dependencies: OK')"

echo "[3/3] Checking Java build tools..."
if command -v java >/dev/null 2>&1; then
    java -version
else
    echo "Warning: Java was not found. The UI can open, but Java project detection requires JDK 17." >&2
fi
if command -v mvn >/dev/null 2>&1; then
    mvn -version
else
    echo "Warning: Maven was not found. Maven projects require Maven 3.9+ or their Maven Wrapper." >&2
fi

echo "Environment setup completed. Start the UI with: ./start-ui.sh"
