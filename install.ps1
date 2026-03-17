#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Repo = if ($env:FREELUNCH_REPO) { $env:FREELUNCH_REPO } else { "jetymas/freelunch" }
$Repo = $Repo.ToLowerInvariant()
$Image = if ($env:FREELUNCH_IMAGE) { $env:FREELUNCH_IMAGE } else { "ghcr.io/$Repo`:latest" }
$InstallDir = if ($env:FREELUNCH_INSTALL_DIR) { $env:FREELUNCH_INSTALL_DIR } else { Join-Path $env:USERPROFILE ".freelunch" }
$DefaultPort = if ($env:FREELUNCH_PORT) { $env:FREELUNCH_PORT } else { "8000" }
$SkipPull = $false
if ($env:FREELUNCH_SKIP_PULL) {
    $SkipPull = @("1", "true", "yes") -contains $env:FREELUNCH_SKIP_PULL.ToLowerInvariant()
}
$script:CreateShortcutResponse = ""
$script:AdminShortcutPath = ""
$script:GatewayPort = ""
$script:Upgrading = $false

function Write-Info { param([string]$Message) Write-Host "[INFO] $Message" -ForegroundColor Cyan }
function Write-Warn { param([string]$Message) Write-Host "[WARN] $Message" -ForegroundColor Yellow }
function Fail { param([string]$Message) throw $Message }

function Read-Value {
    param(
        [string]$Prompt,
        [string]$Default = "",
        [string]$EnvVar = ""
    )
    if ($EnvVar -and (Test-Path "Env:$EnvVar")) {
        return (Get-Item -Path "Env:$EnvVar").Value
    }
    if ($Default) {
        $value = Read-Host "$Prompt [$Default]"
    } else {
        $value = Read-Host $Prompt
    }
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $Default
    }
    return $value
}

function Assert-Docker {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Fail "Docker is required but was not found in PATH."
    }
    docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        Fail "Docker is installed but not running."
    }
    docker compose version *> $null
    if ($LASTEXITCODE -ne 0) {
        Fail "Docker Compose v2 is required."
    }
}

function Setup-InstallDir {
    if (Test-Path (Join-Path $InstallDir ".env")) {
        $upgrade = Read-Value "Existing installation found at $InstallDir. Upgrade and preserve config?" "yes" "FREELUNCH_AUTO_CONFIRM"
        if ($upgrade -notin @("y", "Y", "yes", "YES")) {
            Fail "Installation cancelled."
        }
        $script:Upgrading = $true
        return
    }

    New-Item -ItemType Directory -Path (Join-Path $InstallDir "data") -Force | Out-Null
}

function Write-EnvFile {
    if ($script:Upgrading) {
        $envPath = Join-Path $InstallDir ".env"
        $content = Get-Content $envPath -Raw
        if ($content -notmatch "(?m)^FREELUNCH_IMAGE=") {
            Add-Content -Path $envPath -Value "`r`nFREELUNCH_IMAGE=$Image"
        }
        return
    }

    $openrouterKey = Read-Value "OpenRouter API key" "" "OPENROUTER_API_KEY"
    if ([string]::IsNullOrWhiteSpace($openrouterKey)) {
        Fail "OPENROUTER_API_KEY is required."
    }
    $gatewayKey = Read-Value "Gateway API key (leave blank to disable auth)" "" "GATEWAY_API_KEY"
    $gatewayPort = Read-Value "Gateway port" $DefaultPort "FREELUNCH_PORT"
    $script:GatewayPort = $gatewayPort

    @"
OPENROUTER_API_KEY=$openrouterKey
GATEWAY_API_KEY=$gatewayKey
DATABASE_URL=data/freelunch.db
APP_ENV=dev
FREELUNCH_PORT=$gatewayPort
FREELUNCH_IMAGE=$Image
"@ | Set-Content -Path (Join-Path $InstallDir ".env") -Encoding UTF8
}

function Write-ConfigFile {
    $configPath = Join-Path $InstallDir "config.yaml"
    if (Test-Path $configPath) {
        return
    }

    @"
app:
  env: dev

providers:
  openrouter:
    api_base: "https://openrouter.ai/api/v1"
    active_probe_enabled: true

discovery:
  interval_minutes: 30
  request_timeout_seconds: 15
  leaderboard:
    chatbot_arena:
      enabled: true
      cache_hours: 24
    open_llm:
      enabled: true
      cache_hours: 24

routing:
  default_model: auto
  max_attempts: 3

ranking:
  interval_minutes: 15
  fallback_model: "openrouter/openrouter/free"
  weights:
    benchmark_score: 0.30
    real_world_usage: 0.15
    latency: 0.20
    availability: 0.20
    context_window: 0.10
    feature_support: 0.05

health:
  probe_interval_minutes: 180
  probe_timeout_seconds: 15
  probe_concurrency: 1
  max_probes_per_run: 1
  stale_after_minutes: 360
  top_n_stale_probe: 3
  startup_probe_limit: 2
  consecutive_failures_threshold: 3
  cooldown_minutes: 30
  max_backoff_exponent: 4
  probe_max_tokens: 1
  daily_request_budget_by_provider:
    openrouter: 5

logging:
  request_log_retention_days: 30
"@ | Set-Content -Path $configPath -Encoding UTF8
}

