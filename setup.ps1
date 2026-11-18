# Rebuilds everything this project needs on a Windows PC.
#   powershell -ExecutionPolicy Bypass -File setup.ps1 -DataDir D:\clipper-data
# -DataDir is where tools, models, recordings and clips live (use the drive with the most space).
# Downloads: FFmpeg (about 115 MB), TwitchDownloaderCLI (about 50 MB), a face model and a voice model (about 120 MB).
# The Whisper speech model (about 1.6 GB) downloads by itself on the first transcribe.

param([string]$DataDir = "data")

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$FfmpegUrl = 'https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.0.2-essentials_build.zip'
$TdUrl     = 'https://github.com/lay295/TwitchDownloader/releases/download/1.56.5/TwitchDownloaderCLI-1.56.5-Windows-x64.zip'

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host 'uv is not installed. Install it from https://docs.astral.sh/uv/ and run this script again.' -ForegroundColor Red
    exit 1
}

$DataDir = [System.IO.Path]::GetFullPath($DataDir)
$tools = Join-Path $DataDir 'tools'
$dl = Join-Path $DataDir 'downloads'
New-Item -ItemType Directory -Force $tools, $dl | Out-Null

if (-not (Test-Path config.local.yaml)) {
    "paths:`n  data_dir: $($DataDir -replace '\\','/')" | Out-File config.local.yaml -Encoding utf8
    Write-Host "Wrote config.local.yaml (data_dir = $DataDir)"
}

function Get-Tool($name, $url, $folder) {
    if ((Get-Command $name -ErrorAction SilentlyContinue) -or (Get-ChildItem $tools -Recurse -Filter "$name.exe" -ErrorAction SilentlyContinue)) {
        Write-Host "  OK       $name"
        return
    }
    $zip = Join-Path $dl "$folder.zip"
    Write-Host "  Getting  $name ..."
    # curl resumes (-C -) and retries, because the connection drops.
    curl.exe -L -C - --retry 30 --retry-delay 5 --retry-all-errors -sS -o $zip $url
    if ($LASTEXITCODE -ne 0) { throw "download of $name failed" }
    Expand-Archive -Path $zip -DestinationPath (Join-Path $tools $folder) -Force
    Write-Host "  OK       $name (installed under $tools)"
}

Write-Host '== External tools =='
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
uv sync --frozen
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ''
Write-Host '== Tests =='
uv run pytest -q
if ($LASTEXITCODE -ne 0) { Write-Host 'Tests failed. Fix before using the pipeline.' -ForegroundColor Red; exit $LASTEXITCODE }

Write-Host ''
Write-Host '== Disk =='
Get-PSDrive -PSProvider FileSystem | Where-Object Free | ForEach-Object {
    Write-Host ("  {0}:  {1:N1} GB free" -f $_.Name, ($_.Free / 1GB))
}
Write-Host ''
uv run clipper hardware --refresh
Write-Host ''
Write-Host 'Ready. Read GUIDE.md, or try:  uv run clipper watch <channel name>'
