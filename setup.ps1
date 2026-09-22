[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$env:UV_CACHE_DIR = Join-Path $projectRoot ".uv-cache"
$env:UV_PYTHON_INSTALL_DIR = Join-Path $projectRoot ".uv-python"
Set-Location $projectRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw @"
uv is required but was not found.
Install it with: winget install --id=astral-sh.uv -e
Then open a new terminal and run setup.cmd again.
"@
}

Write-Host "[1/3] Syncing the locked Python 3.11 environment..."
& uv sync --locked --python 3.11
if ($LASTEXITCODE -ne 0) { throw "uv sync failed with exit code $LASTEXITCODE." }

Write-Host "[2/3] Verifying Python imports..."
& uv run --locked --python 3.11 python -c "import openai, tree_sitter, tree_sitter_java; print('Python dependencies: OK')"
if ($LASTEXITCODE -ne 0) { throw "Python dependency verification failed." }

Write-Host "[3/3] Checking Java build tools..."
if (Get-Command java -ErrorAction SilentlyContinue) {
    & java -version
} else {
    Write-Warning "Java was not found. The UI can open, but Java project detection requires JDK 17."
}
if (Get-Command mvn -ErrorAction SilentlyContinue) {
    & mvn -version
} else {
    Write-Warning "Maven was not found. Maven projects require Maven 3.9+ or their Maven Wrapper."
}

Write-Host "Environment setup completed. Start the UI with: .\start-ui.cmd"
