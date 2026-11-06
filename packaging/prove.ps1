<#
Proves the installer on a machine that has never seen the app. It installs dist\CS2Clipper-Setup.exe,
checks that the app's folder holds the build's files, runs the installed exe's real setup (which
downloads and installs csda, FFmpeg and HLAE), checks what that left, runs setup again, upgrades over a
running copy, updates the way the app does (which starts it again), and uninstalls. Then it installs the
newest published release, updates that copy to this one the same way, as users will, and uninstalls
again.

It is for a throwaway machine, such as the one GitHub lends a release build: it installs for the
signed-in user and takes the app off again. So it refuses without -ThrowawayMachine, and on a PC that
already has the app or its data folder. There, after packaging\build.ps1 -Installer:

    powershell -ExecutionPolicy Bypass -File packaging\prove.ps1 -ThrowawayMachine

Setup asks GitHub's API which HLAE is the newest, and the proof asks it for the newest release. A runner
shares its address with many others, so the workflow gives both a token to ask with, in
CLIPPER_GITHUB_TOKEN.
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

function Wait-ForPage([int]$skip, $process) {
    # The port of the page an app started after the log's first $skip lines, once it answers; $null when none
    # does in two minutes. The page takes the first free port from 8765 up, and the log tells which.
    $patience = [Diagnostics.Stopwatch]::StartNew()
    while ($patience.Elapsed.TotalSeconds -lt 120) {
        Start-Sleep -Seconds 1
        if ($process -and $process.HasExited) { throw "the app ended on its own, with exit code $($process.ExitCode)" }
        $told = Get-Content -Encoding UTF8 -LiteralPath $log | Select-Object -Skip $skip |
            Select-String 'page is on port (\d+)' | Select-Object -Last 1
        if ($told) {
            $candidate = [int]$told.Matches[0].Groups[1].Value
            if (Test-AppAnswers $candidate) { return $candidate }
        }
    }
    return $null
}

function Get-LogLength {
    return @(Get-Content -Encoding UTF8 -LiteralPath $log).Count
}

