#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$SCRIPT_DIR/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
    echo "The locked environment is not installed. Run: bash setup.sh" >&2
    exit 1
fi
exec "$PYTHON" "$SCRIPT_DIR/scripts/check_environment.py" "$@"
