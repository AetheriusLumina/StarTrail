#define AppVersion "0.4.0"

[Setup]
AppId=GitHubRadar
AppName=StarTrail
AppVersion={#AppVersion}
AppPublisher=StarTrail
DefaultDirName={%LOCALAPPDATA}\Programs\GitHubRadar
DefaultGroupName=StarTrail
OutputDir={#OutputPath}
OutputBaseFilename=StarTrail_Setup
PrivilegesRequired=lowest
UsePreviousAppDir=no
DisableDirPage=no
DisableProgramGroupPage=yes
UninstallFilesDir={app}\AppFiles
UninstallDisplayIcon={app}\GitHubRadar.exe
SetupIconFile={#SourcePath}\AppFiles\github_radar\web_assets\startrail.ico
WizardStyle=modern
LanguageDetectionMethod=none
CloseApplications=no
Compression=lzma2
SolidCompression=yes

[Languages]
Name: "zh"; MessagesFile: "ChineseSimplified.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
zh.KeepData=是否保留个人数据？选择“是”将保留关键词、关注、历史和设置；选择“否”将永久删除。
en.KeepData=Keep your personal data? Yes keeps keywords, follows, history and settings; No deletes them permanently.
zh.DeleteAgain=确定永久删除此安装目录中的 UserData 吗？此操作无法撤销。
en.DeleteAgain=Delete UserData in this installation permanently? This cannot be undone.
zh.UninstallStopped=卸载准备未完成，程序与个人数据仍保留。请查看具体原因后重试。
en.UninstallStopped=Uninstall preparation did not finish. The app and personal data remain. Check the cause and try again.
zh.DeleteFailed=个人数据未能完整删除，卸载已暂停。请检查 UserData 文件夹的权限，然后重试。
en.DeleteFailed=Personal data could not be fully removed. Uninstall paused; check UserData permissions and try again.
zh.UnsafeFolder=此位置不可用于安装。请选择当前用户可写的空文件夹，或原来的 StarTrail 安装位置。
en.UnsafeFolder=This location cannot be used. Choose an empty folder you can write to, or the existing StarTrail install location.
zh.ScheduleFailed=程序已安装，但每日更新任务未能注册。打开 StarTrail 后可在“设置”查看原因；“立即更新”仍可用。
en.ScheduleFailed=The app installed, but its daily update task could not be registered. Open Settings for details; Update Now still works.
zh.UpgradeStopped=升级准备未完成，个人数据未被覆盖。请查看具体原因后重试。
en.UpgradeStopped=Upgrade preparation did not finish. Your personal data was not overwritten. Check the cause and try again.
zh.UninstallLinkFailed=未能记录此安装的卸载入口。可从 Windows“已安装的应用”卸载；请检查安装文件夹的写入权限。
en.UninstallLinkFailed=Could not record this installation's uninstall entry. You can uninstall from Windows Installed apps; check write access to the install folder.
zh.ErrorDetails=具体原因请打开这个文本文件：
en.ErrorDetails=Open this text file for the specific cause:

[Dirs]
Name: "{app}\UserData"; Flags: uninsneveruninstall

[Files]
Source: "{#SourcePath}\GitHubRadar.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourcePath}\Uninstall.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourcePath}\使用说明.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourcePath}\UserData\.github-radar-data"; DestDir: "{app}\UserData"; Flags: onlyifdoesntexist uninsneveruninstall
Source: "{#SourcePath}\AppFiles\*"; DestDir: "{app}\AppFiles"; Flags: ignoreversion recursesubdirs createallsubdirs

[UninstallDelete]
Type: files; Name: "{app}\AppFiles\github-radar-uninstaller.txt"

[Icons]
Name: "{code:StartMenuShortcutPath}\StarTrail"; Filename: "{app}\GitHubRadar.exe"
Name: "{code:DesktopShortcutPath}\StarTrail"; Filename: "{app}\GitHubRadar.exe"

[Run]
Filename: "{app}\GitHubRadar.exe"; Description: "{cm:LaunchProgram,StarTrail}"; Flags: nowait postinstall skipifsilent

[Code]
var
  PreviousRoot: String;
  DeleteData: Boolean;
  MaintenanceHandle: Longint;
  MaintenancePath: String;

function OpenMaintenanceFile(Name: String; Access, Share: DWORD;
  Security: Longint; Creation, Attributes: DWORD; Template: Longint): Longint;
  external 'CreateFileW@kernel32.dll stdcall';
function LockMaintenanceFile(Handle: Longint; LowOffset, HighOffset,
  LowCount, HighCount: DWORD): BOOL;
  external 'LockFile@kernel32.dll stdcall';
function UnlockMaintenanceFile(Handle: Longint; LowOffset, HighOffset,
  LowCount, HighCount: DWORD): BOOL;
  external 'UnlockFile@kernel32.dll stdcall';
function CloseMaintenanceFile(Handle: Longint): BOOL;
  external 'CloseHandle@kernel32.dll stdcall';

procedure ReleaseMaintenance;
begin
  if MaintenanceHandle = 0 then Exit;
  UnlockMaintenanceFile(MaintenanceHandle, 0, 0, 1, 0);
  CloseMaintenanceFile(MaintenanceHandle);
  MaintenanceHandle := 0;
end;

function AcquireMaintenance(Root: String): Boolean;
begin
  Result := False;
  if not ForceDirectories(AddBackslash(Root) + 'AppFiles') then Exit;
  MaintenancePath := AddBackslash(Root) + 'AppFiles\.radar-maintenance.lock';
  MaintenanceHandle := OpenMaintenanceFile(MaintenancePath, $C0000000, 7,
    0, 4, $80, 0);
  if MaintenanceHandle = -1 then begin
    MaintenanceHandle := 0;
    Exit;
  end;
  if not LockMaintenanceFile(MaintenanceHandle, 0, 0, 1, 0) then begin
    CloseMaintenanceFile(MaintenanceHandle);
    MaintenanceHandle := 0;
    Exit;
  end;
  Result := True;
end;

procedure DeinitializeSetup;
begin
  ReleaseMaintenance;
end;

procedure DeinitializeUninstall;
begin
  ReleaseMaintenance;
end;

function ExpandUserFolder(Value: String): String;
begin
  StringChangeEx(Value, '%USERPROFILE%', GetEnv('USERPROFILE'), True);
  StringChangeEx(Value, '%APPDATA%', GetEnv('APPDATA'), True);
  Result := Value;
end;

function DesktopShortcutPath(Param: String): String;
begin
  if not RegQueryStringValue(HKCU,
     'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders',
     'Desktop', Result) then
    Result := GetEnv('USERPROFILE') + '\Desktop';
  Result := ExpandUserFolder(Result);
end;

function StartMenuShortcutPath(Param: String): String;
begin
  if not RegQueryStringValue(HKCU,
     'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders',
     'Programs', Result) then
    Result := GetEnv('APPDATA') + '\Microsoft\Windows\Start Menu\Programs';
  Result := ExpandUserFolder(Result);
end;

function IsWithin(Path, Parent: String): Boolean;
var
  P, Base: String;
begin
  P := Lowercase(ExpandFileName(Path));
  Base := Lowercase(AddBackslash(ExpandFileName(Parent)));
  Result := (P = Copy(Base, 1, Length(Base) - 1)) or
    (Copy(P, 1, Length(Base)) = Base);
end;

function HasUnrelatedFiles(Path: String): Boolean;
var
  Item: TFindRec;
begin
  Result := False;
  if not DirExists(Path) then Exit;
  if FindFirst(AddBackslash(Path) + '*', Item) then begin
    try
      repeat
        if (Item.Name <> '.') and (Item.Name <> '..') then begin
          if CompareText(Item.Name, 'UserData') = 0 then begin
            if not DirExists(AddBackslash(Path) + 'UserData') or
               not FileExists(AddBackslash(Path) + 'UserData\.github-radar-data') then begin
              Result := True;
              Exit;
            end;
          end else begin
            Result := True;
            Exit;
          end;
        end;
      until not FindNext(Item);
    finally
      FindClose(Item);
    end;
  end;
end;

function DestinationIsSafe(Path: String): Boolean;
var
  Root, Probe: String;
begin
  Result := False;
  Root := RemoveBackslashUnlessRoot(ExpandFileName(Path));
  if (Root = '') or (CompareText(Root, ExtractFileDrive(Root) + '\') = 0) then Exit;
  if IsWithin(Root, GetEnv('WINDIR')) or
     IsWithin(Root, GetEnv('ProgramFiles')) or
     IsWithin(Root, GetEnv('ProgramFiles(x86)')) or
     IsWithin(Root, GetEnv('ProgramData')) then Exit;
  if (PreviousRoot <> '') and
     (CompareText(Root, RemoveBackslashUnlessRoot(ExpandFileName(PreviousRoot))) <> 0) then Exit;
  if (PreviousRoot = '') and HasUnrelatedFiles(Root) then Exit;
  if not ForceDirectories(Root) then Exit;
  Probe := AddBackslash(Root) + 'github-radar-write-check.tmp';
  if FileExists(Probe) then Exit;
  if not SaveStringToFile(Probe, 'ok', False) then Exit;
  DeleteFile(Probe);
  Result := True;
end;

function WithErrorFile(MessageText, DataRoot: String): String;
var
  ErrorFile: String;
begin
  Result := MessageText;
  ErrorFile := AddBackslash(DataRoot) + 'uninstall-error.txt';
  if FileExists(ErrorFile) then
    Result := Result + #13#10#13#10 +
      ExpandConstant('{cm:ErrorDetails}') + #13#10 + ErrorFile;
end;

function IsNativeUninstallerName(Name: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  if (Length(Name) < 10) or
     (Copy(Lowercase(Name), 1, 5) <> 'unins') or
     (Copy(Lowercase(Name), Length(Name) - 3, 4) <> '.exe') then Exit;
  for I := 6 to Length(Name) - 4 do
    if (Name[I] < '0') or (Name[I] > '9') then Exit;
  Result := True;
end;

function HasRecordedUninstaller(Root: String): Boolean;
var
  Marker, Native: String;
  Name: AnsiString;
begin
  Result := False;
  Marker := AddBackslash(Root) + 'AppFiles\github-radar-uninstaller.txt';
  if FileExists(Marker) then begin
    if not LoadStringFromFile(Marker, Name) then Exit;
    Name := Trim(Name);
    if not IsNativeUninstallerName(Name) then Exit;
    Native := AddBackslash(Root) + 'AppFiles\' + Name;
    Result := FileExists(Native) and FileExists(ChangeFileExt(Native, '.dat'));
    Exit;
  end;
  Native := AddBackslash(Root) + 'AppFiles\unins000.exe';
  Result := FileExists(Native) and FileExists(ChangeFileExt(Native, '.dat'));
end;

procedure InitializeWizard;
var
  CandidateRoot: String;
begin
  if not RegQueryStringValue(HKCU,
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\GitHubRadar_is1',
    'Inno Setup: App Path', CandidateRoot) then
    RegQueryStringValue(HKCU,
      'Software\Microsoft\Windows\CurrentVersion\Uninstall\GitHubRadar_is1',
      'InstallLocation', CandidateRoot);
  if CandidateRoot <> '' then begin
    CandidateRoot := RemoveBackslashUnlessRoot(ExpandFileName(CandidateRoot));
    if FileExists(AddBackslash(CandidateRoot) + 'GitHubRadar.exe') and
       HasRecordedUninstaller(CandidateRoot) then
      PreviousRoot := CandidateRoot;
  end;
  if PreviousRoot <> '' then WizardForm.DirEdit.Text := PreviousRoot;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = wpSelectDir) and (PreviousRoot <> '');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpSelectDir) and not DestinationIsSafe(WizardDirValue) then begin
    MsgBox(ExpandConstant('{cm:UnsafeFolder}'), mbError, MB_OK);
    Result := False;
  end;
end;

function PrepareApplication(Root: String): Boolean;
var
  ExitCode: Integer;
  Command, Params: String;
begin
  Result := True;
  Command := AddBackslash(Root) + 'GitHubRadar.exe';
  if FileExists(Command) then begin
    Params := '--prepare-uninstall --data-dir "' +
      AddBackslash(Root) + 'UserData"';
    Result := Exec(Command, Params, Root, SW_HIDE,
                   ewWaitUntilTerminated, ExitCode);
    if Result then Result := ExitCode = 0;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  ReleaseMaintenance;
  Result := '';
  if not DestinationIsSafe(WizardDirValue) then begin
    Result := ExpandConstant('{cm:UnsafeFolder}');
    Exit;
  end;
  if not PrepareApplication(WizardDirValue) then begin
    Result := WithErrorFile(ExpandConstant('{cm:UpgradeStopped}'),
                            AddBackslash(WizardDirValue) + 'UserData');
    Exit;
  end;
  if not AcquireMaintenance(WizardDirValue) then begin
    Result := WithErrorFile(ExpandConstant('{cm:UpgradeStopped}'),
                            AddBackslash(WizardDirValue) + 'UserData');
    Exit;
  end;
  { A start admitted after the first task removal may have registered again.
    Once exclusively reserved, remove it again with new starts blocked. }
  if not PrepareApplication(WizardDirValue) then begin
    ReleaseMaintenance;
    Result := WithErrorFile(ExpandConstant('{cm:UpgradeStopped}'),
                            AddBackslash(WizardDirValue) + 'UserData');
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ExitCode: Integer;
  Command, Params, UninstallProgram, Marker: String;
begin
  if CurStep <> ssPostInstall then Exit;
  UninstallProgram := ExpandConstant('{uninstallexe}');
  Marker := ExpandConstant('{app}\AppFiles\github-radar-uninstaller.txt');
  if not SaveStringToFile(Marker, ExtractFileName(UninstallProgram), False) then
    RaiseException(ExpandConstant('{cm:UninstallLinkFailed}'));
  { All files are now complete. Runtime/task registration may resume. }
  ReleaseMaintenance;
  Command := ExpandConstant('{app}\GitHubRadar.exe');
  Params := '--sync-scheduler --data-dir "' +
    ExpandConstant('{app}\UserData') + '"';
  if (not Exec(Command, Params, ExpandConstant('{app}'), SW_HIDE,
               ewWaitUntilTerminated, ExitCode)) or (ExitCode <> 0) then
    MsgBox(ExpandConstant('{cm:ScheduleFailed}'), mbInformation, MB_OK);
end;

function InitializeUninstall: Boolean;
var
  Choice: Integer;
begin
  DeleteData := False;
  if UninstallSilent then begin
    Result := True;
    Exit;
  end;
  Choice := MsgBox(ExpandConstant('{cm:KeepData}'), mbConfirmation,
                   MB_YESNOCANCEL);
  if Choice = IDYES then begin
    Result := True;
    Exit;
  end;
  if Choice = IDNO then begin
    DeleteData := MsgBox(ExpandConstant('{cm:DeleteAgain}'), mbConfirmation,
                         MB_YESNO or MB_DEFBUTTON2) = IDYES;
    Result := DeleteData;
    Exit;
  end;
  Result := False;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Command, Params, DataRoot, AppRoot: String;
  ExitCode: Integer;
begin
  if CurUninstallStep = usPostUninstall then begin
    { Keep admission closed until executable/resource deletion is complete. }
    ReleaseMaintenance;
    if MaintenancePath <> '' then DeleteFile(MaintenancePath);
    RemoveDir(ExpandConstant('{app}\AppFiles'));
    Exit;
  end;
  if CurUninstallStep <> usUninstall then Exit;
  AppRoot := ExpandFileName(ExpandConstant('{app}'));
  DataRoot := ExpandFileName(ExpandConstant('{app}\UserData'));
  if CompareText(ExtractFilePath(DataRoot), AddBackslash(AppRoot)) <> 0 then begin
    MsgBox(ExpandConstant('{cm:UninstallStopped}'), mbError, MB_OK);
    Abort;
  end;
  Command := AddBackslash(AppRoot) + 'GitHubRadar.exe';
  Params := '--prepare-uninstall --data-dir "' + DataRoot + '"';
  if not Exec(Command, Params, AppRoot, SW_HIDE,
              ewWaitUntilTerminated, ExitCode) then begin
    MsgBox(ExpandConstant('{cm:UninstallStopped}'), mbError, MB_OK);
    Abort;
  end;
  if ExitCode <> 0 then begin
    MsgBox(WithErrorFile(ExpandConstant('{cm:UninstallStopped}'), DataRoot),
           mbError, MB_OK);
    Abort;
  end;
  if not AcquireMaintenance(AppRoot) then begin
    MsgBox(ExpandConstant('{cm:UninstallStopped}'), mbError, MB_OK);
    Abort;
  end;
  { Clean a task re-registered during the first graceful shutdown. }
  if not PrepareApplication(AppRoot) then begin
    ReleaseMaintenance;
    MsgBox(WithErrorFile(ExpandConstant('{cm:UninstallStopped}'), DataRoot),
           mbError, MB_OK);
    Abort;
  end;
  if DeleteData and DirExists(DataRoot) and
     not DelTree(DataRoot, True, True, True) then begin
    MsgBox(ExpandConstant('{cm:DeleteFailed}'), mbError, MB_OK);
    Abort;
  end;
end;
