# Caduceo Agent - Quick Install (Windows PowerShell)
# Usage:
#   Interattivo:    .\quick-install.ps1
#   Non-interattivo: .\quick-install.ps1 -AgentId "mio-pc"
#
#scarica & esegui in un colpo solo (da PowerShell):
#  Invoke-Expression (Invoke-WebRequest -Uri "https://your-relay.example.com/download/quick-install.ps1").Content

param(
    [string]$AgentId = "",
    [string]$Tags = "caduceo"
)

$RelayUrl = "wss://your-relay.example.com"
$PskHex = "CHANGE_ME_GENERATE_A_NEW_PSK"
$InstallerUrl = "https://your-relay.example.com/download/install.py?platform=windows"
$CaduceoDir = Join-Path $env:USERPROFILE ".caduceo"
$InstallerPath = Join-Path $env:TEMP "caduceo-install.py"

Write-Host "=== Caduceo Agent Quick Install ===" -ForegroundColor Cyan

# 1) Scarica installer
Write-Host "[1/3] Download installer..." -ForegroundColor Yellow
Invoke-WebRequest -Uri $InstallerUrl -OutFile $InstallerPath

# 2) Se AgentId specificato, crea agent.json non-interattivo
if ($AgentId -ne "") {
    Write-Host "[2/3] Creazione config per agent: $AgentId" -ForegroundColor Yellow
    if (-not (Test-Path $CaduceoDir)) { New-Item -ItemType Directory -Path $CaduceoDir | Out-Null }
    
    $agentConfig = @{
        relay_url = $RelayUrl
        psk_hex   = $PskHex
        agent_id  = $AgentId
        tags      = $Tags
    } | ConvertTo-Json
    
    Set-Content -Path (Join-Path $CaduceoDir "agent.json") -Value $agentConfig
    icacls (Join-Path $CaduceoDir "agent.json") /inheritance:r /grant:r "$($env:USERNAME):(R)" | Out-Null
    Write-Host "      Config salvato in $CaduceoDir\agent.json" -ForegroundColor Green
} else {
    Write-Host "[2/3] Modalita' interattiva" -ForegroundColor Yellow
}

# 3) Esegui installer
Write-Host "[3/3] Esecuzione installer..." -ForegroundColor Yellow
python $InstallerPath

Write-Host ""
Write-Host "=== Installazione completata ===" -ForegroundColor Green
Write-Host "L'agent si colleghera' automaticamente a $RelayUrl" -ForegroundColor Gray