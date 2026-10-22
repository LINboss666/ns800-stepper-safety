# io87_follow.ps1 - PHASE8-IO87-IO70-FOLLOW-20261022
# ASCII only. Whitelisted: pin num / pin read PC.23 / pin read PC.6 / pin write PC.23 low|high / motor_status
# Hard budget: HIGH -> LOW issued within 2.0 s (sampling target ~1 s). finally always issues LOW.
$ErrorActionPreference = 'Stop'

$dir = '<WORK>\io87_follow'
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
$tx  = Join-Path $dir 'transcript.txt'
$led = Join-Path $dir 'state_ledger.csv'

"host_time,round,phase,cmd,expect_io87,actual_io87,expect_io70,actual_io70,ms_from_high,note" |
    Out-File -FilePath $led -Encoding utf8

function Log([string]$s) {
    (Get-Date -Format 'HH:mm:ss.fff') + "  " + $s | Out-File -FilePath $tx -Append -Encoding utf8
    Write-Host $s
}
function Ledger($round, $phase, $cmd, $e87, $a87, $e70, $a70, $ms, $note) {
    ((Get-Date -Format 'HH:mm:ss.fff'), $round, $phase, $cmd, $e87, $a87, $e70, $a70, $ms, $note -join ',') |
        Out-File -FilePath $led -Append -Encoding utf8
}

$buf = ''
function Pump { try { $script:buf += $p.ReadExisting() } catch { } }
function WaitFor([string]$pattern, [double]$timeoutSec) {
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $timeoutSec) {
        Start-Sleep -m 20; Pump
        $m = [regex]::Matches($script:buf, $pattern)
        if ($m.Count -gt 0) {
            $last = $m[$m.Count - 1]
            $script:buf = $script:buf.Substring($last.Index + $last.Length)
            return $last
        }
    }
    return $null
}
function SendCmd([string]$cmd, [string]$pattern, [double]$timeoutSec) {
    Log (">>> " + $cmd)
    $p.WriteLine($cmd)
    if (-not $pattern) { Start-Sleep -m 120; Pump; return $null }
    $m = WaitFor $pattern $timeoutSec
    if ($m -eq $null) { Log ("  NO-REPLY within " + $timeoutSec + "s for: " + $cmd); return $null }
    Log ("  REPLY: " + ($m.Value -replace "`r|`n", ' / '))
    return $m
}

$HIGH_BUDGET_MS = 2000
$banner = '(?i)(rt-thread|\[BOOT\]|Phase-7|version:|build Sep|build Oct)'
$verdict = 'NOT_STARTED'
$highIssued = $false

try {
    $p = New-Object System.IO.Ports.SerialPort('COM5', 115200, 'None', 8, 'One')
    $p.ReadTimeout = 50
    $p.WriteTimeout = 1000
    $p.Open()
    Log ('port opened COM5 115200 8N1 at ' + (Get-Date -Format o))
} catch {
    Log ('OPEN-FAILED: ' + $_.Exception.Message)
    Write-Host 'BLOCKED_PORT'; exit 3
}

