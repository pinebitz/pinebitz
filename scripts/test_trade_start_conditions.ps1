param(
    [string]$ApiBaseUrl = "http://127.0.0.1:18000",
    [Parameter(Mandatory = $true)][string]$OwnerKey,
    [Parameter(Mandatory = $true)][string]$PlanName,
    [string]$WebhookSecret = "",
    [string]$Symbol = "BTCUSDT",
    [switch]$VerboseDetail
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Set-Location (Split-Path -Parent $PSScriptRoot)

$TechnicalKinds = @(
    "rsi", "macd", "stochastic", "stoch", "stoch_rsi", "adx", "atr", "ema", "sma", "bb", "bollinger"
)

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
    $json = $Body | ConvertTo-Json -Depth 20
    return Invoke-RestMethod -Method $Method -Uri $uri -Headers $Headers -ContentType "application/json" -Body $json -TimeoutSec 30
}

function New-WebhookHeaders {
    $h = @{
        "X-Owner-Key" = $OwnerKey
    }
    if ($WebhookSecret) {
        $h["X-Webhook-Secret"] = $WebhookSecret
    }
    return $h
}

function Purge-TestJobs {
    $headers = @{ "X-Owner-Key" = $OwnerKey }
    $null = Invoke-ApiJson -Method POST -Path "/execution/jobs/purge-test?statuses=queued,rejected,approved,sent" -Headers $headers
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

function Get-AuditForJob {
    param([Parameter(Mandatory = $true)][string]$JobId)
    $headers = @{ "X-Owner-Key" = $OwnerKey }
    $audit = Invoke-ApiJson -Method GET -Path "/execution/audit?job_id=$JobId&limit=20&offset=0" -Headers $headers
    foreach ($event in $audit.items) {
        if ($event.event_type -eq "job_created") {
            return $event
        }
    }
    return $null
}

function Invoke-TestCase {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][hashtable]$Payload,
        [Parameter(Mandatory = $true)][string]$ExpectedStatus,
        [string[]]$ExpectedReasonContains = @()
    )

    Purge-TestJobs
    $signal = Invoke-ApiJson -Method POST -Path "/signals/tradingview/webhook" -Headers (New-WebhookHeaders) -Body $Payload
    $job = Wait-JobBySignalId -SignalId $signal.signal_id
    $auditEvent = Get-AuditForJob -JobId $job.job_id

    $riskChecks = $job.risk_checks
    $reasons = @()
    if ($riskChecks -and $riskChecks.auto_reject_reasons) {
        $reasons = @($riskChecks.auto_reject_reasons)
    }
    $reasonText = ($reasons -join " | ")

    $statusOk = ($job.status -eq $ExpectedStatus)
    $reasonsOk = $true
    foreach ($token in $ExpectedReasonContains) {
        if (-not $reasonText.Contains($token)) {
            $reasonsOk = $false
        }
    }
    if ($ExpectedStatus -eq "queued" -and $reasons.Count -gt 0) {
        $reasonsOk = $false
    }

    $auditHasTradeStart = $false
    $tradeStart = $null
    if ($auditEvent -and $auditEvent.details -and $null -ne $auditEvent.details.trade_start) {
        $auditHasTradeStart = $true
    }
    if ($riskChecks -and $null -ne $riskChecks.trade_start) {
        $tradeStart = $riskChecks.trade_start
    }

    $passed = $statusOk -and $reasonsOk -and $auditHasTradeStart

    return [PSCustomObject]@{
        case_name = $Name
        pass = $passed
        expected_status = $ExpectedStatus
        actual_status = $job.status
        expected_reason_tokens = ($ExpectedReasonContains -join ",")
        actual_reasons = $reasonText
        signal_id = $signal.signal_id
        job_id = $job.job_id
        audit_has_trade_start = $auditHasTradeStart
        trade_start = $tradeStart
    }
}

$ownerHeaders = @{ "X-Owner-Key" = $OwnerKey }
$plans = Invoke-ApiJson -Method GET -Path "/bot-plans?limit=200&offset=0" -Headers $ownerHeaders
$plan = $null
foreach ($p in $plans.items) {
    if ($p.name -eq $PlanName) {
        $plan = $p
        break
    }
}
if ($null -eq $plan) {
    throw "Plan '$PlanName' not found for owner '$OwnerKey'"
}

$config = $plan.config_json
$tsc = $config.trade_start_conditions
if ($null -eq $tsc -or -not $tsc.enabled) {
    throw "Plan '$PlanName' has trade_start_conditions disabled"
}

$conditions = @()
if ($tsc.conditions) {
    $conditions = @($tsc.conditions)
}
if ($conditions.Count -eq 0) {
    throw "Plan '$PlanName' has no trade_start_conditions rows"
}

$screener = $null
$qflLongExists = $false
$technicalIndexes = @()

for ($i = 0; $i -lt $conditions.Count; $i++) {
    $row = $conditions[$i]
    $kind = [string]$row.kind
    if ($kind -eq "tv_screener" -and $null -eq $screener) {
        $screener = $row
    }
    if ($kind -eq "qfl_long") {
        $qflLongExists = $true
    }
    if ($TechnicalKinds -contains $kind) {
        $technicalIndexes += $i
    }
}

