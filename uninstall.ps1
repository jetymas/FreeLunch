Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$InstallDir = if ($env:FREELUNCH_INSTALL_DIR) { $env:FREELUNCH_INSTALL_DIR } else { Join-Path $env:USERPROFILE ".freelunch" }

Write-Host "This will stop and remove the FreeLunch container, config, and local data in $InstallDir."
$confirm = if (Test-Path "Env:FREELUNCH_AUTO_CONFIRM") {
    (Get-Item -Path "Env:FREELUNCH_AUTO_CONFIRM").Value
} else {
    Read-Host 'Type "yes" to continue'
}

if ($confirm -ne "yes") {
    Write-Host "Uninstall cancelled."
    exit 0
}

$composePath = Join-Path $InstallDir "docker-compose.yml"
if (Test-Path $composePath) {
    docker compose --project-directory $InstallDir -f $composePath down --volumes 2>$null
}

if (Test-Path $InstallDir) {
    Remove-Item -Path $InstallDir -Recurse -Force
}

Write-Host "FreeLunch has been uninstalled."
Write-Host "The Docker image is still cached locally. To remove it:"
Write-Host "  docker rmi ghcr.io/jetymas/freelunch:latest"
