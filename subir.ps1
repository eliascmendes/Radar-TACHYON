# Radar TACHYON: sobe scraper (Docker) + painel web com um comando.
# Uso: .\subir.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

Write-Host "=== Radar TACHYON ===" -ForegroundColor Cyan
Write-Host "Pasta: $Root"

# 1) Dependências Python
if (-not (Test-Path "$Root\.venv")) {
  Write-Host "Criando ambiente virtual .venv ..."
  python -m venv .venv
}
$py = Join-Path $Root ".venv\Scripts\python.exe"
& $py -m pip install --upgrade pip -q
& $py -m pip install -r requirements.txt -q

# 2) Motor do kit (Docker)
$kit = Join-Path $Root "google-maps-scraper-kit"
if (-not (Test-Path $kit)) {
  Write-Host "Clonando google-maps-scraper-kit ..."
  git clone https://github.com/Mahanaicoach/google-maps-scraper-kit.git $kit
}
if (-not (Test-Path $kit)) {
  Write-Host "ERRO: pasta google-maps-scraper-kit nao encontrada." -ForegroundColor Red
  exit 1
}
Write-Host "Subindo scraper Docker (porta 8080) ..."
Push-Location -LiteralPath $kit
try {
  docker compose up -d
} finally {
  Pop-Location
}

# 3) Espera API do scraper
Write-Host "Aguardando motor em http://127.0.0.1:8080 ..."
$ok = $false
for ($i = 1; $i -le 30; $i++) {
  try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:8080/api/v1/jobs" -UseBasicParsing -TimeoutSec 3
    if ($r.StatusCode -eq 200) { $ok = $true; break }
  } catch {
    Start-Sleep -Seconds 2
  }
}
if (-not $ok) {
  Write-Host "AVISO: scraper ainda nao respondeu. O painel sobe assim mesmo." -ForegroundColor Yellow
} else {
  Write-Host "Scraper OK." -ForegroundColor Cyan
}

# 4) Painel FastAPI
Write-Host ""
Write-Host "Painel: http://127.0.0.1:8787" -ForegroundColor Cyan
Write-Host "Scraper UI: http://127.0.0.1:8080"
Write-Host "Ctrl+C para encerrar o painel (Docker do scraper continua)."
Write-Host ""
& $py -m uvicorn app.main:app --host 127.0.0.1 --port 8787 --app-dir $Root