$passTimeframe = "15m"
$passSide = "buy"

if ($screener -and $screener.params -and $screener.params.timeframe) {
    $passTimeframe = [string]$screener.params.timeframe
}
if ($screener -and $screener.params -and $screener.params.signal_value) {
    $passSide = [string]$screener.params.signal_value
}
if ($qflLongExists) {
    $passSide = "buy"
}

$failTimeframe = if ($passTimeframe -eq "1h") { "15m" } else { "1h" }
$failSide = if ($passSide -eq "buy" -or $passSide -eq "long") { "sell" } else { "buy" }

$proofByIndex = @{}
foreach ($idx in $technicalIndexes) {
    $proofByIndex["$idx"] = $true
}

$basePayload = @{
    plan_name = $PlanName
    owner_key = $OwnerKey
    symbol = $Symbol
    side = $passSide
    timeframe = $passTimeframe
    price = 100.0
    volume = 1.0
}

$results = New-Object System.Collections.Generic.List[object]

if ($qflLongExists -and ($passSide -ne "buy")) {
    $results.Add([PSCustomObject]@{
        case_name = "A_all_pass"
        pass = $false
        expected_status = "queued"
        actual_status = "skipped"
        expected_reason_tokens = ""
        actual_reasons = "plan has qfl_long but pass side is not buy"
        signal_id = ""
        job_id = ""
        audit_has_trade_start = $false
    })
}
else {
    $payloadA = $basePayload.Clone()
    if ($technicalIndexes.Count -gt 0) {
        $payloadA["pinebitz_tsc"] = @{ by_index = $proofByIndex }
    }
    $results.Add((Invoke-TestCase -Name "A_all_pass" -Payload $payloadA -ExpectedStatus "queued"))
}

if ($null -ne $screener) {
    $payloadB = $basePayload.Clone()
    $payloadB["timeframe"] = $failTimeframe
    if ($technicalIndexes.Count -gt 0) {
        $payloadB["pinebitz_tsc"] = @{ by_index = $proofByIndex }
    }
    $results.Add((Invoke-TestCase -Name "B_screener_timeframe_mismatch" -Payload $payloadB -ExpectedStatus "rejected" -ExpectedReasonContains @("screener_timeframe_mismatch")))

    $payloadC = $basePayload.Clone()
    $payloadC["side"] = $failSide
    if ($technicalIndexes.Count -gt 0) {
        $payloadC["pinebitz_tsc"] = @{ by_index = $proofByIndex }
    }
    $results.Add((Invoke-TestCase -Name "C_screener_side_mismatch" -Payload $payloadC -ExpectedStatus "rejected" -ExpectedReasonContains @("screener_side_mismatch")))
}
else {
    $results.Add([PSCustomObject]@{
        case_name = "B_screener_timeframe_mismatch"
        pass = $true
        expected_status = "rejected"
        actual_status = "skipped"
        expected_reason_tokens = "screener_timeframe_mismatch"
        actual_reasons = "no tv_screener row in plan"
        signal_id = ""
        job_id = ""
        audit_has_trade_start = $false
    })
    $results.Add([PSCustomObject]@{
        case_name = "C_screener_side_mismatch"
        pass = $true
        expected_status = "rejected"
        actual_status = "skipped"
        expected_reason_tokens = "screener_side_mismatch"
        actual_reasons = "no tv_screener row in plan"
        signal_id = ""
        job_id = ""
        audit_has_trade_start = $false
    })
}

if ($technicalIndexes.Count -gt 0) {
    $payloadD = $basePayload.Clone()
    $results.Add((Invoke-TestCase -Name "D_technical_without_payload_values" -Payload $payloadD -ExpectedStatus "rejected"))
}
else {
    $results.Add([PSCustomObject]@{
        case_name = "D_technical_without_payload_values"
        pass = $true
        expected_status = "rejected"
        actual_status = "skipped"
        expected_reason_tokens = ""
        actual_reasons = "no technical row in plan"
        signal_id = ""
        job_id = ""
        audit_has_trade_start = $false
    })
}

$results | Format-Table -AutoSize

if ($VerboseDetail) {
    Write-Host ""
    Write-Host "=== Verbose trade_start detail ===" -ForegroundColor Cyan
    foreach ($r in $results) {
        Write-Host ""
        Write-Host ("[{0}] pass={1} status={2}" -f $r.case_name, $r.pass, $r.actual_status) -ForegroundColor Yellow
        if ($null -eq $r.trade_start) {
            Write-Host "trade_start: <none>"
            continue
        }
        $r.trade_start | ConvertTo-Json -Depth 20
    }
}

$failed = @($results | Where-Object { -not $_.pass })
if ($failed.Count -gt 0) {
    Write-Host ""
    Write-Host "FAIL: $($failed.Count) case(s) failed." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "PASS: all cases passed." -ForegroundColor Green
