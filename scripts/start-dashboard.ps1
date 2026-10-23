param(
    [int]$Port = 8765,
    [switch]$NoBrowser,
    # 显式指定解释器；缺省读环境变量 INDUSTRYPULSE_PYTHON（可以是 python.exe 或 conda 环境目录）
    [string]$PythonPath = $env:INDUSTRYPULSE_PYTHON
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

# 项目声明 >=3.12,<3.14；3.11 仅尽力运行（低于声明下限时提示但不阻止）
$supportedVersions = @('3.12', '3.13')
$toleratedVersions = @('3.11')

function Get-InterpreterVersion([string]$Exe) {
    # Windows PowerShell 5.1 在 Stop 偏好下会把 stderr 包装成终止性异常，探测需临时降级
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $Exe -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        return ($output | Select-Object -First 1)
    } finally {
        $ErrorActionPreference = $previous
    }
}

# 接受解释器路径或环境目录（conda 环境根目录），返回可用的 python.exe 绝对路径
function Resolve-Interpreter([string]$Candidate) {
    if (-not $Candidate) { return $null }
    $path = $Candidate
    if (Test-Path -LiteralPath $Candidate -PathType Container) {
        foreach ($inner in @('python.exe', 'Scripts\python.exe', 'bin\python.exe')) {
            $innerPath = Join-Path $Candidate $inner
            if (Test-Path -LiteralPath $innerPath) { $path = $innerPath; break }
        }
    }
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    $version = Get-InterpreterVersion $path
    if (-not $version) { return $null }
    if (($supportedVersions + $toleratedVersions) -notcontains $version) { return $null }
    return (Resolve-Path -LiteralPath $path).Path
}

$python = $null
if ($PythonPath) {
    $python = Resolve-Interpreter $PythonPath
    if (-not $python) {
        Write-Host "INDUSTRYPULSE_PYTHON / -PythonPath 已设置但不可用（不是 3.11-3.13 的 python.exe 或环境目录）: $PythonPath" -ForegroundColor Red
        exit 1
    }
} else {
    $candidates = @(
        (Join-Path $projectRoot '.venv\Scripts\python.exe'),
        (Get-Command python -ErrorAction SilentlyContinue).Source,
        (Get-Command py -ErrorAction SilentlyContinue).Source
    )
    foreach ($candidate in $candidates) {
        $python = Resolve-Interpreter $candidate
        if ($python) { break }
    }
}

if (-not $python) {
    Write-Host @'
未找到可用的 Python。任一方式即可解决：
  1. uv sync --all-extras          在项目根目录创建 .venv（推荐）
  2. setx INDUSTRYPULSE_PYTHON "C:\path\envs\myenv"   指向已有环境或 conda 环境目录
  3. ./scripts/start-dashboard.ps1 -PythonPath "C:\path\python.exe"
'@ -ForegroundColor Red
    exit 1
}

$chosen = Get-InterpreterVersion $python
if ($toleratedVersions -contains $chosen) {
    Write-Host "警告: Python $chosen 低于项目声明区间 (>=3.12,<3.14)，继续尝试运行。" -ForegroundColor Yellow
}
Write-Host "Using Python: $python"

$launcherArguments = @((Join-Path $PSScriptRoot 'launch_dashboard.py'), '--port', $Port)
if ($NoBrowser) { $launcherArguments += '--no-browser' }
& $python @launcherArguments
exit $LASTEXITCODE