try {
    # pre-listen 1.2 s: detect reboot banner / residual output
    Start-Sleep -m 1200; Pump
    if ($script:buf -match $banner) {
        Log 'BANNER-AT-OPEN: saving fact, NOT proceeding to HIGH'
        $verdict = 'BLOCKED_RESET_OBSERVED'; exit 4
    }
    $preLen = $script:buf.Length
    Log ('pre-listen drained bytes=' + $preLen + ' (content not used as evidence)')
    $script:buf = ''

    # ---------- A5 preflight ----------
    Log '== A5 preflight =='
    $m87 = SendCmd 'pin num PC.23' 'PC\.23\s*:\s*(\d+)' 1.5
    $m70 = SendCmd 'pin num PC.6'  'PC\.6\s*:\s*(\d+)'   1.5
    if ($m87 -eq $null -or $m70 -eq $null) { $verdict = 'BLOCKED_CAPABILITY'; throw 'pin num unavailable' }
    $n87 = [int]$m87.Groups[1].Value; $n70 = [int]$m70.Groups[1].Value
    Ledger '0' 'A5-num' 'pin num PC.23' '-' ("got=" + $n87) '-' '-' 0 'expect 87'
    Ledger '0' 'A5-num' 'pin num PC.6'  '-' '-' '-' ("got=" + $n70) 0 'expect 70'
    if ($n87 -ne 87 -or $n70 -ne 70) { $verdict = 'BLOCKED_MAPPING'; throw ('mapping mismatch: ' + $n87 + '/' + $n70) }

    $ms1 = SendCmd 'motor_status' '\[MOT\] state=\d+ cur=(\d+) tgt=(\d+)[^\r\n]*\r?\n\[MOT\] armed=(\d)' 2.0
    if ($ms1 -eq $null) { $verdict = 'BLOCKED_CAPABILITY'; throw 'motor_status reply missing' }
    $cur = [int]$ms1.Groups[1].Value; $tgt = [int]$ms1.Groups[2].Value; $arm = [int]$ms1.Groups[3].Value
    Ledger '0' 'A5-motor' 'motor_status' '-' '-' '-' '-' 0 ("armed=" + $arm + " cur=" + $cur + " tgt=" + $tgt)
    if ($arm -ne 0 -or $cur -ne 0 -or $tgt -ne 0) { $verdict = 'BLOCKED_PRECONDITION_MOTION_STATE'; throw 'armed/cur/tgt not zero' }

    $r87 = SendCmd 'pin read PC.23' ('pin\[' + $n87 + '\] = (high|low)') 1.5
    $r70 = SendCmd 'pin read PC.6'  ('pin\[' + $n70 + '\] = (high|low)') 1.5
    if ($r87 -eq $null -or $r70 -eq $null) { $verdict = 'INCOMPLETE_PREFLIGHT'; throw 'initial read missing' }
    $v87 = $r87.Groups[1].Value; $v70 = $r70.Groups[1].Value
    Ledger '0' 'A5-initial' 'pin read' 'low' $v87 'high' $v70 0 'preflight initial state'
    if ($v87 -ne 'low' -or $v70 -ne 'high') {
        Log ('INITIAL_NOT_AS_EXPECTED (io87=' + $v87 + ' io70=' + $v70 + ') -> cancel HIGH, do LOW recovery')
        $verdict = 'CANCELLED_HIGH_INITIAL_MISMATCH'
        SendCmd ('pin write PC.' + $n87 + ' low') $null 0 | Out-Null
        Start-Sleep -m 150; Pump
        $c1 = SendCmd 'pin read PC.23' ('pin\[' + $n87 + '\] = (high|low)') 1.0
        $c2 = SendCmd 'pin read PC.6'  ('pin\[' + $n70 + '\] = (high|low)') 1.0
        Ledger '0' 'LOW-recovery' 'pin write low' 'low' $(if ($c1) { $c1.Groups[1].Value } else { 'NO-REPLY' }) '-' $(if ($c2) { $c2.Groups[1].Value } else { 'NO-REPLY' }) 0 'initial mismatch cleanup'
        exit 6
    }

    # ---------- B: LOW + acquisition capability ----------
    Log '== B LOW capability =='
    SendCmd 'pin write PC.23 low' $null 0.4 | Out-Null
    $b87 = SendCmd 'pin read PC.23' ('pin\[' + $n87 + '\] = (high|low)') 1.5
    $b70 = SendCmd 'pin read PC.6'  ('pin\[' + $n70 + '\] = (high|low)') 1.5
    if ($b87 -eq $null -or $b70 -eq $null -or $b87.Groups[1].Value -ne 'low' -or $b70.Groups[1].Value -ne 'high') {
        $verdict = 'BLOCKED_LOW_CAPABILITY'
        Log 'LOW phase capability not proven (reply missing or wrong level) -> NO HIGH'
        Ledger '0' 'B-low' 'pin write low + reads' 'low' $(if ($b87) { $b87.Groups[1].Value } else { 'NO-REPLY' }) 'high' $(if ($b70) { $b70.Groups[1].Value } else { 'NO-REPLY' }) 0 'capability gate failed'
        exit 7
    }
    Ledger '0' 'B-low' 'pin write low + reads' 'low' 'low' 'high' 'high' 0 'capability proven'

    # ---------- C: up to 3 rounds ----------
    $roundsOk = 0
    for ($r = 1; $r -le 3; $r++) {
        Log ('== ROUND ' + $r + ' ==')
        $pre87 = SendCmd 'pin read PC.23' ('pin\[' + $n87 + '\] = (high|low)') 1.5
        $pre70 = SendCmd 'pin read PC.6'  ('pin\[' + $n70 + '\] = (high|low)') 1.5
        if ($pre87 -eq $null -or $pre70 -eq $null) { $verdict = 'INCOMPLETE_LOST_REPLY'; break }
        $pv87 = $pre87.Groups[1].Value; $pv70 = $pre70.Groups[1].Value
        Ledger $r 'pre' 'pin read both' 'low' $pv87 'high' $pv70 0 'before HIGH'
        if ($pv87 -eq 'low' -and $pv70 -eq 'low') {
            $verdict = 'DISABLE_SIDE_MISMATCH'; Log 'DISABLE_SIDE_MISMATCH before HIGH -> stop rounds'; break
        }
        if ($pv87 -ne 'low' -or $pv70 -ne 'high') {
            Log ('pre-state drifted (io87=' + $pv87 + ' io70=' + $pv70 + ') -> issue LOW cleanup, stop rounds')
            $highIssued = $true
            SendCmd 'pin write PC.23 low' $null 0.4 | Out-Null
            $verdict = 'INCOMPLETE_PRE_STATE_DRIFT'; break
        }

        $swH = [Diagnostics.Stopwatch]::StartNew()
        $highIssued = $true
        SendCmd 'pin write PC.23 high' $null 0.4 | Out-Null
        $h87 = SendCmd 'pin read PC.23' ('pin\[' + $n87 + '\] = (high|low)') 0.45
        $h70 = SendCmd 'pin read PC.6'  ('pin\[' + $n70 + '\] = (high|low)') 0.45
        $msAtLow = [int]$swH.Elapsed.TotalMilliseconds
        if ($msAtLow -gt $HIGH_BUDGET_MS) { Log ('BUDGET exceeded (' + $msAtLow + ' ms) before LOW - reads may be skipped') }
        SendCmd 'pin write PC.23 low' $null 0.4 | Out-Null
        Ledger $r 'high-window' 'write high; 2 reads; write low' 'high->low' $(if ($h87) { $h87.Groups[1].Value } else { 'NO-REPLY' }) 'low' $(if ($h70) { $h70.Groups[1].Value } else { 'NO-REPLY' }) $msAtLow 'HIGH budget 2000ms'

        $post87 = SendCmd 'pin read PC.23' ('pin\[' + $n87 + '\] = (high|low)') 1.5
        $post70 = SendCmd 'pin read PC.6'  ('pin\[' + $n70 + '\] = (high|low)') 1.5
        $p8v = $(if ($post87) { $post87.Groups[1].Value } else { 'NO-REPLY' })
        $p7v = $(if ($post70) { $post70.Groups[1].Value } else { 'NO-REPLY' })
        Ledger $r 'post-low' 'pin read both' 'low' $p8v 'high' $p7v '' 'after LOW'

        $hv87 = $(if ($h87) { $h87.Groups[1].Value } else { 'NO-REPLY' })
        $hv70 = $(if ($h70) { $h70.Groups[1].Value } else { 'NO-REPLY' })
        if ($hv87 -ne 'high') {
            $verdict = 'DRIVE_NOT_ESTABLISHED'
            Log ('HIGH window io87 readback = ' + $hv87 + ' -> stop further HIGH')
            break
        }
        if ($hv70 -eq 'high') {
            $verdict = 'ENABLE_PATH_NOT_OBSERVED'
            Log 'HIGH with io70 still high -> estop-chain may be blocking; stop rounds, no component judgement'
            break
        }
        if ($hv70 -ne 'low') {
            $verdict = 'INCOMPLETE_HIGH_WINDOW_SAMPLE'
            Log ('HIGH window io70 = ' + $hv70 + ' (sample lost) -> stop rounds')
            break
        }
        if ($p8v -ne 'low' -or $p7v -ne 'high') {
            $verdict = 'INCOMPLETE_RECOVERY_NOT_CONFIRMED'
            Log ('recovery reads io87=' + $p8v + ' io70=' + $p7v + ' -> stop rounds')
            break
        }
        $roundsOk++
        Log ('ROUND ' + $r + ' complete and as expected (' + $msAtLow + ' ms HIGH window)')
        Start-Sleep -m 1200
    }

    if ($roundsOk -eq 3) { $verdict = 'GPIO_FOLLOW_PASS' }
    elseif ($verdict -eq 'NOT_STARTED') { $verdict = 'INCOMPLETE_ROUNDS_STOPPED' }

    # ---------- D final state in same session ----------
    Log '== D final check =='
    $f87 = SendCmd 'pin read PC.23' ('pin\[' + $n87 + '\] = (high|low)') 1.5
    $f70 = SendCmd 'pin read PC.6'  ('pin\[' + $n70 + '\] = (high|low)') 1.5
    $fms = SendCmd 'motor_status' '\[MOT\] state=\d+ cur=(\d+) tgt=(\d+)[^\r\n]*\r?\n\[MOT\] armed=(\d)' 2.0
    $fv87 = $(if ($f87) { $f87.Groups[1].Value } else { 'NO-REPLY' })
    $fv70 = $(if ($f70) { $f70.Groups[1].Value } else { 'NO-REPLY' })
    if ($fms) {
        $fcur = [int]$fms.Groups[1].Value; $ftgt = [int]$fms.Groups[2].Value; $farm = [int]$fms.Groups[3].Value
    } else { $fcur = -1; $ftgt = -1; $farm = -1 }
    Ledger 'final' 'D-final' 'pin read + motor_status' 'low' $fv87 'high' $fv70 '' ("armed=" + $farm + " cur=" + $fcur + " tgt=" + $ftgt)
    if ($fv87 -ne 'low' -or $fv70 -ne 'high' -or $farm -ne 0 -or $fcur -ne 0 -or $ftgt -ne 0) {
        Log 'FINAL_STATE_NOT_CLEAN at D'
        if ($verdict -eq 'GPIO_FOLLOW_PASS') { $verdict = 'INCOMPLETE_FINAL_STATE_NOT_CLEAN' }
    }
}
catch {
    Log ('EXCEPTION: ' + $_.Exception.Message)
    if ($verdict -eq 'NOT_STARTED') { $verdict = 'INCOMPLETE_EXCEPTION' }
}
finally {
    # cleanup: ANY attempted HIGH path must end with LOW
    if ($highIssued) {
        try {
            Log '== CLEANUP LOW =='
            SendCmd 'pin write PC.23 low' $null 0.4 | Out-Null
            Start-Sleep -m 200; Pump
            $cl1 = SendCmd 'pin read PC.23' ('pin\[' + $n87 + '\] = (high|low)') 1.2
            $cl2 = SendCmd 'pin read PC.6'  ('pin\[' + $n70 + '\] = (high|low)') 1.2
            $clv = $(if ($cl1) { $cl1.Groups[1].Value } else { 'NO-REPLY' })
            if ($clv -ne 'low') {
                Log 'first LOW not confirmed -> one extra LOW only'
                SendCmd 'pin write PC.23 low' $null 0.4 | Out-Null
                Start-Sleep -m 200; Pump
                $cl1 = SendCmd 'pin read PC.23' ('pin\[' + $n87 + '\] = (high|low)') 1.2
                $cl2 = SendCmd 'pin read PC.6'  ('pin\[' + $n70 + '\] = (high|low)') 1.2
                $clv = $(if ($cl1) { $cl1.Groups[1].Value } else { 'NO-REPLY' })
                if ($clv -ne 'low') { Log 'FINAL_STATE_UNKNOWN - operator must apply hardware stop/safe power-off' }
                else { Log 'FINAL_STATE_LOW_CONFIRMED on second attempt' }
            } else { Log 'FINAL_STATE_LOW_CONFIRMED' }
            Ledger 'cleanup' 'finally' 'pin write low + reads' 'low' $clv 'high' $(if ($cl2) { $cl2.Groups[1].Value } else { 'NO-REPLY' }) '' 'cleanup result'
        } catch { Log ('CLEANUP-FAILED: ' + $_.Exception.Message) }
    } else {
        Log 'no HIGH was ever issued; nothing to clean up'
    }
    try { $p.Close() } catch { }
    Log ('VERDICT=' + $verdict)
    Write-Host ('VERDICT=' + $verdict)
}
