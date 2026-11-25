@echo off
rem Builds dist\Stream-Clipper-Setup.exe from Setup.cs with the C# compiler that is part of Windows. Nothing to install.
cd /d "%~dp0.."
set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not exist "%CSC%" set "CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
if not exist "%CSC%" (
  echo The C# compiler that comes with Windows was not found at %CSC%.
  exit /b 1
)
if not exist dist mkdir dist
"%CSC%" /nologo /target:winexe /optimize+ /platform:anycpu /out:dist\Stream-Clipper-Setup.exe /win32icon:installer\app.ico ^
  /r:System.dll /r:System.Core.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll /r:System.IO.Compression.dll /r:System.IO.Compression.FileSystem.dll ^
  installer\Setup.cs
if errorlevel 1 exit /b 1
echo Built dist\Stream-Clipper-Setup.exe
