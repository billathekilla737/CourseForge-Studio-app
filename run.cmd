@echo off
REM Start CourseForge Studio and open it in the default browser.
REM Run this from a NORMAL terminal, not from inside a Claude Code session,
REM so the Claude CLI can see your login.

cd /d "%~dp0"

where claude >nul 2>&1
if errorlevel 1 (
  echo.
  echo   Claude Code CLI not found on PATH.
  echo   Install it from https://claude.com/claude-code and reopen this window.
  echo.
  pause
  exit /b 1
)

if not exist config.json (
  echo Creating config.json from config.example.json ...
  copy /y config.example.json config.json >nul
)

start "" http://127.0.0.1:8900
python -m canvasgrader serve
pause