function Write-ComposeFile {
    @'
services:
  freelunch:
    image: ${FREELUNCH_IMAGE:-ghcr.io/jetymas/freelunch:latest}
    restart: unless-stopped
    ports:
      - "${FREELUNCH_PORT:-8000}:8000"
    env_file:
      - .env
    volumes:
      - ./data:/app/data
      - ./config.yaml:/app/config.yaml:ro
'@ | Set-Content -Path (Join-Path $InstallDir "docker-compose.yml") -Encoding UTF8
}

function Sync-PortFromEnv {
    $envPath = Join-Path $InstallDir ".env"
    if (-not (Test-Path $envPath)) {
        $script:GatewayPort = $DefaultPort
        return
    }
    $line = Get-Content $envPath | Where-Object { $_ -like "FREELUNCH_PORT=*" } | Select-Object -Last 1
    if ($line) {
        $parts = $line -split "=", 2
        if ($parts.Length -ge 2) {
            $script:GatewayPort = $parts[1]
            return
        }
    }
    $script:GatewayPort = $DefaultPort
}

function Determine-ShortcutPreference {
    if ($script:CreateShortcutResponse) {
        return
    }
    if ($env:FREELUNCH_CREATE_ADMIN_SHORTCUT) {
        $script:CreateShortcutResponse = $env:FREELUNCH_CREATE_ADMIN_SHORTCUT
        return
    }
    if ($env:FREELUNCH_AUTO_CONFIRM) {
        $script:CreateShortcutResponse = "yes"
        return
    }
    $script:CreateShortcutResponse = Read-Value "Create Admin UI desktop shortcut?" "yes" "FREELUNCH_CREATE_ADMIN_SHORTCUT"
}

function Should-CreateShortcut {
    if (-not $script:CreateShortcutResponse) {
        return $false
    }
    switch ($script:CreateShortcutResponse.ToLowerInvariant()) {
        "1" { return $true }
        "y" { return $true }
        "yes" { return $true }
        "true" { return $true }
        "on" { return $true }
    }
    return $false
}

function Create-AdminShortcut {
    if (-not (Should-CreateShortcut)) {
        return
    }
    if (-not $script:GatewayPort) {
        Sync-PortFromEnv
    }
    $desktopDir = Join-Path $env:USERPROFILE "Desktop"
    if (-not (Test-Path $desktopDir)) {
        Write-Warn "Desktop directory not found; skipping Admin UI shortcut creation."
        return
    }
    $url = "http://localhost:$($script:GatewayPort)/admin/ui"
    $shortcutPath = Join-Path $desktopDir "FreeLunch Admin UI.url"
    "[InternetShortcut]" + "`n" + "URL=$url" | Set-Content -Path $shortcutPath -Encoding ASCII
    $script:AdminShortcutPath = $shortcutPath
    Write-Info "Created Admin UI shortcut at $shortcutPath"
}

function Start-FreeLunch {
    if (-not $SkipPull) {
        Write-Info "Pulling $Image"
        docker pull $Image
        if ($LASTEXITCODE -ne 0) {
            Fail "Failed to pull image: $Image"
        }
    } else {
        Write-Info "Skipping image pull for $Image (FREELUNCH_SKIP_PULL=$env:FREELUNCH_SKIP_PULL)"
    }
    Write-Info "Starting FreeLunch"
    docker compose --project-directory $InstallDir -f (Join-Path $InstallDir "docker-compose.yml") up -d
    if ($LASTEXITCODE -ne 0) {
        Fail "Failed to start FreeLunch via docker compose."
    }
}

function Print-Summary {
    $envPath = Join-Path $InstallDir ".env"
    $port = (($envLines = Get-Content $envPath) | Where-Object { $_ -like "FREELUNCH_PORT=*" } | Select-Object -Last 1)
    if ($port) {
        $port = ($port -split "=", 2)[1]
    } else {
        $port = $DefaultPort
    }

    Write-Host ""
    Write-Host "FreeLunch installed."
    Write-Host "Gateway URL: http://localhost:$port/v1"
    Write-Host "Admin UI: http://localhost:$port/admin/ui"
    Write-Host "Health check: http://localhost:$port/healthz"
    Write-Host "Install dir: $InstallDir"
    Write-Host "Logs: docker compose --project-directory `"$InstallDir`" -f `"$InstallDir\\docker-compose.yml`" logs -f"
    Write-Host "Stop: docker compose --project-directory `"$InstallDir`" -f `"$InstallDir\\docker-compose.yml`" down"
    Write-Host "Uninstall: .\\uninstall.ps1"
    if ($script:AdminShortcutPath) {
        Write-Host "Desktop shortcut: $($script:AdminShortcutPath)"
    }
}

Write-Info "Installing FreeLunch from $Image"
Assert-Docker
Setup-InstallDir
Write-EnvFile
Sync-PortFromEnv
Determine-ShortcutPreference
Write-ConfigFile
Write-ComposeFile
Start-FreeLunch
Create-AdminShortcut
Print-Summary
