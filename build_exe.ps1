$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$venvPython = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
	throw 'No se encontró .venv\Scripts\python.exe. Activa o crea la venv antes de generar el exe.'
}

$iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue

Write-Host 'Building frontend...'
Set-Location (Join-Path $root 'frontend')
npm run build

Set-Location $root
Write-Host 'Building exe...'
& $venvPython -m PyInstaller launcher.py --onefile --name ChatDocumentAI --add-data "backend/static;backend/static"

if ($iscc) {
	Write-Host 'Building installer...'
	New-Item -ItemType Directory -Force -Path (Join-Path $root 'dist\installer') | Out-Null
	& $iscc.Path (Join-Path $root 'installer.iss')
} else {
	Write-Warning 'Inno Setup no está instalado. Se generó solo el exe; el instalador requiere ISCC.exe.'
}

Write-Host 'Done. The executable is in dist\ChatDocumentAI.exe'
