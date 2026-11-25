# Makes sure Windows has the Microsoft Visual C++ runtime, which captions (speech-to-text) need.
#   powershell -ExecutionPolicy Bypass -File install-vc-runtime.ps1 -Downloads D:\clipper-data\downloads
# setup.ps1 runs this by itself; it can also be run alone.
#
# Exit code 0: the runtime is there (it already was, or it was installed now).
# Exit code 1: it is not there, and the lines printed say why and what to do by hand.
#
# What it does when the runtime is missing or too old: fetches Microsoft's own installer from Microsoft's address,
# asks Windows whether Microsoft really signed that file, and only then runs it. Installing it changes Windows itself,
# so Windows asks "Do you want to allow this app to make changes to your device?" once. Answering No is fine:
# nothing is installed and this script says how to do it later.
#
# -DryRun fetches and checks the file but does not run it (to try this script on a PC that already has the runtime).
# -Url and -Dll are only for trying this script: another file for the signature check, another place to look for the runtime.
# Whatever they name, a file is still run only if Microsoft signed it and Windows was told Yes.
param([string]$Downloads = "", [switch]$DryRun, [string]$Url = 'https://aka.ms/vs/17/release/vc_redist.x64.exe', [string]$Dll = "")

$ErrorActionPreference = 'Stop'
$Microsoft = 'https://aka.ms/vs/17/release/vc_redist.x64.exe'
if (-not $Dll) { $Dll = Join-Path $env:SystemRoot 'System32\msvcp140.dll' }

# "" when the runtime is fine, otherwise what is wrong with it. Speech-to-text needs 14.40 or newer.
function Get-RuntimeProblem {
    if (-not (Test-Path $Dll)) { return 'it is not on this computer' }
    $v = (Get-Item $Dll).VersionInfo
    if (($v.FileMajorPart -lt 14) -or ($v.FileMajorPart -eq 14 -and $v.FileMinorPart -lt 40)) {
        return "version $($v.FileMajorPart).$($v.FileMinorPart) is installed and 14.40 or newer is needed"
    }
    return ''
}

function Write-ByHand($why) {
    Write-Host "  MISSING  Microsoft Visual C++ runtime: $why" -ForegroundColor Yellow
    Write-Host "           Captions cannot work without it. To install it yourself: download $Microsoft" -ForegroundColor Yellow
    Write-Host "           (Microsoft's own free file, about 25 MB), run it, restart the computer, then run setup again." -ForegroundColor Yellow
}

$problem = Get-RuntimeProblem
if (-not $problem -and -not $DryRun) { Write-Host '  OK       Microsoft Visual C++ runtime'; exit 0 }

if (-not $Downloads) { $Downloads = Join-Path $env:TEMP 'stream-clipper-downloads' }
New-Item -ItemType Directory -Force $Downloads | Out-Null
$exe = Join-Path $Downloads 'vc_redist.x64.exe'
if ($problem) { Write-Host "  Microsoft Visual C++ runtime: $problem. Captions need it, so it is installed now." }
Write-Host '  Getting  Microsoft Visual C++ runtime (about 25 MB, from Microsoft) ...'
curl.exe -f -L --retry 8 --retry-delay 5 --retry-all-errors -sS -o $exe $Url
if ($LASTEXITCODE -ne 0) {
    Remove-Item $exe -ErrorAction SilentlyContinue
    Write-ByHand "it could not be downloaded (curl exit code $LASTEXITCODE). Check the internet connection"
    exit 1
}

# Never run a downloaded program on trust: Windows must confirm that Microsoft signed this exact file.
$sig = Get-AuthenticodeSignature -FilePath $exe
$signer = if ($sig.SignerCertificate) { $sig.SignerCertificate.Subject } else { 'nobody' }
if ($sig.Status -ne 'Valid' -or $signer -notmatch '(^|,\s*)O=Microsoft Corporation(,|$)') {
    Remove-Item $exe -ErrorAction SilentlyContinue
    Write-ByHand "the downloaded file is not signed by Microsoft (signature: $($sig.Status), signer: $signer), so it was not run"
    exit 1
}
Write-Host "  Checked: the file is signed by Microsoft ($((Get-Item $exe).VersionInfo.ProductName) $((Get-Item $exe).VersionInfo.ProductVersion))."

if ($DryRun) {
    Write-Host "  Dry run: the installer was fetched and checked but not run. It is at $exe"
    if ($problem) { exit 1 } else { exit 0 }
}

Write-Host '  WINDOWS NOW ASKS: "Do you want to allow this app to make changes to your device?" for Microsoft Visual C++.'
Write-Host '  Press Yes. (The question may be waiting behind this window or blinking on the taskbar.)'
$code = $null
try {
    $p = Start-Process -FilePath $exe -ArgumentList '/install', '/quiet', '/norestart' -Verb RunAs -PassThru
    $null = $p.Handle   # keeps the exit code readable after it ends
    if (-not $p.WaitForExit(900000)) {
        Write-ByHand 'its installer was still running after 15 minutes, so setup stopped waiting for it'
        exit 1
    }
    $code = $p.ExitCode
} catch {
    Write-ByHand "Windows was not told Yes, or would not start it ($($_.Exception.Message))"
    exit 1
}

# What counts is whether the runtime is there now, not what the installer said about itself.
$problem = Get-RuntimeProblem
if (-not $problem) {
    Remove-Item $exe -ErrorAction SilentlyContinue
    Write-Host '  OK       Microsoft Visual C++ runtime (installed now)'
    if ($code -eq 3010) { Write-Host '           Windows wants a restart to finish it: restart the computer before making clips.' -ForegroundColor Yellow }
    exit 0
}
$said = if ($null -eq $code) { 'its installer gave no result' } else { "its installer ended with code $code" }
if ($code -eq 3010) { $said = 'it is installed but Windows needs a restart first: restart the computer, then run setup again' }
if ($code -eq 1602) { $said = 'its installer was cancelled' }
Write-ByHand "$said; after that, $problem"
exit 1
