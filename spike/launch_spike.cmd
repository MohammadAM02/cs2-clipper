@echo off
setlocal enableextensions

REM ===========================================================================
REM  Phase 0 spike launcher — unattended CS2 demo render via HLAE.
REM  Edit ONLY this block, then run:  spike\launch_spike.cmd
REM ===========================================================================
set "HLAE_DIR=C:\HLAE"
set "CS2_EXE=G:\SteamLibrary\steamapps\common\Counter-Strike Global Offensive\game\bin\win64\cs2.exe"
set "CSGO_DIR=G:\SteamLibrary\steamapps\common\Counter-Strike Global Offensive\game\csgo"
set "REPO=%~dp0.."
set "DEMO_SRC=%REPO%\spike\demos\spike_demo.dem"
set "CFG_NAME=spike_autoexec"
REM ===========================================================================

if not exist "%HLAE_DIR%\HLAE.exe"      ( echo [FAIL] HLAE not found at %HLAE_DIR% & exit /b 1 )
if not exist "%HLAE_DIR%\x64\AfxHookSource2.dll" ( echo [FAIL] AfxHookSource2.dll missing & exit /b 1 )
if not exist "%CS2_EXE%"                ( echo [FAIL] cs2.exe not found & exit /b 1 )
if not exist "%DEMO_SRC%"               ( echo [FAIL] drop a Demo at %DEMO_SRC% first & exit /b 1 )
if not exist "%REPO%\spike\cfg\%CFG_NAME%.cfg" ( echo [FAIL] generate the cfg first: python spike\scripts\gen_spike_cfg.py spike\render_plan.example.json --out-dir spike\cfg & exit /b 1 )

echo [1/3] staging Demo  -> %CSGO_DIR%\spike_demo.dem
copy /y "%DEMO_SRC%" "%CSGO_DIR%\spike_demo.dem" >nul || ( echo [FAIL] demo copy & exit /b 1 )

echo [2/3] staging cfg   -> %CSGO_DIR%\cfg\%CFG_NAME%.cfg
copy /y "%REPO%\spike\cfg\%CFG_NAME%.cfg" "%CSGO_DIR%\cfg\%CFG_NAME%.cfg" >nul || ( echo [FAIL] cfg copy & exit /b 1 )
if exist "%CSGO_DIR%\console.log" del /q "%CSGO_DIR%\console.log"

echo [3/3] launching CS2 via HLAE custom loader (no GUI)
set "GAMEOPTS=-steam -insecure -console -sw -w 1920 -h 1080 -condebug +exec %CFG_NAME%"
"%HLAE_DIR%\HLAE.exe" -customLoader -noGui -autoStart ^
  -hookDllPath "%HLAE_DIR%\x64\AfxHookSource2.dll" ^
  -programPath "%CS2_EXE%" ^
  -cmdLine "%GAMEOPTS%"
echo HLAE launcher exit code: %ERRORLEVEL%   (0 = launched without failure)

echo.
echo Waiting for SPIKE_DONE in console.log (max 20 min, Ctrl+C to abort)...
set /a waited=0
:wait
if exist "%CSGO_DIR%\console.log" findstr /c:"SPIKE_DONE" "%CSGO_DIR%\console.log" >nul 2>&1 && goto done
timeout /t 10 /nobreak >nul
set /a waited+=10
if %waited% lss 1200 goto wait
echo [TIMEOUT] no SPIKE_DONE marker — inspect %CSGO_DIR%\console.log
exit /b 1

:done
echo.
echo [OK] SPIKE_DONE seen after %waited%s. Clips: check the output_dir in the cfg.
findstr /c:"CLIP_DONE" "%CSGO_DIR%\console.log"
exit /b 0
