# ASCII source; SERIAL ONLY. No DAP, Flash, Keil, or active reset path.
[CmdletBinding()]
param(
    [switch]$SelfTest,
    [switch]$ExecuteApprovedSerialContinuation,
    [Parameter(Mandatory=$true)][string]$PythonExe,
    [Parameter(Mandatory=$true)][string]$GateFile,
    [Parameter(Mandatory=$true)][string]$OutDir,
    [string]$BootFixture,
    [switch]$ImageContinuityConfirmed
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$GateSha = '94e8317577a0fd8f6fd2bccafc0bb16753f3fb900c1adc7d254ee57cf3898c7d'
if ($SelfTest.IsPresent -eq $ExecuteApprovedSerialContinuation.IsPresent) { throw 'Select exactly one mode.' }
if ((Test-Path $OutDir) -and @(Get-ChildItem -Force $OutDir).Count) { throw 'Output directory must be new/empty.' }
[void][IO.Directory]::CreateDirectory($OutDir)
$OutDir = (Resolve-Path $OutDir).Path
$GateFile = (Resolve-Path $GateFile).Path
$PythonExe = (Resolve-Path $PythonExe).Path
if ((Get-FileHash -Algorithm SHA256 $GateFile).Hash.ToLowerInvariant() -ne $GateSha) { throw 'Gate bytes differ.' }
$script:Port = $null; $script:EventId = $null; $script:IsOffline=$SelfTest.IsPresent
$script:Rx = [IO.File]::Open((Join-Path $OutDir 'session.rx.bin'), 'CreateNew', 'Write', 'Read')
$script:Ledger = [IO.StreamWriter]::new((Join-Path $OutDir 'ledger.jsonl'), $false, [Text.UTF8Encoding]::new($false))
$script:Reply = $null; $script:ReplyFile = $null; $script:ReplyStart = 0L; $script:LastRx = [DateTime]::UtcNow
$script:Sequence = 0; $script:ClearPermit = $false; $script:StartPermit = ''
$script:Counts = @{}
$script:Limits = @{
    'free'=2; 'ps'=2; 'safety_status'=2; 'step_status'=2; 'diag_status'=1;
    'motor_status'=2; 'config_show'=1; 'blackbox_status'=2;
    'spm status'=8; 'spm cap'=1; 'spm init'=1;
    'spm start trip_fast'=2; 'spm start off_attempt'=1;
    'spm trip'=17; 'motor_disarm'=1; 'spm stop'=3; 'spm dump'=3; 'spm clear_records'=3
}
function Note([string]$Kind, $Value) {
    $s = [ordered]@{at=[DateTimeOffset]::Now.ToString('o'); kind=$Kind; data=$Value} | ConvertTo-Json -Compress -Depth 12
    $script:Ledger.WriteLine($s); $script:Ledger.Flush(); $script:Ledger.BaseStream.Flush($true)
}
function Save-New([string]$Path, [byte[]]$Bytes) {
    $f = [IO.File]::Open($Path, 'CreateNew', 'Write', 'Read')
    try { $f.Write($Bytes,0,$Bytes.Length); $f.Flush($true) } finally { $f.Dispose() }
}
function Begin-Reply([string]$Label) {
    if ($null -ne $script:Reply) { throw 'Previous reply still open.' }
    $script:Sequence++; $script:ReplyStart = $script:Rx.Position
    $script:Reply = [IO.MemoryStream]::new()
    $script:ReplyPath = Join-Path $OutDir ('{0:D3}_{1}.rx.bin' -f $script:Sequence,$Label)
    $script:ReplyFile = [IO.File]::Open($script:ReplyPath,'CreateNew','Write','Read')
    $script:LastRx = [DateTime]::UtcNow
    Note 'RX_BEGIN' @{path=[IO.Path]::GetFileName($script:ReplyPath); start=$script:ReplyStart}
}
function Persist-Chunk([byte[]]$Buffer, [int]$Count) {
    $script:Rx.Write($Buffer,0,$Count); $script:Rx.Flush($true)
    $script:ReplyFile.Write($Buffer,0,$Count); $script:ReplyFile.Flush($true)
    $script:Reply.Write($Buffer,0,$Count); $script:LastRx = [DateTime]::UtcNow
    if ($script:Rx.Position -gt 4194304) { throw 'RX session exceeds 4 MiB cap.' }
}
function Drain-Rx {
    $buf = [byte[]]::new(4096)
    while ($script:Port.BytesToRead -gt 0) {
        $n = $script:Port.Read($buf,0,[Math]::Min($buf.Length,$script:Port.BytesToRead))
        if ($n -le 0) { throw 'Serial read made no progress.' }
        Persist-Chunk $buf $n
    }
    if ($null -ne $script:EventId) {
        $ev = @(Get-Event -SourceIdentifier $script:EventId -ErrorAction SilentlyContinue)
        if ($ev.Count) { Note 'SERIAL_ERROR' @($ev | ForEach-Object {$_.SourceEventArgs.EventType.ToString()}); throw 'Serial error event.' }
    }
}
function End-Reply {
    [byte[]]$b = $script:Reply.ToArray()
    $r = [pscustomobject]@{Bytes=$b; Text=[Text.Encoding]::ASCII.GetString($b); Start=$script:ReplyStart; End=$script:Rx.Position; Path=$script:ReplyPath}
    $script:ReplyFile.Dispose(); $script:ReplyFile=$null
    $script:Reply.Dispose(); $script:Reply=$null
    Note 'RX_FILE' @{path=[IO.Path]::GetFileName($r.Path); start=$r.Start; end=$r.End; bytes=$b.Length; sha256=(Get-FileHash -Algorithm SHA256 $r.Path).Hash.ToLowerInvariant()}
    return $r
}
function Wait-Prompt([int]$TimeoutMs) {
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.ElapsedMilliseconds -lt $TimeoutMs) {
        Drain-Rx
        $view = [Text.Encoding]::ASCII.GetString($script:Reply.ToArray())
        if ($view -match '(?m)(?:^|[\r\n])msh >[ \t\r\n]*$' -and ([DateTime]::UtcNow-$script:LastRx).TotalMilliseconds -ge 120) { return End-Reply }
        [Threading.Thread]::Sleep(5)
    }
    throw 'Reply timeout; no retry and no cleanup.'
}
function Send-Tx([string]$Command) {
    if (-not $script:Limits.ContainsKey($Command)) { throw 'Command outside whitelist.' }
    $n = 0; if ($script:Counts.ContainsKey($Command)) { $n=$script:Counts[$Command] }
    if ($n -ge $script:Limits[$Command]) { throw 'Command cap reached.' }
    if ($Command -eq 'spm clear_records') {
        if (-not $script:ClearPermit) { throw 'No current clear grant.' }
        $script:ClearPermit=$false
    }
    if ($Command.StartsWith('spm start ')) {
        if ($script:StartPermit -cne $Command) { throw 'No current next-batch grant.' }
        $script:StartPermit=''
    }
    $script:Counts[$Command]=$n+1
    $kind='TX_ATTEMPT'; if ($script:IsOffline) {$kind='MOCK_TX_ATTEMPT'}
    Note $kind @{command=$Command; attempt=$n+1}
    [byte[]]$tx=[Text.Encoding]::ASCII.GetBytes($Command+"`r`n")
    $script:Port.Write($tx,0,$tx.Length)
}
function Cmd([string]$Command,[int]$TimeoutMs=5000) {
    Begin-Reply ($Command.Replace(' ','_'))
    Send-Tx $Command
    return Wait-Prompt $TimeoutMs
}
function Need([string]$Text,[string]$Pattern) { if ($Text -notmatch $Pattern) { throw ('Required observation missing: '+$Pattern) } }
function Idle-Status($Reply) {
    $pattern='(?m)^\[spm\] status: state=0 entry=- records=0 in_flight=0 dropped=0 counter=(COUNTER_OK|COUNTER_UNAVAILABLE|COUNTER_UNCHECKED) clock=(SOURCE_ASSUMED|CONFIG_READBACK|EXTERNAL_VERIFIED) guard=0 gates_now=0x[0-9a-f]+\r?$'
    if ([regex]::Matches($Reply.Text,$pattern).Count -ne 1) {throw 'Idle status malformed or not idle.'}
}
function Wait-ReadOnlyProbeWindow {
    $sw=[Diagnostics.Stopwatch]::StartNew()
    while ($sw.ElapsedMilliseconds -lt 20000) {
        Drain-Rx
        $view=[Text.Encoding]::ASCII.GetString($script:Reply.ToArray())
        $bootSeen=($view -match 'RT-Thread|\[BOOT\]')
        $bootDone=($view -match '\[MAIN\] boot complete')
        # Quiet schedules one read-only probe; it never proves readiness or grants cleanup.
        if ($sw.ElapsedMilliseconds -ge 2000 -and ([DateTime]::UtcNow-$script:LastRx).TotalMilliseconds -ge 1200 -and ((-not $bootSeen) -or $bootDone)) {
            Note 'READ_ONLY_PROBE_READY' @{boot_seen=$bootSeen; boot_done=$bootDone; startup_prompt_required=$false}
            return End-Reply
        }
        [Threading.Thread]::Sleep(5)
    }
    throw 'No bounded quiet probe window; no command sent and no retry.'
}
function Check-Decision([int]$Rc,$D,[string]$Batch,[int]$N,[string]$Entry,[long]$Length) {
    try {
        return ($Rc -eq 0 -and $D.gate_revision -ceq 'r3' -and $D.verdict -ceq 'ACCEPT' -and
            $D.batch -ceq $Batch -and $D.expected_records -eq $N -and $D.batch_entry -ceq $Entry -and $D.raw_bytes -eq $Length -and
            $D.action_plan.clear_records_send_count -eq 1 -and $D.action_plan.next_batch_trigger_send_count -eq 1 -and
            $D.action_plan.extra_protection_actions -eq 0 -and $D.timing_stats_inclusion.trusted -eq $true)
    } catch { return $false }
}
function Run-Gate([string]$Raw,[string]$Batch,[int]$N,[string]$Entry) {
    if ((Get-FileHash -Algorithm SHA256 $GateFile).Hash.ToLowerInvariant() -ne $GateSha) { throw 'Gate changed.' }
    $j=Join-Path $OutDir ($Batch+'.gate.json'); if (Test-Path $j) { throw 'Stale gate JSON.' }
    $before=(Get-FileHash -Algorithm SHA256 $Raw).Hash
    & $PythonExe $GateFile --raw $Raw --batch $Batch --expected-records $N --entry $Entry --json-out $j 1> (Join-Path $OutDir ($Batch+'.gate.stdout.txt')) 2> (Join-Path $OutDir ($Batch+'.gate.stderr.txt'))
    $rc=$LASTEXITCODE; $d=$null
    if (Test-Path $j) { try {$d=Get-Content -Raw -Encoding UTF8 $j | ConvertFrom-Json} catch {$d=$null} }
    $ok=(Check-Decision $rc $d $Batch $N $Entry (Get-Item $Raw).Length)
    if ((Get-FileHash -Algorithm SHA256 $Raw).Hash -ne $before) { throw 'Raw changed during gate.' }
    Note 'GATE' @{batch=$Batch; rc=$rc; allowed=$ok; raw_sha256=$before.ToLowerInvariant()}
    return [pscustomobject]@{Allowed=$ok; Decision=$d; Rc=$rc}
}
function Safety-Rows([byte[]]$Raw,[string]$Entry,[int]$N) {
    $lines=[Text.Encoding]::ASCII.GetString($Raw) -split "`n"
    $headers=@($lines | Where-Object {$_.StartsWith('schema,gen,')})
    if ($headers.Count -ne 1) { throw 'Missing exact header after gate.' }
    $names=$headers[0].TrimEnd([char]13).Split(',')
    $rows=@($lines | Where-Object {$_ -match '^2,'})
    if ($rows.Count -ne $N) { throw 'Safety row count differs.' }
    foreach ($line in $rows) {
        $v=$line.TrimEnd([char]13).Split(','); $d=@{}
        for ($i=0;$i -lt $names.Count;$i++) {$d[$names[$i]]=$v[$i]}
        foreach ($k in @('pwm_called','low_called','pwm_attempted','permit_written','inhibit')) {if ([int]$d[$k] -ne 1) {throw ('Missing close fact: '+$k)}}
        foreach ($k in @('pwm_raw_rc','permit_write_rc','outcome','permit_level')) {if ([long]$d[$k] -ne 0) {throw ('Failed close fact: '+$k)}}
        if ([int]$d['pwm_result'] -ne 4) {throw 'PWM API accepted fact missing.'}
        if ($d['pwm_epoch'] -ne $d['epoch_after'] -or $d['permit_epoch'] -ne $d['epoch_after']) {throw 'Close facts have stale identity.'}
        $branch=10; if ($Entry -eq 'off_attempt') {$branch=17}
        if ([int]$d['branch'] -ne $branch) {throw 'Wrong production branch; no replacement trigger.'}
    }
}
function Freeze-Range([long]$Start,[long]$End,[string]$Name) {
    $script:Rx.Flush($true); $n=$End-$Start
    if ($n -lt 1 -or $n -gt 1048576) {throw 'Bad batch extent.'}
    $f=[IO.File]::Open((Join-Path $OutDir 'session.rx.bin'),'Open','Read','ReadWrite')
    try {
        [void]$f.Seek($Start,[IO.SeekOrigin]::Begin); $b=[byte[]]::new([int]$n); $done=0
        while ($done -lt $n) {$x=$f.Read($b,$done,[int]$n-$done); if ($x -le 0) {throw 'Short persisted range.'}; $done+=$x}
    } finally {$f.Dispose()}
    $path=Join-Path $OutDir ($Name+'.batch.rx.bin'); Save-New $path $b
    Note 'BATCH_RANGE' @{batch=$Name; start=$Start; end=$End; bytes=$n; sha256=(Get-FileHash -Algorithm SHA256 $path).Hash.ToLowerInvariant()}
    return $path
}

