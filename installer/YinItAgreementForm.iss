; Inno Setup script - build with build_installer.bat (needs Inno Setup 6: https://jrsoftware.org/isdl.php)
;
; Installs per user by default (no admin rights needed - works on locked-down business laptops):
;   %LOCALAPPDATA%\Programs\YinItAgreementForm
; An administrator can choose "install for all users" in the first dialog instead.
; Settings and rate sheets live in %APPDATA%\YinItAgreementForm and survive upgrades/uninstall.

#define MyAppName "YinItAgreementForm"
#define MyAppExe "YinItAgreementForm.exe"
#ifndef MyAppVersion
  ; not given on the command line (/DMyAppVersion=1.2.3): the version the built program carries
  ; (ProductVersion, written by YinItAgreementForm.spec)
  #define MyAppVersion GetStringFileInfo(AddBackslash(SourcePath) + "..\dist\" + MyAppName + "\" + MyAppExe, "ProductVersion")
#endif
#if MyAppVersion == ""
  #error The version is unknown: build the program first (build_installer.bat), or pass /DMyAppVersion=X.Y.Z
#endif
#define MyAppPublisher "Noah Collin"
#define MyContact "noahcollincourtreporter@gmail.com"

[Setup]
; Never change AppId - Windows uses it to recognise upgrades of the same program.
AppId={{BAC19382-28F2-4566-8CD2-F65646DA10D0}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppContact={#MyContact}
AppSupportURL=mailto:{#MyContact}
VersionInfoVersion={#MyAppVersion}
VersionInfoDescription={#MyAppName} Setup
DefaultDirName={autopf}\{#MyAppName}
; The app was DjinnItAgreementForm until 2.0: an upgrade goes to the new folder (see [InstallDelete]), not the old
UsePreviousAppDir=no
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Windows 10 1809 or newer (Windows OCR + Qt 6)
MinVersion=10.0.17763
OutputDir=..\dist\installer
OutputBaseFilename=YinItAgreementForm-Setup-{#MyAppVersion}
SetupIconFile=..\minute_filler\assets\app.ico
UninstallDisplayIcon={app}\{#MyAppExe}
UninstallDisplayName={#MyAppName}
WizardStyle=modern
WizardImageFile=wizard.bmp,wizard@2x.bmp
WizardSmallImageFile=small.bmp,small@2x.bmp
Compression=lzma2/ultra64
SolidCompression=yes
; Close a running copy before upgrading
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; clear the previous version's program files so removed libraries don't linger
Type: filesandordirs; Name: "{app}\_internal"
; the program under its old name (before 2.0), and its shortcuts: replaced by this one
Type: filesandordirs; Name: "{autopf}\DjinnItAgreementForm"
Type: files; Name: "{autoprograms}\DjinnItAgreementForm.lnk"
Type: files; Name: "{autodesktop}\DjinnItAgreementForm.lnk"

[Files]
Source: "..\dist\YinItAgreementForm\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[Code]
const
  OldName = 'DjinnItAgreementForm';
  // the uninstall entry of this AppId (unchanged since the old name): where the last version was installed
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{BAC19382-28F2-4566-8CD2-F65646DA10D0}_is1';

{ The folder the previous version was installed in, when it was the program under its old name; '' otherwise. }
function OldAppDir(): String;
var
  Dir: String;
begin
  Result := '';
  if RegQueryStringValue(HKA, UninstallKey, 'Inno Setup: App Path', Dir) then
  begin
    Dir := RemoveBackslash(Dir);
    if CompareText(ExtractFileName(Dir), OldName) = 0 then
      Result := Dir;
  end;
end;

{ Whether a program file is in use: a running program's file can't be renamed. }
function InUse(Exe: String): Boolean;
begin
  Result := False;
  if FileExists(Exe) then
  begin
    if RenameFile(Exe, Exe + '.check') then
      RenameFile(Exe + '.check', Exe)
    else
      Result := True;
  end;
end;

{ CloseApplications only closes copies running from this install's own folder: the program under its old
  name, still running from its old folder, would be left half replaced. Setup stops and says so. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Dir: String;
begin
  Result := '';
  Dir := OldAppDir();
  if InUse(ExpandConstant('{autopf}\' + OldName + '\' + OldName + '.exe')) or
     ((Dir <> '') and InUse(Dir + '\' + OldName + '.exe')) then
    Result := 'Please close ' + OldName + ', then click Back and Next.';
end;

{ The program under its old name installed in a folder of the user's choosing: removed as [InstallDelete]
  removes the one in the usual place. }
procedure CurStepChanged(CurStep: TSetupStep);
var
  Dir: String;
begin
  if CurStep = ssInstall then
  begin
    Dir := OldAppDir();
    if (Dir <> '') and (CompareText(Dir, RemoveBackslash(ExpandConstant('{app}'))) <> 0) then
      DelTree(Dir, True, True, True);
  end;
end;
