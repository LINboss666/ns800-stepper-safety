# sw1_cut.ps1 - PHASE8-SW1-HARDWARE-CUT-20261022 collector (ASCII only)
# Modes: -Mode SelfTest | Preflight | Identify -Position A|B | Cut
# Rules implemented (review F-01/F-02 + task 4):
#   * every permit write is the LITERAL 'pin write PC.23 low' / 'pin write PC.23 high' (never PC.<num>)
#   * full RX stream and selected replies are stored separately; disqualifiers are kept, not swallowed
#   * a query is INVALID on: echo-only, timeout, conflicting duplicates, 'command not found',
#     'Parameter invalid', boot banner -> never a PASS
#   * cleanup decision requires IO87=low AND IO70=high together; at most one extra LOW
#   * monotonic host budget; on overrun LOW is issued first and no further HIGH; overrun => non-zero rc
#   * new run id, refuses to overwrite existing files; no rm -rf, no background retry
param(
    [string]$Mode = 'SelfTest',
    [string]$Position = 'A',
    [int]$IdentifyBudgetMs = 1000,
    [int]$CutBudgetMs = 10000,
    [int]$LeadInSec = 0,
    [string]$RunId = '',
    [string]$RootOut = ''
)

$ErrorActionPreference = 'Stop'
$LITERAL_LOW  = 'pin write PC.23 low'
$LITERAL_HIGH = 'pin write PC.23 high'
$CMD_WHITELIST = @(
    'pin num PC.23', 'pin num PC.6',
    'pin read PC.23', 'pin read PC.6',
    $LITERAL_LOW, $LITERAL_HIGH,
    'motor_status'
)
$DISQUALIFIERS = @('command not found', 'Parameter invalid', 'unknown command', 'Usage:',
                   'RT-Thread', '[BOOT]', 'Phase-7', 'build Sep', 'build Oct')

# ---------- adapter (real serial / fake scripted) ----------
$script:Adapter = 'none'
$script:Port = $null
$script:FakePending = ''
$script:FakeScript = @{}
$script:FakeTx = New-Object System.Collections.ArrayList
$script:RxAll = New-Object System.Collections.ArrayList

function Open-Real {
    $p = New-Object System.IO.Ports.SerialPort('COM5', 115200, 'None', 8, 'One')
    $p.ReadTimeout = 50
    $p.WriteTimeout = 1000
    $p.Open()
    $script:Port = $p
    $script:Adapter = 'real'
}
function Ad-Send([string]$cmd) {
    if ($CMD_WHITELIST -notcontains $cmd) { throw ("REFUSED non-whitelist command: " + $cmd) }
    [void]$script:FakeTx.Add($cmd)
    Add-Trace ("SEND " + (Get-Date -Format 'HH:mm:ss.fff') + " " + $cmd)
    if ($script:Adapter -eq 'real') {
        $script:Port.WriteLine($cmd)
    } else {
        $rep = ''
        if ($script:FakeScript.ContainsKey($cmd)) {
            $q = $script:FakeScript[$cmd]
            if ($q.Count -gt 0) {
                $rep = $q[0]
                if ($rep -eq 'ECHO_ONLY') { $rep = '' }
                if ($q.Count -gt 1) { $script:FakeScript[$cmd] = $q[1..($q.Count-1)] } else { $script:FakeScript[$cmd] = @() }
            }
        }
        $script:FakePending = $cmd + "`r`n" + $rep
    }
}
function Ad-Poll {
    if ($script:Adapter -eq 'real') {
        try { $s = $script:Port.ReadExisting() } catch { throw ("SERIAL-EXCEPTION: " + $_.Exception.Message) }
    } else {
        $s = $script:FakePending; $script:FakePending = ''
    }
    if ($s -and $s.Length) {
        [void]$script:RxAll.Add((Get-Date -Format 'HH:mm:ss.fff') + "|" + ($s -replace "`r|`n", '\n'))
    }
    return $s
}
function Ad-Close {
    if ($script:Adapter -eq 'real' -and $script:Port) { try { $script:Port.Close() } catch { } }
    $script:Adapter = 'none'
}

