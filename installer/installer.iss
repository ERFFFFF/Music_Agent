; Music Agent - Inno Setup Installer Script
; Compile with: ISCC.exe installer\installer.iss (from repo root)
; Requires: PyInstaller build output in dist\Music Agent\

#define MyAppName "Music Agent"
#define MyAppVersion "4.0.0"
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

[UninstallDelete]
; Legacy: older versions of this installer wrote a .env here. The app now keeps its own next to the
; exe when writable, else in AppData (which the uninstall step below offers to clear).
Type: files; Name: "{app}\.env"

[Code]
{ There is deliberately NO Spotify credentials page here any more.

  The app supports two accounts — a Cadence sign-in or a Spotify Client ID + Secret — and asks which
  one you want on first launch (login_ui.choose_mode). Collecting Spotify credentials at install time
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
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\MusicAgent');
    if DirExists(DataDir) then
    begin
      if MsgBox(
        'Do you want to remove Music Agent application data?' + #13#10 +
        '(saved sign-in, Spotify credentials, OAuth tokens, logs)' + #13#10 + #13#10 +
        'Location: ' + DataDir,
        mbConfirmation, MB_YESNO
      ) = IDYES then
      begin
        DelTree(DataDir, True, True, True);
      end;
    end;
  end;
end;
