@echo off
rem Installs or repairs Stream Clipper. Double-click this file. It is safe to run again: what is already there is kept.
title Install Stream Clipper
cd /d "%~dp0"
echo.
echo   Stream Clipper: install
echo   ------------------------
echo.

where uv >nul 2>nul
if errorlevel 1 (
  echo   This needs a small free tool called "uv" ^(it installs Python and the program's parts^).
  where winget >nul 2>nul
  if errorlevel 1 (
    echo   Install it from https://docs.astral.sh/uv/ and then double-click this file again.
    pause
    exit /b 1
  )
  choice /c YN /m "  Install uv now with Windows' own installer (winget)"
  if errorlevel 2 (
    echo   Not installed. Install uv from https://docs.astral.sh/uv/ and run this again.
    pause
    exit /b 1
  )
  winget install --id astral-sh.uv -e --accept-source-agreements --accept-package-agreements
  echo.
  echo   uv is installed. Close this window and double-click Install-Stream-Clipper.cmd again, so Windows can find it.
  pause
  exit /b 0
)

if exist config.local.yaml (
  echo   This PC is already set up. Checking it and downloading anything that is missing...
  echo.
  powershell -NoProfile -ExecutionPolicy Bypass -File setup.ps1
  goto done
)

set "DATA=%~dp0data"
if exist D:\ set "DATA=D:\clipper-data"
echo   Clips, downloads and models need a folder with free space ^(5 GB or more^).
echo   Press Enter to use:  %DATA%
set "ANSWER="
set /p "ANSWER=  or type another folder: "
if not "%ANSWER%"=="" set "DATA=%ANSWER%"
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File setup.ps1 -DataDir "%DATA%"

:done
if errorlevel 1 (
  echo.
  echo   The install stopped with a problem. The lines above say why. Fix that and double-click Install-Stream-Clipper.cmd again.
  pause
  exit /b 1
)
echo.
echo   Done. Start it with the "Stream Clipper" shortcut on your desktop, or Stream-Clipper.cmd in this folder.
pause
