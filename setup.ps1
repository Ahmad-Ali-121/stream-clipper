# Rebuilds everything this project needs on a Windows PC.
#   powershell -ExecutionPolicy Bypass -File setup.ps1 -DataDir D:\clipper-data
# -DataDir is where tools, models, recordings and clips live (use the drive with the most space).
# Downloads: FFmpeg (about 115 MB), TwitchDownloaderCLI (about 50 MB), a face model and a voice model (about 120 MB).
# The Whisper speech model (about 1.6 GB) downloads by itself on the first transcribe.

# Add -Gpu on a PC with an NVIDIA card to also install NVIDIA's libraries (about 1 GB), so speech-to-text runs on the card.
# Add -NoShortcut to skip the "Stream Clipper" shortcut on the desktop.
# Leave -DataDir out on a PC that is already set up: the folder it uses now is kept.
param([string]$DataDir = "", [switch]$Gpu, [switch]$NoShortcut)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
# Two addresses for the same FFmpeg build: the author's site, and his copy on GitHub for when the site is unreachable.
$FfmpegUrl = 'https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.0.2-essentials_build.zip',
             'https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip'
$TdUrl     = 'https://github.com/lay295/TwitchDownloader/releases/download/1.56.5/TwitchDownloaderCLI-1.56.5-Windows-x64.zip'

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host 'uv is not installed. Install it from https://docs.astral.sh/uv/ and run this script again.' -ForegroundColor Red
    exit 1
}

if ($args.Count -gt 0) {
    throw "Did not understand '$($args -join ' ')'. If the data folder's name has a space in it, put it in quotes: -DataDir `"D:\my folder`""
}
$saved = ''
if (Test-Path config.local.yaml) {
    $line = Get-Content config.local.yaml | Where-Object { $_ -match '^\s*data_dir\s*:\s*(.+?)\s*$' } | Select-Object -First 1
    if ($line -and $line -match '^\s*data_dir\s*:\s*(.+?)\s*$') { $saved = $Matches[1].Trim('"', "'") }
}
if (-not $DataDir) { $DataDir = if ($saved) { $saved } else { 'data' } }
$DataDir = [System.IO.Path]::GetFullPath($DataDir)
# The program reads its data folder from config.local.yaml. Installing the tools anywhere else would leave it unable to find them.
if (Test-Path config.local.yaml) {
    $line = Get-Content config.local.yaml | Where-Object { $_ -match '^\s*data_dir\s*:\s*(.+?)\s*$' } | Select-Object -First 1
    if ($line -and $line -match '^\s*data_dir\s*:\s*(.+?)\s*$') {
        $existing = [System.IO.Path]::GetFullPath($Matches[1].Trim('"', "'"))
        if ($existing.TrimEnd('\') -ne $DataDir.TrimEnd('\')) {
            throw "config.local.yaml already says the data folder is $existing, but this run was given $DataDir. Run setup again with -DataDir `"$existing`", or delete config.local.yaml first to move to the new folder."
        }
    }
}
$tools = Join-Path $DataDir 'tools'
$dl = Join-Path $DataDir 'downloads'
New-Item -ItemType Directory -Force $tools, $dl | Out-Null

if (-not (Test-Path config.local.yaml)) {
    "paths:`n  data_dir: $($DataDir -replace '\\','/')" | Out-File config.local.yaml -Encoding utf8
    Write-Host "Wrote config.local.yaml (data_dir = $DataDir)"
}

function Get-Tool($name, $urls, $folder) {
    if ((Get-Command $name -ErrorAction SilentlyContinue) -or (Get-ChildItem $tools -Recurse -Filter "$name.exe" -ErrorAction SilentlyContinue)) {
        Write-Host "  OK       $name"
        return
    }
    $zip = Join-Path $dl "$folder.zip"
    Write-Host "  Getting  $name ..."
    # curl resumes (-C -) and retries, because the connection drops. If one address keeps failing, the next is tried.
    $ok = $false
    foreach ($url in @($urls)) {
        curl.exe -f -L -C - --retry 8 --retry-delay 5 --retry-all-errors -sS -o $zip $url
        if ($LASTEXITCODE -eq 0) { $ok = $true; break }
        Write-Host "  That address did not work ($(([uri]$url).Host)); trying another if there is one ..."
        Remove-Item $zip -ErrorAction SilentlyContinue   # a partial file from one address must not be resumed from another
    }
    if (-not $ok) {
        throw "download of $name failed. Check the internet connection (a firewall, antivirus or the provider may be blocking it) and run this script again."
    }
    Expand-Archive -Path $zip -DestinationPath (Join-Path $tools $folder) -Force
    Write-Host "  OK       $name (installed under $tools)"
}

