[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CheckArguments
)

$ErrorActionPreference = "Stop"
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "The locked environment is not installed. Run setup.cmd first."
}
& $python (Join-Path $PSScriptRoot "scripts\check_environment.py") @CheckArguments
exit $LASTEXITCODE
