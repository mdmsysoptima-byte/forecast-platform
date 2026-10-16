# Forecast Platformu: tek komutla ayaga kalkar.
#
#   .\run.ps1            uygulamayı başlatır (http://127.0.0.1:8000)
#   .\run.ps1 -Demo      demo verisini sıfırdan yükler, sonra başlatır
#   .\run.ps1 -Test      test paketini koşar
#   .\run.ps1 -Sector pharma   başka sektör konfigürasyonu ile yükler

param(
  [switch]$Demo,
  [switch]$Test,
  [switch]$Install,
  [string]$Sector = "demo",
  [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

# Bu makinede kullanıcı site-packages dizini venv'e sızıyor, bu yuzden temizliyoruz.
$env:PYTHONPATH = Join-Path $root "backend"
$env:PYTHONNOUSERSITE = "1"
$env:SECTOR = $Sector

if (-not (Test-Path $python)) {
  Write-Host "Sanal ortam kuruluyor..." -ForegroundColor Cyan
  python -m venv (Join-Path $root ".venv")
  $Install = $true
}

if ($Install) {
  Write-Host "Bagimliliklar kuruluyor..." -ForegroundColor Cyan
  & $python -m pip install --upgrade pip --quiet
  & $python -m pip install --quiet --upgrade --force-reinstall -r (Join-Path $root "requirements.txt")
}

if ($Test) {
  Set-Location (Join-Path $root "backend")
  & $python -m pytest tests -q --no-header
  exit $LASTEXITCODE
}

if ($Demo) {
  Write-Host "Demo verisi yükleniyor (sektör: $Sector)..." -ForegroundColor Cyan
  Set-Location (Join-Path $root "backend")
  & $python -m app.demo.loader
  if ($LASTEXITCODE -ne 0) { throw "Demo yüklemesi başarısız" }
}

Write-Host ""
Write-Host "Uygulama: http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host "API dokümanı: http://127.0.0.1:$Port/api/docs" -ForegroundColor Green
Write-Host "Giriş: planner@demo.local / demo" -ForegroundColor Green
Write-Host ""

Set-Location (Join-Path $root "backend")
& $python -m uvicorn app.main:app --host 127.0.0.1 --port $Port
