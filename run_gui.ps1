[CmdletBinding()]
param(
    [string]$Python = "",
    [string]$Venv = ".venv",
    [switch]$NoSetup,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ProjectRoot "scripts\_common.ps1")

Set-Location $ProjectRoot

$VenvPath = Resolve-ProjectPath -ProjectRoot $ProjectRoot -Path $Venv
$VenvPython = Get-VenvPythonPath -VenvPath $VenvPath

if (-not (Test-Path -LiteralPath $VenvPython)) {
    if ($NoSetup) {
        throw "Development environment was not found: $VenvPath. Run .\setup.ps1 first."
    }

    & (Join-Path $ProjectRoot "setup.ps1") -Python $Python -Venv $Venv
}

if ($CheckOnly) {
    Write-Host "Checking GUI imports..."
    Invoke-CheckedCommand -FilePath $VenvPython -ArgumentList @(
        "-c",
        "import livedanmaku.gui; from PySide6.QtWidgets import QApplication; print('GUI import check passed')"
    )
    return
}

Write-Host "Starting LiveDanmaku GUI..."
Invoke-CheckedCommand -FilePath $VenvPython -ArgumentList @(
    "-m", "livedanmaku.gui"
)
