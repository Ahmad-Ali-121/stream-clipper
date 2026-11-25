@echo off
rem Starts Stream Clipper and opens its page in the browser. Double-click this file, or the desktop shortcut made by setup.
rem When the page's Update button asks for a restart, the program ends with code 42 and this starts it again.
title Stream Clipper
cd /d "%~dp0"
set CLIPPER_LAUNCHER=1
rem A copy put in by Stream-Clipper-Setup.exe keeps its own uv and Git beside the program: use those first.
if exist "%~dp0..\tools\uv\uv.exe" set "PATH=%~dp0..\tools\uv;%PATH%"
if exist "%~dp0..\tools\git\cmd\git.exe" set "PATH=%~dp0..\tools\git\cmd;%PATH%"
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
