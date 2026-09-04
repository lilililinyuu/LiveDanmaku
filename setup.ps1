[CmdletBinding()]
param(
    [string]$Python = "",
    [string]$Venv = ".venv"
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

Write-Host "Installing DanmakuStudio and development dependencies..."
Invoke-CheckedCommand -FilePath $VenvPython -ArgumentList @(
    "-m", "pip", "install", "--editable", ".", "--group", "dev"
)

Write-Host "Checking runtime and GUI imports..."
& $VenvPython -c "import danmakustudio; from PySide6.QtGui import QImage; print('Runtime import check passed')"
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
