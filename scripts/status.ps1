$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

docker compose ps
Write-Host ''
python -c "import urllib.request, json; print('api_live', urllib.request.urlopen('http://127.0.0.1:18000/health/live').status); print('api_ready', urllib.request.urlopen('http://127.0.0.1:18000/health/ready').status); req=urllib.request.Request('http://127.0.0.1:18000/metrics/prometheus', headers={'X-Metrics-Token':'change-me'}); print('metrics', urllib.request.urlopen(req).status); data=json.loads(urllib.request.urlopen('http://127.0.0.1:19090/api/v1/targets').read().decode()); print('prom_targets', len(data['data']['activeTargets']))"
