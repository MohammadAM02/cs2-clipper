<#
Proves the installer on a machine that has never seen the app. It installs dist\CS2Clipper-Setup.exe,
runs the installed exe's real setup (which downloads and installs csda, FFmpeg and HLAE), checks what
that left, runs setup again, upgrades over a running copy, and uninstalls.

It is for a throwaway machine, such as the one GitHub lends a release build: it installs for the
signed-in user and takes the app off again. So it refuses without -ThrowawayMachine, and on a PC that
already has the app or its data folder. There, after packaging\build.ps1 -Installer:

    powershell -ExecutionPolicy Bypass -File packaging\prove.ps1 -ThrowawayMachine

Setup asks GitHub's API which HLAE is the newest. A runner shares its address with many others, so the
workflow gives it a token to ask with, in CLIPPER_GITHUB_TOKEN.
#>
param([switch]$ThrowawayMachine)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'    # Invoke-RestMethod's progress bar, which a log has no use for

function Confirm-That($holds, [string]$what) {
    if (-not $holds) { throw "Not so: $what" }
    Write-Host "  ok: $what"
}

function Start-Program([string]$exe, [string]$arguments) {
    $start = New-Object Diagnostics.ProcessStartInfo $exe
    $start.Arguments = $arguments
    $start.UseShellExecute = $false
    return [Diagnostics.Process]::Start($start)
}

function Start-AndWait([string]$exe, [string]$arguments, [int]$seconds) {
    # Waits for that one process, and returns its exit code. Start-Process -Wait would wait for every
    # process it started as well.
    $process = Start-Program $exe $arguments
    if (-not $process.WaitForExit($seconds * 1000)) {
        $process.Kill()
        throw "$exe $arguments did not end in $seconds seconds"
    }
    return $process.ExitCode
}

function Show-File([string]$file, [int]$last = 0) {
    Write-Host "--- $file"
    if (-not (Test-Path -LiteralPath $file)) { Write-Host '  (not there)'; return }
    if ($last) { $lines = Get-Content -Encoding UTF8 -LiteralPath $file -Tail $last }
    else { $lines = Get-Content -Encoding UTF8 -LiteralPath $file }
    $lines | ForEach-Object { Write-Host "  $_" }
}

function Get-Shortcut([string]$file) {
    return (New-Object -ComObject WScript.Shell).CreateShortcut($file)
}

function Test-AppAnswers([int]$port) {
    try { return (Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 3).app -eq 'cs2-clipper' }
    catch { return $false }
}

$root = Split-Path -Parent $PSScriptRoot
$setup = Join-Path $root 'dist\CS2Clipper-Setup.exe'
$appDir = Join-Path $env:LOCALAPPDATA 'Programs\CS2Clipper'
$exe = Join-Path $appDir 'CS2Clipper.exe'
$data = Join-Path $env:LOCALAPPDATA 'CS2Clipper'
$log = Join-Path $data 'logs\clipper.log'
$tools = Join-Path $data 'tools'
$inStartMenu = Join-Path ([Environment]::GetFolderPath('Programs')) 'CS2 Clipper.lnk'
$inStartup = Join-Path ([Environment]::GetFolderPath('Startup')) 'CS2 Clipper.lnk'
$onDesktop = Join-Path ([Environment]::GetFolderPath('DesktopDirectory')) 'CS2 Clipper.lnk'
# The AppId of packaging\cs2clipper.iss, under which Windows lists an install for this user only.
$listing = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{03EF2ADC-14C4-4DA0-8A95-52187AA0BF34}_is1'
$installerLogs = Join-Path ([IO.Path]::GetTempPath()) "cs2clipper-prove-$PID"
$silently = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART'

if (-not $ThrowawayMachine) {
    throw ('This installs the app for you and takes it off again. ' +
           'It is for a throwaway machine: pass -ThrowawayMachine there, and nowhere else.')
}
if ($env:CLIPPER_DATA_DIR) {
    throw 'CLIPPER_DATA_DIR is set: the proof is of the data folder a customer gets.'
}
foreach ($seen in $appDir, $data) {
    if (Test-Path -LiteralPath $seen) { throw "$seen exists: this is not a machine that has never seen the app." }
}

