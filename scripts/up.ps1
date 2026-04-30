param(
    [switch]$Build
)

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path '.env.compose') -and (Test-Path '.env.compose.example')) {
    Write-Host 'No .env.compose found; using docker-compose defaults / example values.' -ForegroundColor Yellow
}

if ($Build) {
    docker compose up -d --build
} else {
    docker compose up -d
}
