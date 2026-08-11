; Music Agent - Inno Setup Installer Script
; Compile with: ISCC.exe installer\installer.iss (from repo root)
; Requires: PyInstaller build output in dist\Music Agent\

#define MyAppName "Music Agent"
; The version comes from pyproject.toml, passed in by build_installer.ps1 as /DMyAppVersion=x.y.z.
; Typed in both places it had already drifted, and the installer announced a version that no longer
; matched what it contained. Compiling this file by hand instead says so, loudly, rather than lying.
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif
#define MyAppPublisher "erfffff"
#define MyAppURL "https://github.com/ERFFFFF/Music_Agent"
#define MyAppExeName "Music Agent.exe"
#define MyAppId "{{E7F3A1B2-4C5D-6E7F-8A9B-0C1D2E3F4A5B}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
CloseApplications=no
OutputDir=installer_output
OutputBaseFilename=MusicAgentSetup
SetupIconFile=poulet.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
InfoBeforeFile=installer\INSTALL_INFO.txt
SourceDir=..

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "startup"; Description: "Start Music Agent on Windows login"; GroupDescription: "Additional options:"

[Files]
; Main exe
Source: "dist\{#MyAppName}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; Entire _internal directory
Source: "dist\{#MyAppName}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
; Icon
Source: "poulet.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Start Menu
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\poulet.ico"; WorkingDir: "{app}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
; Startup shortcut (only if task selected)
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\poulet.ico"; WorkingDir: "{app}"; Tasks: startup

[Registry]
Root: HKCU; Subkey: "Software\Classes\AppUserModelIDs\com.erfffff.musicagent"; ValueType: string; ValueName: ""; ValueData: "{#MyAppName}"; Flags: uninsdeletekey

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

; There is deliberately no [UninstallDelete] for {app}\.env any more. It used to be listed here
; because an ancient installer WROTE that file; today it is the operator's own — hand-edited, holding
; the server address and account — and this section runs on every UPGRADE too (InitializeSetup below
; uninstalls the old version first). So an upgrade silently deleted the file the app is configured
; from. It is offered with the rest of the data below instead, where there is a question attached.

[Code]
{ There is deliberately NO Spotify credentials page here any more.

  The app supports two accounts — a Cadence sign-in or a Spotify Client ID + Secret — and asks which
  one you want on first launch (ui/login.py choose_mode). Collecting Spotify credentials at install time
  forced a choice the user may not want, and left a Cadence user unable to finish setup at all. The
  app writes its own .env when you pick Spotify, so this installer now only installs files. }

function InitializeSetup(): Boolean;
var
  UninstallKey: String;
  UninstallString: String;
  ResultCode: Integer;
begin
  Result := True;

  { Kill running Music Agent process }
  Exec('taskkill', '/F /IM "Music Agent.exe"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);

  { Check if already installed — uninstall first }
  UninstallKey := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppId}_is1';
  if RegQueryStringValue(HKCU, UninstallKey, 'UninstallString', UninstallString) then
  begin
    { Run the uninstaller silently }
    UninstallString := RemoveQuotes(UninstallString);
    if FileExists(UninstallString) then
    begin
      Exec(UninstallString, '/VERYSILENT /NORESTART /SUPPRESSMSGBOXES', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
  EnvFile: String;
  ConfigFile: String;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;

  { An UPGRADE gets here too: InitializeSetup runs the old uninstaller with /VERYSILENT. Asking then
    is wrong twice over — /SUPPRESSMSGBOXES does not suppress a plain MsgBox, so the install stalls
    behind a hidden dialog, and answering Yes wipes the settings of an app that is being kept. Nobody
    uninstalling silently is there to be asked, so silent means "leave the data alone". }
  if UninstallSilent then
    Exit;

  // Both places the app keeps things: beside the exe when that folder is writable (config.data_dir),
  // and AppData otherwise. An installed copy under %LOCALAPPDATA%\Programs is writable, so the
  // config normally sits in the app folder -- left behind, it is an encrypted session nobody can use
  // and a folder that survives the uninstall for no reason.
  // (// and not { }, because a brace comment ends at the first closing brace, and an app-dir constant
  //  written in one would end it in the middle of a sentence. It did, and the compile failed here.)
  DataDir := ExpandConstant('{localappdata}\MusicAgent');
  EnvFile := ExpandConstant('{app}\.env');
  ConfigFile := ExpandConstant('{app}\cadence_config.txt');
  if (not DirExists(DataDir)) and (not FileExists(EnvFile)) and (not FileExists(ConfigFile)) then
    Exit;

  if MsgBox(
    'Do you want to remove Music Agent settings and credentials?' + #13#10 +
    '(saved sign-in, Spotify credentials, OAuth tokens, and your .env if you wrote one)' + #13#10 + #13#10 +
    'Locations:' + #13#10 + DataDir + #13#10 + EnvFile,
    mbConfirmation, MB_YESNO
  ) = IDYES then
  begin
    if DirExists(DataDir) then
      DelTree(DataDir, True, True, True);
    DeleteFile(EnvFile);
    DeleteFile(ConfigFile);
  end;
end;
