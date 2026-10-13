@echo off
rem Full project folder migration: DanmakuStudio -> LiveDanmaku (renames the
rem folder and rebuilds .venv). CLOSE ZCode / terminals / Explorer windows that
rem are inside the project folder BEFORE running this, otherwise the rename
rem fails because the directory is in use.
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\relocate.ps1" -NewName LiveDanmaku
pause