# ---------- output ----------
$script:OutFiles = @()
function Set-OutDir([string]$dir) {
    if (Test-Path $dir) { throw ("REFUSED-OUTDIR exists (no overwrite): " + $dir) }
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
    $script:TracePath = Join-Path $dir 'command_trace.txt'
    $script:LedgerPath = Join-Path $dir 'state_ledger.csv'
    $script:RxPath = Join-Path $dir 'rx_full.txt'
    $script:SummaryPath = Join-Path $dir 'summary.txt'
    "host_time,section,cmd_or_event,expect,actual,ms_from_ref,note" | Out-File -FilePath $script:LedgerPath -Encoding utf8
    "" | Out-File -FilePath $script:TracePath -Encoding utf8
}
function Add-Trace([string]$s) {
    if ($script:TracePath) { $s | Out-File -FilePath $script:TracePath -Append -Encoding utf8 }
}
function Add-Ledger($section, $cmd, $expect, $actual, $ms, $note) {
    ((Get-Date -Format 'HH:mm:ss.fff'), $section, $cmd, $expect, $actual, $ms, $note -join ',') |
        Out-File -FilePath $script:LedgerPath -Append -Encoding utf8
}
function Flush-Rx {
    if ($script:RxPath) {
        "=== full received text (raw, as polled; selected replies are in command_trace.txt) ===" |
            Out-File -FilePath $script:RxPath -Encoding utf8
        $script:RxAll | Out-File -FilePath $script:RxPath -Append -Encoding utf8
    }
}

# ---------- query with validation ----------
# returns @{ok=..; level='high|low|'; reason='..'}
function Query-Level([string]$cmd, [int]$pinNum, [double]$timeoutSec, [string]$section) {
    $pat = 'pin\[' + $pinNum + '\] = (high|low)'
    $script:buf = ''
    Ad-Send $cmd
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $timeoutSec) {
        Start-Sleep -m 20
        $script:buf += Ad-Poll
        $ms = [regex]::Matches($script:buf, $pat)
        $bad = @($DISQUALIFIERS | Where-Object { $script:buf -match [regex]::Escape($_) })
        if ($bad.Count -gt 0) {
            Add-Trace ("  INVALID disqualifier='" + ($bad -join '|') + "' window=" + ($script:buf -replace "`r|`n", '\n'))
            Add-Ledger $section $cmd 'high|low' 'INVALID' '' ('disqualifier:' + ($bad -join '|'))
            return @{ ok = $false; level = ''; reason = 'INVALID_OUTPUT' }
        }
        if ($ms.Count -gt 1) {
            $vals = @($ms | ForEach-Object { $_.Groups[1].Value } | Select-Object -Unique)
            if ($vals.Count -gt 1) {
                Add-Ledger $section $cmd 'high|low' 'CONFLICT' '' 'duplicate replies disagree'
                return @{ ok = $false; level = ''; reason = 'CONFLICTING_REPLIES' }
            }
        }
        if ($ms.Count -ge 1) {
            $lv = $ms[$ms.Count - 1].Groups[1].Value
            Add-Trace ("  REPLY pin[" + $pinNum + "] = " + $lv)
            Add-Ledger $section $cmd '-' $lv '' 'ok'
            return @{ ok = $true; level = $lv; reason = '' }
        }
    }
    Add-Trace ("  NO-REPLY within " + $timeoutSec + "s (echo only?) buffer=[" + ($script:buf -replace "`r|`n", '\n') + "]")
    Add-Ledger $section $cmd 'high|low' 'NO-REPLY' '' 'timeout'
    return @{ ok = $false; level = ''; reason = 'NO_REPLY' }
}

function Query-Motor([string]$section) {
    $cmd = 'motor_status'
    $script:buf = ''
    Ad-Send $cmd
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt 2.0) {
        Start-Sleep -m 20
        $script:buf += Ad-Poll
        $bad = @($DISQUALIFIERS | Where-Object { $script:buf -match [regex]::Escape($_) })
        if ($bad.Count -gt 0) { return @{ ok = $false; reason = 'INVALID_OUTPUT' } }
        $m = [regex]::Match($script:buf, '\[MOT\] state=\d+ cur=(\d+) tgt=(\d+).*?\[MOT\] armed=(\d)', 'Singleline')
        if ($m.Success) {
            return @{ ok = $true; cur = [int]$m.Groups[1].Value; tgt = [int]$m.Groups[2].Value; arm = [int]$m.Groups[3].Value }
        }
    }
    return @{ ok = $false; reason = 'NO_REPLY' }
}