$version = (Get-Item (Join-Path $root 'dist\CS2Clipper.exe')).VersionInfo.ProductVersion
New-Item -ItemType Directory -Force $installerLogs | Out-Null
try {
    Write-Host '::group::Install'
    $code = Start-AndWait $setup "$silently /LOG=`"$installerLogs\install.log`"" 600
    Confirm-That ($code -eq 0) "the installer ended with exit code 0 (it gave $code)"
    Confirm-That (Test-Path -LiteralPath $exe) "it installed $exe"
    Confirm-That ((Get-Item $exe).VersionInfo.ProductVersion -eq $version) "the installed exe is version $version"
    Confirm-That ((Get-Shortcut $inStartMenu).TargetPath -eq $exe) 'the Start Menu shortcut starts it'
    $atSignIn = Get-Shortcut $inStartup
    Confirm-That ($atSignIn.TargetPath -eq $exe -and $atSignIn.Arguments -eq '--background') `
        'the sign-in shortcut starts it in the tray'
    Confirm-That (-not (Test-Path -LiteralPath $onDesktop)) 'there is no desktop shortcut: its box is not ticked'
    $listed = Get-ItemProperty -LiteralPath $listing
    Confirm-That ($listed.DisplayName -eq 'CS2 Clipper' -and $listed.DisplayVersion -eq $version) `
        "Windows lists CS2 Clipper $version among the installed apps"
    Write-Host '::endgroup::'

    Write-Host '::group::Setup'
    # Setup installs FFmpeg only on a PC that has none on PATH, and leaves one that is there alone.
    $hadFfmpeg = [bool](Get-Command ffmpeg -ErrorAction SilentlyContinue) -and
                 [bool](Get-Command ffprobe -ErrorAction SilentlyContinue)
    $code = Start-AndWait $exe 'setup' 1800
    Show-File $log
    Confirm-That ($code -eq 0) "setup ended with exit code 0 (it gave $code)"
    $said = Get-Content -Encoding UTF8 -LiteralPath $log
    foreach ($name in 'csda', 'FFmpeg', 'HLAE') {
        if ($name -eq 'FFmpeg' -and $hadFfmpeg) {
            Confirm-That (-not ($said -match 'setup: installing FFmpeg')) 'setup left the FFmpeg on PATH alone'
        } else {
            Confirm-That ($said -match "setup: $name is in place") "setup installed $name"
        }
    }
    Write-Host '::endgroup::'

    Write-Host '::group::What setup left'
    $csda = Join-Path $tools 'csda\csda.exe'
    Confirm-That (Test-Path -LiteralPath $csda) "csda is at $csda"

    $ffmpeg = 'ffmpeg'
    if (-not $hadFfmpeg) {
        $ffmpeg = Join-Path $tools 'ffmpeg\bin\ffmpeg.exe'
        $named = (Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $data 'settings.json') | ConvertFrom-Json).ffmpeg
        Confirm-That ($named -eq $ffmpeg) "the app's settings name the FFmpeg setup installed, $ffmpeg"
    }
    $answer = & $ffmpeg -version
    Confirm-That ($LASTEXITCODE -eq 0 -and @($answer)[0] -match '^ffmpeg version') "FFmpeg runs, $ffmpeg"
    Write-Host "  $(@($answer)[0])"

    $hlae = Join-Path $tools 'hlae\HLAE.exe'
    Confirm-That (Test-Path -LiteralPath $hlae) "HLAE is at $hlae"
    Write-Host "  its version: $((Get-Item $hlae).VersionInfo.ProductVersion)"
    Write-Host '::endgroup::'

    Write-Host '::group::Setup again'
    $installs = @($said -match 'setup: installing').Count
    $code = Start-AndWait $exe 'setup' 600
    Confirm-That ($code -eq 0) "a second setup ended with exit code 0 (it gave $code)"
    $said = Get-Content -Encoding UTF8 -LiteralPath $log
    Confirm-That (@($said -match 'setup: installing').Count -eq $installs) 'and it installed nothing'
    Write-Host '::endgroup::'

    Write-Host '::group::Upgrade over a running copy'
    $before = @($said).Count
    $running = Start-Program $exe 'run --headless'
    $port = $null
    $patience = [Diagnostics.Stopwatch]::StartNew()
    while (-not $port -and $patience.Elapsed.TotalSeconds -lt 120) {
        Start-Sleep -Seconds 1
        if ($running.HasExited) { throw "the app ended on its own, with exit code $($running.ExitCode)" }
        # The page takes the first free port from 8765 up, and the log tells which.
        $told = Get-Content -Encoding UTF8 -LiteralPath $log | Select-Object -Skip $before |
            Select-String 'page is on port (\d+)' | Select-Object -Last 1
        if ($told) {
            $candidate = [int]$told.Matches[0].Groups[1].Value
            if (Test-AppAnswers $candidate) { $port = $candidate }
        }
    }
    Confirm-That $port 'the installed app runs, and its page answers'
    Write-Host "  on port $port"
    $code = Start-AndWait $setup "$silently /LOG=`"$installerLogs\upgrade.log`"" 600
    Show-File "$installerLogs\upgrade.log"
    Confirm-That ($code -eq 0) "the installer ended with exit code 0 over the running copy (it gave $code)"
    Confirm-That ((Get-Content -LiteralPath "$installerLogs\upgrade.log") -match 'CS2Clipper\.exe quit: exit code 0') `
        'it asked the running copy to quit, which it did'
    Confirm-That ($running.WaitForExit(30000)) 'the copy that ran has ended'
    Confirm-That (-not (Get-Process -Name CS2Clipper -ErrorAction SilentlyContinue)) 'no copy of the app runs'
    Confirm-That (-not (Test-AppAnswers $port)) "nothing answers on port $port any more"
    Confirm-That ((Get-Item $exe).VersionInfo.ProductVersion -eq $version) "the installed exe is version $version"
    Write-Host '::endgroup::'

    Write-Host '::group::Uninstall'
    $code = Start-AndWait (Join-Path $appDir 'unins000.exe') "$silently /LOG=`"$installerLogs\uninstall.log`"" 600
    Confirm-That ($code -eq 0) "the uninstaller ended with exit code 0 (it gave $code)"
    # It hands over to a copy of itself in the temp folder, which may still be at work.
    $patience = [Diagnostics.Stopwatch]::StartNew()
    while ((Test-Path -LiteralPath $appDir) -and $patience.Elapsed.TotalSeconds -lt 60) { Start-Sleep -Seconds 1 }
    Confirm-That (-not (Test-Path -LiteralPath $appDir)) "$appDir is gone"
    Confirm-That (-not (Test-Path -LiteralPath $inStartMenu)) 'the Start Menu shortcut is gone'
    Confirm-That (-not (Test-Path -LiteralPath $inStartup)) 'the sign-in shortcut is gone'
    Confirm-That (-not (Test-Path -LiteralPath $listing)) 'Windows no longer lists the app'
    Confirm-That (Test-Path -LiteralPath $csda) "the data folder is left as it was, $data"
    Write-Host '::endgroup::'

    Write-Host ''
    Write-Host "Proved: CS2 Clipper $version installs, sets a PC up, upgrades over a running copy, and uninstalls."
} catch {
    Write-Host '::endgroup::'
    Write-Host "::error::$($_.Exception.Message)"
    Show-File $log 80
    Get-ChildItem -LiteralPath $installerLogs -Filter *.log | ForEach-Object { Show-File $_.FullName 60 }
    throw
} finally {
    # Leave nothing running: a copy of the app that a failed check left.
    Get-Process -Name CS2Clipper -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
