@echo off
rem Starts Stream Clipper and opens its page in the browser. Double-click this file, or the desktop shortcut made by setup.
rem When the page's Update button asks for a restart, the program ends with code 42 and this starts it again.
title Stream Clipper
cd /d "%~dp0"
set CLIPPER_LAUNCHER=1
where uv >nul 2>nul
if errorlevel 1 (
  echo uv is not installed. Install it from https://docs.astral.sh/uv/ and run setup.ps1 first.
  pause
  exit /b 1
)
:run
uv run clipper review --open
if %errorlevel%==42 (
  echo Restarting after the update ...
  goto run
)
if not %errorlevel%==0 (
  echo.
  echo Stream Clipper stopped with a problem. The lines above say why.
  pause
)
