# One-shot Windows build: venv -> deps -> models -> tests -> PyInstaller -> Inno Setup.
#   powershell -ExecutionPolicy Bypass -File build\build.ps1
# Needs Python 3.11 (py launcher) and Inno Setup 6.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path .venv)) { py -3.11 -m venv .venv }
$py = Join-Path $root ".venv\Scripts\python.exe"
& $py -m pip install --upgrade pip
& $py -m pip install -r requirements.txt -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

& $py build\fetch_models.py
if ($LASTEXITCODE -ne 0) { throw "model download/verification failed" }

& $py -m pytest -q tests
if ($LASTEXITCODE -ne 0) { throw "tests failed" }

New-Item -ItemType Directory -Force build\downloads | Out-Null
$wv = "build\downloads\MicrosoftEdgeWebview2Setup.exe"
if (-not (Test-Path $wv)) {
  Invoke-WebRequest "https://go.microsoft.com/fwlink/p/?LinkId=2124703" -OutFile $wv
}

& $py -m PyInstaller build\mirage.spec --noconfirm --distpath dist --workpath build\out
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) { $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
& $iscc build\installer.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
Write-Host "Built dist\MirageSetup.exe"
