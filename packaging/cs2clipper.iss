; Builds dist\CS2Clipper-Setup.exe, the installer, from dist\CS2Clipper.exe. Inno Setup compiles it;
; packaging\build.ps1 -Installer runs that.
;
; The installer only puts the app on the PC: the exe under the user's own Programs folder, which takes no
; admin prompt, a Start Menu shortcut, and a sign-in shortcut that starts the app in the tray. What the
; app needs to render (CS Demo Manager, Postgres, FFmpeg, HLAE) is installed by the app itself, from its
; Status page or with `CS2Clipper.exe setup`. Uninstalling removes the exe and the shortcuts and leaves
; %LOCALAPPDATA%\CS2Clipper, the user's settings, database and tools, alone.
;
; Adapted from thelifeofsuleyman/cs2-clipper's `packaging/aegis.iss` (MIT). What we changed: one exe
; instead of a folder, an install for this user only with no choice of folder, the version read from the
; exe, no WebView2 bootstrapper, the sign-in shortcut starts in the tray and is ticked by default, a
; shortcut whose box is unticked on an upgrade is removed, the running copy is asked to quit before its
; exe is replaced or removed, and no relaunch after a silent install.
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

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "--background"; Tasks: startup

[InstallDelete]
; An upgrade remembers the boxes ticked last time: a shortcut whose box is unticked now has to go.
Type: files; Name: "{autodesktop}\{#AppName}.lnk"; Tasks: not desktopicon
Type: files; Name: "{userstartup}\{#AppName}.lnk"; Tasks: not startup

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

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

function InitializeUninstall(): Boolean;
begin
  QuitTheRunningCopy();
  Result := True;
end;
