param(
    [int]$Port = 8765,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$pythonPath = Join-Path (Get-Location) '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Host 'Python environment missing. Run uv sync --all-extras first.' -ForegroundColor Red
    exit 1
}
$launcherArguments = @((Join-Path $PSScriptRoot 'launch_dashboard.py'), '--port', $Port)
if ($NoBrowser) { $launcherArguments += '--no-browser' }
& $pythonPath @launcherArguments
exit $LASTEXITCODE
