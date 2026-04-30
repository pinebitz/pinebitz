param(
    [string]$ApiBaseUrl = "http://127.0.0.1:8000",
    [Parameter(Mandatory = $true)][string]$OwnerKey,
    [Parameter(Mandatory = $true)][string]$PlanName,
    [string]$WebhookSecret = "",
    [string]$Symbol = "BTCUSDT",
    [switch]$ApplyPrune
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

function Get-TradeStartTechnicalLabelMap {
    return [ordered]@{
        rsi = 'RSI'
        ultimate_oscillator = 'Ultimate Oscillator'
        bollinger_pctb = 'Bollinger Bands %B'
        ma = 'Moving Average (MA)'
        adx = 'Average Directional Index'
        stochastic = 'Stochastic'
        macd = 'MACD'
        parabolic_sar = 'Parabolic SAR'
        mfi = 'Money Flow Index'
        cci = 'Commodity Channel Index'
        heikin_ashi = 'Heikin Ashi'
    }
}

function Rewrite-TechnicalTradeStartKinds {
    param(
        [Parameter(Mandatory = $true)][string[]]$KindsToKeepSorted
    )
    $repoRoot = Split-Path -Parent $PSScriptRoot
    $labelMap = Get-TradeStartTechnicalLabelMap
    foreach ($k in $KindsToKeepSorted) {
        if (-not $labelMap.Contains($k)) {
            throw "Unknown technical kind '$k' (not in label map)"
        }
    }

    # --- pinebitz/web/dashboard.js ---
    $jsPath = Join-Path $repoRoot "pinebitz\web\dashboard.js"
    $jsRaw = Get-Content -LiteralPath $jsPath -Raw -Encoding UTF8
    $startMarker = 'const TRADE_START_KINDS = ['
    $needleAfter = "`nconst TRADE_START_DIRECT_KINDS"
    $si = $jsRaw.IndexOf($startMarker)
    if ($si -lt 0) { throw "TRADE_START_KINDS block not found in dashboard.js" }
    $sj = $jsRaw.IndexOf($needleAfter, $si + $startMarker.Length)
    if ($sj -lt 0) { throw "TRADE_START_DIRECT_KINDS anchor not found in dashboard.js" }

    $bodyLines = @(
        "  { v: 'tv_webhook', label: 'TradingView custom signal' },",
        "  { v: 'tv_screener', label: 'TradingView Crypto Screener' },",
        "  { v: 'qfl_long', label: 'QFL (only long signals)' },"
    )
    foreach ($k in $KindsToKeepSorted) {
        $lab = [string]$labelMap[$k]
        $escaped = ($lab.Replace("'", "\'"))
        $bodyLines += "  { v: '$k', label: '$escaped' },"
    }
    $before = $jsRaw.Substring(0, $si + $startMarker.Length)
    $after = $jsRaw.Substring($sj)
    $newJs = "$before`n" + ($bodyLines -join "`n") + "`n];$after"

    # TRADE_START_PAYLOAD_KINDS … new Set([ … ]);
    $tag = 'const TRADE_START_PAYLOAD_KINDS = new Set(['
    $ps = $newJs.IndexOf($tag)
    if ($ps -lt 0) { throw "TRADE_START_PAYLOAD_KINDS not found in dashboard.js" }
    $closeToken = "`n]);"
    $pe = $newJs.IndexOf($closeToken, $ps + $tag.Length)
    if ($pe -lt 0) { throw "could not find end of TRADE_START_PAYLOAD_KINDS block" }
    $peExclusive = $pe + $closeToken.Length
    $payloadLines = @()
    foreach ($k in $KindsToKeepSorted) {
        $payloadLines += "  '$k',"
    }
    if ($payloadLines.Count -eq 0) {
        $pjBlockNew = "const TRADE_START_PAYLOAD_KINDS = new Set([`n]);"
    } else {
        $pjBlockNew = "const TRADE_START_PAYLOAD_KINDS = new Set([`n" + ($payloadLines -join "`n") + "`n]);"
    }
    $newJs = $newJs.Substring(0, $ps) + $pjBlockNew + $newJs.Substring($peExclusive)

    Set-Content -LiteralPath $jsPath -Encoding UTF8 -Value $newJs -NoNewline

    # --- pinebitz/api/app.py ---
    $pyPath = Join-Path $repoRoot "pinebitz\api\app.py"
    $pyRaw = Get-Content -LiteralPath $pyPath -Raw -Encoding UTF8
    $frozeLines = @()
    foreach ($k in $KindsToKeepSorted) {
        $frozeLines += "    `"$k`","
    }
    if ($frozeLines.Count -eq 0) {
        $pyBlockNew = "TECHNICAL_TRADE_START_KINDS = frozenset({})`n"
    }
    else {
        $pyBlockNew = @"
TECHNICAL_TRADE_START_KINDS = frozenset({
$( $frozeLines -join "`n" )
})

"@
    }

    $bs = $pyRaw.IndexOf('TECHNICAL_TRADE_START_KINDS = frozenset({')
    if ($bs -lt 0) { throw "TECHNICAL_TRADE_START_KINDS marker not found in app.py" }
    $brace = $pyRaw.IndexOf('{', $bs)
    if ($brace -lt 0) { throw "frozenset opening brace not found in app.py" }
    $depth = 0
    $closingParen = $null
    for ($xi = $brace; $xi -lt $pyRaw.Length; $xi++) {
        $ch = [string]$pyRaw[$xi]
        if ($ch -eq '{') { $depth++; continue }
        if ($ch -eq '}') {
            $depth--
            if ($depth -eq 0) {
                if (($xi + 1 -ge $pyRaw.Length) -or ([string]$pyRaw[$xi + 1] -ne ')')) {
                    throw "frozenset set literal closing `}` missing `)` in app.py near position $xi"
                }
                $closingParen = $xi + 1
                break
            }
        }
    }
    if ($null -eq $closingParen) { throw 'could not match frozenset { ... } in TECHNICAL_TRADE_START_KINDS' }
    $be = [int]$closingParen + 1
    while (($be -lt $pyRaw.Length) -and [char]::IsWhiteSpace([char]$pyRaw[$be])) { $be++ }
    $replacement = ($pyBlockNew.TrimEnd("`r", "`n") + "`r`n")
    $newPy = $pyRaw.Substring(0, $bs) + $replacement + $pyRaw.Substring($be)
    Set-Content -LiteralPath $pyPath -Encoding UTF8 -Value $newPy -NoNewline
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
$passedKindsSorted = @(($results | Where-Object { $_.pass } | ForEach-Object { [string]$_.kind }) | Sort-Object -Unique)

if ($ApplyPrune -and $failed.Count -gt 0) {
    Write-Host ""
    Write-Host "Applying prune: rewriting dashboard.js + api/app.py to keep only PASSED technical kinds." -ForegroundColor Yellow
    if ($passedKindsSorted.Count -eq 0) {
        Write-Host "WARNING: zero technical kinds passed — UI/backend will expose only webhook / screener / QFL." -ForegroundColor Yellow
    }
    Rewrite-TechnicalTradeStartKinds -KindsToKeepSorted $passedKindsSorted
    Write-Host ("PRUNE DONE. Kept [{0}]" -f ($passedKindsSorted -join ', ')) -ForegroundColor Cyan
    exit 0
}

if ($failed.Count -gt 0) {
    Write-Host ""
    Write-Host "FAIL: $($failed.Count) indicator(s)." -ForegroundColor Red
    $failed | ForEach-Object {
        Write-Host ("  - {0} ({1}) :: {2}" -f $_.kind, $_.indicator, $(if ([string]::IsNullOrWhiteSpace($_.reject_reasons)) { $_.trade_start_reason } else { $_.reject_reasons }))
    }
    Write-Host ""
    Write-Host "To auto-remove failed kinds from the codebase, re-run:" -ForegroundColor Yellow
    Write-Host "  make trade-start-examples-prune OWNER_KEY=$OwnerKey PLAN_NAME=`"$PlanName`"" -ForegroundColor Yellow
    Write-Host ""
    Write-Host ("Passed ({0}/{1}):" -f ($results.Count - $failed.Count), $results.Count) -ForegroundColor Green
    (($results | Where-Object { $_.pass }) | ForEach-Object { "{0}`t{1}" -f $_.kind, $_.indicator }) | Out-String | Write-Host
    exit 1
}

Write-Host ""
Write-Host "PASS: all $($results.Count) technical indicator acceptance cases passed." -ForegroundColor Green