# ---------- permit helpers ----------
function Write-Low([string]$section) { Ad-Send $LITERAL_LOW; Start-Sleep -m 60; [void](Ad-Poll) }
function Read-Pair([string]$section, [double]$t) {
    $a = Query-Level 'pin read PC.23' 87 $t ($section + '/io87')
    $b = Query-Level 'pin read PC.6'  70 $t ($section + '/io70')
    return @{ p87 = $a; p70 = $b }
}
function Establish-LowBaseline([string]$section) {
    # returns @{ok; reason}
    $r = Read-Pair $section 1.5
    if (-not $r.p87.ok -or -not $r.p70.ok) { return @{ ok = $false; reason = $(if ($r.p87.reason) { $r.p87.reason } else { $r.p70.reason }) } }
    Add-Ledger $section 'baseline' 'low/high' ($r.p87.level + '/' + $r.p70.level) '' ''
    if ($r.p87.level -ne 'low') {
        Write-Low ($section + '/recover')          # LITERAL PC.23, never PC.<num>
        $c = Query-Level 'pin read PC.23' 87 1.5 ($section + '/recover/io87')
        $d = Query-Level 'pin read PC.6'  70 1.5 ($section + '/recover/io70')
        if (-not $c.ok -or -not $d.ok) { return @{ ok = $false; reason = 'NO_REPLY' } }
        Add-Ledger $section 'after-recover' 'low/high' ($c.level + '/' + $d.level) '' ''
        if ($c.level -ne 'low' -or $d.level -ne 'high') { return @{ ok = $false; reason = 'BASELINE_NOT_ESTABLISHED' } }
    } elseif ($r.p70.level -ne 'high') {
        return @{ ok = $false; reason = 'BASELINE_IO70_NOT_HIGH' }
    }
    return @{ ok = $true; reason = '' }
}

# ---------- cleanup: LOW + IO87 low AND IO70 high, jointly ----------
function Invoke-Cleanup([string]$section) {
    $result = 'UNKNOWN'
    try {
        Write-Low $section
        $r = Read-Pair ($section + '/cleanup') 1.5
        $ok = ($r.p87.ok -and $r.p70.ok -and $r.p87.level -eq 'low' -and $r.p70.level -eq 'high')
        Add-Ledger $section 'cleanup-1' 'low AND high' ($r.p87.level + '/' + $r.p70.level) '' ('joint=' + $ok)
        if ($ok) { return 'LOW_AND_HIGH_CONFIRMED' }
        Write-Low ($section + '/cleanup-2')
        $r2 = Read-Pair ($section + '/cleanup-2') 1.5
        $ok2 = ($r2.p87.ok -and $r2.p70.ok -and $r2.p87.level -eq 'low' -and $r2.p70.level -eq 'high')
        Add-Ledger $section 'cleanup-2' 'low AND high' ($r2.p87.level + '/' + $r2.p70.level) '' ('joint=' + $ok2)
        if ($ok2) { return 'LOW_AND_HIGH_CONFIRMED_ON_2ND' }
        return 'FINAL_STATE_UNKNOWN'
    } catch {
        Add-Trace ('CLEANUP-EXCEPTION ' + $_.Exception.Message)
        return ('CLEANUP_EXCEPTION')
    }
}

# ---------- Identify one SW1 position ----------
function Do-Identify([string]$pos) {
    $sec = 'identify-' + $pos
    $b = Establish-LowBaseline ($sec + '/pre')
    if (-not $b.ok) { return @{ verdict = 'BLOCKED_BASELINE'; reason = $b.reason } }
    $sw = [Diagnostics.Stopwatch]::StartNew()
    Ad-Send $LITERAL_HIGH; Start-Sleep -m 40; [void](Ad-Poll)
    $h87 = Query-Level 'pin read PC.23' 87 0.4 ($sec + '/high/io87')
    $h70 = Query-Level 'pin read PC.6'  70 0.4 ($sec + '/high/io70')
    $ms = [int]$sw.Elapsed.TotalMilliseconds
    Write-Low $sec
    $p = Read-Pair ($sec + '/post') 1.5
    if ($ms -gt $IdentifyBudgetMs) {
        Add-Ledger $sec 'budget' ('<=' + $IdentifyBudgetMs + 'ms') $ms '' 'BUDGET_EXCEEDED: LOW issued first, no further HIGH'
        return @{ verdict = 'INCOMPLETE_BUDGET'; reason = ('HIGH->LOW ' + $ms + 'ms') }
    }
    if (-not $h87.ok -or -not $h70.ok -or -not $p.p87.ok -or -not $p.p70.ok) {
        return @{ verdict = 'INCOMPLETE_SAMPLE'; reason = 'lost or invalid reply' }
    }
    if ($h87.level -ne 'high') { return @{ verdict = 'DRIVE_NOT_ESTABLISHED'; reason = ('io87=' + $h87.level) } }
    if ($p.p87.level -ne 'low' -or $p.p70.level -ne 'high') { return @{ verdict = 'RECOVERY_NOT_CONFIRMED'; reason = ($p.p87.level + '/' + $p.p70.level) } }
    Add-Ledger $sec 'result' 'io87=high' ('io70=' + $h70.level) $ms ('position ' + $pos)
    return @{ verdict = 'OBSERVED'; io70 = $h70.level; ms = $ms }
}

