# 将项目文件夹重命名为新名字，并重建 .venv（venv 内含绝对路径，必须重建）
# 用法：关闭所有占用项目目录的程序（ZCode / 终端 / 资源管理器窗口）后，
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\relocate.ps1 -NewName LiveDanmaku
[CmdletBinding()]
param(
    [string]$NewName = "LiveDanmaku"
)

$ErrorActionPreference = "Stop"

# scripts/ 的上一级即项目根（脚本自身已在内存中，重命名后仍可继续执行）
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Parent = Split-Path -Parent $ProjectRoot
$CurrentName = Split-Path -Leaf $ProjectRoot

if ($CurrentName -eq $NewName) {
    Write-Host "项目目录已经叫 $NewName，无需迁移。"
    exit 0
}

# 关键：把当前 PowerShell 的工作目录移出项目文件夹，否则自身占用导致重命名失败
Set-Location $Parent

Write-Host "重命名: $CurrentName -> $NewName"
try {
    Rename-Item -LiteralPath $ProjectRoot -NewName $NewName -ErrorAction Stop
}
catch {
    Write-Host ""
    Write-Host "重命名失败：目录被占用。" -ForegroundColor Red
    Write-Host "请关闭以下占用后重试：ZCode 会话、打开此目录的终端/编辑器/资源管理器窗口。"
    Write-Host $_.Exception.Message
    exit 1
}

$NewRoot = Join-Path $Parent $NewName
Set-Location $NewRoot

# ---- 重建 .venv（旧 venv 的脚本内嵌旧绝对路径，已不可用） ----
Write-Host "重建 .venv ..."
$venv = Join-Path $NewRoot ".venv"
if (Test-Path $venv) {
    Remove-Item -LiteralPath $venv -Recurse -Force
}

$basePython = (& py -3.14 -c "import sys; print(sys.executable)" 2>$null)
if (-not $basePython) {
    $basePython = "C:\Users\Yuu\AppData\Local\Python\pythoncore-3.14-64\python.exe"
}
if (-not (Test-Path $basePython)) {
    Write-Host "找不到 Python 3.14（原 .venv 的基础解释器）。" -ForegroundColor Red
    exit 1
}

& $basePython -m venv $venv
if ($LASTEXITCODE -ne 0) { Write-Host "venv 创建失败" -ForegroundColor Red; exit 1 }

$venvPython = Join-Path $venv "Scripts\python.exe"

Write-Host "安装锁定版本的依赖（requirements-lock.txt，优先走本地缓存）..."
& $venvPython -m pip install --disable-pip-version-check -q -r (Join-Path $NewRoot "requirements-lock.txt")
if ($LASTEXITCODE -ne 0) { Write-Host "依赖安装失败（可能需联网）" -ForegroundColor Red; exit 1 }

Write-Host "安装项目（editable）..."
& $venvPython -m pip install --disable-pip-version-check -q -e . --no-deps
if ($LASTEXITCODE -ne 0) { Write-Host "项目安装失败" -ForegroundColor Red; exit 1 }

Write-Host "验证导入..."
& $venvPython -c "import livedanmaku, livedanmaku.gui"
if ($LASTEXITCODE -ne 0) { Write-Host "验证失败" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "=========================================" -ForegroundColor Green
Write-Host "迁移完成！新路径: $NewRoot" -ForegroundColor Green
Write-Host "之后请在新路径打开 ZCode / 终端；双击 启动GUI.bat 即可启动界面。" -ForegroundColor Green
Write-Host "=========================================" -ForegroundColor Green
