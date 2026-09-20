$ErrorActionPreference = "Stop"
$env:UV_CACHE_DIR = Join-Path $PSScriptRoot ".uv-cache"
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
uv run python -m studio.server --host 127.0.0.1 --port 8765
