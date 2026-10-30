#define AppName "Yuki"
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#define AppPublisher "TriveniCo"

[Setup]
AppId={{8C2F7A6B-2C71-4E41-AC29-9B9B0A8A4F31}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\Yuki
DefaultGroupName=Yuki
OutputDir=..\dist
OutputBaseFilename=Yuki-Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=lowest
WizardStyle=modern
UninstallDisplayIcon={app}\YukiDesktop\YukiDesktop.exe

[Files]
Source: "..\dist\YukiDesktop\*"; DestDir: "{app}\YukiDesktop"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\dist\YukiRuntime\*"; DestDir: "{app}\YukiRuntime"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\config.yaml"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Yuki Desktop"; Filename: "{app}\YukiDesktop\YukiDesktop.exe"
Name: "{group}\Yuki CLI"; Filename: "{app}\YukiRuntime\YukiRuntime.exe"
Name: "{autodesktop}\Yuki"; Filename: "{app}\YukiDesktop\YukiDesktop.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"
