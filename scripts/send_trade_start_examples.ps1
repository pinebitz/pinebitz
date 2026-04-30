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

# One row per TECHNICAL_TRADE_START_KINDS in API; payloads chosen to satisfy evaluator.
$cases = @(
    @{ name = "RSI"; kind = "rsi"; params = @{ length = 14; timeframe = "1h"; compare = "lt"; value = 30 }; payload = @{ rsi = 25.4 } }
    @{ name = "Ultimate Oscillator"; kind = "ultimate_oscillator"; params = @{ len_short = 7; len_mid = 14; len_long = 28; compare = "lt"; value = 30; timeframe = "1h" }; payload = @{ ultimate_oscillator = 22.5 } }
    @{ name = "Bollinger %B"; kind = "bollinger_pctb"; params = @{ period = 20; stddev = 2; pctb_condition = "below_lower"; timeframe = "1h" }; payload = @{ pctb = -0.1 } }
    @{ name = "MA"; kind = "ma"; params = @{ period = 20; ma_type = "sma"; condition = "price_above"; timeframe = "1h" }; payload = @{ ma = 64000 } }
    @{ name = "ADX"; kind = "adx"; params = @{ period = 14; threshold = 25; timeframe = "1h" }; payload = @{ adx = 32 } }
    @{ name = "Stochastic"; kind = "stochastic"; params = @{ k_length = 14; k_smoothing = 1; d_smoothing = 3; k_condition = "lt"; k_signal_value = 20; crossover = "k_cross_up_d"; timeframe = "1h" }; payload = @{ stoch_k = 18.2; stoch_d = 15.0 } }
    @{ name = "MACD"; kind = "macd"; params = @{ fast = 12; slow = 26; signal_line = 9; macd_trigger = "crossing_up"; line_trigger = "less_than_0"; timeframe = "1h" }; payload = @{ macd_macd_line = -0.12; macd_signal = -0.18 } }
    @{ name = "Parabolic SAR"; kind = "parabolic_sar"; params = @{ step = 0.02; max_af = 0.2; trigger = "flip_bull"; timeframe = "1h" }; payload = @{ sar = 63000 } }
    @{ name = "MFI"; kind = "mfi"; params = @{ length = 14; compare = "lt"; value = 20; timeframe = "1h" }; payload = @{ mfi = 17.5 } }
    @{ name = "CCI"; kind = "cci"; params = @{ length = 20; compare = "lt"; value = -100; timeframe = "1h" }; payload = @{ cci = -120 } }
    @{ name = "Heikin Ashi"; kind = "heikin_ashi"; params = @{ trend = "bullish"; timeframe = "1h" }; payload = @{ heikin_ashi = @{ trend = "bullish" } } }
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
        $tradeDetail = ""
        if ($job.risk_checks -and $job.risk_checks.trade_start -and $job.risk_checks.trade_start.detail_rows) {
            foreach ($dr in @($job.risk_checks.trade_start.detail_rows)) {
                if ($null -eq $dr) { continue }
                if (($dr.reason -eq $null -or [string]::IsNullOrEmpty([string]$dr.reason)) -and $dr.note) {
                    continue
                }
                $tradeDetail = [string]$dr.reason
                break
            }
        }
        $results.Add([PSCustomObject]@{
            kind = [string]$case.kind
            indicator = $case.name
            expected_status = "queued"
            actual_status = $job.status
            pass = ($job.status -eq "queued")
            trade_start_reason = $tradeDetail
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
    Write-Host "FAIL: $($failed.Count) indicator(s). Remove these kinds from mandatory trade-start clauses until payloads are wired:" -ForegroundColor Red
    $failed | ForEach-Object {
        Write-Host ("  - {0} ({1}) :: {2}" -f $_.kind, $_.indicator, $(if ([string]::IsNullOrWhiteSpace($_.reject_reasons)) { $_.trade_start_reason } else { $_.reject_reasons }))
    }
    Write-Host ""
    Write-Host ("Passed ({0}/{1}):" -f ($results.Count - $failed.Count), $results.Count) -ForegroundColor Green
    (($results | Where-Object { $_.pass }) | ForEach-Object { "{0}`t{1}" -f $_.kind, $_.indicator }) | Out-String | Write-Host
    exit 1
}

Write-Host ""
Write-Host "PASS: all $($results.Count) technical indicator acceptance cases passed." -ForegroundColor Green
