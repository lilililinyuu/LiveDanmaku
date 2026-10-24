[CmdletBinding()]
param(
    [string]$Python = "",
    [string]$Venv = ".venv",
    # 系统 PATH 上已有 yt-dlp 时可加此开关，跳过把 yt-dlp 装进 venv；
    # GUI 找不到 venv 内的 yt-dlp 会自动回退调用系统 PATH 上的版本
    [switch]$NoDownload
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ProjectRoot "scripts\_common.ps1")

Set-Location $ProjectRoot

$Python = Resolve-PythonCommand -Python $Python
Assert-SupportedPython -Python $Python

$VenvPath = Resolve-ProjectPath -ProjectRoot $ProjectRoot -Path $Venv
$VenvPython = New-PythonVenvIfMissing -Python $Python -VenvPath $VenvPath

Write-Host "Upgrading pip..."
Invoke-CheckedCommand -FilePath $VenvPython -ArgumentList @(
    "-m", "pip", "install", "--upgrade", "pip"
)

$installTarget = ".[download]"
if ($NoDownload) {
    $installTarget = "."
    Write-Host "NoDownload: skipping yt-dlp; the system PATH yt-dlp will be used."
}

Write-Host "Installing LiveDanmaku and development dependencies..."
Invoke-CheckedCommand -FilePath $VenvPython -ArgumentList @(
    "-m", "pip", "install", "--editable", $installTarget, "--group", "dev"
)

Write-Host "Checking runtime and GUI imports..."
& $VenvPython -c "import livedanmaku; from PySide6.QtGui import QImage; print('Runtime import check passed')"
if ($LASTEXITCODE -ne 0) {
    throw (
        "PySide6 could not load with the selected Python: $Python. " +
        "Use python.org CPython 3.13+ or rerun setup.ps1 with -Python pointing to a compatible python.exe."
    )
}

$missingTools = @("ffmpeg", "ffprobe") | Where-Object {
    -not (Get-Command $_ -ErrorAction SilentlyContinue)
}
if ($missingTools.Count -gt 0) {
    Write-Warning (
        "Python dependencies are installed, but these external tools are not on Path: " +
        ($missingTools -join ", ")
    )
}

Write-Host ""
Write-Host "Setup complete: $VenvPython"
Write-Host "Run tests:  powershell -NoProfile -ExecutionPolicy Bypass -File .\test.ps1"
Write-Host "Run GUI:    powershell -NoProfile -ExecutionPolicy Bypass -File .\run_gui.ps1"