function Get-Files([string]$folder) {
    # The files under $folder, as paths relative to it, in order.
    $prefix = (Get-Item -LiteralPath $folder).FullName.TrimEnd('\') + '\'
    return @(Get-ChildItem -LiteralPath $folder -Recurse -File |
        ForEach-Object { $_.FullName.Substring($prefix.Length) } | Sort-Object)
}

function Confirm-TheBuildsFiles {
    # The app's folder holds every file of the build, and no other: none an older version left. The uninstaller's
    # unins000 files are Inno Setup's own.
    $built = @(Get-Files $bundle)
    $installed = @(Get-Files $appDir | Where-Object { $_ -notlike 'unins*' })
    foreach ($file in $built) { if ($installed -notcontains $file) { Write-Host "  missing: $file" } }
    foreach ($file in $installed) { if ($built -notcontains $file) { Write-Host "  not the build's: $file" } }
    Confirm-That (($built -join '|') -eq ($installed -join '|')) "$appDir holds the build's $($built.Count) files, and no others"
}

function Uninstall-TheApp([string]$logName) {
    $code = Start-AndWait (Join-Path $appDir 'unins000.exe') "$silently /LOG=`"$installerLogs\$logName`"" 600
    Confirm-That ($code -eq 0) "the uninstaller ended with exit code 0 (it gave $code)"
    # It hands over to a copy of itself in the temp folder, which may still be at work.
    $patience = [Diagnostics.Stopwatch]::StartNew()
    while ((Test-Path -LiteralPath $appDir) -and $patience.Elapsed.TotalSeconds -lt 60) { Start-Sleep -Seconds 1 }
    Confirm-That (-not (Test-Path -LiteralPath $appDir)) "$appDir is gone"
}

$root = Split-Path -Parent $PSScriptRoot
$setup = Join-Path $root 'dist\CS2Clipper-Setup.exe'
$bundle = Join-Path $root 'dist\CS2Clipper'      # what the build made, which the installer copies
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

$version = (Get-Item (Join-Path $bundle 'CS2Clipper.exe')).VersionInfo.ProductVersion
New-Item -ItemType Directory -Force $installerLogs | Out-Null
try {
    Write-Host '::group::Install'
    $code = Start-AndWait $setup "$silently /LOG=`"$installerLogs\install.log`"" 600
    Confirm-That ($code -eq 0) "the installer ended with exit code 0 (it gave $code)"
    Confirm-That (Test-Path -LiteralPath $exe) "it installed $exe"
    Confirm-That ((Get-Item $exe).VersionInfo.ProductVersion -eq $version) "the installed exe is version $version"
    Confirm-TheBuildsFiles
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
    $port = Wait-ForPage $before $running
    Confirm-That $port 'the installed app runs, and its page answers'
    Write-Host "  on port $port"
    # A file an older version had in _internal and this one has not: the upgrade must not leave it there.
    Set-Content -LiteralPath (Join-Path $appDir '_internal\left-by-an-older-version.txt') -Value 'stale'
    $code = Start-AndWait $setup "$silently /LOG=`"$installerLogs\upgrade.log`"" 600
    Show-File "$installerLogs\upgrade.log"
    Confirm-That ($code -eq 0) "the installer ended with exit code 0 over the running copy (it gave $code)"
    Confirm-That ((Get-Content -LiteralPath "$installerLogs\upgrade.log") -match 'CS2Clipper\.exe quit: exit code 0') `
        'it asked the running copy to quit, which it did'
    Confirm-That ($running.WaitForExit(30000)) 'the copy that ran has ended'
    Confirm-That (-not (Get-Process -Name CS2Clipper -ErrorAction SilentlyContinue)) 'no copy of the app runs'
    Confirm-That (-not (Test-AppAnswers $port)) "nothing answers on port $port any more"
    Confirm-That ((Get-Item $exe).VersionInfo.ProductVersion -eq $version) "the installed exe is version $version"
    Confirm-TheBuildsFiles
    Write-Host '::endgroup::'

    Write-Host '::group::An update starts the app again'
    # The app's own update runs the installer as the upgrade above did, with /RELAUNCH (clipper.update): the
    # running copy quits, the exe is replaced, and the installer starts the app again.
    $before = Get-LogLength
    $running = Start-Program $exe 'run --headless'
    Confirm-That (Wait-ForPage $before $running) 'the installed app runs, and its page answers'
    $before = Get-LogLength
    $code = Start-AndWait $setup "$silently /RELAUNCH /LOG=`"$installerLogs\update.log`"" 600
    Show-File "$installerLogs\update.log"
    Confirm-That ($code -eq 0) "the installer ended with exit code 0 over the running copy (it gave $code)"
    Confirm-That ($running.WaitForExit(30000)) 'the copy that ran has ended'
    $port = Wait-ForPage $before $null
    Confirm-That $port 'the installer started the app again, and its page answers'
    Write-Host "  on port $port"
    $code = Start-AndWait $exe 'quit' 120
    Confirm-That ($code -eq 0) "the app it started quits when asked (exit code $code)"
    Confirm-That (-not (Get-Process -Name CS2Clipper -ErrorAction SilentlyContinue)) 'no copy of the app runs'
    Write-Host '::endgroup::'

    Write-Host '::group::Uninstall'
    Uninstall-TheApp 'uninstall.log'
    Confirm-That (-not (Test-Path -LiteralPath $inStartMenu)) 'the Start Menu shortcut is gone'
    Confirm-That (-not (Test-Path -LiteralPath $inStartup)) 'the sign-in shortcut is gone'
    Confirm-That (-not (Test-Path -LiteralPath $listing)) 'Windows no longer lists the app'
    Confirm-That (Test-Path -LiteralPath $csda) "the data folder is left as it was, $data"
    Write-Host '::endgroup::'

    Write-Host '::group::An update from the newest published release'
    # How users get this version: the copy the newest published release installed updates itself, the way the
    # app does. A release before the app was a folder was one exe, with no _internal. This comes after setup, so
    # that copy does not start a setup of its own.
    $repo = if ($env:GITHUB_REPOSITORY) { $env:GITHUB_REPOSITORY } else { 'MohammadAM02/cs2-clipper' }
    $asking = @{ Accept = 'application/vnd.github+json'; 'User-Agent' = 'cs2-clipper-prove' }
    if ($env:CLIPPER_GITHUB_TOKEN) { $asking.Authorization = "Bearer $env:CLIPPER_GITHUB_TOKEN" }
    $latest = Invoke-RestMethod -Uri "https://api.github.com/repos/$repo/releases/latest" -Headers $asking
    $asset = @($latest.assets | Where-Object { $_.name -eq 'CS2Clipper-Setup.exe' })[0]
    Confirm-That $asset "the newest published release, $($latest.tag_name), has an installer"
    $published = Join-Path $installerLogs 'published-setup.exe'
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $published -UseBasicParsing
    $digest = 'sha256:' + (Get-FileHash -Algorithm SHA256 -LiteralPath $published).Hash.ToLowerInvariant()
    Confirm-That ($digest -eq $asset.digest) 'the download is the installer GitHub publishes'
    $code = Start-AndWait $published "$silently /LOG=`"$installerLogs\published.log`"" 600
    Confirm-That ($code -eq 0) "it installed with exit code 0 (it gave $code)"
    $before = Get-LogLength
    $running = Start-Program $exe 'run --headless'
    Confirm-That (Wait-ForPage $before $running) "$($latest.tag_name) runs, and its page answers"
    $before = Get-LogLength
    $code = Start-AndWait $setup "$silently /RELAUNCH /LOG=`"$installerLogs\from-published.log`"" 600
    Show-File "$installerLogs\from-published.log"
    Confirm-That ($code -eq 0) "this installer ended with exit code 0 over it (it gave $code)"
    Confirm-That ($running.WaitForExit(30000)) "the copy of $($latest.tag_name) has ended"
    Confirm-That ((Get-Item $exe).VersionInfo.ProductVersion -eq $version) "the installed exe is version $version"
    Confirm-TheBuildsFiles
    $port = Wait-ForPage $before $null
    Confirm-That $port 'the installer started this version, and its page answers'
    $code = Start-AndWait $exe 'quit' 120
    Confirm-That ($code -eq 0) "it quits when asked (exit code $code)"
    Write-Host '::endgroup::'

    Write-Host '::group::Uninstall again'
    Uninstall-TheApp 'uninstall-again.log'
    Write-Host '::endgroup::'

    Write-Host ''
    Write-Host ("Proved: CS2 Clipper $version installs, sets a PC up, upgrades over a running copy, updates " +
                "the way the app does, also from $($latest.tag_name), and uninstalls.")
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
