; Inno Setup script for Pipevoice.
; Build Pipevoice.exe first (run build_exe.bat), then compile this with Inno Setup
; (ISCC.exe installer\Pipevoice.iss, or open in the Inno Setup IDE).
; Produces installer\Output\Pipevoice-Setup.exe — a per-user install (no admin).

#define AppName "Pipevoice"
; Overridden by CI: ISCC /DAppVersion=<version read from wisprlite/__init__.py>.
; The literal below is only a local-build fallback. It was the ONLY value for a
; long time, so every installer from 2.25.0 onward reported 2.25.0 in Add/Remove
; Programs no matter what version it actually contained - which also means winget
; could never tell an upgrade was needed.
#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif
#define AppExe "Pipevoice.exe"

[Setup]
AppId={{41C3C77C-2125-40AF-AE40-5AAC67809491}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Pipevoice
; What Add/Remove Programs shows, and what winget compares against to decide
; whether an upgrade is available. Without it ARP falls back to AppVersion only.
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename={#AppName}-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
WizardImageFile=wizard-image.bmp
WizardSmallImageFile=wizard-small.bmp
CloseApplications=yes
RestartApplications=yes
SetupIconFile=..\assets\wisprlite.ico
UninstallDisplayIcon={app}\{#AppExe}

[Files]
; onedir build: bundle the whole PyInstaller folder (exe + _internal/ DLLs).
; This avoids the onefile _MEI runtime extraction that broke updates with
; "Failed to load Python DLL".
Source: "..\dist\Pipevoice\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\.env.example"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
; NO stub .env here. Seeding one from .env.example gave every install a SECOND
; key store next to the exe whose DEEPGRAM_API_KEY= was blank, and a blank read
; as "already set" — so it silently masked the real key in %APPDATA%. Keys are
; entered in the app (Settings > API keys) and live in {userappdata}\Pipevoice\.env.
; An existing {app}\.env is left alone and is still read, so nobody loses a key.

[Dirs]
; The app creates this on first run, but the "Edit API keys" shortcut below can
; be clicked before the app has ever started — Notepad cannot save into a folder
; that does not exist.
Name: "{userappdata}\{#AppName}"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
; Point at the store the app actually writes to. This used to open {app}\.env,
; so a key typed here and a key typed in Settings landed in different files.
Name: "{group}\Edit API keys (.env)"; Filename: "notepad.exe"; Parameters: """{userappdata}\{#AppName}\.env"""
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: startup

[Tasks]
Name: "startup"; Description: "Start {#AppName} automatically when I log in"; GroupDescription: "Startup:"; Flags: unchecked

[Run]
; Interactive install: optional "Launch Pipevoice" checkbox on the final page.
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
; Silent self-update (/VERYSILENT from the in-app updater): relaunch the app
; ourselves. Restart Manager's RESTARTAPPLICATIONS is unreliable after a forced
; close, and the postinstall entry above is skipped when silent, so without this
; the update could finish with no app running. The single-instance lock makes any
; overlap with RESTARTAPPLICATIONS safe (the second launch just exits).
Filename: "{app}\{#AppExe}"; Flags: nowait; Check: WizardSilent

[UninstallDelete]
Type: filesandordirs; Name: "{userappdata}\{#AppName}"
