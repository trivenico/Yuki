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
DefaultDirName={autopf}\Yuki
DefaultGroupName=Yuki
OutputDir=..\dist
OutputBaseFilename=Yuki-Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=admin
WizardStyle=modern
UninstallDisplayIcon={app}\YukiDesktop.exe

[Files]
Source: "..\dist\YukiDesktop.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\YukiRuntime.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\config.yaml"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Yuki Desktop"; Filename: "{app}\YukiDesktop.exe"
Name: "{group}\Yuki CLI"; Filename: "{app}\YukiRuntime.exe"
Name: "{autodesktop}\Yuki"; Filename: "{app}\YukiDesktop.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"
