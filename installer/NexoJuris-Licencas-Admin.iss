#define MyAppName "NexoJuris Licenças Admin"
#define MyAppVersion "1.4.5"
#define MyAppPublisher "NexoJuris"
#define MyAppExeName "NexoJuris Licenças Admin.exe"

[Setup]
AppId={{B718D66B-5076-4A3C-BC6D-C75130396D02}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\release\packages
OutputBaseFilename=NexoJuris-Licencas-Admin-Setup-v{#MyAppVersion}
SetupIconFile=..\assets\nexojuris.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=Instalador privado do {#MyAppName}
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoCopyright=Copyright (C) 2026 {#MyAppPublisher}

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Files]
Source: "..\release\dist\NexoJuris Licenças Admin\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\abrir_admin_licencas.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\abrir_admin_licencas.ps1"""; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"

[UninstallDelete]
Type: dirifempty; Name: "{app}"