# ---------- Cut (C section) ----------
function Do-Cut {
    $sec = 'cut'
    $b = Establish-LowBaseline ($sec + '/pre')
    if (-not $b.ok) { return @{ verdict = 'BLOCKED_BASELINE'; reason = $b.reason } }
    if ($LeadInSec -gt 0) {
        Add-Trace ('LEAD-IN ' + $LeadInSec + 's in LOW state before HIGH window')
        for ($i = $LeadInSec; $i -gt 0; $i--) {
            Add-Trace ('  starting in ' + $i + 's')
            try { [Console]::Beep(500, 70) } catch { }
            Start-Sleep -m 1000
        }
        Add-Trace '  T-0: HIGH window opens on the next line'
        try { [Console]::Beep(1400, 250) } catch { }
    }
    $sw = [Diagnostics.Stopwatch]::StartNew()
    Ad-Send $LITERAL_HIGH; Start-Sleep -m 40; [void](Ad-Poll)
    $a87 = Query-Level 'pin read PC.23' 87 0.6 ($sec + '/establish/io87')
    $a70 = Query-Level 'pin read PC.6'  70 0.6 ($sec + '/establish/io70')
    if (-not $a87.ok -or -not $a70.ok) { $c = Invoke-Cleanup $sec; return @{ verdict = 'INCOMPLETE_SAMPLE'; cleanup = $c } }
    if ($a87.level -ne 'high')          { $c = Invoke-Cleanup $sec; return @{ verdict = 'DRIVE_NOT_ESTABLISHED'; cleanup = $c } }
    if ($a70.level -ne 'low') {
        $c = Invoke-Cleanup $sec
        return @{ verdict = 'ENABLE_SIDE_NOT_BUILT'; cleanup = $c; reason = ('io70=' + $a70.level) }
    }
    Add-Trace 'HIGH WINDOW OPEN - operator: flip SW1 to the BLOCK position NOW and hold'
    try { [Console]::Beep(1200, 500); Start-Sleep -m 120; [Console]::Beep(1200, 500) } catch { }
    $seq = @()
    $prev87 = 'high'
    $cutPairs = 0
    $lowWritten = $false
    $res = 'WAIT'
    while ($true) {
        $el = [int]$sw.Elapsed.TotalMilliseconds
        if ($el -gt ($CutBudgetMs - 700)) { break }
        $r87 = Query-Level 'pin read PC.23' 87 0.5 ($sec + '/win/io87')
        $r70 = Query-Level 'pin read PC.6'  70 0.5 ($sec + '/win/io70')
        if (-not $r87.ok -or -not $r70.ok) { $res = 'LOST_SAMPLE'; break }
        $seq += , @($r87.level, $r70.level)
        Add-Ledger $sec 'window' 'search io70=high under io87=high' ($r87.level + '/' + $r70.level) $el ''
        if ($r70.level -eq 'high') {
            if ($prev87 -eq 'high' -and $r87.level -eq 'high') { $cutPairs++ }
        }
        if ($cutPairs -ge 2) { $res = 'CUT_OBSERVED'; break }
        if ($r87.level -ne 'high') { $res = 'INTERFERENCE_SOFT_LOW'; break }
        $prev87 = $r87.level
    }
    $msAtLow = [int]$sw.Elapsed.TotalMilliseconds
    $c = Invoke-Cleanup $sec
    $overrun = ($msAtLow -gt $CutBudgetMs)
    return @{ verdict = $res; cutPairs = $cutPairs; ms = $msAtLow; cleanup = $c; overrun = $overrun; seq = $seq }
}

