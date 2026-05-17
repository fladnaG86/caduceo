# Caduceo Agent - Avvio rapido Windows
# Richiede solo Python 3.11+ gia installato

$RELAY_URL = 'wss://533q08lvroip.shares.zrok.io/agent'
$PSK_HEX   = '0bccdc4cffb30e10eab32a41f43d7802763c6c3cd9ec1b4975a9b80aff96bdba'
$TAGS       = 'remoto'

Write-Host ''
Write-Host '  ========================================' -ForegroundColor Cyan
Write-Host '  |   CADUCEO AGENT - Avvio rapido       |' -ForegroundColor Cyan
Write-Host '  ========================================' -ForegroundColor Cyan
Write-Host ''

# -- Config -----------------------------------------------------------
$configDir = Join-Path $env:USERPROFILE '.caduceo'
$configFile = Join-Path $configDir 'agent.json'

if (-not (Test-Path $configDir)) {
    New-Item -ItemType Directory -Force -Path $configDir | Out-Null
}

$config = @{
    relay_url = $RELAY_URL
    psk_hex   = $PSK_HEX
    tags      = @($TAGS)
} | ConvertTo-Json -Depth 3

Set-Content -Path $configFile -Value $config -Encoding UTF8
Write-Host '[OK] Config: ' -NoNewline -ForegroundColor Green; Write-Host $configFile

# -- Install ----------------------------------------------------------
Write-Host ''
Write-Host '[*] Installazione dipendenze...' -ForegroundColor Cyan

pip install --upgrade pip --quiet 2>$null
pip install 'caduceo-common @ git+https://github.com/fladnaG86/caduceo.git#subdirectory=caduceo-common' --quiet
pip install 'caduceo-agent @ git+https://github.com/fladnaG86/caduceo.git#subdirectory=caduceo-agent' --quiet

if ($LASTEXITCODE -ne 0) {
    Write-Host '[X] Installazione fallita. Provo con --user...' -ForegroundColor Red
    pip install 'caduceo-common @ git+https://github.com/fladnaG86/caduceo.git#subdirectory=caduceo-common' --user --quiet
    pip install 'caduceo-agent @ git+https://github.com/fladnaG86/caduceo.git#subdirectory=caduceo-agent' --user --quiet
}

Write-Host '[OK] Dipendenze installate' -ForegroundColor Green

# -- Test connessione -------------------------------------------------
Write-Host ''
Write-Host '[*] Test connessione al relay...' -ForegroundColor Cyan
try {
    $r = Invoke-RestMethod -Uri 'https://533q08lvroip.shares.zrok.io/api/health' -SkipCertificateCheck
    Write-Host '[OK] Relay online! Versione: ' -NoNewline -ForegroundColor Green; Write-Host $r.version
} catch {
    Write-Host '[!] Relay non raggiungibile - verifica che il server e zrok siano attivi' -ForegroundColor Yellow
}

# -- Avvia agent ------------------------------------------------------
Write-Host ''
Write-Host '[*] Avvio Caduceo Agent...' -ForegroundColor Cyan
Write-Host '     Config: ' -NoNewline; Write-Host $configFile -ForegroundColor Gray
Write-Host '     Relay:  ' -NoNewline; Write-Host $RELAY_URL -ForegroundColor Gray
Write-Host ''
Write-Host '  Premi Ctrl+C per fermare' -ForegroundColor Yellow
Write-Host ''

python -m caduceo_agent --config $configFile --verbose