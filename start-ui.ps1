$ErrorActionPreference = "Stop"
$env:UV_CACHE_DIR = Join-Path $PSScriptRoot ".uv-cache"
Set-Location $PSScriptRoot
uv run python -m app.server --host 127.0.0.1 --port 8765
