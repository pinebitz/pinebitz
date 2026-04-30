param(
    [string]$ApiBaseUrl = "http://127.0.0.1:8000",
    [Parameter(Mandatory = $true)][string]$OwnerKey,
    [Parameter(Mandatory = $true)][string]$PlanName,
    [string]$WebhookSecret = "",
    [string]$Symbol = "BTCUSDT"
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Set-Location (Split-Path -Parent $PSScriptRoot)

function Invoke-ApiJson {
    param(
        [Parameter(Mandatory = $true)][string]$Method,
        [Parameter(Mandatory = $true)][string]$Path,
        [hashtable]$Headers = @{},
        [object]$Body = $null
    )
    $uri = "$ApiBaseUrl$Path"
    if ($null -eq $Body) {
        return Invoke-RestMethod -Method $Method -Uri $uri -Headers $Headers -TimeoutSec 30
    }
    $json = $Body | ConvertTo-Json -Depth 30
    return Invoke-RestMethod -Method $Method -Uri $uri -Headers $Headers -ContentType "application/json" -Body $json -TimeoutSec 30
}

function Copy-Object {
    param([Parameter(Mandatory = $true)][object]$InputObject)
    return ($InputObject | ConvertTo-Json -Depth 40 | ConvertFrom-Json)
}

function Wait-JobBySignalId {
    param([Parameter(Mandatory = $true)][string]$SignalId)
    $headers = @{ "X-Owner-Key" = $OwnerKey }
    for ($i = 0; $i -lt 25; $i++) {
        $jobs = Invoke-ApiJson -Method GET -Path "/execution/jobs?limit=100&offset=0" -Headers $headers
        foreach ($job in $jobs.items) {
            if ($job.signal_id -eq $SignalId) {
                return $job
            }
        }
        Start-Sleep -Milliseconds 200
    }
    throw "No execution job found for signal_id=$SignalId"
}

function Purge-TestJobs {
    $headers = @{ "X-Owner-Key" = $OwnerKey }
    $null = Invoke-ApiJson -Method POST -Path "/execution/jobs/purge-test?statuses=queued,rejected,approved,sent" -Headers $headers
}

function New-WebhookHeaders {
    $h = @{ "X-Owner-Key" = $OwnerKey }
    if ($WebhookSecret) { $h["X-Webhook-Secret"] = $WebhookSecret }
    return $h
}

$ownerHeaders = @{ "X-Owner-Key" = $OwnerKey }
$plans = Invoke-ApiJson -Method GET -Path "/bot-plans?limit=200&offset=0" -Headers $ownerHeaders
$plan = $null
foreach ($p in $plans.items) {
    if ($p.name -eq $PlanName) { $plan = $p; break }
}
if ($null -eq $plan) { throw "Plan '$PlanName' not found" }

$planId = [string]$plan.id
$originalConfig = Copy-Object -InputObject $plan.config_json
$pairSymbol = if ($plan.config_json.pair) { [string]$plan.config_json.pair } else { $Symbol }

$cases = @(
    @{
        name = "RSI"
        kind = "rsi"
        params = @{ length = 14; timeframe = "1h"; compare = "lt"; value = 30 }
        payload = @{ rsi = 25.4 }
    },
    @{
        name = "MACD"
        kind = "macd"
        params = @{ fast = 12; slow = 26; signal_line = 9; macd_trigger = "crossing_up"; line_trigger = "less_than_0"; timeframe = "1h" }
        payload = @{ macd = -0.12; signal = -0.18 }
    },
    @{
        name = "Stochastic"
        kind = "stochastic"
        params = @{ k_length = 14; k_smoothing = 1; d_smoothing = 3; k_condition = "lt"; k_signal_value = 20; crossover = "k_cross_up_d"; timeframe = "1h" }
        payload = @{ stoch_k = 18.2; stoch_d = 15.0 }
    },
    @{
        name = "MA"
        kind = "ma"
        params = @{ period = 20; ma_type = "sma"; condition = "price_above"; timeframe = "1h" }
        payload = @{ ma = 64000 }
    }
)

$results = New-Object System.Collections.Generic.List[object]

try {
    foreach ($case in $cases) {
        $cfg = Copy-Object -InputObject $originalConfig
        $cfg.trade_start_conditions = @{
            enabled = $true
            conditions = @(
                @{
                    kind = [string]$case.kind
                    timeframe = $null
                    signal_value = $null
                    params = $case.params
                }
            )
        }
        $null = Invoke-ApiJson -Method PATCH -Path "/bot-plans/$planId" -Headers $ownerHeaders -Body @{ config_json = $cfg }

        Purge-TestJobs

        $payload = @{
            plan_name = $PlanName
            owner_key = $OwnerKey
            symbol = $pairSymbol
            side = "buy"
            timeframe = "1h"
            price = 65000
            volume = 1
        }
        foreach ($k in $case.payload.Keys) { $payload[$k] = $case.payload[$k] }

        $signal = Invoke-ApiJson -Method POST -Path "/signals/tradingview/webhook" -Headers (New-WebhookHeaders) -Body $payload
        $job = Wait-JobBySignalId -SignalId $signal.signal_id
        $reasons = if ($job.risk_checks -and $job.risk_checks.auto_reject_reasons) { @($job.risk_checks.auto_reject_reasons) } else { @() }
        $results.Add([PSCustomObject]@{
            indicator = $case.name
            expected_status = "queued"
            actual_status = $job.status
            pass = ($job.status -eq "queued")
            reject_reasons = ($reasons -join " | ")
        })
    }
}
finally {
    $null = Invoke-ApiJson -Method PATCH -Path "/bot-plans/$planId" -Headers $ownerHeaders -Body @{ config_json = $originalConfig }
}

$results | Format-Table -AutoSize

$failed = @($results | Where-Object { -not $_.pass })
if ($failed.Count -gt 0) {
    Write-Host ""
    Write-Host "FAIL: $($failed.Count) case(s) failed." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "PASS: all indicator payload examples are accepted." -ForegroundColor Green
