; Inno Setup script — hwp-agent Windows installer.
; Bundles hwp-agent.exe + a private Temurin JRE so .hwp conversion works with
; ZERO separate Java install, and auto-registers the local stdio MCP server in
; selected AI clients (Claude Desktop, Claude Code, Codex, Gemini CLI).
; Per-user install (no admin needed).
;
; The CI workflow stages files under installer\payload\ (hwp-agent.exe + jre\)
; and runs:  ISCC.exe installer\hwp-agent.iss

#define AppName "HWP 폼 채우기 (hwp-agent)"
; AppVer 는 릴리스 빌드(windows-exe)가 태그 값으로 바꿔 찍는다
#define AppVer "0.0.0"

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
    FileExists(ExpandConstant('{%USERPROFILE}\.claude.json')) or
    DirExists(ExpandConstant('{%USERPROFILE}\.claude')) or
    FileExists(ExpandConstant('{%USERPROFILE}\.local\bin\claude.exe')) or
    FileExists(ExpandConstant('{userappdata}\npm\claude.cmd'));
end;

function IsCodexInstalled(): boolean;
begin
  Result :=
    DirExists(ExpandConstant('{%USERPROFILE}\.codex')) or
    FileExists(ExpandConstant('{userappdata}\npm\codex.cmd'));
end;

// jaix(연구원 PC 설치기)가 있으면 Claude Code·Codex 의 hwp-agent 는 jaix 가 플러그인·uvx 로 연결한다.
// 여기서 그 둘을 등록하면 jaix 의 Codex [mcp_servers.hwp-agent] 를 이 exe 경로로 덮어쓰고, 제거할 때
// (command 가 이 exe 라서) 그 항목까지 지운다 — 그래서 jaix 가 보이면 둘을 기본으로 끈다 (#85).
function IsJaixInstalled(): boolean;
begin
  Result :=
    DirExists(ExpandConstant('{localappdata}\jaix')) or
    (FileSearch('jaix.exe', GetEnv('PATH')) <> '');
end;

function IsGeminiInstalled(): boolean;
begin
  Result :=
    DirExists(ExpandConstant('{%USERPROFILE}\.gemini')) or
    FileExists(ExpandConstant('{userappdata}\npm\gemini.cmd'));
end;

procedure InitializeWizard();
var
  Jaix: boolean;
  Intro: string;
begin
  Jaix := IsJaixInstalled();
  Intro := '이 PC에 설치된 AI가 자동으로 체크되었습니다. 체크한 앱의 설정 파일에 hwp-agent 도구를 자동 등록합니다 (기존 설정은 유지됩니다).';
  if Jaix then
    Intro := Intro + #13#10 + #13#10 +
      'jaix가 설치된 PC라 Claude Code·Codex 체크를 껐습니다 — 이 둘은 jaix가 연결하며, 여기서 체크하면 jaix 설정을 이 설치본으로 덮어씁니다.';
  AiClientPage := CreateInputOptionPage(
    wpSelectTasks,
    'AI 클라이언트 연결 (MCP 자동 등록)',
    '어떤 AI에서 hwp-agent를 쓰시나요?',
    Intro,
    False,
    False
  );
  AiClientPage.Add('Claude Desktop (권장 — 채팅창에서 바로 사용)');
  AiClientPage.Add('Claude Code (터미널/IDE)');
  AiClientPage.Add('Codex CLI');
  AiClientPage.Add('Gemini CLI');

  AiClientPage.Values[0] := IsClaudeDesktopInstalled();
  AiClientPage.Values[1] := IsClaudeCodeInstalled() and not Jaix;
  AiClientPage.Values[2] := IsCodexInstalled() and not Jaix;
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

// ── 제거 ─────────────────────────────────────────────────────────────────
// {app}(= %LOCALAPPDATA%\hwp-agent)은 jaix 와 hwp-agent setup 이 받은 hwp2hwpx jar·rhwp 도 담는 폴더다.
// 그래서 폴더를 통째로 지우지 않는다: Inno 는 자기가 깐 파일만 지우고 폴더는 비었을 때만 지운다.
// 아래 정리도 이 exe 하나와 빈 폴더만 다룬다 (tests/test_installer_iss.py 가 지킨다).

// Code 탭 등이 MCP 로 띄운 이 경로의 hwp-agent.exe 만 끝낸다. jaix 가 uvx 로 띄운 hwp-agent.exe 는
// 경로가 달라 건드리지 않는다. 안 끝내면 exe 를 못 지워 "일부 요소를 제거할 수 없습니다" 가 뜬다.
procedure StopOwnProcesses();
var
  ResultCode: Integer;
  Exe: string;
begin
  Exe := ExpandConstant('{app}\hwp-agent.exe');
  StringChangeEx(Exe, '''', '''''', True);  // PowerShell 작은따옴표 문자열 안이라 ' 를 겹친다
  // Win32_Process.ExecutablePath 는 32/64비트 어느 쪽 프로세스에서 봐도 채워진다(Get-Process .Path 는 아님)
  Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -NonInteractive -Command "Get-CimInstance Win32_Process | ' +
    'Where-Object { $_.Name -eq ''hwp-agent.exe'' -and $_.ExecutablePath -eq ''' + Exe + ''' } | ' +
    'ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; ' +
    'Start-Sleep -Milliseconds 500"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

// 사용자 PATH 에서 {app} 를 뺀다. 설치 때 [Registry] 로 넣기만 하고 빼는 쪽이 없었다.
procedure RemoveFromUserPath(Dir: string);
var
  OrigPath, P, Target: string;
  I: Integer;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', OrigPath) then
    exit;
  P := ';' + OrigPath + ';';
  Target := ';' + Uppercase(Dir) + ';';
  I := Pos(Target, Uppercase(P));
  while I > 0 do
  begin
    Delete(P, I, Length(Target) - 1);
    I := Pos(Target, Uppercase(P));
  end;
  P := Copy(P, 2, Length(P) - 2);
  if P <> OrigPath then
    RegWriteExpandStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', P);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Exe, AppDir: string;
begin
  if CurUninstallStep = usUninstall then
    StopOwnProcesses();
  if CurUninstallStep = usPostUninstall then
  begin
    AppDir := ExpandConstant('{app}');
    Exe := AppDir + '\hwp-agent.exe';
    RemoveFromUserPath(AppDir);
    // 그래도 exe 가 잠겨 남았으면 다음 로그온 때 지운다. 사용자 범위 설치라 재부팅 삭제
    // (uninsrestartdelete, 관리자 필요)를 못 쓴다. rmdir 은 /s 없이 — 폴더가 비었을 때만 지워진다.
    if FileExists(Exe) then
      RegWriteStringValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\RunOnce',
        'hwp-agent-uninstall',
        ExpandConstant('{cmd}') + ' /c del /f /q "' + Exe + '" & rmdir "' + AppDir + '"');
  end;
end;

[Registry]
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; \
  ValueData: "{olddata};{app}"; \
  Tasks: addtopath; Check: NeedsAddPath(ExpandConstant('{app}'))
