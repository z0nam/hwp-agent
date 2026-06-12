; Inno Setup script — hwp-agent Windows installer.
; Bundles hwp-agent.exe + a private Temurin JRE so .hwp conversion works with
; ZERO separate Java install. Per-user install (no admin needed).
;
; The CI workflow stages files under installer\payload\ (hwp-agent.exe + jre\)
; and runs:  ISCC.exe installer\hwp-agent.iss

#define AppName "HWP 폼 채우기 (hwp-agent)"
#define AppVer "0.1.0"

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

[Code]
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

[Registry]
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; \
  ValueData: "{olddata};{app}"; \
  Tasks: addtopath; Check: NeedsAddPath(ExpandConstant('{app}'))