# ---------- SelfTest (offline, drives the real control flow) ----------
function New-FakeScenario($map) {
    $script:FakeScript = @{}
    foreach ($k in $map.Keys) { $script:FakeScript[$k] = @($map[$k]) }
    $script:FakeTx = New-Object System.Collections.ArrayList
    $script:RxAll = New-Object System.Collections.ArrayList
    $script:Adapter = 'fake'
    $script:FakePending = ''
}
function RxFake([string]$txt) {
    # a fake adapter poll hook: pre-queue raw text
    $script:FakePending = $txt
}
function Run-SelfTest {
    $out = New-Object System.Collections.ArrayList
    $script:fails = 0
    $fails = 0
    function Check([string]$name, [bool]$cond, [string]$detail) {
        $line = ('{0,-46} {1}  {2}' -f $name, $(if ($cond) { 'OK  ' } else { 'FAIL' }), $detail)
        Write-Host $line
        if (-not $cond) { $script:fails++ }
        [void]$out.Add($line)
    }
    $r87 = 'pin read PC.23'; $r70 = 'pin read PC.6'

    # 1) identify: normal observation (allow side io70=low)
    New-FakeScenario @{
        'pin read PC.23' = @('pin[87] = low', 'pin[87] = high', 'pin[87] = low');
        'pin read PC.6'  = @('pin[70] = high', 'pin[70] = low', 'pin[70] = high');
    }
    $s = Do-Identify 'A'
    Check 'identify-normal' ($s.verdict -eq 'OBSERVED' -and $s.io70 -eq 'low') ($s.verdict + ' io70=' + $s.io70 + ' ms=' + $s.ms)

    # 2) baseline anomaly -> recovery must use literal PC.23 and must NOT reach a HIGH
    New-FakeScenario @{
        'pin read PC.23' = @('pin[87] = high', 'pin[87] = high');
        'pin read PC.6'  = @('pin[70] = low', 'pin[70] = high');
    }
    $s = Do-Identify 'B'
    $tx = $script:FakeTx -join ';'
    Check 'baseline-anomaly-uses-literal-PC23' ($tx -eq ('pin read PC.23;pin read PC.6;' + $LITERAL_LOW + ';pin read PC.23;pin read PC.6')) ('tx=' + $tx)
    Check 'baseline-anomaly-sends-no-high' ($script:FakeTx -notcontains $LITERAL_HIGH) 'no HIGH write'
    Check 'baseline-anomaly-not-pass' ($s.verdict -ne 'OBSERVED') $s.verdict

    # 3) cleanup: io70 missing on both attempts => must NOT be confirmed
    New-FakeScenario @{
        'pin read PC.23' = @('pin[87] = low', 'pin[87] = low');
        'pin read PC.6'  = @('ECHO_ONLY', 'ECHO_ONLY');
    }
    $c = Invoke-Cleanup 'sec-c3'
    Check 'cleanup-io70-missing-cannot-pass' ($c -eq 'FINAL_STATE_UNKNOWN') $c

    # 4) error output mixed with a valid reply => query INVALID
    New-FakeScenario @{ 'pin read PC.23' = @("pin[87] = low`r`npin read PC.6: command not found."); }
    $q = Query-Level 'pin read PC.23' 87 1.0 'sec-c4'
    Check 'mixed-error-output-rejected' ((-not $q.ok) -and $q.reason -eq 'INVALID_OUTPUT') $q.reason

    # 5) echo only / no reply => refused
    New-FakeScenario @{ 'pin read PC.6' = @(''); }
    $q = Query-Level 'pin read PC.6' 70 0.4 'sec-c5'
    Check 'echo-only-refused' ((-not $q.ok) -and $q.reason -eq 'NO_REPLY') $q.reason

    # 6) conflicting duplicate replies => refused
    New-FakeScenario @{ 'pin read PC.23' = @("pin[87] = high`r`npin[87] = low"); }
    $q = Query-Level 'pin read PC.23' 87 1.0 'sec-c6'
    Check 'conflicting-replies-refused' ((-not $q.ok) -and $q.reason -eq 'CONFLICTING_REPLIES') $q.reason

    # 7) cut: establish HIGH, operator flips mid-window -> io70 low,low,high,high with io87 high around
    New-FakeScenario @{
        'pin read PC.23' = @('pin[87] = low', 'pin[87] = high', 'pin[87] = high', 'pin[87] = high', 'pin[87] = high', 'pin[87] = high', 'pin[87] = low', 'pin[87] = low');
        'pin read PC.6'  = @('pin[70] = high', 'pin[70] = low', 'pin[70] = low', 'pin[70] = low', 'pin[70] = high', 'pin[70] = high', 'pin[70] = high', 'pin[70] = high');
    }
    $CutBudgetMs = 10000
    $s = Do-Cut
    Check 'cut-observed-two-adjacent-samples' ($s.verdict -eq 'CUT_OBSERVED' -and $s.cutPairs -ge 2) ($s.verdict + ' pairs=' + $s.cutPairs + ' ms=' + $s.ms)
    Check 'cut-cleanup-confirmed' ($s.cleanup -like 'LOW_AND_HIGH*') $s.cleanup
    $highIdx = $script:FakeTx.IndexOf($LITERAL_HIGH)
    $lowIdx = $script:FakeTx.IndexOf($LITERAL_LOW)
    Check 'cut-no-low-before-evidence' ($lowIdx -gt $highIdx) ('high@' + $highIdx + ' low@' + $lowIdx)

    # 8) cut: io87 pulled low by software -> INTERFERENCE, cleanup, never pass
    New-FakeScenario @{
        'pin read PC.23' = @('pin[87] = low', 'pin[87] = high', 'pin[87] = high', 'pin[87] = low', 'pin[87] = low', 'pin[87] = low');
        'pin read PC.6'  = @('pin[70] = high', 'pin[70] = low', 'pin[70] = low', 'pin[70] = low', 'pin[70] = high', 'pin[70] = high');
    }
    $s = Do-Cut
    Check 'interference-soft-low-detected' ($s.verdict -eq 'INTERFERENCE_SOFT_LOW') $s.verdict

    # 9) budget overrun must still issue LOW first and no further HIGH
    # 9) budget spent -> window must not run, literal LOW cleanup issued, only one HIGH ever
    New-FakeScenario @{
        'pin read PC.23' = @('pin[87] = low', 'pin[87] = high', 'pin[87] = low', 'pin[87] = low');
        'pin read PC.6'  = @('pin[70] = high', 'pin[70] = low', 'pin[70] = high', 'pin[70] = high');
    }
    $CutBudgetMs = 200
    $s = Do-Cut
    $CutBudgetMs = 10000
    $tx9 = $script:FakeTx
    $nHigh = @($tx9 | Where-Object { $_ -eq $LITERAL_HIGH }).Count
    Check 'budget-spent-skips-window-and-cleans-up' (($s.verdict -eq 'WAIT') -and ($s.cleanup -like 'LOW_AND_HIGH*')) ($s.verdict + ' ' + $s.cleanup)
    Check 'budget-only-one-high-ever-issued' ($nHigh -eq 1) ('high count=' + $nHigh)
    Check 'budget-cleanup-uses-literal-LOW' ($tx9 -contains $LITERAL_LOW) ('tx=' + ($tx9 -join ';'))

    # 10) serial exception during window must still attempt the literal LOW cleanup
    New-FakeScenario @{
        'pin read PC.23' = @('pin[87] = low', 'pin[87] = high', 'pin[87] = low', 'pin[87] = low');
        'pin read PC.6'  = @('pin[70] = high', 'pin[70] = low', 'pin[70] = high', 'pin[70] = high');
    }
    $c = $null
    try {
        $b = Establish-LowBaseline 'sec-c10/pre'
        Ad-Send $LITERAL_HIGH; Start-Sleep -m 20; [void](Ad-Poll)
        $q = Query-Level 'pin read PC.23' 87 0.6 'sec-c10/a'
        throw 'SERIAL-EXCEPTION: simulated port loss'
    } catch {
        $c = Invoke-Cleanup 'sec-c10/exception'
    }
    $tx10 = $script:FakeTx
    Check 'serial-exception-attempts-literal-LOW' (($tx10 -contains $LITERAL_LOW) -and ($tx10.IndexOf($LITERAL_LOW) -gt $tx10.IndexOf($LITERAL_HIGH))) ($c + ' tx=' + ($tx10 -join ';'))
    Check 'serial-exception-cleanup-confirmed' ($c -like 'LOW_AND_HIGH*') $c

    # 11) whitelist guard: non-whitelist command must be refused
    $refused = $false
    try { Ad-Send 'pin mode PC.23 output' } catch { $refused = $true }
    Check 'non-whitelist-command-refused' $refused 'guard'

    ("=== SELFTEST " + $(if ($script:fails -eq 0) { 'PASS' } else { ('FAIL ' + $script:fails) }) + " ===") | Out-File -FilePath $script:SelfTestPath -Encoding utf8
    $out | Out-File -FilePath $script:SelfTestPath -Append -Encoding utf8
    return $script:fails
}