Write-Host '== External tools =='
# Speech-to-text crashes ("access violation") on a Windows C++ runtime older than 14.40. Say so now rather than later.
$vc = Join-Path $env:SystemRoot 'System32\msvcp140.dll'
$vcOld = $true
if (Test-Path $vc) { $v = (Get-Item $vc).VersionInfo; $vcOld = ($v.FileMajorPart -lt 14) -or ($v.FileMajorPart -eq 14 -and $v.FileMinorPart -lt 40) }
function Write-RuntimeNote {
    Write-Host ""
    Write-Host "IMPORTANT: this computer's Microsoft Visual C++ runtime is missing or too old." -ForegroundColor Red
    Write-Host "Captions (speech-to-text) cannot work until it is installed. It is Microsoft's own free file (about 25 MB):" -ForegroundColor Red
    Write-Host "  https://aka.ms/vs/17/release/vc_redist.x64.exe" -ForegroundColor Red
    Write-Host "Download it, run it, restart the computer, then run this setup again." -ForegroundColor Red
    Write-Host "(Or run:  winget install Microsoft.VCRedist.2015+.x64 )" -ForegroundColor Red
}
if ($vcOld) { Write-Host '  MISSING  Microsoft Visual C++ runtime: captions need it (how to get it is at the end)' -ForegroundColor Yellow }
else { Write-Host '  OK       Microsoft Visual C++ runtime' }

Get-Tool 'ffmpeg' $FfmpegUrl 'ffmpeg'
Get-Tool 'TwitchDownloaderCLI' $TdUrl 'twitchdownloader'
$models = Join-Path $DataDir 'models'
New-Item -ItemType Directory -Force $models | Out-Null
$yunet = Join-Path $models 'face_detection_yunet_2023mar.onnx'
if (Test-Path $yunet) { Write-Host '  OK       face detection model' }
else {
    Write-Host '  Getting  face detection model (230 KB) ...'
    curl.exe -L --retry 10 --retry-all-errors -sS -o $yunet 'https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx'
    if ($LASTEXITCODE -ne 0) { throw 'download of the face detection model failed' }
}
$kokoro = Join-Path $models 'kokoro'
New-Item -ItemType Directory -Force $kokoro | Out-Null
foreach ($f in 'voices-v1.0.bin', 'kokoro-v1.0.int8.onnx') {
    $target = Join-Path $kokoro $f
    if (Test-Path $target) { Write-Host "  OK       voice model $f" }
    else {
        Write-Host "  Getting  voice model $f ..."
        curl.exe -L -C - --retry 20 --retry-all-errors -sS -o $target "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/$f"
        if ($LASTEXITCODE -ne 0) { throw "download of $f failed" }
    }
}
if (Get-Command node -ErrorAction SilentlyContinue) { Write-Host '  OK       node (needed by yt-dlp for YouTube)' }
else { Write-Host '  MISSING  node: install Node.js, yt-dlp needs it for YouTube' -ForegroundColor Yellow }
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) { Write-Host '  NVIDIA GPU found: it is used automatically (speech also needs the CUDA 12 and cuDNN 9 libraries)' }

Write-Host ''
Write-Host '== Python environment (pinned by uv.lock) =='
# Once chosen, -Gpu stays chosen: a later run without it must not remove a 1 GB download.
if (Test-Path '.venv\Lib\site-packages\nvidia\cublas') { $Gpu = $true }
if ($Gpu) {
    Write-Host "Installing the Python packages, with NVIDIA's libraries for speech on the graphics card (about 1 GB) ..."
    uv sync --frozen --extra gpu
} else {
    uv sync --frozen
}
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ''
Write-Host '== Tests =='
uv run pytest -q
if ($LASTEXITCODE -ne 0) {
    $code = $LASTEXITCODE
    Write-Host 'Tests failed. Fix before using the pipeline.' -ForegroundColor Red
    if ($vcOld) { Write-RuntimeNote }   # the likeliest reason on a new PC, said last so it is what is read
    exit $code
}

Write-Host ''
Write-Host '== Disk =='
Get-PSDrive -PSProvider FileSystem | Where-Object Free | ForEach-Object {
    Write-Host ("  {0}:  {1:N1} GB free" -f $_.Name, ($_.Free / 1GB))
}
Write-Host ''
uv run clipper hardware --refresh
Write-Host ''
if (-not $NoShortcut) {
    try {
        $desktop = [Environment]::GetFolderPath('Desktop')
        $link = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop 'Stream Clipper.lnk'))
        $link.TargetPath = (Join-Path $PSScriptRoot 'Stream-Clipper.cmd')
        $link.WorkingDirectory = $PSScriptRoot
        $link.Description = 'Start Stream Clipper and open its page'
        $link.Save()
        Write-Host "A 'Stream Clipper' shortcut is on your desktop: double-click it to start."
    } catch {
        Write-Host "Could not make the desktop shortcut ($($_.Exception.Message)). Start with:  uv run clipper review --open" -ForegroundColor Yellow
    }
}
Write-Host 'Ready. Double-click "Stream Clipper" on the desktop, or run:  uv run clipper review --open'

if ($vcOld) { Write-RuntimeNote }