try {
    if ($SelfTest) {
        # No serial object, process reset, device discovery, or device handle in this branch.
        Begin-Reply 'offline_bytes'; [byte[]]$test=@(0,13,10,0xE4,0xB8,0xAD,0xFF,65)
        Persist-Chunk $test 3; Persist-Chunk $test[3..7] 5; $r=End-Reply
        if ([Convert]::ToBase64String($test) -cne [Convert]::ToBase64String([IO.File]::ReadAllBytes($r.Path))) {throw 'Byte persistence mismatch.'}
        $mock=[pscustomobject]@{Calls=0}
        $mock | Add-Member ScriptMethod Write {param($b,$i,$n) $this.Calls++}
        $script:Port=$mock; $blocked=0
        foreach ($c in @('spm clear_records','spm start trip_fast','pin 1 1')) {
            try {Send-Tx $c} catch {$blocked++}
        }
        if ($blocked -ne 3 -or $mock.Calls -ne 0) {throw 'Denied command reached mock wire.'}
        $helper=Join-Path $OutDir 'fixture.py'
        $code=@'
import importlib.util,sys
from pathlib import Path
s=importlib.util.spec_from_file_location('g',sys.argv[1]);g=importlib.util.module_from_spec(s);s.loader.exec_module(g)
def good(L):
    return g.edit_row(g.with_progress(L),row='all',inhibit=1,pwm_called=1,low_called=1,pwm_attempted=1,pwm_result=4,permit_written=1,permit_level=0,pwm_raw_rc=0,permit_write_rc=0,outcome=0,branch=10)
r=g.fixture(rows=1,corrupt=good);Path(sys.argv[2]).write_bytes(r);Path(sys.argv[3]).write_bytes(r+b'\0extra\r\n')
'@
        [IO.File]::WriteAllText($helper,$code,[Text.UTF8Encoding]::new($false))
        $good=Join-Path $OutDir 'positive.rx.bin'; $bad=Join-Path $OutDir 'negative.rx.bin'
        & $PythonExe $helper $GateFile $good $bad
        if ($LASTEXITCODE -ne 0) {throw 'Fixture generation failed.'}
        $g=Run-Gate $good 'self_positive' 1 'trip_fast'; if (-not $g.Allowed) {throw 'Healthy gate denied.'}
        Safety-Rows ([IO.File]::ReadAllBytes($good)) 'trip_fast' 1
        $g0=Run-Gate $bad 'self_negative' 1 'trip_fast'; if ($g0.Allowed -or $g0.Rc -ne 1) {throw 'NUL negative passed.'}
        $d=$g.Decision; if (Check-Decision 1 $d 'self_positive' 1 'trip_fast' (Get-Item $good).Length) {throw 'Nonzero rc passed.'}
        if (Check-Decision 0 $d 'wrong_batch' 1 'trip_fast' (Get-Item $good).Length) {throw 'Stale JSON passed.'}
        if (Check-Decision 0 $null 'self_positive' 1 'trip_fast' (Get-Item $good).Length) {throw 'Missing JSON passed.'}
        $d.action_plan.clear_records_send_count=0
        if (Check-Decision 0 $d 'self_positive' 1 'trip_fast' (Get-Item $good).Length) {throw 'Missing clear grant passed.'}
        if (-not (Test-Path $BootFixture -PathType Leaf)) {throw 'Missing real BOARD-B boot replay fixture.'}
        if ((Get-FileHash -Algorithm SHA256 $BootFixture).Hash.ToLowerInvariant() -ne '97b37f32aa03c86b8708ae088d0ae65a8f92520f1a1ed70beb74663049321749') {throw 'Wrong boot fixture.'}
        $fake=[pscustomobject]@{Payload=[IO.File]::ReadAllBytes($BootFixture); Index=0; Calls=0; AfterWrite=[byte[]]@()}
        $fake | Add-Member ScriptProperty BytesToRead {return $this.Payload.Length-$this.Index}
        $fake | Add-Member ScriptMethod Read {
            param([byte[]]$b,[int]$at,[int]$count)
            $n=[Math]::Min($count,[Math]::Min(7,$this.Payload.Length-$this.Index))
            [Array]::Copy($this.Payload,$this.Index,$b,$at,$n); $this.Index+=$n; return $n
        }
        $fake | Add-Member ScriptMethod Write {
            param([byte[]]$b,[int]$at,[int]$count)
            $this.Calls++; $this.Payload=$this.AfterWrite; $this.Index=0
        }
        $script:Port=$fake
        Begin-Reply 'offline_real_boot'; $replayed=Wait-ReadOnlyProbeWindow
        if ($fake.Calls -ne 0 -or $replayed.Bytes.Length -ne 3757 -or $replayed.Text.Contains('msh >')) {throw 'Boot replay was altered or needed an unsolicited prompt.'}
        if ((Get-FileHash -Algorithm SHA256 $replayed.Path).Hash.ToLowerInvariant() -ne '97b37f32aa03c86b8708ae088d0ae65a8f92520f1a1ed70beb74663049321749') {throw 'Boot replay byte mismatch.'}
        $status='[spm] status: state=0 entry=- records=0 in_flight=0 dropped=0 counter=COUNTER_UNCHECKED clock=SOURCE_ASSUMED guard=0 gates_now=0x0'
        $fake.AfterWrite=[Text.Encoding]::ASCII.GetBytes($status+"`r`nmsh >`r`n")
        $probeResponse=Cmd 'spm status' 1000; Idle-Status $probeResponse
        if ($fake.Calls -ne 1) {throw 'First read-only probe not issued exactly once.'}
        # A no-prompt command response still must time out; no quiet-only fallback.
        $fake.AfterWrite=[Text.Encoding]::ASCII.GetBytes($status+"`r`n"); $timeoutSeen=$false
        try {$probeResponse=Cmd 'spm status' 250} catch {
            if ($_.Exception.Message -cne 'Reply timeout; no retry and no cleanup.') {throw}
            $timeoutSeen=$true; $partial=End-Reply
        }
        if (-not $timeoutSeen) {throw 'Missing response prompt was accepted.'}
        $fake.AfterWrite=[Text.Encoding]::ASCII.GetBytes($status.Replace('dropped=0','dropped=0BAD')+"`r`nmsh >`r`n")
        $probeResponse=Cmd 'spm status' 1000; $badStatusSeen=$false
        try {Idle-Status $probeResponse} catch {
            if ($_.Exception.Message -cne 'Idle status malformed or not idle.') {throw}
            $badStatusSeen=$true
        }
        if (-not $badStatusSeen -or $script:ClearPermit -or $script:StartPermit -ne '') {throw 'Bad status authorized continuation.'}
        Note 'OFFLINE_SELFTEST' @{result='PASS'; device_actions=0; real_wire_calls=0; mock_read_probes=$fake.Calls; mock_forbidden_calls=$mock.Calls; boot_fixture_bytes=$replayed.Bytes.Length}
        Write-Host 'BOARD-B-SERIAL-R1-OFFLINE-SELFTEST PASS; device_actions=0'
    } else {
        # A separately approved single COM5 reopen; unchanged image established by BOARD-B.
        if (-not $ImageContinuityConfirmed) {throw 'BOARD-B image continuity not confirmed.'}
        $script:Port=[IO.Ports.SerialPort]::new('COM5',115200,[IO.Ports.Parity]::None,8,[IO.Ports.StopBits]::One)
        $script:Port.Handshake=[IO.Ports.Handshake]::None; $script:Port.DtrEnable=$false; $script:Port.RtsEnable=$false
        $script:Port.ReadTimeout=100; $script:Port.WriteTimeout=1000; $script:Port.ReadBufferSize=65536
        $script:EventId='A0BoardBSerialR1Error_'+[Guid]::NewGuid().ToString('N')
        Register-ObjectEvent -InputObject $script:Port -EventName ErrorReceived -SourceIdentifier $script:EventId | Out-Null
        $script:Port.Open(); Note 'SERIAL_OPEN' @{port='COM5'; baud=115200; format='8N1'; dtr=$false; rts=$false}
        Begin-Reply 'attach_passive'; $passive=Wait-ReadOnlyProbeWindow
        # Warm attachment need not receive a boot banner. Check any complete banner that is observed.
        if ($passive.Text -match '5\.3\.0 build [A-Za-z]{3} +[0-9]+ 20[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}') {
            Need $passive.Text '5\.3\.0 build Oct 26 2026 20:57:05'
        }
        # First TX is the already-budgeted read-only status. Wait-Prompt is response-only.
        $first=Cmd 'spm status'; Idle-Status $first
        foreach ($c in @('free','ps','safety_status','step_status','diag_status','motor_status','config_show','blackbox_status')) {
            $r=Cmd $c
            if ($c -eq 'motor_status') {Need $r.Text 'armed=0'; Need $r.Text 'drv_en_verified_pad=0'; Need $r.Text 'drive request=LOW pad=0 expected_ENN=HIGH ENN_feedback=1\(resolved\) verdict=OK'}
        }
        $cap=Cmd 'spm cap'; Need $cap.Text 'schema=2 slots=64 record_bytes=120 array_bytes=7680 sizeof_ctx=44'
        Need $cap.Text 'MOTOR_HW=0 STEP_DIAG_ISO=0 TMC_CFG_HW=0 LIMIT_EN=0'
        Need $cap.Text 'quiescent=YES state=0 records=0 in_flight=0 dropped=0'
        $init=Cmd 'spm init'; Need $init.Text '\[spm\] init: counter=COUNTER_(OK|UNAVAILABLE|UNCHECKED)'
        Idle-Status (Cmd 'spm status')
        $batches=@(@{name='b1'; entry='trip_fast'; count=1},@{name='b2'; entry='trip_fast'; count=16},@{name='b3'; entry='off_attempt'; count=1})
        $script:StartPermit='spm start trip_fast'
        for ($i=0;$i -lt $batches.Count;$i++) {
            $b=$batches[$i]; $r=Cmd ('spm start '+$b.entry); Need $r.Text ('\[spm\] start: entry='+$b.entry+' ')
            for ($j=0;$j -lt $b.count;$j++) {
                if ($b.entry -eq 'trip_fast') {
                    $r=Cmd 'spm trip'; Need $r.Text 'DRV_ENABLE LOW verified'; Need $r.Text 'ENN feedback OK'; Need $r.Text 'motor_rc=0 diag_rc=0'; Need $r.Text '\[spm\] trip:'
                } else {$r=Cmd 'motor_disarm'; Need $r.Text '\[MOT\] DISARM'; Need $r.Text 'LOW verified'; Need $r.Text 'ENN feedback=OK'}
            }
            # Fixed command boundary, not content-selected filtering: stop -> status -> dump.
            $begin=$script:Rx.Position; $r=Cmd 'spm stop'; $r=Cmd 'spm status'; $r=Cmd 'spm dump' 10000
            $raw=Freeze-Range $begin $script:Rx.Position $b.name
            $verdict=Run-Gate $raw $b.name $b.count $b.entry
            if (-not $verdict.Allowed) {throw 'Batch gate rejected; preserve RAM and stop.'}
            Safety-Rows ([IO.File]::ReadAllBytes($raw)) $b.entry $b.count
            $script:ClearPermit=$true; $r=Cmd 'spm clear_records'
            Need $r.Text '\[spm\] clear_records:'; if ($r.Text -match '\[spm\] clear_records: BUSY') {throw 'Clear busy; no retry.'}
            Idle-Status (Cmd 'spm status')
            if ($i+1 -lt $batches.Count) {$script:StartPermit='spm start '+$batches[$i+1].entry}
        }
        foreach ($c in @('safety_status','step_status','motor_status','blackbox_status','free','ps')) {
            $r=Cmd $c
            if ($c -eq 'motor_status') {Need $r.Text 'armed=0'; Need $r.Text 'drive request=LOW pad=0 expected_ENN=HIGH ENN_feedback=1\(resolved\) verdict=OK'}
        }
        foreach ($c in $script:Limits.Keys) {if (-not $script:Counts.ContainsKey($c) -or $script:Counts[$c] -ne $script:Limits[$c]) {throw ('Command count not complete: '+$c)}}
        Note 'CAPTURE_COMPLETE' @{commands=54; trips=17; disarm=1; clears=3; next_batch_starts=2; timing_acceptance='NOT_DETERMINED'}
        Write-Host 'BOARD-B SERIAL-R1 capture complete. Closeout still requires reported physical power-off.'
    }
} catch {
    Note 'STOP_FAILURE' @{message=$_.Exception.Message; counts=$script:Counts}
    throw
} finally {
    if ($null -ne $script:ReplyFile) {$script:ReplyFile.Dispose()}
    if ($null -ne $script:Reply) {$script:Reply.Dispose()}
    if ($null -ne $script:Port -and $script:Port -is [IO.Ports.SerialPort]) {if ($script:Port.IsOpen) {$script:Port.Close()}; $script:Port.Dispose()}
    if ($null -ne $script:EventId) {Unregister-Event -SourceIdentifier $script:EventId -ErrorAction SilentlyContinue}
    $script:Rx.Dispose(); $script:Ledger.Dispose()
}
