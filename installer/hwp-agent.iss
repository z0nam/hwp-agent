; Inno Setup script — hwp-agent Windows installer.
; Bundles hwp-agent.exe + a private Temurin JRE so .hwp conversion works with
; ZERO separate Java install, and auto-registers the local stdio MCP server in
; selected AI clients (Claude Desktop, Claude Code, Codex, Gemini CLI).
; Per-user install (no admin needed).
;
; The CI workflow stages files under installer\payload\ (hwp-agent.exe + jre\)
; and runs:  ISCC.exe installer\hwp-agent.iss

#define AppName "HWP 폼 채우기 (hwp-agent)"
#define AppVer "0.2.0"

[Setup]
AppId={{8E0C2C9A-3B7E-4E2A-9C2E-1A2B3C4D5E6F}
AppName={#AppName}
AppVersion={#AppVer}
AppPublisher=z0nam
DefaultDirName={localappdata}\hwp-agent
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64
OutputDir=Output
OutputBaseFilename=hwp-agent-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ChangesEnvironment=yes
UninstallDisplayName={#AppName}

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "addtopath"; Description: "PATH에 추가 (어디서나 hwp-agent 명령 사용)"; GroupDescription: "옵션:"

[Files]
Source: "payload\hwp-agent.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "payload\jre\*"; DestDir: "{app}\jre"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\hwp-agent 명령창"; Filename: "{cmd}"; Parameters: "/K cd /d ""{app}"""; Comment: "이 폴더에서 hwp-agent.exe 실행"
Name: "{autoprograms}\hwp-agent 폴더 열기"; Filename: "{app}"

[Run]
Filename: "{app}\hwp-agent.exe"; Parameters: "--version"; Flags: runhidden nowait skipifsilent

[UninstallRun]
Filename: "{app}\hwp-agent.exe"; Parameters: "setup --unregister all --matching-command ""{app}\hwp-agent.exe"""; Flags: runhidden; RunOnceId: "UnregisterMcp"

[Code]
var
  AiClientPage: TInputOptionWizardPage;
  AiGuidePage: TOutputMsgMemoWizardPage;

function NeedsAddPath(Param: string): boolean;
var
  OrigPath: string;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', OrigPath) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + Uppercase(Param) + ';', ';' + Uppercase(OrigPath) + ';') = 0;
end;

function IsClaudeDesktopInstalled(): boolean;
begin
  Result :=
    DirExists(ExpandConstant('{userappdata}\Claude')) or
    DirExists(ExpandConstant('{localappdata}\AnthropicClaude')) or
    DirExists(ExpandConstant('{localappdata}\Programs\Claude'));
end;

function IsClaudeCodeInstalled(): boolean;
begin
  Result :=
    FileExists(ExpandConstant('{userprofile}\.claude.json')) or
    DirExists(ExpandConstant('{userprofile}\.claude')) or
    FileExists(ExpandConstant('{userprofile}\.local\bin\claude.exe')) or
    FileExists(ExpandConstant('{userappdata}\npm\claude.cmd'));
end;

function IsCodexInstalled(): boolean;
begin
  Result :=
    DirExists(ExpandConstant('{userprofile}\.codex')) or
    FileExists(ExpandConstant('{userappdata}\npm\codex.cmd'));
end;

function IsGeminiInstalled(): boolean;
begin
  Result :=
    DirExists(ExpandConstant('{userprofile}\.gemini')) or
    FileExists(ExpandConstant('{userappdata}\npm\gemini.cmd'));
end;

procedure InitializeWizard();
begin
  AiClientPage := CreateInputOptionPage(
    wpSelectTasks,
    'AI 클라이언트 연결 (MCP 자동 등록)',
    '어떤 AI에서 hwp-agent를 쓰시나요?',
    '이 PC에 설치된 AI가 자동으로 체크되었습니다. 체크한 앱의 설정 파일에 hwp-agent 도구를 자동 등록합니다 (기존 설정은 유지됩니다).',
    False,
    False
  );
  AiClientPage.Add('Claude Desktop (권장 — 채팅창에서 바로 사용)');
  AiClientPage.Add('Claude Code (터미널/IDE)');
  AiClientPage.Add('Codex CLI');
  AiClientPage.Add('Gemini CLI');

  AiClientPage.Values[0] := IsClaudeDesktopInstalled();
  AiClientPage.Values[1] := IsClaudeCodeInstalled();
  AiClientPage.Values[2] := IsCodexInstalled();
  AiClientPage.Values[3] := IsGeminiInstalled();

  AiGuidePage := CreateOutputMsgMemoPage(
    wpInstalling,
    'AI 클라이언트 연결 안내',
    '이렇게 쓰세요',
    '설치 및 선택한 AI 클라이언트 설정이 완료되었습니다.',
    ''
  );
end;

function SelectedClientsCsv(): string;
var
  Items: array[0..3] of string;
  I: Integer;
begin
  Items[0] := 'claude-desktop';
  Items[1] := 'claude-code';
  Items[2] := 'codex';
  Items[3] := 'gemini';
  Result := '';
  for I := 0 to 3 do
  begin
    if AiClientPage.Values[I] then
    begin
      if Result <> '' then
        Result := Result + ',';
      Result := Result + Items[I];
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ClientsCsv: string;
  ExePath: string;
  GuideFile: string;
  Params: string;
  ResultCode: Integer;
  GuideLines: TArrayOfString;
  I: Integer;
  GuideText: string;
begin
  if CurStep = ssPostInstall then
  begin
    ClientsCsv := SelectedClientsCsv();
    if ClientsCsv = '' then
    begin
      AiGuidePage.RichEditViewer.Lines.Text :=
        '선택된 AI 클라이언트가 없어 MCP 자동 등록을 건너뛰었습니다.' + #13#10 + #13#10 +
        '나중에 연결하려면 명령창에서 다음을 실행하세요:' + #13#10 +
        '  hwp-agent setup --register claude,codex,gemini' + #13#10 + #13#10 +
        'ChatGPT · Gemini 웹/앱 사용자는 웹 링크(https://hwp.namun.net)를 이용하세요.';
      exit;
    end;

    ExePath := ExpandConstant('{app}\hwp-agent.exe');
    GuideFile := ExpandConstant('{tmp}\hwp-agent-mcp-guide.txt');
    Params :=
      'setup --register ' + ClientsCsv +
      ' --command "' + ExePath + '"' +
      ' --guide-out "' + GuideFile + '"';

    if Exec(ExePath, Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and
       FileExists(GuideFile) and
       LoadStringsFromFile(GuideFile, GuideLines) then
    begin
      GuideText := '';
      for I := 0 to GetArrayLength(GuideLines) - 1 do
      begin
        if I > 0 then
          GuideText := GuideText + #13#10;
        GuideText := GuideText + GuideLines[I];
      end;
      AiGuidePage.RichEditViewer.Lines.Text := GuideText;
    end
    else
    begin
      AiGuidePage.RichEditViewer.Lines.Text :=
        '경고: MCP 설정 자동 등록 중 오류가 발생했습니다 (코드: ' + IntToStr(ResultCode) + ').' + #13#10 +
        '명령창에서 직접 실행해 주세요:' + #13#10 +
        '  hwp-agent setup --register ' + ClientsCsv;
    end;
  end;
end;

[Registry]
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; \
  ValueData: "{olddata};{app}"; \
  Tasks: addtopath; Check: NeedsAddPath(ExpandConstant('{app}'))