# ---------- main ----------
$rc = 9
try {
    if ([string]::IsNullOrEmpty($RunId)) { $RunId = (Get-Date -Format 'yyyyMMdd_HHmmss') }
    if ([string]::IsNullOrEmpty($RootOut)) { $RootOut = Join-Path $env:TEMP ('sw1_run_' + $RunId) }
    $script:SelfTestPath = Join-Path $RootOut 'selftest.txt'
    if (Test-Path $RootOut) { Write-Host ('REFUSED output dir already exists: ' + $RootOut); exit 4 }
    New-Item -ItemType Directory -Path $RootOut -Force | Out-Null
    Set-OutDir (Join-Path $RootOut 'run')
    Add-Trace ('mode=' + $Mode + ' runid=' + $RunId + ' start=' + (Get-Date -Format o))

    if ($Mode -eq 'SelfTest') {
        $script:Adapter = 'fake'
        $f = Run-SelfTest
        Flush-Rx
        Write-Host ('SELFTEST fails=' + $f + ' out=' + $RootOut)
        if ($f -eq 0) { $rc = 0 } else { $rc = 1 }
    } else {
        Open-Real
        Add-Trace 'COM5 opened 115200 8N1'
        [void](Ad-Poll)   # whatever arrived at open is captured into RxAll, not used as a reply
        if ($Mode -eq 'Identify') {
            $s = Do-Identify $Position
            Add-Trace ('IDENTIFY result: ' + ($s | Out-String))
            if ($s.verdict -eq 'OBSERVED') { $rc = 0 } else { $rc = 1 }
            $c = Invoke-Cleanup 'final'
            if ($c -notlike 'LOW_AND_HIGH*') { $rc = 7 }
        } elseif ($Mode -eq 'Cut') {
            $s = Do-Cut
            Add-Trace ('CUT result verdict=' + $s.verdict + ' pairs=' + $s.cutPairs + ' ms=' + $s.ms + ' cleanup=' + $s.cleanup)
            if ($s.verdict -eq 'CUT_OBSERVED' -and $s.cutPairs -ge 2 -and ($s.cleanup -like 'LOW_AND_HIGH*') -and (-not $s.overrun)) { $rc = 0 }
            elseif ($s.cleanup -like 'FINAL_STATE*' -or $s.cleanup -eq 'CLEANUP_EXCEPTION') { $rc = 7 }
            else { $rc = 1 }
            $f2 = Read-Pair 'terminal' 1.5
            $m = Query-Motor 'terminal'
            Add-Ledger 'terminal' 'pin read + motor_status' 'low/high/armed0' ($f2.p87.level + '/' + $f2.p70.level + '/armed=' + $m.arm) '' ''
        } else {
            Add-Trace 'MODE must be SelfTest | Identify | Cut'
            $rc = 2
        }
        Ad-Close
    }
}
catch {
    Add-Trace ('EXCEPTION ' + $_.Exception.Message)
    Write-Host ('EXCEPTION: ' + $_.Exception.Message)
    if ($script:Adapter -eq 'real') { try { $c = Invoke-Cleanup 'after-exception'; Ad-Close } catch { } }
    if ($rc -eq 0) { $rc = 8 }
    $rc = 8
}
finally {
    Flush-Rx
    ('rc=' + $rc + ' mode=' + $Mode + ' position=' + $Position + ' end=' + (Get-Date -Format o)) |
        Out-File -FilePath $script:SummaryPath -Append -Encoding utf8
    Write-Host ('RC=' + $rc + ' out=' + $RootOut)
}
exit $rc
