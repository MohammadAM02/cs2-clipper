; Builds dist\CS2Clipper-Setup.exe, the installer, from dist\CS2Clipper.exe. Inno Setup compiles it;
; packaging\build.ps1 -Installer runs that.
;
; The installer only puts the app on the PC: the exe under the user's own Programs folder, which takes no
; admin prompt, a Start Menu shortcut, and a sign-in shortcut that starts the app in the tray. What the
; app needs to analyze and render (csda, FFmpeg, HLAE, about 122 MB) the app downloads by itself when it
; first opens, as the Ready and Finished pages say (or `CS2Clipper.exe setup`). The Start Menu shortcut
; carries the app's AppUserModelID, so its toasts say CS2 Clipper. Uninstalling removes the exe, the
; shortcuts and what the app registered for itself, and leaves %LOCALAPPDATA%\CS2Clipper, the user's
; settings, index, analyses and tools, alone.
;
; Adapted from thelifeofsuleyman/cs2-clipper's `packaging/aegis.iss` (MIT). What we changed: one exe
; instead of a folder, an install for this user only with no choice of folder, the version read from the
; exe, no WebView2 bootstrapper, the sign-in shortcut starts in the tray and is ticked by default, a
; shortcut whose box is unticked on an upgrade is removed, the running copy is asked to quit before its
; exe is replaced or removed, and no relaunch after a silent install unless /RELAUNCH asks for one.
;
; MIT License
;
; Copyright (c) 2026 thelifeofsuleyman
;
; Permission is hereby granted, free of charge, to any person obtaining a copy
; of this software and associated documentation files (the "Software"), to deal
; in the Software without restriction, including without limitation the rights
; to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
; copies of the Software, and to permit persons to whom the Software is
; furnished to do so, subject to the following conditions:
;
; The above copyright notice and this permission notice shall be included in all
; copies or substantial portions of the Software.
;
; THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
; IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
; FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
; AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
; LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
; OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
; SOFTWARE.

#define AppName "CS2 Clipper"
#define AppExe "CS2Clipper.exe"
; clipper.notify.APP_ID: the id the app's toasts and its window go by.
#define AppUserModelID "CS2Clipper.App"
; The version is the exe's own, which the build stamps on it from pyproject.toml.
#define AppVersion GetStringFileInfo(AddBackslash(SourcePath) + "..\dist\" + AppExe, "ProductVersion")
#if AppVersion == ""
  #error dist\CS2Clipper.exe is missing or has no version. Build it first: packaging\build.ps1 -Installer
#endif

[Setup]
; Windows knows the app by this id: an installer with another one installs a second copy, not an upgrade.
AppId={{03EF2ADC-14C4-4DA0-8A95-52187AA0BF34}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppName}
VersionInfoVersion={#AppVersion}
PrivilegesRequired=lowest
DefaultDirName={autopf}\CS2Clipper
DisableDirPage=yes
DisableProgramGroupPage=yes
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
SetupIconFile=..\build\CS2Clipper.ico
OutputDir=..\dist
OutputBaseFilename=CS2Clipper-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; The fallback when the running copy does not answer `quit` (see [Code]).
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "startup"; Description: "Start {#AppName} when I sign in to Windows"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion

[Messages]
; Said before anything is installed, and again at the end: the first start downloads the tools.
ReadyLabel2a=Click Install to install CS2 Clipper.%n%nWhen it first opens, CS2 Clipper downloads the tools it records with by itself: csda, FFmpeg and HLAE, about 122 MB. It never runs while CS2 is open.
FinishedLabel=CS2 Clipper is installed.%n%nWhen it first opens, it downloads the tools it records with by itself: csda, FFmpeg and HLAE, about 122 MB.

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "{#AppUserModelID}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "{#AppUserModelID}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "--background"; Tasks: startup

[Registry]
; The app registers these itself at every start (clipper.links, clipper.notify): the cs2clipper: links its toast
; buttons open, and the name and mark its toasts show. Listed here so that uninstalling takes them away.
Root: HKCU; Subkey: "Software\Classes\cs2clipper"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\AppUserModelId\{#AppUserModelID}"; Flags: uninsdeletekey

[InstallDelete]
; An upgrade remembers the boxes ticked last time: a shortcut whose box is unticked now has to go.
Type: files; Name: "{autodesktop}\{#AppName}.lnk"; Tasks: not desktopicon
Type: files; Name: "{userstartup}\{#AppName}.lnk"; Tasks: not startup

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; The app's own update runs this installer silently with /RELAUNCH (clipper.update): the app quit for it, so
; it is started again.
Filename: "{app}\{#AppExe}"; Flags: nowait skipifnotsilent; Check: RelaunchAsked

[Code]
// An exe that is running cannot be replaced or removed, and the app lives in the tray with no window to
// close. Its own `quit` asks the running copy to quit and waits until it has (clipper.app.quit_running).
// The exe asked is the one that is installed already, so `quit` has to stay a command it knows.
procedure QuitTheRunningCopy();
var
  Exe: String;
  ResultCode: Integer;
begin
  Exe := ExpandConstant('{app}\{#AppExe}');
  if not FileExists(Exe) then
    exit;
  if Exec(Exe, 'quit', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    Log(Format('%s quit: exit code %d', [Exe, ResultCode]))
  else
    Log(Format('%s quit could not be started: %s', [Exe, SysErrorMessage(ResultCode)]));
end;

// Setup calls this before it looks for files that are in use.
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  QuitTheRunningCopy();
  Result := '';
end;

// Whether the command line has /RELAUNCH, which the app's own update passes.
function RelaunchAsked(): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), '/RELAUNCH') = 0 then
      Result := True;
end;

function InitializeUninstall(): Boolean;
begin
  QuitTheRunningCopy();
  Result := True;
end;
