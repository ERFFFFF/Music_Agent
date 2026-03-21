; Music Agent - Inno Setup Installer Script
; Compile with: ISCC.exe installer\installer.iss (from repo root)
; Requires: PyInstaller build output in dist\Music Agent\

#define MyAppName "Music Agent"
#define MyAppVersion "2.0.0"
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
; .env is created by the installer script, not shipped in [Files]
Type: files; Name: "{app}\.env"

[Code]
var
  CredentialsPage: TInputQueryWizardPage;

function VerifySpotifyCredentials(ClientID, ClientSecret: String): Boolean;
var
  WinHttpReq: Variant;
  StatusCode: Integer;
  PostData: String;
begin
  Result := False;
  try
    WinHttpReq := CreateOleObject('WinHttp.WinHttpRequest.5.1');
    WinHttpReq.Open('POST', 'https://accounts.spotify.com/api/token', False);
    WinHttpReq.SetRequestHeader('Content-Type', 'application/x-www-form-urlencoded');

    PostData := 'grant_type=client_credentials'
      + '&client_id=' + ClientID
      + '&client_secret=' + ClientSecret;

    WinHttpReq.Send(PostData);
    StatusCode := WinHttpReq.Status;
    Result := (StatusCode = 200);
  except
    Result := False;
  end;
end;

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

procedure InitializeWizard();
begin
  { Create custom page after directory selection }
  CredentialsPage := CreateInputQueryPage(
    wpSelectDir,
    'Spotify API Credentials',
    'Enter your Spotify Developer application credentials.',
    'Create a Spotify app at https://developer.spotify.com/dashboard/' + #13#10 +
    'then copy your Client ID and Client Secret from the app settings.' + #13#10 + #13#10 +
    'Your credentials will be verified with Spotify before continuing.'
  );

  CredentialsPage.Add('Spotify Client ID:', False);
  CredentialsPage.Add('Spotify Client Secret:', False);
end;

procedure CurPageChanged(CurPageID: Integer);
var
  Lines: TArrayOfString;
  i: Integer;
  Line: String;
  EnvPath: String;
begin
  { Pre-populate credentials from existing .env when arriving at the credentials page }
  if CurPageID = CredentialsPage.ID then
  begin
    EnvPath := ExpandConstant('{app}\.env');
    if FileExists(EnvPath) then
    begin
      if LoadStringsFromFile(EnvPath, Lines) then
      begin
        for i := 0 to GetArrayLength(Lines) - 1 do
        begin
          Line := Lines[i];
          if Pos('SPOTIFY_CLIENT_ID=', Line) = 1 then
            CredentialsPage.Values[0] := Copy(Line, Length('SPOTIFY_CLIENT_ID=') + 1, MaxInt);
          if Pos('SPOTIFY_CLIENT_SECRET=', Line) = 1 then
            CredentialsPage.Values[1] := Copy(Line, Length('SPOTIFY_CLIENT_SECRET=') + 1, MaxInt);
        end;
      end;
    end;
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  ClientID, ClientSecret: String;
begin
  Result := True;

  if CurPageID = CredentialsPage.ID then
  begin
    ClientID := Trim(CredentialsPage.Values[0]);
    ClientSecret := Trim(CredentialsPage.Values[1]);

    if ClientID = '' then
    begin
      MsgBox('Please enter your Spotify Client ID.', mbError, MB_OK);
      Result := False;
      Exit;
    end;

    if ClientSecret = '' then
    begin
      MsgBox('Please enter your Spotify Client Secret.', mbError, MB_OK);
      Result := False;
      Exit;
    end;

    { Verify credentials with Spotify API }
    WizardForm.NextButton.Enabled := False;
    WizardForm.BackButton.Enabled := False;
    try
      if not VerifySpotifyCredentials(ClientID, ClientSecret) then
      begin
        MsgBox(
          'Invalid Spotify credentials.' + #13#10 + #13#10 +
          'Please check your Client ID and Client Secret ' +
          'on the Spotify Developer Dashboard and try again.',
          mbError, MB_OK
        );
        Result := False;
      end;
    finally
      WizardForm.NextButton.Enabled := True;
      WizardForm.BackButton.Enabled := True;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  EnvFile: String;
  EnvContent: String;
begin
  if CurStep = ssPostInstall then
  begin
    EnvFile := ExpandConstant('{app}\.env');
    EnvContent :=
      'SPOTIFY_CLIENT_ID=' + Trim(CredentialsPage.Values[0]) + #13#10 +
      'SPOTIFY_CLIENT_SECRET=' + Trim(CredentialsPage.Values[1]) + #13#10 +
      'SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback' + #13#10;

    if not SaveStringToFile(EnvFile, EnvContent, False) then
    begin
      MsgBox('Failed to write .env configuration file.' + #13#10 +
             'You may need to create it manually in:' + #13#10 +
             ExpandConstant('{app}'),
             mbError, MB_OK);
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
        '(OAuth tokens, logs, cached data)' + #13#10 + #13#10 +
        'Location: ' + DataDir,
        mbConfirmation, MB_YESNO
      ) = IDYES then
      begin
        DelTree(DataDir, True, True, True);
      end;
    end;
  end;
end;
