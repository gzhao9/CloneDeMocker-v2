$ErrorActionPreference = "Stop"
$env:UV_CACHE_DIR = Join-Path $PSScriptRoot ".uv-cache"
$env:UV_PYTHON_INSTALL_DIR = Join-Path $PSScriptRoot ".uv-python"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv was not found. Run setup.cmd first."
}
# A developer may keep the local key in this project or in the transfer
# directory that contains it. Existing process environment values win.
foreach ($candidate in @((Join-Path $PSScriptRoot ".env"), (Join-Path (Split-Path $PSScriptRoot -Parent) ".env"))) {
    if (-not (Test-Path -LiteralPath $candidate)) { continue }
    Get-Content -LiteralPath $candidate -Encoding UTF8 | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
            $name = $matches[1]
            $value = $matches[2].Trim('"').Trim("'")
            if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name, "Process"))) {
                Set-Item -Path ("Env:" + $name) -Value $value
            }
        }
    }
}
Set-Location $PSScriptRoot
& uv sync --locked --python 3.11
if ($LASTEXITCODE -ne 0) { throw "uv sync failed with exit code $LASTEXITCODE." }
& uv run --locked --python 3.11 python -m studio.server --host 127.0.0.1 --port 8765
