[CmdletBinding()]
param(
    [string]$Python = "",
    [string]$Venv = ".venv-build",
    [switch]$SkipInstall,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ProjectRoot "scripts\_common.ps1")

$BuildVenv = Resolve-ProjectPath -ProjectRoot $ProjectRoot -Path $Venv
$BuildPython = Get-VenvPythonPath -VenvPath $BuildVenv
$SpecFile = Join-Path $ProjectRoot "DanmakuStudio.spec"
$OutputDir = Join-Path $ProjectRoot "dist\DanmakuStudio"
$OutputExe = Join-Path $OutputDir "DanmakuStudio.exe"

Set-Location $ProjectRoot

if (-not (Test-Path -LiteralPath $SpecFile)) {
    throw "PyInstaller spec was not found: $SpecFile"
}

$Python = Resolve-PythonCommand -Python $Python
Assert-SupportedPython -Python $Python
$BuildPython = New-PythonVenvIfMissing -Python $Python -VenvPath $BuildVenv

if (-not $SkipInstall) {
    Write-Host "Installing build dependencies..."
    Invoke-CheckedCommand -FilePath $BuildPython -ArgumentList @(
        "-m", "pip", "install", "--upgrade", "pip"
    )
    Invoke-CheckedCommand -FilePath $BuildPython -ArgumentList @(
        "-m", "pip", "install", "--editable", ".", "pyinstaller"
    )
}

Write-Host "Checking build imports..."
Invoke-CheckedCommand -FilePath $BuildPython -ArgumentList @(
    "-c",
    "import PyInstaller, danmakustudio; print('Build import check passed')"
)

if ($CheckOnly) {
    Write-Host "Build environment is ready: $BuildPython"
    return
}

Write-Host "Building DanmakuStudio..."
Invoke-CheckedCommand -FilePath $BuildPython -ArgumentList @(
    "-m", "PyInstaller", "--noconfirm", "--clean", $SpecFile
)

$ConfigSource = Join-Path $ProjectRoot "danmakustudio.yaml"
if (Test-Path -LiteralPath $ConfigSource) {
    New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
    Copy-Item -LiteralPath $ConfigSource -Destination $OutputDir -Force
}

if (-not (Test-Path -LiteralPath $OutputExe)) {
    throw "Build finished, but executable was not found: $OutputExe"
}

Write-Host ""
Write-Host "Done: $OutputExe"
Write-Host "Run it by double-clicking the exe, or from PowerShell:"
Write-Host "  $OutputExe"
