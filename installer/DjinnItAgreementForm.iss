; Inno Setup script - build with build_installer.bat (needs Inno Setup 6: https://jrsoftware.org/isdl.php)
;
; Installs per user by default (no admin rights needed - works on locked-down business laptops):
;   %LOCALAPPDATA%\Programs\DjinnItAgreementForm
; An administrator can choose "install for all users" in the first dialog instead.
; Settings and rate sheets live in %APPDATA%\DjinnItAgreementForm and survive upgrades/uninstall.

#define MyAppName "DjinnItAgreementForm"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppExe "DjinnItAgreementForm.exe"
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
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Windows 10 1809 or newer (Windows OCR + Qt 6)
MinVersion=10.0.17763
OutputDir=..\dist\installer
OutputBaseFilename=DjinnItAgreementForm-Setup-{#MyAppVersion}
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

[Files]
Source: "..\dist\DjinnItAgreementForm\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
