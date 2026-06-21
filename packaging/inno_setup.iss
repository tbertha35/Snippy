; Inno Setup script for Snippy
; Compiled with ISCC.exe (Inno Setup 6.x). Produces Snippy-Setup-<version>.exe
; and Snippy-<version>-portable.zip.
;
; SnippyVersion is set on the ISCC command line:
;     iscc packaging/inno_setup.iss /DSnippyVersion=0.3.0
; (the .github/workflows/release.yml workflow does this automatically from
;  the git tag).
;
; What this script does:
;   1. Copies the prebuilt PyInstaller `dist\Snippy\` folder into
;      {app}\Snippy\
;   2. Drops a Start-Menu shortcut to Snippy.exe
;   3. Registers an Add/Remove Programs entry
;   4. Generates an uninstaller that also offers to delete user data
;      (snippets in %APPDATA%\Snippy\) behind a checkbox.
;   5. Optionally builds a portable .zip from the same folder.

#define MyAppName "Snippy"
#define MyAppPublisher "Snippy Contributors"
#define MyAppURL "https://github.com/tbertha35/snippy"
#define MyAppExeName "Snippy.exe"

#ifndef SnippyVersion
  #define SnippyVersion "0.0.0-dev"
#endif

[Setup]
AppId={{A6B8E5C2-3F4D-4E1A-9B7C-2D5E6F8A1B2C}
AppName={#MyAppName}
AppVersion={#SnippyVersion}
AppVerName={#MyAppName} {#SnippyVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=Snippy-Setup-{#SnippyVersion}
SetupIconFile=..\snippy\assets\snippy.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Point at the bundled .ico so the icon appears in Control Panel / Apps.
; The PyInstaller build copies snippy.ico into the app root.
UninstallDisplayIcon={app}\snippy.ico
UninstallDisplayName={#MyAppName}
VersionInfoVersion={#SnippyVersion}
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Source = the PyInstaller output folder (built by `build_windows.bat` before this runs)
Source: "..\dist\Snippy\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Explicitly ensure the .ico is present for the uninstall entry icon.
Source: "..\snippy\assets\snippy.ico"; DestDir: "{app}"; Flags: ignoreversion
; NOTE: do not use "Flags: ignoreversion" on the shared system files (none here, but keep the rule)

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Ask the user before nuking the user data dir (defaults to NO).
Type: filesandordirs; Name: "{userappdata}\{#MyAppName}"
Type: filesandordirs; Name: "{localappdata}\{#MyAppName}"

[Code]
// Offer to delete user data on uninstall behind a Yes/No MsgBox.
// This is the "Also delete my snippets" option described in the README.
function InitializeUninstall(): Boolean;
var
  ButtonPressed: Integer;
begin
  Result := True;
  if MsgBox(
    'Do you also want to remove your snippets and settings?' + #13#10 +
    'Your library lives in:' + #13#10 +
    '  {userappdata}\Snippy\' + #13#10 +
    'Choosing Yes will permanently delete all your saved snippets, ' +
    'config, and themes. This cannot be undone.',
    mbConfirmation, MB_YESNO
  ) = IDYES then
  begin
    DelTree(ExpandConstant('{userappdata}\Snippy'), True, True, True);
    DelTree(ExpandConstant('{localappdata}\Snippy'), True, True, True);
  end;
end;
