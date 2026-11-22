; Inno Setup script — builds dist\MirageSetup.exe from the PyInstaller folder dist\Mirage.
;   iscc build\installer.iss
; Expects build\downloads\MicrosoftEdgeWebview2Setup.exe (build.ps1 downloads it).

#define AppName "Mirage"
#define AppVersion "1.0.0"
#define CamName "Mirage Camera"

[Setup]
AppId={{8E3B0C52-6F0D-4C8B-9B57-6A2C7E1F4D11}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Sono Technologies
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=MirageSetup
SetupIconFile=mirage.ico
UninstallDisplayIcon={app}\Mirage.exe
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
MinVersion=10.0
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\Mirage\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "downloads\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: NeedsWebView2

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\Mirage.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\Mirage.exe"; Tasks: desktopicon

[Run]
; WebView2 runtime: preinstalled on most Windows 10/11 machines; install the bootstrapper if missing.
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Installing Microsoft Edge WebView2…"; Flags: waituntilterminated; Check: NeedsWebView2
; Register the virtual camera (DirectShow filter) as "Mirage Camera", 64- and 32-bit (32-bit apps see the 32-bit one).
Filename: "{sys}\regsvr32.exe"; Parameters: "/s ""{app}\_internal\drivers\UnityCaptureFilter64.dll"" ""/i:UnityCaptureName={#CamName}"""; StatusMsg: "Registering Mirage Camera…"; Flags: runhidden waituntilterminated
Filename: "{syswow64}\regsvr32.exe"; Parameters: "/s ""{app}\_internal\drivers\UnityCaptureFilter32.dll"" ""/i:UnityCaptureName={#CamName}"""; Flags: runhidden waituntilterminated
Filename: "{app}\Mirage.exe"; Description: "Launch Mirage"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\regsvr32.exe"; Parameters: "/u /s ""{app}\_internal\drivers\UnityCaptureFilter64.dll"""; RunOnceId: "UnregCam64"; Flags: runhidden waituntilterminated
Filename: "{syswow64}\regsvr32.exe"; Parameters: "/u /s ""{app}\_internal\drivers\UnityCaptureFilter32.dll"""; RunOnceId: "UnregCam32"; Flags: runhidden waituntilterminated

[UninstallDelete]
; User data in %APPDATA%\Mirage (faces, settings) is deliberately kept.
Type: filesandordirs; Name: "{app}"

[Code]
function HasWebView2(Root: Integer; Key: String): Boolean;
var V: String;
begin
  Result := RegQueryStringValue(Root, Key, 'pv', V) and (V <> '') and (V <> '0.0.0.0');
end;

function NeedsWebView2(): Boolean;
var Client: String;
begin
  Client := 'Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  Result := not (HasWebView2(HKLM, 'SOFTWARE\WOW6432Node\' + Client)
              or HasWebView2(HKLM, 'SOFTWARE\' + Client)
              or HasWebView2(HKCU, 'Software\' + Client));
end;
