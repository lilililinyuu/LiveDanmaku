[CmdletBinding()]
param(
    [string]$Python = "",
    [string]$Venv = ".venv",
    [switch]$NoSetup
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

Write-Host "Running tests..."
$PytestTemp = Join-Path $ProjectRoot ".pytest-tmp"
Invoke-CheckedCommand -FilePath $VenvPython -ArgumentList @(
    "-m", "pytest", "-p", "no:cacheprovider", "--basetemp", $PytestTemp
)
