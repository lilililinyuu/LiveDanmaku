@echo off
rem DanmakuStudio GUI launcher (double-click to start, no console window)
rem To see console logs for debugging, run instead:
rem   .venv\Scripts\python.exe -m danmakustudio.gui
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
  echo [DanmakuStudio] .venv not found. Run setup first:
  echo   powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
  pause
  exit /b 1
)
start "" "%~dp0.venv\Scripts\pythonw.exe" -m danmakustudio.gui
