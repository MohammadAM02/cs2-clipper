<#
Builds dist\CS2Clipper.exe, the whole app in one file, and checks it. From the repo:

    powershell -ExecutionPolicy Bypass -File packaging\build.ps1

The check runs the new exe with --bundle-check: it loads what the app only loads once it is running
(the tray, the pages, the window's .NET and WebView2) and writes a report, printed here. It starts
nothing and gets a throwaway data folder, never %LOCALAPPDATA%\CS2Clipper.

With -Installer it then compiles dist\CS2Clipper-Setup.exe, the installer, from packaging\cs2clipper.iss.
That takes Inno Setup (https://jrsoftware.org/isdl.php).

Adapted from thelifeofsuleyman/cs2-clipper's `packaging/build.ps1` (MIT). What we changed: uv instead
of a venv's python, one file instead of a folder, the refusal while the app runs, the bundle check, and
of its switches only -Installer is kept.

MIT License

Copyright (c) 2026 thelifeofsuleyman

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
#>
param([switch]$Installer)
$ErrorActionPreference = 'Stop'

function Invoke-Tool([string]$what, [scriptblock]$run) {
    # uv and PyInstaller write their progress to stderr, which Windows PowerShell turns into errors when
    # the output is redirected, and 'Stop' would throw on the first one: only the exit code decides.
    $ErrorActionPreference = 'Continue'
    & $run 2>&1 | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" }
}

$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    if (Get-Process -Name CS2Clipper -ErrorAction SilentlyContinue) {
        throw 'CS2 Clipper is running. Quit it from the tray first: its exe cannot be replaced while it runs.'
    }

    Invoke-Tool 'uv sync' { uv sync --group build }
    # The FACEIT key every install gets, written to the gitignored packaging\shipped.env and bundled
    # into the exe by the spec. Never printed; the app protects it for the account that runs it.
    Invoke-Tool 'shipped.env' { uv run --no-sync python packaging\make_shipped_env.py }
    Invoke-Tool 'PyInstaller' { uv run --no-sync pyinstaller --noconfirm --clean packaging\cs2clipper.spec }

    $exe = Join-Path $root 'dist\CS2Clipper.exe'
    $scratch = Join-Path ([IO.Path]::GetTempPath()) "cs2clipper-bundle-check-$PID"
    New-Item -ItemType Directory -Force $scratch | Out-Null
    try {
        $report = Join-Path $scratch 'report.txt'
        $start = New-Object Diagnostics.ProcessStartInfo $exe
        $start.Arguments = "--bundle-check `"$report`""
        $start.UseShellExecute = $false
        $start.EnvironmentVariables['CLIPPER_DATA_DIR'] = Join-Path $scratch 'data'
        $clock = [Diagnostics.Stopwatch]::StartNew()
        $check = [Diagnostics.Process]::Start($start)
        if (-not $check.WaitForExit(120000)) {
            $check.Kill()
            throw 'The bundle check did not finish in 2 minutes (is the exe showing an error dialog?)'
        }
        $seconds = [math]::Round($clock.Elapsed.TotalSeconds, 1)
        Write-Host ''
        Write-Host "Bundle check ($seconds s, exit $($check.ExitCode)):"
        if (Test-Path $report) { Get-Content $report | ForEach-Object { Write-Host "  $_" } }
        else { Write-Host '  no report: the exe failed before the checks ran' }
        if ($check.ExitCode -ne 0) { throw 'The bundle check failed.' }
    } finally {
        Remove-Item -Recurse -Force $scratch -ErrorAction SilentlyContinue
    }

    $size = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host ''
    Write-Host "Built $exe ($size MB)"

    if ($Installer) {
        # Inno Setup's compiler: the one on PATH, or where Inno Setup's own installer puts it. The release
        # is built with 6, which GitHub's Windows runner has; 7 installs beside it.
        $iscc = Get-Command iscc -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($iscc) { $iscc = $iscc.Source }
        else {
            $iscc = foreach ($version in 6, 7) {
                foreach ($programs in ${env:ProgramFiles(x86)}, $env:ProgramFiles) {
                    if ($programs) { Join-Path $programs "Inno Setup $version\ISCC.exe" }
                }
            }
            $iscc = $iscc | Where-Object { Test-Path $_ } | Select-Object -First 1
        }
        if (-not $iscc) {
            throw 'Inno Setup is not installed: its ISCC.exe compiles the installer (https://jrsoftware.org/isdl.php).'
        }

        Invoke-Tool 'Inno Setup' { & $iscc packaging\cs2clipper.iss }

        $setup = Join-Path $root 'dist\CS2Clipper-Setup.exe'
        $size = [math]::Round((Get-Item $setup).Length / 1MB, 1)
        Write-Host ''
        Write-Host "Built $setup ($size MB)"
    }
} finally {
    Pop-Location
}
