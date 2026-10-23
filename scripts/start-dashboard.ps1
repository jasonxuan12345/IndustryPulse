param(
    [int]$Port = 8765,
    [switch]$NoBrowser,
    # 显式指定解释器；缺省读环境变量 INDUSTRYPULSE_PYTHON（可以是 python.exe 或 conda 环境目录）
    [string]$PythonPath = $env:INDUSTRYPULSE_PYTHON,
    # 强制进入交互式路径选择（默认仅在没有可用解释器且控制台可交互时提示）
    [switch]$Prompt
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$settingsPath = Join-Path $projectRoot 'data\launcher.json'

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

function Get-SavedPython {
    if (-not (Test-Path -LiteralPath $settingsPath)) { return $null }
    try {
        $saved = (Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json).python
    } catch { return $null }
    if ($saved) { return [string]$saved }
    return $null
}

function Save-Python([string]$Exe) {
    $directory = Split-Path -Parent $settingsPath
    if (-not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }
    $json = @{ python = $Exe } | ConvertTo-Json
    # UTF-8 无 BOM，便于其他工具读取
    [System.IO.File]::WriteAllText($settingsPath, $json, (New-Object System.Text.UTF8Encoding($false)))
}

function Select-PythonInteractively {
    # 非交互控制台（计划任务 / CI，stdin 被重定向）跳过提示，保持可脚本化
    if (-not $Prompt -and ([Console]::IsInputRedirected -or -not [Environment]::UserInteractive)) {
        return $null
    }
    Write-Host @'
没有找到可用的 Python。可以直接粘贴路径，验证通过后会记住，后续启动不再询问。
可以粘贴 python.exe，也可以粘贴环境目录（如 conda：C:\Users\you\.conda\envs\myenv）。
'@
    foreach ($attempt in 1..5) {
        $entered = (Read-Host 'Python 路径（直接回车放弃）').Trim()
        if (-not $entered) { return $null }
        $resolved = Resolve-Interpreter $entered
        if ($resolved) {
            Save-Python $resolved
            Write-Host "已保存到 data/launcher.json，后续启动将自动使用。" -ForegroundColor Green
            return $resolved
        }
        Write-Host '该路径不可用：需要能运行的 3.11-3.13 python.exe 或环境目录，请重试。' -ForegroundColor Yellow
    }
    return $null
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
        (Get-SavedPython),
        (Join-Path $projectRoot '.venv\Scripts\python.exe'),
        (Get-Command python -ErrorAction SilentlyContinue).Source,
        (Get-Command py -ErrorAction SilentlyContinue).Source
    )
    foreach ($candidate in $candidates) {
        $python = Resolve-Interpreter $candidate
        if ($python) { break }
    }
    if (-not $python) { $python = Select-PythonInteractively }
}

if (-not $python) {
    Write-Host @'
仍未选择 Python。任一方式即可解决：
  1. uv sync --all-extras          在项目根目录创建 .venv（推荐）
  2. setx INDUSTRYPULSE_PYTHON "C:\path\envs\myenv"   指向已有环境或 conda 环境目录
  3. 再次运行启动脚本，按提示粘贴 Python 路径
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
