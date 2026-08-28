; sdtools one-click installer (LAStools-style: double-click, done).
; Extracts the toolkit and offers to run install.ps1 (uv + pixi + sdtools command).
; Built by make_installer.ps1 (payload staged at C:\sdt_stage).

#ifndef AppVer
  #define AppVer "dev"
#endif
#ifndef OutDir
  #define OutDir "C:\sdt_out"
#endif

[Setup]
AppName=sdtools
AppVersion={#AppVer}
AppPublisher=SpatialData
DefaultDirName=C:\sdtools
DisableProgramGroupPage=yes
; user-level install: uv/pixi/envs are per-user, and pixi envs build INSIDE
; the install dir, so it must stay user-writable (NOT Program Files)
PrivilegesRequired=lowest
OutputBaseFilename=sdtools-setup-{#AppVer}
OutputDir={#OutDir}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=sdtools console

[Files]
Source: "C:\sdt_stage\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Run]
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\install.ps1"""; \
  Description: "Set up sdtools now (installs uv + pixi, registers the 'sdtools' command)"; \
  Flags: postinstall skipifsilent

[Code]
function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  { the pipelines pass interpreter paths through command strings that cannot
    carry spaces (aecon.py --engine) - refuse a spaced install dir up front }
  if (CurPageID = wpSelectDir) and (Pos(' ', WizardDirValue) > 0) then
  begin
    MsgBox('Please choose an install folder WITHOUT spaces in the path' + #13#10 +
           '(e.g. C:\sdtools). The processing tools cannot run from a spaced path.',
           mbError, MB_OK);
    Result := False;
  end;
end;
