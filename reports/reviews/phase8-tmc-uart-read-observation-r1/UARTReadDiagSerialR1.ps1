# ASCII source. URO-R1 read-diagnosis collector: seven read-only commands, same order as M0.
# Reuse (task book 6.3: the transport layer stays byte-for-byte the accepted one):
#   * 15 functions (Note / Save-New / Begin-Reply / Persist-Chunk / Drain-Rx / End-Reply /
#     Wait-Prompt / Send-Tx / Cmd / Need / Idle-Status / Wait-ReadOnlyProbeWindow / One-Line /
#     Make-Response / Fixture-Responses) plus the global setup block and the finally block are
#     copied BYTE FOR BYTE out of the accepted M0Serial.ps1 (24064 B / sha256
#     2131af0ef32b0d9acb3a2a0489d47b90e26968e2fedd7c4ee4624fede0687e3f);
#   * the six non-probe command checks are copied verbatim out of the previous round's
#     UARTReadDiagSerial.ps1. This generator re-verifies both after writing the file.
# New in R1: Check-TmroReply (minimal TMRO completeness: one BEGIN and one END in order,
# agreeing reads within 0..3, exactly one record each for reg 06/01/02, every required fact
# line, and sample chunks whose printed bytes rebuild the declared prefix/tail/candidate
# lengths), read_ok/identity_ok/fields_ok separated from capture, full-32-bit raw checks
# instead of masked summaries, business FAIL allowed on a COMPLETE capture (open/txn/read/
# identity/fields stages listed apart), and Resolve-BannerGate (an observed banner must match
# this round's manifest exactly; an unobserved one is only acceptable behind a byte-verified
# readback of this package and is recorded as BANNER_NOT_OBSERVED - never a fabricated banner,
# never a reset or a second port open to chase one). Fixtures are the real stdout of this
# round's host probe of the final firmware bytes. Online mode also needs -ApprovalSource.
# Device actions in -SelfTest: none (no SerialPort object, no port enumeration, no pyOCD).
[CmdletBinding()]
param(
    [switch]$SelfTest,
    [switch]$ExecuteApprovedR1ReadDiag,
    [Parameter(Mandatory=$true)][string]$OutDir,
    [string]$BootFixture,
    [string]$ExpectedBuildBanner,
    [string]$ApprovalSource,
    [switch]$ImageContinuityConfirmed,
    [switch]$SiteConfirmed
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if ($SelfTest.IsPresent -eq $ExecuteApprovedR1ReadDiag.IsPresent) {throw 'Select exactly one mode.'}
if ((Test-Path $OutDir) -and @(Get-ChildItem -Force $OutDir).Count) {throw 'Output directory must be new/empty.'}
[void][IO.Directory]::CreateDirectory($OutDir)
$OutDir=(Resolve-Path $OutDir).Path
$script:Port=$null; $script:EventId=$null; $script:IsOffline=$SelfTest.IsPresent
$script:Rx=[IO.File]::Open((Join-Path $OutDir 'session.rx.bin'),'CreateNew','Write','Read')
$script:Ledger=[IO.StreamWriter]::new((Join-Path $OutDir 'ledger.jsonl'),$false,[Text.UTF8Encoding]::new($false))
$script:Reply=$null; $script:ReplyFile=$null; $script:ReplyStart=0L; $script:LastRx=[DateTime]::UtcNow
$script:Sequence=0; $script:ClearPermit=$false; $script:StartPermit=''; $script:Counts=@{}
$script:Limits=@{'spm status'=1;'spm cap'=1;'safety_state'=1;'safety_status'=1;'motor_status'=1;'step_status'=1;'tmc_uart_probe'=1}
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
function One-Line([string]$Text,[string]$Prefix,[string]$Pattern) {
    # Legacy kprintf truncation can end in a literal NUL and join the next call.
    # Preserve every byte; only recognize the next exact label after that boundary.
    # spm responses are separately required to contain zero NUL bytes.
    $anchor0='(?m)(?:^|(?<=\x00))'
    $starts=[regex]::Matches($Text,$anchor0+[regex]::Escape($Prefix))
    $matches0=[regex]::Matches($Text,$Pattern.Replace('(?m)^',$anchor0))
    if ($starts.Count -ne 1 -or $matches0.Count -ne 1) {throw ('M0_OBSERVATION_REJECTED: '+$Prefix)}
    return $matches0[0]
}
function Resolve-BannerGate([string]$PassiveText,[string]$Expected,[bool]$Continuity)
{
    # The expected banner always comes from THIS round's build manifest; nothing here ever
    # fabricates one. A passive window can legitimately hold 0 bytes: the board printed its
    # banner before COM5 was opened (seen on the real M0 board). Not seeing a banner is
    # therefore not proof of wrong firmware - only an exact match, or a byte-verified
    # readback of this package's own image, can carry the identity.
    $m=[regex]::Matches($PassiveText,'5\.3\.0 build [A-Za-z]{3} +[0-9]+ 20[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}')
    if ($m.Count -eq 0) {
        if (-not $Continuity) {throw 'UROR1_BANNER_UNRESOLVED: no version banner was captured and this package has no byte-verified image readback behind it'}
        return 'BANNER_NOT_OBSERVED'
    }
    $distinct=@($m | ForEach-Object {$_.Value} | Select-Object -Unique)
    if ($distinct.Count -ne 1) {throw ('UROR1_BANNER_CONTRADICTION: '+$distinct.Count+' different version banners inside one passive window')}
    if ([string]::IsNullOrWhiteSpace($Expected)) {throw 'UROR1_BANNER_UNRESOLVED: a banner was captured but no expected banner was supplied'}
    if ($distinct[0] -cne $Expected.Trim()) {throw ('UROR1_BANNER_MISMATCH: captured ['+$distinct[0]+'] expected ['+$Expected.Trim()+']')}
    return 'BANNER_MATCHED'
}
function Assert-NoReset($Response,[string]$Label)
{
    # No-reset guard: if the board rebooted mid-capture the boot banner appears again inside
    # this reply, so the reply is not trustworthy. Original bytes are already flushed; stop.
    if ($Response.Text -match 'RT-Thread \| Micro Kernel' -or $Response.Text -match '5\.3\.0 build ' -or $Response.Text -match '(?m)^\[BOOT\]') {
        throw ('UROR1_CAPTURE_INCOMPLETE: reset banner inside reply: ' + $Label)
    }
}
function Bit-At([int]$Value,[int]$Index)
{
    # Plain arithmetic instead of -shr: Windows PowerShell 5.1 has no shift operators.
    return ([int][Math]::Floor($Value/[Math]::Pow(2,$Index))) % 2
}
function Check-TmroReply([string]$text0)
{
    # Two verdicts are judged apart and never merged: capture completeness (does this reply
    # carry the whole observation, byte for byte) and the business result (what the bus did).
    # A failed read is NOT a reason to throw evidence away, and complete evidence is NOT a
    # successful link. Device/waveform meaning is still read offline by a human.
    $known=@('06','01','02')
    if ($text0.IndexOf([char]0) -ge 0) {throw 'UROR1_CAPTURE_INCOMPLETE: NUL byte inside the probe response'}
    if ($text0 -match 'EXPORT_ERROR') {throw 'UROR1_CAPTURE_INCOMPLETE: EXPORT_ERROR marker present'}
    $tmroAll=[regex]::Matches($text0,'(?m)^\[TMRO\]').Count
    if ($tmroAll -eq 0) {throw 'UROR1_CAPTURE_INCOMPLETE: no TMRO frame at all'}
    $tmroOk=[regex]::Matches($text0,'(?m)^\[TMRO\] [^\r\n]*\r?$').Count
    if ($tmroAll -ne $tmroOk) {throw 'UROR1_CAPTURE_INCOMPLETE: TMRO line not terminated cleanly'}
    # The firmware prints at most 96 bytes including LF, so a longer line can only be a
    # console clip (RT_CONSOLEBUF_SIZE) that lost trailing fields.
    if ([regex]::Matches($text0,'(?m)^\[TMRO\] [^\r\n]{89,}').Count -ne 0) {throw 'UROR1_CAPTURE_INCOMPLETE: TMRO line exceeds the 96 byte print bound'}

    $beg=[regex]::Matches($text0,'(?m)^\[TMRO\] BEGIN v=([0-9]+) addr=([0-9]+) planned=([0-9]+) reads=([0-9]+)\r?$')
    if ($beg.Count -ne 1) {throw ('UROR1_CAPTURE_INCOMPLETE: BEGIN lines='+$beg.Count)}
    $endf=[regex]::Matches($text0,'(?m)^\[TMRO\] END reads=([0-9]+) writes=([0-9]+) export_err=([0-9]+) observation_complete=([0-9]+)\r?$')
    if ($endf.Count -ne 1) {throw ('UROR1_CAPTURE_INCOMPLETE: END lines='+$endf.Count)}
    if ($beg[0].Groups[1].Value -cne '1') {throw 'UROR1_CAPTURE_INCOMPLETE: observation version is not this package s v=1'}
    if ($beg[0].Groups[2].Value -cne '0') {throw 'UROR1_CAPTURE_INCOMPLETE: probe address is not this package s addr=0'}
    if ($beg[0].Groups[3].Value -cne '3') {throw 'UROR1_CAPTURE_INCOMPLETE: planned is not 3'}
    $readsBeg=[int]$beg[0].Groups[4].Value
    $readsEnd=[int]$endf[0].Groups[1].Value
    if ($readsBeg -ne $readsEnd) {throw ('UROR1_CAPTURE_INCOMPLETE: BEGIN reads='+$readsBeg+' but END reads='+$readsEnd)}
    if ($readsEnd -lt 0 -or $readsEnd -gt 3) {throw ('UROR1_CAPTURE_INCOMPLETE: reads='+$readsEnd+' outside the 0..3 cap of this command')}
    if ($endf[0].Groups[2].Value -cne '0') {throw 'UROR1_CAPTURE_INCOMPLETE: probe reported a WRITE frame'}
    if ($endf[0].Groups[3].Value -cne '0') {throw 'UROR1_CAPTURE_INCOMPLETE: probe export buffer overflowed'}
    if ($endf[0].Groups[4].Value -cne '1') {throw 'UROR1_CAPTURE_INCOMPLETE: observation_complete is not 1'}
    if ($text0.IndexOf('[TMRO] END') -lt $text0.IndexOf('[TMRO] BEGIN')) {throw 'UROR1_CAPTURE_INCOMPLETE: END frame precedes BEGIN'}

    $res=[regex]::Matches($text0,'(?m)^\[TMRO\] reg=([0-9A-F]{2}) result=(SUCCESS|FAIL|NOT_REACHED) rc=(-?[0-9]+|-) reason=([a-z\-]+|-)\r?$')
    if ($res.Count -ne 3) {throw ('UROR1_CAPTURE_INCOMPLETE: per-record result lines='+$res.Count+' (want exactly 3)')}
    $recs=@{}
    foreach ($m0 in $res) {
        $k0=$m0.Groups[1].Value
        if ($recs.ContainsKey($k0)) {throw ('UROR1_CAPTURE_INCOMPLETE: duplicate record reg='+$k0)}
        $recs[$k0]=[pscustomobject]@{reg=$k0;result=$m0.Groups[2].Value;rc=$m0.Groups[3].Value;reason=$m0.Groups[4].Value}
    }
    foreach ($want in $known) {if (-not $recs.ContainsKey($want)) {throw ('UROR1_CAPTURE_INCOMPLETE: record reg='+$want+' is absent')}}
    $otherRegs=@([regex]::Matches($text0,'(?m)^\[TMRO\] reg=([0-9A-F]{2}) ') | Where-Object {-not ($known -contains $_.Groups[1].Value)})
    if ($otherRegs.Count -ne 0) {throw 'UROR1_CAPTURE_INCOMPLETE: a record line carries a register outside IOIN/GSTAT/IFCNT'}
    # The read set is IOIN first: if IOIN failed, the two later records never ran. This is
    # checked before the arithmetic so a fabricated later outcome is named as the real defect.
    if ($recs['06'].result -ceq 'FAIL' -and -not ($recs['01'].result -ceq 'NOT_REACHED' -and $recs['02'].result -ceq 'NOT_REACHED')) {
        throw 'UROR1_CAPTURE_INCOMPLETE: the early exit rule was broken (IOIN failed but a later record claims an outcome)'}
    $nSuccess=0;$nFail=0;$nNr=0
    foreach ($want in $known) {
        $r0=$recs[$want].result
        if ($r0 -ceq 'SUCCESS') {$nSuccess++} elseif ($r0 -ceq 'FAIL') {$nFail++} else {$nNr++}
    }
    if ($nSuccess+$nFail -ne $readsEnd) {throw ('UROR1_CAPTURE_INCOMPLETE: SUCCESS+FAIL='+($nSuccess+$nFail)+' but END reads='+$readsEnd)}
    if ($nNr -ne (3-$readsEnd)) {throw ('UROR1_CAPTURE_INCOMPLETE: NOT_REACHED='+$nNr+' but planned-minus-attempted='+(3-$readsEnd))}

    $facts=[ordered]@{
        'stage'   ='result=(SUCCESS|FAIL|NOT_REACHED) rc=(-?[0-9]+|-) reason=([a-z\-]+|-)';
        'ctx'     ='ctx=([01]) txn=([01])/(-?[0-9]+) open=([A-Z_]+)/(-?[0-9]+) lock=([0-9]+)/([0-9]+)/(-?[0-9]+)';
        'flush'   ='flush=([01])/(-?[0-9]+) bytes=([0-9]+) ms=([0-9]+)';
        'tx'      ='tx=([01])/([01])/(-?[0-9]+) off=([0-9]+) req=([0-9A-F ]*)';
        'written' ='tx_written=([0-9]+)';
        'timing'  ='(start=[0-9]+ total=[0-9]+ rx_begin=[0-9]+ rx_dl=[0-9]+ done=[0-9]+|timing=- \(budget never opened\))';
        'rx'      ='rx=([0-9]+) hdr=([01]) full=([01]) crc=(NOT_REACHED|OK|FAIL)';
        'counts'  ='total=([0-9]+) buf=([0-9]+) pub=([01]) late=([01]) exp=([ny-])/([ny-]) pre_trunc=([01]) gap=([01])';
        'echo'    ='echo=(NONE|ECHO_ONLY|ECHO_WITHIN|OTHER|UNKNOWN) match=([0-9]+)/([0-9]+) val=([0-9A-F]{8})';
        'samples' ='samples pre=([0-9]+) tail=([0-9]+) cand=([0-9]+)'
    }
    foreach ($want in $known) {
        foreach ($name in $facts.Keys) {
            $p0='(?m)^\[TMRO\] reg='+$want+' '+$facts[$name]+'\r?$'
            $c0=[regex]::Matches($text0,$p0).Count
            if ($c0 -ne 1) {throw ('UROR1_CAPTURE_INCOMPLETE: record '+$want+' fact line '+$name+' appears '+$c0+' times (want exactly 1)')}
        }
        $idm=[regex]::Match($text0,'(?m)^\[TMRO\] reg='+$want+' ctx=([01]) txn=([01])/(-?[0-9]+) open=([A-Z_]+)/(-?[0-9]+) lock=([0-9]+)/([0-9]+)/(-?[0-9]+)\r?$')
        $cnm=[regex]::Match($text0,'(?m)^\[TMRO\] reg='+$want+' total=([0-9]+) buf=([0-9]+) pub=([01]) late=([01]) exp=([ny-])/([ny-]) pre_trunc=([01]) gap=([01])\r?$')
        $ecm=[regex]::Match($text0,'(?m)^\[TMRO\] reg='+$want+' echo=(NONE|ECHO_ONLY|ECHO_WITHIN|OTHER|UNKNOWN) match=([0-9]+)/([0-9]+) val=([0-9A-F]{8})\r?$')
        $wrm=[regex]::Match($text0,'(?m)^\[TMRO\] reg='+$want+' tx_written=([0-9]+)\r?$')
        $old=$recs[$want]
        $rxm=[regex]::Match($text0,'(?m)^\[TMRO\] reg='+$want+' rx=([0-9]+) hdr=([01]) full=([01]) crc=(NOT_REACHED|OK|FAIL)\r?$')
        $recs[$want]=[pscustomobject]@{reg=$want;result=$old.result;rc=$old.rc;reason=$old.reason;
            ctx=$idm.Groups[1].Value;txn=$idm.Groups[2].Value;txnrc=$idm.Groups[3].Value;
            open=$idm.Groups[4].Value;openrc=$idm.Groups[5].Value;
            rxTotal=[int]$cnm.Groups[1].Value;published=$cnm.Groups[3].Value;
            crc=$rxm.Groups[4].Value;val=$ecm.Groups[4].Value;written=[int]$wrm.Groups[1].Value}
        if ($recs[$want].ctx -cne '1') {throw ('UROR1_CONTRADICTION: record '+$want+' claims an illegal context although the probe entry gate passed')}
        if ($recs[$want].result -ceq 'NOT_REACHED') {
            if ($recs[$want].rc -cne '-' -or $recs[$want].reason -cne '-') {throw ('UROR1_CAPTURE_INCOMPLETE: NOT_REACHED record '+$want+' carries a numeric rc/reason')}
            if ([regex]::Matches($text0,'(?m)^\[TMRO\] reg='+$want+' timing=- \(budget never opened\)\r?$').Count -ne 1) {throw ('UROR1_CAPTURE_INCOMPLETE: NOT_REACHED record '+$want+' has no unopened-budget timing line')}
            if ($recs[$want].written -ne 0) {throw ('UROR1_CAPTURE_INCOMPLETE: NOT_REACHED record '+$want+' reports tx_written='+$recs[$want].written)}
            if ($recs[$want].rxTotal -ne 0) {throw ('UROR1_CAPTURE_INCOMPLETE: NOT_REACHED record '+$want+' reports '+$recs[$want].rxTotal+' rx bytes')}
            if ($recs[$want].published -cne '0') {throw ('UROR1_CAPTURE_INCOMPLETE: NOT_REACHED record '+$want+' claims a published value')}
        } else {
            if ([regex]::Matches($text0,'(?m)^\[TMRO\] reg='+$want+' start=[0-9]+ total=[0-9]+ rx_begin=[0-9]+ rx_dl=[0-9]+ done=[0-9]+\r?$').Count -ne 1) {throw ('UROR1_CAPTURE_INCOMPLETE: reached record '+$want+' has no real timing line')}
            if ($recs[$want].result -ceq 'SUCCESS') {
                if ($recs[$want].rc -cne '0' -or $recs[$want].reason -cne 'ok') {throw ('UROR1_CONTRADICTION: record '+$want+' says SUCCESS with rc='+$recs[$want].rc+' reason='+$recs[$want].reason)}
                if ($recs[$want].crc -cne 'OK' -or $recs[$want].published -cne '1') {throw ('UROR1_CONTRADICTION: record '+$want+' says SUCCESS without crc=OK and pub=1')}
            } else {
                if ($recs[$want].rc -ceq '0') {throw ('UROR1_CONTRADICTION: record '+$want+' says FAIL with rc=0')}
                if ($recs[$want].reason -ceq 'ok') {throw ('UROR1_CONTRADICTION: record '+$want+' says FAIL with reason=ok')}
                if ($recs[$want].published -cne '0') {throw ('UROR1_CONTRADICTION: record '+$want+' failed but published a value')}
            }
        }
        # sample chunk arithmetic: every named sample is rebuilt from the printed bytes
        $sam=[regex]::Match($text0,'(?m)^\[TMRO\] reg='+$want+' samples pre=([0-9]+) tail=([0-9]+) cand=([0-9]+)\r?$')
        $decl=[ordered]@{'rx1'=[int]$sam.Groups[1].Value;'rxT'=[int]$sam.Groups[2].Value;'cand'=[int]$sam.Groups[3].Value}
        foreach ($tag in $decl.Keys) {
            $cm=[regex]::Matches($text0,'(?m)^\[TMRO\] reg='+$want+' '+$tag+' off=([0-9]+) n=([0-9]+) hex=([^\r\n]*)\r?$')
            if ($cm.Count -lt 1) {throw ('UROR1_CAPTURE_INCOMPLETE: sample '+$tag+' of record '+$want+' has no chunk line')}
            $pos=0;$sum=0
            foreach ($c1 in $cm) {
                $off1=[int]$c1.Groups[1].Value;$n1=[int]$c1.Groups[2].Value;$hx=$c1.Groups[3].Value
                if ($hx -ceq '-') {$bytes=@()} else {
                    if ($hx -notmatch '^[0-9A-F]{2}( [0-9A-F]{2})*$') {throw ('UROR1_CAPTURE_INCOMPLETE: '+$want+' '+$tag+' off='+$off1+' hex is not an uppercase byte list')}
                    $bytes=@($hx.Split(' '))}
                if ($n1 -ne $bytes.Count) {throw ('UROR1_CAPTURE_INCOMPLETE: '+$want+' '+$tag+' off='+$off1+' claims n='+$n1+' but prints '+$bytes.Count+' hex bytes')}
                if ($off1 -ne $pos) {throw ('UROR1_CAPTURE_INCOMPLETE: '+$want+' '+$tag+' chunk offsets are not contiguous (off='+$off1+' want '+$pos+')')}
                if ($n1 -gt 16) {throw ('UROR1_CAPTURE_INCOMPLETE: '+$want+' '+$tag+' chunk exceeds the 16 byte print block')}
                $pos+=$n1;$sum+=$n1
            }
            if ($sum -ne $decl[$tag]) {throw ('UROR1_CAPTURE_INCOMPLETE: '+$want+' '+$tag+' chunks add up to '+$sum+' but the samples line declares '+$decl[$tag])}
            if ($decl[$tag] -eq 0 -and ($cm.Count -ne 1 -or $cm[0].Groups[3].Value -cne '-')) {throw ('UROR1_CAPTURE_INCOMPLETE: '+$want+' '+$tag+' empty sample is not exported as n=0 hex=-')}
        }
    }

    # open/context/transaction identity must be one coherent story across the three records
    $opens=@($known | ForEach-Object {$recs[$_].open})
    $knownOpen=@('ALREADY_OPEN','OPENED_NOW','OPEN_FAILED','OPEN_NOT_REACHED')
    foreach ($want in $known) {if ($knownOpen -notcontains $recs[$want].open) {throw ('UROR1_CONTRADICTION: record '+$want+' reports open='+$recs[$want].open+' which the firmware never prints')}}
    if ((@($opens | Select-Object -Unique)).Count -ne 1) {throw 'UROR1_CONTRADICTION: the three records disagree about the open identity of this probe'}
    $openName=$opens[0]
    $held=@($known | ForEach-Object {$recs[$_].txn})
    $txnrcs=@($known | ForEach-Object {$recs[$_].txnrc})
    if ((@($held | Select-Object -Unique)).Count -ne 1) {throw 'UROR1_CONTRADICTION: the three records disagree about the transaction'}
    $heldName=$held[0]
    if ((@($txnrcs | Select-Object -Unique)).Count -ne 1) {throw 'UROR1_CONTRADICTION: the three records disagree about the transaction return code'}
    $txnrc=$txnrcs[0]
    $failOpen=0
    if ($openName -ceq 'OPEN_FAILED') {
        $failOpen=1
        if ($readsEnd -ne 0) {throw 'UROR1_CONTRADICTION: reads were attempted although this probe failed to open'}
        foreach ($want in $known) {if ([int]$recs[$want].openrc -eq 0) {throw ('UROR1_CONTRADICTION: OPEN_FAILED on record '+$want+' carries rc=0')}}
    } elseif ($openName -ceq 'OPEN_NOT_REACHED') {
        if ($readsEnd -ne 0) {throw 'UROR1_CONTRADICTION: reads were attempted although the open step was never reached'}
        if ($heldName -cne '0') {throw 'UROR1_CONTRADICTION: open=OPEN_NOT_REACHED while the transaction claims to be held'}
    } else {
        if ($heldName -cne '1') {throw 'UROR1_CONTRADICTION: the open identity claims a live port although no transaction was held'}
        foreach ($want in $known) {if ([int]$recs[$want].openrc -ne 0) {throw ('UROR1_CONTRADICTION: '+$openName+' on record '+$want+' carries rc='+$recs[$want].openrc)}}
    }
    $refused=[regex]::Match($text0,'(?m)^\[TMC\] probe REFUSED \((-?[0-9]+)\)[^\r\n]*\r?$')
    $title=[regex]::Matches($text0,'(?m)^\[TMC\] probe: read-only identity check txn-held IOIN\+GSTAT\+IFCNT \(0 WRITE frames\)\r?$').Count
    if ($heldName -ceq '0') {
        if ($refused.Success -eq $false) {throw 'UROR1_CAPTURE_INCOMPLETE: no transaction was held but no probe REFUSED line carries the reason'}
        if ($refused.Groups[1].Value -cne $txnrc) {throw ('UROR1_CONTRADICTION: probe REFUSED rc='+$refused.Groups[1].Value+' but the records say txn_rc='+$txnrc)}
        if ($title -ne 0) {throw 'UROR1_CONTRADICTION: the txn-held title appears although no transaction was held'}
    } else {
        if ($title -ne 1) {throw 'UROR1_CAPTURE_INCOMPLETE: the txn-held title is missing or duplicated'}
        if ($refused.Success) {throw 'UROR1_CONTRADICTION: probe REFUSED appears although the transaction was held'}
    }
    if ($heldName -ceq '1' -and $failOpen -eq 0 -and $readsEnd -eq 0 -and $openName -cne 'OPEN_FAILED') {throw 'UROR1_CAPTURE_INCOMPLETE: nothing was attempted and no refusal or open failure explains it'}

    # human summary lines must be the full raw values, never a masked-only claim
    $ioLine=[regex]::Match($text0,'(?m)^\[TMC\] IOIN = 0x([0-9A-F]{8}) VERSION = 0x([0-9A-F]{2}) -> [^\r\n]*\r?$')
    $ioFail=[regex]::Match($text0,'(?m)^\[TMC\] IOIN read failed \((-?[0-9]+)\) -> identity NOT confirmed[^\r\n]*\r?$')
    $gsLine=[regex]::Match($text0,'(?m)^\[TMC\] GSTAT = 0x([0-9A-F]{2}) raw=0x([0-9A-F]{8}) reset=([01]) drv_err=([01]) uv_cp=([01])\r?$')
    $gsWarn=[regex]::Match($text0,'(?m)^\[TMC\] !! GSTAT=0x([0-9A-F]{8}) has bits the manual leaves undefined[^\r\n]*\r?$')
    $icLine=[regex]::Match($text0,'(?m)^\[TMC\] IFCNT = ([0-9]+) raw=0x([0-9A-F]{8}) [^\r\n]*\r?$')
    if ($recs['06'].published -ceq '1') {
        if ($ioLine.Success -eq $false) {throw 'UROR1_CAPTURE_INCOMPLETE: IOIN published but no human IOIN line carries the raw value'}
        if ($ioLine.Groups[1].Value -cne $recs['06'].val) {throw 'UROR1_CONTRADICTION: the human IOIN line is not the raw value of record 06'}
        if ($ioFail.Success) {throw 'UROR1_CONTRADICTION: an IOIN read-failed line appears next to a published IOIN value'}
    } elseif ($recs['06'].result -ceq 'FAIL') {
        if ($ioFail.Success -eq $false) {throw 'UROR1_CAPTURE_INCOMPLETE: record 06 failed but no IOIN read-failed line carries the rc'}
        if ($ioFail.Groups[1].Value -cne $recs['06'].rc) {throw 'UROR1_CONTRADICTION: IOIN read-failed rc differs from record 06'}
        if ($ioLine.Success) {throw 'UROR1_CONTRADICTION: an IOIN identity line appears for a read that never published'}
    } elseif ($ioLine.Success -or $ioFail.Success) {throw 'UROR1_CONTRADICTION: an IOIN summary line for a record that never reached the bus'}
    $versionHex='00'; if ($ioLine.Success) {$versionHex=$ioLine.Groups[2].Value}
    if ($ioLine.Success -and $versionHex -cne $ioLine.Groups[1].Value.Substring(0,2)) {throw 'UROR1_CONTRADICTION: the VERSION field is not the top byte of the raw IOIN value'}
    $gsRaw='00000000'; $gsMask='00'
    if ($gsLine.Success) {
        if ($recs['01'].published -cne '1') {throw 'UROR1_CONTRADICTION: a GSTAT summary line although record 01 published nothing'}
        $gsRaw=$gsLine.Groups[2].Value
        if ($gsRaw -cne $recs['01'].val) {throw 'UROR1_CONTRADICTION: the GSTAT raw value is not the value of record 01'}
        $gsMask=$gsRaw.Substring(6,2)
        if ($gsLine.Groups[1].Value -cne $gsMask) {throw 'UROR1_CONTRADICTION: the masked GSTAT byte is not the low byte of the raw value'}
        $gNum=[Convert]::ToInt32($gsMask,16)
        if ([int]$gsLine.Groups[3].Value -ne (Bit-At $gNum 0) -or [int]$gsLine.Groups[4].Value -ne (Bit-At $gNum 1) -or [int]$gsLine.Groups[5].Value -ne (Bit-At $gNum 2)) {throw 'UROR1_CONTRADICTION: the decoded GSTAT bits do not match the raw value'}
        if ($gsLine.Groups[6].Value -ceq '1' -and -not ($text0 -match '(?m)^\[TMC\] !! uv_cp=1[^\r\n]*\r?$')) {throw 'UROR1_CAPTURE_INCOMPLETE: uv_cp=1 without its explanation line'}
    } elseif ($recs['01'].published -ceq '1') {throw 'UROR1_CAPTURE_INCOMPLETE: record 01 published but no GSTAT summary line carries the raw value'}
    $gsOutOfDomain=($gsRaw -notmatch '^0{7}[0-7]$')
    if ($gsLine.Success -and (($gsOutOfDomain -and $gsWarn.Success -eq $false) -or ((-not $gsOutOfDomain) -and $gsWarn.Success))) {throw 'UROR1_CONTRADICTION: the undefined-bit warning and the raw GSTAT value disagree'}
    if ($gsWarn.Success -and $gsWarn.Groups[1].Value -cne $gsRaw) {throw 'UROR1_CONTRADICTION: the undefined-bit warning names a different raw GSTAT value'}
    $icRaw='00000000'
    if ($icLine.Success) {
        if ($recs['02'].published -cne '1') {throw 'UROR1_CONTRADICTION: an IFCNT summary line although record 02 published nothing'}
        $icRaw=$icLine.Groups[2].Value
        if ($icRaw -cne $recs['02'].val) {throw 'UROR1_CONTRADICTION: the IFCNT raw value is not the value of record 02'}
        if ([UInt32]::Parse($icLine.Groups[1].Value) -ne [Convert]::ToUInt32($icRaw.Substring(6,2),16)) {throw 'UROR1_CONTRADICTION: the IFCNT decimal is not the low byte of the raw value'}
    } elseif ($recs['02'].published -ceq '1') {throw 'UROR1_CAPTURE_INCOMPLETE: record 02 published but no IFCNT summary line carries the raw value'}
    $icOutOfDomain=($icRaw -notmatch '^0{6}[0-9A-F]{2}$')

    $sum=[regex]::Match($text0,'(?m)^\[TMRO\] read_ok=([01]) identity_ok=([01]) fields_ok=([01]) link=(READ_OK|UNPROVEN) safe_for_enable=([01])\r?$')
    if ($sum.Success -eq $false) {throw 'UROR1_CAPTURE_INCOMPLETE: the summary line is missing'}
    if ($sum.Groups[5].Value -cne '0') {throw 'UROR1_CONTRADICTION: the probe granted enable permission'}
    $readOk=[int]$sum.Groups[1].Value;$identityOk=[int]$sum.Groups[2].Value;$fieldsOk=[int]$sum.Groups[3].Value
    $expectRead=0; if ($nSuccess -eq 3) {$expectRead=1}
    if ($readOk -ne $expectRead) {throw ('UROR1_CONTRADICTION: read_ok='+$readOk+' but the records give '+$nSuccess+' of 3 SUCCESS')}
    $expectLink='UNPROVEN'; if ($expectRead -eq 1) {$expectLink='READ_OK'}
    if ($sum.Groups[4].Value -cne $expectLink) {throw ('UROR1_CONTRADICTION: link='+$sum.Groups[5].Value+' but the records give '+$nSuccess+'/3 SUCCESS')}
    $expectIdentity=0
    if ($expectRead -eq 1 -and $versionHex -ceq '21') {$expectIdentity=1}
    if ($identityOk -ne $expectIdentity) {throw ('UROR1_CONTRADICTION: identity_ok='+$identityOk+' but VERSION='+$versionHex+' and reads='+$nSuccess+'/3')}
    $expectFields=1
    if ($recs['01'].published -ceq '1' -and $gsOutOfDomain) {$expectFields=0}
    if ($recs['02'].published -ceq '1' -and $icOutOfDomain) {$expectFields=0}
    if ($fieldsOk -ne $expectFields) {throw ('UROR1_CONTRADICTION: fields_ok='+$fieldsOk+' but the raw values are GSTAT=0x'+$gsRaw+' IFCNT=0x'+$icRaw)}

    $business='SUCCESS';$stage='NONE'
    if ($failOpen -eq 1) {$business='FAIL';$stage='OPEN'}
    elseif ($heldName -ceq '0') {$business='FAIL';$stage='TXN'}
    elseif ($nFail -gt 0) {$business='FAIL';$stage='READ'}
    elseif ($identityOk -ne 1) {$business='FAIL';$stage='IDENTITY_MISMATCH'}
    elseif ($fieldsOk -ne 1) {$business='FAIL';$stage='FIELDS_OUT_OF_DOMAIN'}
    return [pscustomobject]@{capture='COMPLETE';business=$business;fail_stage=$stage;attempted_reads=$readsEnd;
        read_ok=$readOk;identity_ok=$identityOk;fields_ok=$fieldsOk;link=$sum.Groups[4].Value;
        register_write_frames=0;safe_for_enable=$false;tmro_lines=$tmroAll;ioin=$recs['06'].val;
        gstat_raw=$gsRaw;ifcnt_raw=$icRaw;open_identity=$openName;txn_held=$heldName;txn_rc=$txnrc;
        record_01=$recs['01'].result;record_02=$recs['02'].result}
}
function Check-UroR1Reply([string]$Command,$Response)
{
    $text0=$Response.Text
    if ($Command -ceq 'tmc_uart_probe') {return Check-TmroReply $text0}
    if ($Command.StartsWith('spm ') -and $Response.Bytes -contains [byte]0) {throw 'URO_OBSERVATION_REJECTED: NUL in spm response'}
    switch -CaseSensitive ($Command) {
        'spm status' {Idle-Status $Response; return}
        'spm cap' {
            [void](One-Line $text0 '[spm] schema=' '(?m)^\[spm\] schema=2 slots=64 record_bytes=120 array_bytes=7680 sizeof_ctx=44\r?$')
            $all0=[regex]::Matches($text0,'(?m)^\[spm\] [^\r\n]*MOTOR_HW=0 STEP_DIAG_ISO=0 TMC_CFG_HW=0 LIMIT_EN=0(?: [^\r\n]*)?\r?$')
            if ($all0.Count -ne 1) {throw 'URO_OBSERVATION_REJECTED: four macros are not all zero'}
            [void](One-Line $text0 '[spm] counter=' '(?m)^\[spm\] counter=(COUNTER_OK|COUNTER_UNAVAILABLE|COUNTER_UNCHECKED) clock_basis=(SOURCE_ASSUMED|CONFIG_READBACK|EXTERNAL_VERIFIED) systick_load=[0-9]+ guard_ready=(YES|NO) quiescent=YES state=0 records=0 in_flight=0 dropped=0\r?$')
            return
        }
        'safety_state' {
            [void](One-Line $text0 '[SS] state=' '(?m)^\[SS\] state=(FAULT_LATCHED|ESTOP|MANUAL_CLEAR|READY) fault_code=[0-9]+ degraded=0x[0-9A-F]{2}\r?$'); return
        }
        'safety_status' {
            [void](One-Line $text0 '[SAFETY] ready=' '(?m)^\[SAFETY\] ready=YES first_err=0\r?$')
            [void](One-Line $text0 '[SAFETY] MCU_DRV_ENABLE =' '(?m)^\[SAFETY\] MCU_DRV_ENABLE = 0 \([^\r\n]*\)\r?$')
            [void](One-Line $text0 '[SAFETY] DRV_ENABLE last' '(?m)^\[SAFETY\] DRV_ENABLE last write\+readback verified = 0 \(-1 = never verified\)\r?$')
            [void](One-Line $text0 '[SAFETY] request=' '(?m)^\[SAFETY\] request=DISABLE\(LOW\) pad=0 expected_FB=HIGH FB=1 -> verdict=OK\r?$'); return
        }
        'motor_status' {
            [void](One-Line $text0 '[MOT] state=' '(?m)^\[MOT\] state=(0|4) cur=0 tgt=0 accel=[0-9]+ decel=[0-9]+ dir=[01]\r?$')
            [void](One-Line $text0 '[MOT] armed=' '(?m)^\[MOT\] armed=0 drv_en_verified_pad=0 health=OK\r?$')
            [void](One-Line $text0 '[MOT] STEP stop:' '(?m)^\[MOT\] STEP stop: result=api-accepted\([^\r\n\x00)]*\) src=[a-z0-9_-]+ rc=0 fail_latched=0 \| [^\r\n]*\r?$')
            [void](One-Line $text0 '[MOT] drive request=' '(?m)^\[MOT\] drive request=LOW pad=0 expected_ENN=HIGH ENN_feedback=1\(resolved\) verdict=OK\r?$')
            [void](One-Line $text0 '[MOT] step-diag permission=' '(?m)^\[MOT\] step-diag permission=DENIED(?: [^\r\n]*)?\r?$')
            return
        }
        'step_status' {
            $stepLine=One-Line $text0 '[STEP] dev=' '(?m)^\[STEP\] dev=epwm1 ch=0 enabled=no\([^\r\n\x00)]*\) last_stop=(not-attempted stop_rc=0|api-accepted\([^\r\n]*)\r?$'
            $visibleRc=[regex]::Matches($stepLine.Value,' stop_rc=(-?[0-9]+)(?![0-9A-Za-z_])')
            if ($visibleRc.Count -gt 1 -or ($visibleRc.Count -eq 1 -and $visibleRc[0].Groups[1].Value -cne '0') -or ($visibleRc.Count -eq 0 -and -not $stepLine.Value.Contains([char]0))) {throw 'URO_OBSERVATION_REJECTED: step rc inconsistent or unexplained missing field'}
            return
        }
        default {throw 'UROR1_CAPTURE_INCOMPLETE: no checker for command'}
    }
}
function Run-UroR1ReadDiag
{
    $commResult=$null
    foreach ($literal in @('spm status','spm cap','safety_state','safety_status','motor_status','step_status','tmc_uart_probe')) {
        $commandReply=Cmd $literal
        Assert-NoReset $commandReply $literal
        $parsed0=Check-UroR1Reply $literal $commandReply
        if ($literal -ceq 'tmc_uart_probe') {$commResult=$parsed0}
        Note 'UROR1_COMMAND_CHECKED' @{command=$literal; result='ACCEPT'; raw_path=[IO.Path]::GetFileName($commandReply.Path)}
    }
    foreach ($literal in $script:Limits.Keys) {if (-not $script:Counts.ContainsKey($literal) -or $script:Counts[$literal] -ne 1) {throw 'UROR1 command count differs'} }
    return $commResult
}
function Fixture-Wire
{
    $wire0=@{}
    $wire0['spm status']='W3NwbV0gc3RhdHVzOiBzdGF0ZT0wIGVudHJ5PS0gcmVjb3Jkcz0wIGluX2ZsaWdodD0wIGRyb3BwZWQ9MCBjb3VudGVyPUNPVU5URVJfVU5DSEVDS0VEIGNsb2NrPVNPVVJDRV9BU1NVTUVEIGd1YXJkPTAgZ2F0ZXNfbm93PTB4MA0KbXNoID4NCm1zaCA+DQo='
    $wire0['spm cap']='W3NwbV0gc2NoZW1hPTIgc2xvdHM9NjQgcmVjb3JkX2J5dGVzPTEyMCBhcnJheV9ieXRlcz03NjgwIHNpemVvZl9jdHg9NDQNCltzcG1dIOe8luivkeWbm+WujzogTU9UT1JfSFc9MCBTVEVQX0RJQUdfSVNPPTAgVE1DX0NGR19IVz0wIExJTUlUX0VOPTAgKOa1i+mHj+W8gOWFs+S4jeaUueWPmOi/meWbm+mBk+mXqCkNCltzcG1dIGNvdW50ZXI9Q09VTlRFUl9VTkNIRUNLRUQgY2xvY2tfYmFzaXM9U09VUkNFX0FTU1VNRUQgc3lzdGlja19sb2FkPTAgZ3VhcmRfcmVhZHk9Tk8gcXVpZXNjZW50PVlFUyBzdGF0ZT0wIHJlY29yZHM9MCBpbl9mbGlnaHQ9MCBkcm9wcGVkPTANCm1zaCA+DQptc2ggPg0K'
    $wire0['safety_state']='W1NTXSBzdGF0ZT1GQVVMVF9MQVRDSEVEIGZhdWx0X2NvZGU9OCBkZWdyYWRlZD0weDAwDQptc2ggPg0KbXNoID4NCg=='
    $wire0['safety_status']='W1NBRkVUWV0gcmVhZHk9WUVTIGZpcnN0X2Vycj0wDQpbU0FGRVRZXSBNQ1VfRFJWX0VOQUJMRSA9IDAgKOemgeatoijlronlhagpKQ0KW1NBRkVUWV0gRFJWX0VOQUJMRSBsYXN0IHdyaXRlK3JlYWRiYWNrIHZlcmlmaWVkID0gMCAoLTEgPSBuZXZlciB2ZXJpZmllZCkNCltTQUZFVFldIEVOTl9GRUVEQkFDSyhQQy42ID0g5p2/5LiKIFRNQ19FTk4pIHJhdz0xIHJlc29sdmVkPVlFUyBb5Y+q6K+76L6T5YWlLCDpnZ7mgKXlgZzmhJ/mtYs7IEhJR0g956aB6IO95L6nLCBMT1c95pyq5pa95Yqg5wBbU0FGRVRZXSByZXF1ZXN0PURJU0FCTEUoTE9XKSBwYWQ9MCBleHBlY3RlZF9GQj1ISUdIIEZCPTEgLT4gdmVyZGljdD1PSw0KW1NBRkVUWV0gTElNSVQgaW5wdXRzID0gRElTQUJMRURfQllfQ09ORklHICjpmZDkvY3lrp7njrDkv53nlZksIOS4jeino+aekC/kuI3phY3nva4v5LiN6K+7L+S4jeazqOWGjCBJUlEpDQptc2ggPg0KbXNoID4NCg=='
    $wire0['motor_status']='W01PVF0gc3RhdGU9NCBjdXI9MCB0Z3Q9MCBhY2NlbD0yMDAwIGRlY2VsPTIwMDAgZGlyPTANCltNT1RdIGFybWVkPTAgZHJ2X2VuX3ZlcmlmaWVkX3BhZD0wIGhlYWx0aD1PSw0KW01PVF0gZHJ2LWd1YXJkOiBzdG9wcz0wIGxhc3RfcmVhc29uPTAgKDA9bm9uZSAxPWNmZy1sb3N0IDI9aW50ZWdyaXR5IDM9dHhuLWJ1c3ktbGltaXQgND1lbm4tZmVlZGJhY2spDQpbTU9UXSBTVEVQIHN0b3A6IHJlc3VsdD1hcGktYWNjZXB0ZWQo6amx5Yqo5o6l5Y+X5YWz5patLCDpnZ7ms6LlvaLor4HmmI4pIHNyYz1lbWVyZ2VuY3ktc3RvcCByYz0wIGZhaWxfbGF0Y2hlZD0wIHwgZGlhZ19vZmZfY28AW01PVF0gYXJtIGdhdGUgZmFpbCBtYXNrPTB4MUIgKGFybSB3aWxsIGJlIFJFRlVTRUQpDQpbTU9UXSBnYXRlNyBkZXRhaWw6IG1vdG9yX3N0b3A9YXBpLWFjY2VwdGVkKOmpseWKqOaOpeWPl+WFs+aWrSwg6Z2e5rOi5b2i6K+B5piOKSBzcmM9ZW1lcmdlbmN5LXN0b3AgcmM9MCBsYXRjaGVkPW5vIHwgZGlhZ19vZmYAW01PVF0gZHJpdmUgcmVxdWVzdD1MT1cgcGFkPTAgZXhwZWN0ZWRfRU5OPUhJR0ggRU5OX2ZlZWRiYWNrPTEocmVzb2x2ZWQpIHZlcmRpY3Q9T0sNCltNT1RdIGxpbWl0IGlucHV0cz1ESVNBQkxFRF9CWV9DT05GSUcgKFNBRkVUWV9MSU1JVF9JTlBVVFNfRU5BQkxFRDsgT0ZGID0+IERJU0FCTEVEX0JZX0NPTkZJRywg5LiN6Kej5p6QL+S4jeivuy/kuI3ms6jlhowpDQpbTU9UXSBzdGVwLWRpYWcgcGVybWlzc2lvbj1ERU5JRUQgKGVuYWJsZS1wYXRoIC8gaXNvbGF0ZWQtZW52IOS4pOS4quWuj+iHs+WwkeS4gOS4quS4uiBUUlVFIOaJjeWFgeiuuOi+k+WHuiBTVEVQKQ0KW01PVF0gdG1jIHJ1bnRpbWUgd3JpdGUgZ2F0ZSBtYXNrPTB4M0INCm1zaCA+DQptc2ggPg0K'
    $wire0['step_status']='W1NURVBdIGRldj1lcHdtMSBjaD0wIGVuYWJsZWQ9bm8o6amx5YqoIEFQSSDmjqXlj5flhbPmlq07IOacquWBmuazouW9oumqjOivgSkgbGFzdF9zdG9wPWFwaS1hY2NlcHRlZCjpqbHliqjmjqXlj5flhbPmlq0sIOmdnuazAFtTVEVQXSDor7TmmI46ICfpqbHliqggQVBJIOaOpeWPl+WFs+aWrScgIT0g56S65rOi5Zmo6K+B5piO5peg6ISJ5YayKEJVRy0wMDgg5pyq6aqM5pS2KTsg5pyq5oyB5pyJ6K+K5pat5omA5pyJ5p2D5Lmf5LiN5Luj6KGo5gBtc2ggPg0KbXNoID4NCg=='
    $wire0['tmc_uart_probe']='W1RNUk9dIEJFR0lOIHY9MSBhZGRyPTAgcGxhbm5lZD0zIHJlYWRzPTMNCltUTUNdIHByb2JlOiByZWFkLW9ubHkgaWRlbnRpdHkgY2hlY2sgdHhuLWhlbGQgSU9JTitHU1RBVCtJRkNOVCAoMCBXUklURSBmcmFtZXMpDQpbVE1ST10gcmVnPTA2IHJlc3VsdD1TVUNDRVNTIHJjPTAgcmVhc29uPW9rDQpbVE1ST10gcmVnPTA2IGN0eD0xIHR4bj0xLzAgb3Blbj1BTFJFQURZX09QRU4vMCBsb2NrPTEvMS8wDQpbVE1ST10gcmVnPTA2IGZsdXNoPTEvMCBieXRlcz0wIG1zPTANCltUTVJPXSByZWc9MDYgdHg9MS8xLzAgb2ZmPTMgcmVxPTA1IDAwIDA2IDZGDQpbVE1ST10gcmVnPTA2IHR4X3dyaXR0ZW49NA0KW1RNUk9dIHJlZz0wNiBzdGFydD0xMDAgdG90YWw9MTI1IHJ4X2JlZ2luPTMgcnhfZGw9MTAzIGRvbmU9Mw0KW1RNUk9dIHJlZz0wNiByeD0xIGhkcj0xIGZ1bGw9MSBjcmM9T0sNCltUTVJPXSByZWc9MDYgdG90YWw9OCBidWY9OCBwdWI9MSBsYXRlPTAgZXhwPW4vbiBwcmVfdHJ1bmM9MCBnYXA9MA0KW1RNUk9dIHJlZz0wNiBlY2hvPU9USEVSIG1hdGNoPTAvMCB2YWw9MjEwMDAwNDANCltUTVJPXSByZWc9MDYgc2FtcGxlcyBwcmU9OCB0YWlsPTggY2FuZD04DQpbVE1ST10gcmVnPTA2IHJ4MSBvZmY9MCBuPTggaGV4PTA1IEZGIDA2IDIxIDAwIDAwIDQwIDRGDQpbVE1ST10gcmVnPTA2IHJ4VCBvZmY9MCBuPTggaGV4PTA1IEZGIDA2IDIxIDAwIDAwIDQwIDRGDQpbVE1ST10gcmVnPTA2IGNhbmQgb2ZmPTAgbj04IGhleD0wNSBGRiAwNiAyMSAwMCAwMCA0MCA0Rg0KW1RNUk9dIHJlZz0wMSByZXN1bHQ9U1VDQ0VTUyByYz0wIHJlYXNvbj1vaw0KW1RNUk9dIHJlZz0wMSBjdHg9MSB0eG49MS8wIG9wZW49QUxSRUFEWV9PUEVOLzAgbG9jaz0xLzEvMA0KW1RNUk9dIHJlZz0wMSBmbHVzaD0xLzAgYnl0ZXM9MCBtcz0wDQpbVE1ST10gcmVnPTAxIHR4PTEvMS8wIG9mZj00IHJlcT0wNSAwMCAwMSBDMQ0KW1RNUk9dIHJlZz0wMSB0eF93cml0dGVuPTQNCltUTVJPXSByZWc9MDEgc3RhcnQ9MTAzIHRvdGFsPTEyNSByeF9iZWdpbj00IHJ4X2RsPTEwNCBkb25lPTQNCltUTVJPXSByZWc9MDEgcng9MSBoZHI9MSBmdWxsPTEgY3JjPU9LDQpbVE1ST10gcmVnPTAxIHRvdGFsPTggYnVmPTggcHViPTEgbGF0ZT0wIGV4cD1uL24gcHJlX3RydW5jPTAgZ2FwPTANCltUTVJPXSByZWc9MDEgZWNobz1PVEhFUiBtYXRjaD0wLzAgdmFsPTAwMDAwMDAxDQpbVE1ST10gcmVnPTAxIHNhbXBsZXMgcHJlPTggdGFpbD04IGNhbmQ9OA0KW1RNUk9dIHJlZz0wMSByeDEgb2ZmPTAgbj04IGhleD0wNSBGRiAwMSAwMCAwMCAwMCAwMSA5QQ0KW1RNUk9dIHJlZz0wMSByeFQgb2ZmPTAgbj04IGhleD0wNSBGRiAwMSAwMCAwMCAwMCAwMSA5QQ0KW1RNUk9dIHJlZz0wMSBjYW5kIG9mZj0wIG49OCBoZXg9MDUgRkYgMDEgMDAgMDAgMDAgMDEgOUENCltUTVJPXSByZWc9MDIgcmVzdWx0PVNVQ0NFU1MgcmM9MCByZWFzb249b2sNCltUTVJPXSByZWc9MDIgY3R4PTEgdHhuPTEvMCBvcGVuPUFMUkVBRFlfT1BFTi8wIGxvY2s9MS8xLzANCltUTVJPXSByZWc9MDIgZmx1c2g9MS8wIGJ5dGVzPTAgbXM9MA0KW1RNUk9dIHJlZz0wMiB0eD0xLzEvMCBvZmY9NCByZXE9MDUgMDAgMDIgOEYNCltUTVJPXSByZWc9MDIgdHhfd3JpdHRlbj00DQpbVE1ST10gcmVnPTAyIHN0YXJ0PTEwNyB0b3RhbD0xMjUgcnhfYmVnaW49NCByeF9kbD0xMDQgZG9uZT00DQpbVE1ST10gcmVnPTAyIHJ4PTEgaGRyPTEgZnVsbD0xIGNyYz1PSw0KW1RNUk9dIHJlZz0wMiB0b3RhbD04IGJ1Zj04IHB1Yj0xIGxhdGU9MCBleHA9bi9uIHByZV90cnVuYz0wIGdhcD0wDQpbVE1ST10gcmVnPTAyIGVjaG89T1RIRVIgbWF0Y2g9MC8wIHZhbD0wMDAwMDAwMA0KW1RNUk9dIHJlZz0wMiBzYW1wbGVzIHByZT04IHRhaWw9OCBjYW5kPTgNCltUTVJPXSByZWc9MDIgcngxIG9mZj0wIG49OCBoZXg9MDUgRkYgMDIgMDAgMDAgMDAgMDAgNEMNCltUTVJPXSByZWc9MDIgcnhUIG9mZj0wIG49OCBoZXg9MDUgRkYgMDIgMDAgMDAgMDAgMDAgNEMNCltUTVJPXSByZWc9MDIgY2FuZCBvZmY9MCBuPTggaGV4PTA1IEZGIDAyIDAwIDAwIDAwIDAwIDRDDQpbVE1DXSBJT0lOID0gMHgyMTAwMDA0MCBWRVJTSU9OID0gMHgyMSAtPiBUTUMyMjA5ICgweDIxKSBpZGVudGl0eSBtYXRjaA0KW1RNQ10gR1NUQVQgPSAweDAxIHJhdz0weDAwMDAwMDAxIHJlc2V0PTEgZHJ2X2Vycj0wIHV2X2NwPTANCltUTUNdIElGQ05UID0gMCByYXc9MHgwMDAwMDAwMCAocmVhZC1vbmx5OiB0aGlzIGNvbW1hbmQgd3JvdGUgbm8gcmVnaXN0ZXIpDQpbVE1ST10gcmVhZF9vaz0xIGlkZW50aXR5X29rPTEgZmllbGRzX29rPTEgbGluaz1SRUFEX09LIHNhZmVfZm9yX2VuYWJsZT0wDQpbVE1ST10gRU5EIHJlYWRzPTMgd3JpdGVzPTAgZXhwb3J0X2Vycj0wIG9ic2VydmF0aW9uX2NvbXBsZXRlPTENCm1zaCA+DQptc2ggPg0K'
    return $wire0
}
function Fixture-TmroSlices
{
    # Every slice below is the REAL stdout of this round's host probe of the FINAL
    # firmware bytes (out-of-repo fixture harness); nothing here is hand-written.
    $s0=@{}
    $s0['healthy']='W1RNUk9dIEJFR0lOIHY9MSBhZGRyPTAgcGxhbm5lZD0zIHJlYWRzPTMNCltUTUNdIHByb2JlOiByZWFkLW9ubHkgaWRlbnRpdHkgY2hlY2sgdHhuLWhlbGQgSU9JTitHU1RBVCtJRkNOVCAoMCBXUklURSBmcmFtZXMpDQpbVE1ST10gcmVnPTA2IHJlc3VsdD1TVUNDRVNTIHJjPTAgcmVhc29uPW9rDQpbVE1ST10gcmVnPTA2IGN0eD0xIHR4bj0xLzAgb3Blbj1BTFJFQURZX09QRU4vMCBsb2NrPTEvMS8wDQpbVE1ST10gcmVnPTA2IGZsdXNoPTEvMCBieXRlcz0wIG1zPTANCltUTVJPXSByZWc9MDYgdHg9MS8xLzAgb2ZmPTMgcmVxPTA1IDAwIDA2IDZGDQpbVE1ST10gcmVnPTA2IHR4X3dyaXR0ZW49NA0KW1RNUk9dIHJlZz0wNiBzdGFydD0xMDAgdG90YWw9MTI1IHJ4X2JlZ2luPTMgcnhfZGw9MTAzIGRvbmU9Mw0KW1RNUk9dIHJlZz0wNiByeD0xIGhkcj0xIGZ1bGw9MSBjcmM9T0sNCltUTVJPXSByZWc9MDYgdG90YWw9OCBidWY9OCBwdWI9MSBsYXRlPTAgZXhwPW4vbiBwcmVfdHJ1bmM9MCBnYXA9MA0KW1RNUk9dIHJlZz0wNiBlY2hvPU9USEVSIG1hdGNoPTAvMCB2YWw9MjEwMDAwNDANCltUTVJPXSByZWc9MDYgc2FtcGxlcyBwcmU9OCB0YWlsPTggY2FuZD04DQpbVE1ST10gcmVnPTA2IHJ4MSBvZmY9MCBuPTggaGV4PTA1IEZGIDA2IDIxIDAwIDAwIDQwIDRGDQpbVE1ST10gcmVnPTA2IHJ4VCBvZmY9MCBuPTggaGV4PTA1IEZGIDA2IDIxIDAwIDAwIDQwIDRGDQpbVE1ST10gcmVnPTA2IGNhbmQgb2ZmPTAgbj04IGhleD0wNSBGRiAwNiAyMSAwMCAwMCA0MCA0Rg0KW1RNUk9dIHJlZz0wMSByZXN1bHQ9U1VDQ0VTUyByYz0wIHJlYXNvbj1vaw0KW1RNUk9dIHJlZz0wMSBjdHg9MSB0eG49MS8wIG9wZW49QUxSRUFEWV9PUEVOLzAgbG9jaz0xLzEvMA0KW1RNUk9dIHJlZz0wMSBmbHVzaD0xLzAgYnl0ZXM9MCBtcz0wDQpbVE1ST10gcmVnPTAxIHR4PTEvMS8wIG9mZj00IHJlcT0wNSAwMCAwMSBDMQ0KW1RNUk9dIHJlZz0wMSB0eF93cml0dGVuPTQNCltUTVJPXSByZWc9MDEgc3RhcnQ9MTAzIHRvdGFsPTEyNSByeF9iZWdpbj00IHJ4X2RsPTEwNCBkb25lPTQNCltUTVJPXSByZWc9MDEgcng9MSBoZHI9MSBmdWxsPTEgY3JjPU9LDQpbVE1ST10gcmVnPTAxIHRvdGFsPTggYnVmPTggcHViPTEgbGF0ZT0wIGV4cD1uL24gcHJlX3RydW5jPTAgZ2FwPTANCltUTVJPXSByZWc9MDEgZWNobz1PVEhFUiBtYXRjaD0wLzAgdmFsPTAwMDAwMDAxDQpbVE1ST10gcmVnPTAxIHNhbXBsZXMgcHJlPTggdGFpbD04IGNhbmQ9OA0KW1RNUk9dIHJlZz0wMSByeDEgb2ZmPTAgbj04IGhleD0wNSBGRiAwMSAwMCAwMCAwMCAwMSA5QQ0KW1RNUk9dIHJlZz0wMSByeFQgb2ZmPTAgbj04IGhleD0wNSBGRiAwMSAwMCAwMCAwMCAwMSA5QQ0KW1RNUk9dIHJlZz0wMSBjYW5kIG9mZj0wIG49OCBoZXg9MDUgRkYgMDEgMDAgMDAgMDAgMDEgOUENCltUTVJPXSByZWc9MDIgcmVzdWx0PVNVQ0NFU1MgcmM9MCByZWFzb249b2sNCltUTVJPXSByZWc9MDIgY3R4PTEgdHhuPTEvMCBvcGVuPUFMUkVBRFlfT1BFTi8wIGxvY2s9MS8xLzANCltUTVJPXSByZWc9MDIgZmx1c2g9MS8wIGJ5dGVzPTAgbXM9MA0KW1RNUk9dIHJlZz0wMiB0eD0xLzEvMCBvZmY9NCByZXE9MDUgMDAgMDIgOEYNCltUTVJPXSByZWc9MDIgdHhfd3JpdHRlbj00DQpbVE1ST10gcmVnPTAyIHN0YXJ0PTEwNyB0b3RhbD0xMjUgcnhfYmVnaW49NCByeF9kbD0xMDQgZG9uZT00DQpbVE1ST10gcmVnPTAyIHJ4PTEgaGRyPTEgZnVsbD0xIGNyYz1PSw0KW1RNUk9dIHJlZz0wMiB0b3RhbD04IGJ1Zj04IHB1Yj0xIGxhdGU9MCBleHA9bi9uIHByZV90cnVuYz0wIGdhcD0wDQpbVE1ST10gcmVnPTAyIGVjaG89T1RIRVIgbWF0Y2g9MC8wIHZhbD0wMDAwMDAwMA0KW1RNUk9dIHJlZz0wMiBzYW1wbGVzIHByZT04IHRhaWw9OCBjYW5kPTgNCltUTVJPXSByZWc9MDIgcngxIG9mZj0wIG49OCBoZXg9MDUgRkYgMDIgMDAgMDAgMDAgMDAgNEMNCltUTVJPXSByZWc9MDIgcnhUIG9mZj0wIG49OCBoZXg9MDUgRkYgMDIgMDAgMDAgMDAgMDAgNEMNCltUTVJPXSByZWc9MDIgY2FuZCBvZmY9MCBuPTggaGV4PTA1IEZGIDAyIDAwIDAwIDAwIDAwIDRDDQpbVE1DXSBJT0lOID0gMHgyMTAwMDA0MCBWRVJTSU9OID0gMHgyMSAtPiBUTUMyMjA5ICgweDIxKSBpZGVudGl0eSBtYXRjaA0KW1RNQ10gR1NUQVQgPSAweDAxIHJhdz0weDAwMDAwMDAxIHJlc2V0PTEgZHJ2X2Vycj0wIHV2X2NwPTANCltUTUNdIElGQ05UID0gMCByYXc9MHgwMDAwMDAwMCAocmVhZC1vbmx5OiB0aGlzIGNvbW1hbmQgd3JvdGUgbm8gcmVnaXN0ZXIpDQpbVE1ST10gcmVhZF9vaz0xIGlkZW50aXR5X29rPTEgZmllbGRzX29rPTEgbGluaz1SRUFEX09LIHNhZmVfZm9yX2VuYWJsZT0wDQpbVE1ST10gRU5EIHJlYWRzPTMgd3JpdGVzPTAgZXhwb3J0X2Vycj0wIG9ic2VydmF0aW9uX2NvbXBsZXRlPTENCm1zaCA+DQptc2ggPg0K'
    $s0['probe_junk']='W1RNUk9dIEJFR0lOIHY9MSBhZGRyPTAgcGxhbm5lZD0zIHJlYWRzPTMNCltUTUNdIHByb2JlOiByZWFkLW9ubHkgaWRlbnRpdHkgY2hlY2sgdHhuLWhlbGQgSU9JTitHU1RBVCtJRkNOVCAoMCBXUklURSBmcmFtZXMpDQpbVE1ST10gcmVnPTA2IHJlc3VsdD1TVUNDRVNTIHJjPTAgcmVhc29uPW9rDQpbVE1ST10gcmVnPTA2IGN0eD0xIHR4bj0xLzAgb3Blbj1BTFJFQURZX09QRU4vMCBsb2NrPTEvMS8wDQpbVE1ST10gcmVnPTA2IGZsdXNoPTEvMCBieXRlcz0wIG1zPTANCltUTVJPXSByZWc9MDYgdHg9MS8xLzAgb2ZmPTMgcmVxPTA1IDAwIDA2IDZGDQpbVE1ST10gcmVnPTA2IHR4X3dyaXR0ZW49NA0KW1RNUk9dIHJlZz0wNiBzdGFydD0xMDAgdG90YWw9MTI1IHJ4X2JlZ2luPTMgcnhfZGw9MTAzIGRvbmU9Mw0KW1RNUk9dIHJlZz0wNiByeD0xIGhkcj0xIGZ1bGw9MSBjcmM9T0sNCltUTVJPXSByZWc9MDYgdG90YWw9MzIgYnVmPTMyIHB1Yj0xIGxhdGU9MCBleHA9bi9uIHByZV90cnVuYz0wIGdhcD0wDQpbVE1ST10gcmVnPTA2IGVjaG89T1RIRVIgbWF0Y2g9MC8wIHZhbD0yMTAwMDA0MA0KW1RNUk9dIHJlZz0wNiBzYW1wbGVzIHByZT0zMiB0YWlsPTE2IGNhbmQ9OA0KW1RNUk9dIHJlZz0wNiByeDEgb2ZmPTAgbj0xNiBoZXg9MTAgMTEgMTIgMTMgMTQgMTUgMTYgMTcgMTggMTkgMUEgMUIgMUMgMUQgMUUgMUYNCltUTVJPXSByZWc9MDYgcngxIG9mZj0xNiBuPTE2IGhleD0yMCAyMSAyMiAyMyAyNCAyNSAyNiAyNyAwNSBGRiAwNiAyMSAwMCAwMCA0MCA0Rg0KW1RNUk9dIHJlZz0wNiByeFQgb2ZmPTAgbj0xNiBoZXg9MjAgMjEgMjIgMjMgMjQgMjUgMjYgMjcgMDUgRkYgMDYgMjEgMDAgMDAgNDAgNEYNCltUTVJPXSByZWc9MDYgY2FuZCBvZmY9MCBuPTggaGV4PTA1IEZGIDA2IDIxIDAwIDAwIDQwIDRGDQpbVE1ST10gcmVnPTAxIHJlc3VsdD1TVUNDRVNTIHJjPTAgcmVhc29uPW9rDQpbVE1ST10gcmVnPTAxIGN0eD0xIHR4bj0xLzAgb3Blbj1BTFJFQURZX09QRU4vMCBsb2NrPTEvMS8wDQpbVE1ST10gcmVnPTAxIGZsdXNoPTEvMCBieXRlcz0wIG1zPTANCltUTVJPXSByZWc9MDEgdHg9MS8xLzAgb2ZmPTQgcmVxPTA1IDAwIDAxIEMxDQpbVE1ST10gcmVnPTAxIHR4X3dyaXR0ZW49NA0KW1RNUk9dIHJlZz0wMSBzdGFydD0xMDMgdG90YWw9MTI1IHJ4X2JlZ2luPTQgcnhfZGw9MTA0IGRvbmU9NA0KW1RNUk9dIHJlZz0wMSByeD0xIGhkcj0xIGZ1bGw9MSBjcmM9T0sNCltUTVJPXSByZWc9MDEgdG90YWw9MzIgYnVmPTMyIHB1Yj0xIGxhdGU9MCBleHA9bi9uIHByZV90cnVuYz0wIGdhcD0wDQpbVE1ST10gcmVnPTAxIGVjaG89T1RIRVIgbWF0Y2g9MC8wIHZhbD0wMDAwMDAwMQ0KW1RNUk9dIHJlZz0wMSBzYW1wbGVzIHByZT0zMiB0YWlsPTE2IGNhbmQ9OA0KW1RNUk9dIHJlZz0wMSByeDEgb2ZmPTAgbj0xNiBoZXg9MTAgMTEgMTIgMTMgMTQgMTUgMTYgMTcgMTggMTkgMUEgMUIgMUMgMUQgMUUgMUYNCltUTVJPXSByZWc9MDEgcngxIG9mZj0xNiBuPTE2IGhleD0yMCAyMSAyMiAyMyAyNCAyNSAyNiAyNyAwNSBGRiAwMSAwMCAwMCAwMCAwMSA5QQ0KW1RNUk9dIHJlZz0wMSByeFQgb2ZmPTAgbj0xNiBoZXg9MjAgMjEgMjIgMjMgMjQgMjUgMjYgMjcgMDUgRkYgMDEgMDAgMDAgMDAgMDEgOUENCltUTVJPXSByZWc9MDEgY2FuZCBvZmY9MCBuPTggaGV4PTA1IEZGIDAxIDAwIDAwIDAwIDAxIDlBDQpbVE1ST10gcmVnPTAyIHJlc3VsdD1TVUNDRVNTIHJjPTAgcmVhc29uPW9rDQpbVE1ST10gcmVnPTAyIGN0eD0xIHR4bj0xLzAgb3Blbj1BTFJFQURZX09QRU4vMCBsb2NrPTEvMS8wDQpbVE1ST10gcmVnPTAyIGZsdXNoPTEvMCBieXRlcz0wIG1zPTANCltUTVJPXSByZWc9MDIgdHg9MS8xLzAgb2ZmPTQgcmVxPTA1IDAwIDAyIDhGDQpbVE1ST10gcmVnPTAyIHR4X3dyaXR0ZW49NA0KW1RNUk9dIHJlZz0wMiBzdGFydD0xMDcgdG90YWw9MTI1IHJ4X2JlZ2luPTQgcnhfZGw9MTA0IGRvbmU9NA0KW1RNUk9dIHJlZz0wMiByeD0xIGhkcj0xIGZ1bGw9MSBjcmM9T0sNCltUTVJPXSByZWc9MDIgdG90YWw9MzIgYnVmPTMyIHB1Yj0xIGxhdGU9MCBleHA9bi9uIHByZV90cnVuYz0wIGdhcD0wDQpbVE1ST10gcmVnPTAyIGVjaG89T1RIRVIgbWF0Y2g9MC8wIHZhbD0wMDAwMDAwMA0KW1RNUk9dIHJlZz0wMiBzYW1wbGVzIHByZT0zMiB0YWlsPTE2IGNhbmQ9OA0KW1RNUk9dIHJlZz0wMiByeDEgb2ZmPTAgbj0xNiBoZXg9MTAgMTEgMTIgMTMgMTQgMTUgMTYgMTcgMTggMTkgMUEgMUIgMUMgMUQgMUUgMUYNCltUTVJPXSByZWc9MDIgcngxIG9mZj0xNiBuPTE2IGhleD0yMCAyMSAyMiAyMyAyNCAyNSAyNiAyNyAwNSBGRiAwMiAwMCAwMCAwMCAwMCA0Qw0KW1RNUk9dIHJlZz0wMiByeFQgb2ZmPTAgbj0xNiBoZXg9MjAgMjEgMjIgMjMgMjQgMjUgMjYgMjcgMDUgRkYgMDIgMDAgMDAgMDAgMDAgNEMNCltUTVJPXSByZWc9MDIgY2FuZCBvZmY9MCBuPTggaGV4PTA1IEZGIDAyIDAwIDAwIDAwIDAwIDRDDQpbVE1DXSBJT0lOID0gMHgyMTAwMDA0MCBWRVJTSU9OID0gMHgyMSAtPiBUTUMyMjA5ICgweDIxKSBpZGVudGl0eSBtYXRjaA0KW1RNQ10gR1NUQVQgPSAweDAxIHJhdz0weDAwMDAwMDAxIHJlc2V0PTEgZHJ2X2Vycj0wIHV2X2NwPTANCltUTUNdIElGQ05UID0gMCByYXc9MHgwMDAwMDAwMCAocmVhZC1vbmx5OiB0aGlzIGNvbW1hbmQgd3JvdGUgbm8gcmVnaXN0ZXIpDQpbVE1ST10gcmVhZF9vaz0xIGlkZW50aXR5X29rPTEgZmllbGRzX29rPTEgbGluaz1SRUFEX09LIHNhZmVfZm9yX2VuYWJsZT0wDQpbVE1ST10gRU5EIHJlYWRzPTMgd3JpdGVzPTAgZXhwb3J0X2Vycj0wIG9ic2VydmF0aW9uX2NvbXBsZXRlPTENCm1zaCA+DQptc2ggPg0K'
    $s0['rx_timeout']='W1RNUk9dIEJFR0lOIHY9MSBhZGRyPTAgcGxhbm5lZD0zIHJlYWRzPTENCltUTUNdIHByb2JlOiByZWFkLW9ubHkgaWRlbnRpdHkgY2hlY2sgdHhuLWhlbGQgSU9JTitHU1RBVCtJRkNOVCAoMCBXUklURSBmcmFtZXMpDQpbVE1ST10gcmVnPTA2IHJlc3VsdD1GQUlMIHJjPS0xMTcgcmVhc29uPXJ4LXRpbWVvdXQtZWNobw0KW1RNUk9dIHJlZz0wNiBjdHg9MSB0eG49MS8wIG9wZW49QUxSRUFEWV9PUEVOLzAgbG9jaz0xLzEvMA0KW1RNUk9dIHJlZz0wNiBmbHVzaD0xLzAgYnl0ZXM9MCBtcz0wDQpbVE1ST10gcmVnPTA2IHR4PTEvMS8wIG9mZj0zIHJlcT0wNSAwMCAwNiA2Rg0KW1RNUk9dIHJlZz0wNiB0eF93cml0dGVuPTQNCltUTVJPXSByZWc9MDYgc3RhcnQ9MTAwIHRvdGFsPTEyNSByeF9iZWdpbj0zIHJ4X2RsPTEwMyBkb25lPTEwMw0KW1RNUk9dIHJlZz0wNiByeD0xIGhkcj0wIGZ1bGw9MCBjcmM9Tk9UX1JFQUNIRUQNCltUTVJPXSByZWc9MDYgdG90YWw9MCBidWY9MCBwdWI9MCBsYXRlPTAgZXhwPW4veSBwcmVfdHJ1bmM9MCBnYXA9MA0KW1RNUk9dIHJlZz0wNiBlY2hvPU5PTkUgbWF0Y2g9MC8wIHZhbD0wMDAwMDAwMA0KW1RNUk9dIHJlZz0wNiBzYW1wbGVzIHByZT0wIHRhaWw9MCBjYW5kPTANCltUTVJPXSByZWc9MDYgcngxIG9mZj0wIG49MCBoZXg9LQ0KW1RNUk9dIHJlZz0wNiByeFQgb2ZmPTAgbj0wIGhleD0tDQpbVE1ST10gcmVnPTA2IGNhbmQgb2ZmPTAgbj0wIGhleD0tDQpbVE1ST10gcmVnPTAxIHJlc3VsdD1OT1RfUkVBQ0hFRCByYz0tIHJlYXNvbj0tDQpbVE1ST10gcmVnPTAxIGN0eD0xIHR4bj0xLzAgb3Blbj1BTFJFQURZX09QRU4vMCBsb2NrPTAvMC8wDQpbVE1ST10gcmVnPTAxIGZsdXNoPTAvMCBieXRlcz0wIG1zPTANCltUTVJPXSByZWc9MDEgdHg9MC8wLzAgb2ZmPTAgcmVxPQ0KW1RNUk9dIHJlZz0wMSB0eF93cml0dGVuPTANCltUTVJPXSByZWc9MDEgdGltaW5nPS0gKGJ1ZGdldCBuZXZlciBvcGVuZWQpDQpbVE1ST10gcmVnPTAxIHJ4PTAgaGRyPTAgZnVsbD0wIGNyYz1OT1RfUkVBQ0hFRA0KW1RNUk9dIHJlZz0wMSB0b3RhbD0wIGJ1Zj0wIHB1Yj0wIGxhdGU9MCBleHA9LS8tIHByZV90cnVuYz0wIGdhcD0wDQpbVE1ST10gcmVnPTAxIGVjaG89VU5LTk9XTiBtYXRjaD0wLzAgdmFsPTAwMDAwMDAwDQpbVE1ST10gcmVnPTAxIHNhbXBsZXMgcHJlPTAgdGFpbD0wIGNhbmQ9MA0KW1RNUk9dIHJlZz0wMSByeDEgb2ZmPTAgbj0wIGhleD0tDQpbVE1ST10gcmVnPTAxIHJ4VCBvZmY9MCBuPTAgaGV4PS0NCltUTVJPXSByZWc9MDEgY2FuZCBvZmY9MCBuPTAgaGV4PS0NCltUTVJPXSByZWc9MDIgcmVzdWx0PU5PVF9SRUFDSEVEIHJjPS0gcmVhc29uPS0NCltUTVJPXSByZWc9MDIgY3R4PTEgdHhuPTEvMCBvcGVuPUFMUkVBRFlfT1BFTi8wIGxvY2s9MC8wLzANCltUTVJPXSByZWc9MDIgZmx1c2g9MC8wIGJ5dGVzPTAgbXM9MA0KW1RNUk9dIHJlZz0wMiB0eD0wLzAvMCBvZmY9MCByZXE9DQpbVE1ST10gcmVnPTAyIHR4X3dyaXR0ZW49MA0KW1RNUk9dIHJlZz0wMiB0aW1pbmc9LSAoYnVkZ2V0IG5ldmVyIG9wZW5lZCkNCltUTVJPXSByZWc9MDIgcng9MCBoZHI9MCBmdWxsPTAgY3JjPU5PVF9SRUFDSEVEDQpbVE1ST10gcmVnPTAyIHRvdGFsPTAgYnVmPTAgcHViPTAgbGF0ZT0wIGV4cD0tLy0gcHJlX3RydW5jPTAgZ2FwPTANCltUTVJPXSByZWc9MDIgZWNobz1VTktOT1dOIG1hdGNoPTAvMCB2YWw9MDAwMDAwMDANCltUTVJPXSByZWc9MDIgc2FtcGxlcyBwcmU9MCB0YWlsPTAgY2FuZD0wDQpbVE1ST10gcmVnPTAyIHJ4MSBvZmY9MCBuPTAgaGV4PS0NCltUTVJPXSByZWc9MDIgcnhUIG9mZj0wIG49MCBoZXg9LQ0KW1RNUk9dIHJlZz0wMiBjYW5kIG9mZj0wIG49MCBoZXg9LQ0KW1RNQ10gSU9JTiByZWFkIGZhaWxlZCAoLTExNykgLT4gaWRlbnRpdHkgTk9UIGNvbmZpcm1lZCAoY2hpcCBkYW1hZ2UgTk9UIGltcGxpZWQpDQpbVE1ST10gcmVhZF9vaz0wIGlkZW50aXR5X29rPTAgZmllbGRzX29rPTEgbGluaz1VTlBST1ZFTiBzYWZlX2Zvcl9lbmFibGU9MA0KW1RNUk9dIEVORCByZWFkcz0xIHdyaXRlcz0wIGV4cG9ydF9lcnI9MCBvYnNlcnZhdGlvbl9jb21wbGV0ZT0xDQptc2ggPg0KbXNoID4NCg=='
    $s0['open_fail']='W1RNUk9dIEJFR0lOIHY9MSBhZGRyPTAgcGxhbm5lZD0zIHJlYWRzPTANCltUTUNdIHByb2JlOiByZWFkLW9ubHkgaWRlbnRpdHkgY2hlY2sgdHhuLWhlbGQgSU9JTitHU1RBVCtJRkNOVCAoMCBXUklURSBmcmFtZXMpDQpbVE1ST10gcmVnPTA2IHJlc3VsdD1OT1RfUkVBQ0hFRCByYz0tIHJlYXNvbj0tDQpbVE1ST10gcmVnPTA2IGN0eD0xIHR4bj0xLzAgb3Blbj1PUEVOX0ZBSUxFRC8tMjU1IGxvY2s9MC8wLzANCltUTVJPXSByZWc9MDYgZmx1c2g9MC8wIGJ5dGVzPTAgbXM9MA0KW1RNUk9dIHJlZz0wNiB0eD0wLzAvMCBvZmY9MCByZXE9DQpbVE1ST10gcmVnPTA2IHR4X3dyaXR0ZW49MA0KW1RNUk9dIHJlZz0wNiB0aW1pbmc9LSAoYnVkZ2V0IG5ldmVyIG9wZW5lZCkNCltUTVJPXSByZWc9MDYgcng9MCBoZHI9MCBmdWxsPTAgY3JjPU5PVF9SRUFDSEVEDQpbVE1ST10gcmVnPTA2IHRvdGFsPTAgYnVmPTAgcHViPTAgbGF0ZT0wIGV4cD0tLy0gcHJlX3RydW5jPTAgZ2FwPTANCltUTVJPXSByZWc9MDYgZWNobz1VTktOT1dOIG1hdGNoPTAvMCB2YWw9MDAwMDAwMDANCltUTVJPXSByZWc9MDYgc2FtcGxlcyBwcmU9MCB0YWlsPTAgY2FuZD0wDQpbVE1ST10gcmVnPTA2IHJ4MSBvZmY9MCBuPTAgaGV4PS0NCltUTVJPXSByZWc9MDYgcnhUIG9mZj0wIG49MCBoZXg9LQ0KW1RNUk9dIHJlZz0wNiBjYW5kIG9mZj0wIG49MCBoZXg9LQ0KW1RNUk9dIHJlZz0wMSByZXN1bHQ9Tk9UX1JFQUNIRUQgcmM9LSByZWFzb249LQ0KW1RNUk9dIHJlZz0wMSBjdHg9MSB0eG49MS8wIG9wZW49T1BFTl9GQUlMRUQvLTI1NSBsb2NrPTAvMC8wDQpbVE1ST10gcmVnPTAxIGZsdXNoPTAvMCBieXRlcz0wIG1zPTANCltUTVJPXSByZWc9MDEgdHg9MC8wLzAgb2ZmPTAgcmVxPQ0KW1RNUk9dIHJlZz0wMSB0eF93cml0dGVuPTANCltUTVJPXSByZWc9MDEgdGltaW5nPS0gKGJ1ZGdldCBuZXZlciBvcGVuZWQpDQpbVE1ST10gcmVnPTAxIHJ4PTAgaGRyPTAgZnVsbD0wIGNyYz1OT1RfUkVBQ0hFRA0KW1RNUk9dIHJlZz0wMSB0b3RhbD0wIGJ1Zj0wIHB1Yj0wIGxhdGU9MCBleHA9LS8tIHByZV90cnVuYz0wIGdhcD0wDQpbVE1ST10gcmVnPTAxIGVjaG89VU5LTk9XTiBtYXRjaD0wLzAgdmFsPTAwMDAwMDAwDQpbVE1ST10gcmVnPTAxIHNhbXBsZXMgcHJlPTAgdGFpbD0wIGNhbmQ9MA0KW1RNUk9dIHJlZz0wMSByeDEgb2ZmPTAgbj0wIGhleD0tDQpbVE1ST10gcmVnPTAxIHJ4VCBvZmY9MCBuPTAgaGV4PS0NCltUTVJPXSByZWc9MDEgY2FuZCBvZmY9MCBuPTAgaGV4PS0NCltUTVJPXSByZWc9MDIgcmVzdWx0PU5PVF9SRUFDSEVEIHJjPS0gcmVhc29uPS0NCltUTVJPXSByZWc9MDIgY3R4PTEgdHhuPTEvMCBvcGVuPU9QRU5fRkFJTEVELy0yNTUgbG9jaz0wLzAvMA0KW1RNUk9dIHJlZz0wMiBmbHVzaD0wLzAgYnl0ZXM9MCBtcz0wDQpbVE1ST10gcmVnPTAyIHR4PTAvMC8wIG9mZj0wIHJlcT0NCltUTVJPXSByZWc9MDIgdHhfd3JpdHRlbj0wDQpbVE1ST10gcmVnPTAyIHRpbWluZz0tIChidWRnZXQgbmV2ZXIgb3BlbmVkKQ0KW1RNUk9dIHJlZz0wMiByeD0wIGhkcj0wIGZ1bGw9MCBjcmM9Tk9UX1JFQUNIRUQNCltUTVJPXSByZWc9MDIgdG90YWw9MCBidWY9MCBwdWI9MCBsYXRlPTAgZXhwPS0vLSBwcmVfdHJ1bmM9MCBnYXA9MA0KW1RNUk9dIHJlZz0wMiBlY2hvPVVOS05PV04gbWF0Y2g9MC8wIHZhbD0wMDAwMDAwMA0KW1RNUk9dIHJlZz0wMiBzYW1wbGVzIHByZT0wIHRhaWw9MCBjYW5kPTANCltUTVJPXSByZWc9MDIgcngxIG9mZj0wIG49MCBoZXg9LQ0KW1RNUk9dIHJlZz0wMiByeFQgb2ZmPTAgbj0wIGhleD0tDQpbVE1ST10gcmVnPTAyIGNhbmQgb2ZmPTAgbj0wIGhleD0tDQpbVE1ST10gcmVhZF9vaz0wIGlkZW50aXR5X29rPTAgZmllbGRzX29rPTEgbGluaz1VTlBST1ZFTiBzYWZlX2Zvcl9lbmFibGU9MA0KW1RNUk9dIEVORCByZWFkcz0wIHdyaXRlcz0wIGV4cG9ydF9lcnI9MCBvYnNlcnZhdGlvbl9jb21wbGV0ZT0xDQptc2ggPg0KbXNoID4NCg=='
    $s0['txn_unavailable']='W1RNUk9dIEJFR0lOIHY9MSBhZGRyPTAgcGxhbm5lZD0zIHJlYWRzPTANCltUTUNdIHByb2JlIFJFRlVTRUQgKC00NSk6IGNmZyB0eG4gdW5hdmFpbGFibGUgb3IgYnVzeSAtPiAwIGJ1cyByZWFkcw0KW1RNUk9dIHJlZz0wNiByZXN1bHQ9Tk9UX1JFQUNIRUQgcmM9LSByZWFzb249LQ0KW1RNUk9dIHJlZz0wNiBjdHg9MSB0eG49MC8tNDUgb3Blbj1PUEVOX05PVF9SRUFDSEVELzAgbG9jaz0wLzAvMA0KW1RNUk9dIHJlZz0wNiBmbHVzaD0wLzAgYnl0ZXM9MCBtcz0wDQpbVE1ST10gcmVnPTA2IHR4PTAvMC8wIG9mZj0wIHJlcT0NCltUTVJPXSByZWc9MDYgdHhfd3JpdHRlbj0wDQpbVE1ST10gcmVnPTA2IHRpbWluZz0tIChidWRnZXQgbmV2ZXIgb3BlbmVkKQ0KW1RNUk9dIHJlZz0wNiByeD0wIGhkcj0wIGZ1bGw9MCBjcmM9Tk9UX1JFQUNIRUQNCltUTVJPXSByZWc9MDYgdG90YWw9MCBidWY9MCBwdWI9MCBsYXRlPTAgZXhwPS0vLSBwcmVfdHJ1bmM9MCBnYXA9MA0KW1RNUk9dIHJlZz0wNiBlY2hvPVVOS05PV04gbWF0Y2g9MC8wIHZhbD0wMDAwMDAwMA0KW1RNUk9dIHJlZz0wNiBzYW1wbGVzIHByZT0wIHRhaWw9MCBjYW5kPTANCltUTVJPXSByZWc9MDYgcngxIG9mZj0wIG49MCBoZXg9LQ0KW1RNUk9dIHJlZz0wNiByeFQgb2ZmPTAgbj0wIGhleD0tDQpbVE1ST10gcmVnPTA2IGNhbmQgb2ZmPTAgbj0wIGhleD0tDQpbVE1ST10gcmVnPTAxIHJlc3VsdD1OT1RfUkVBQ0hFRCByYz0tIHJlYXNvbj0tDQpbVE1ST10gcmVnPTAxIGN0eD0xIHR4bj0wLy00NSBvcGVuPU9QRU5fTk9UX1JFQUNIRUQvMCBsb2NrPTAvMC8wDQpbVE1ST10gcmVnPTAxIGZsdXNoPTAvMCBieXRlcz0wIG1zPTANCltUTVJPXSByZWc9MDEgdHg9MC8wLzAgb2ZmPTAgcmVxPQ0KW1RNUk9dIHJlZz0wMSB0eF93cml0dGVuPTANCltUTVJPXSByZWc9MDEgdGltaW5nPS0gKGJ1ZGdldCBuZXZlciBvcGVuZWQpDQpbVE1ST10gcmVnPTAxIHJ4PTAgaGRyPTAgZnVsbD0wIGNyYz1OT1RfUkVBQ0hFRA0KW1RNUk9dIHJlZz0wMSB0b3RhbD0wIGJ1Zj0wIHB1Yj0wIGxhdGU9MCBleHA9LS8tIHByZV90cnVuYz0wIGdhcD0wDQpbVE1ST10gcmVnPTAxIGVjaG89VU5LTk9XTiBtYXRjaD0wLzAgdmFsPTAwMDAwMDAwDQpbVE1ST10gcmVnPTAxIHNhbXBsZXMgcHJlPTAgdGFpbD0wIGNhbmQ9MA0KW1RNUk9dIHJlZz0wMSByeDEgb2ZmPTAgbj0wIGhleD0tDQpbVE1ST10gcmVnPTAxIHJ4VCBvZmY9MCBuPTAgaGV4PS0NCltUTVJPXSByZWc9MDEgY2FuZCBvZmY9MCBuPTAgaGV4PS0NCltUTVJPXSByZWc9MDIgcmVzdWx0PU5PVF9SRUFDSEVEIHJjPS0gcmVhc29uPS0NCltUTVJPXSByZWc9MDIgY3R4PTEgdHhuPTAvLTQ1IG9wZW49T1BFTl9OT1RfUkVBQ0hFRC8wIGxvY2s9MC8wLzANCltUTVJPXSByZWc9MDIgZmx1c2g9MC8wIGJ5dGVzPTAgbXM9MA0KW1RNUk9dIHJlZz0wMiB0eD0wLzAvMCBvZmY9MCByZXE9DQpbVE1ST10gcmVnPTAyIHR4X3dyaXR0ZW49MA0KW1RNUk9dIHJlZz0wMiB0aW1pbmc9LSAoYnVkZ2V0IG5ldmVyIG9wZW5lZCkNCltUTVJPXSByZWc9MDIgcng9MCBoZHI9MCBmdWxsPTAgY3JjPU5PVF9SRUFDSEVEDQpbVE1ST10gcmVnPTAyIHRvdGFsPTAgYnVmPTAgcHViPTAgbGF0ZT0wIGV4cD0tLy0gcHJlX3RydW5jPTAgZ2FwPTANCltUTVJPXSByZWc9MDIgZWNobz1VTktOT1dOIG1hdGNoPTAvMCB2YWw9MDAwMDAwMDANCltUTVJPXSByZWc9MDIgc2FtcGxlcyBwcmU9MCB0YWlsPTAgY2FuZD0wDQpbVE1ST10gcmVnPTAyIHJ4MSBvZmY9MCBuPTAgaGV4PS0NCltUTVJPXSByZWc9MDIgcnhUIG9mZj0wIG49MCBoZXg9LQ0KW1RNUk9dIHJlZz0wMiBjYW5kIG9mZj0wIG49MCBoZXg9LQ0KW1RNUk9dIHJlYWRfb2s9MCBpZGVudGl0eV9vaz0wIGZpZWxkc19vaz0xIGxpbms9VU5QUk9WRU4gc2FmZV9mb3JfZW5hYmxlPTANCltUTVJPXSBFTkQgcmVhZHM9MCB3cml0ZXM9MCBleHBvcnRfZXJyPTAgb2JzZXJ2YXRpb25fY29tcGxldGU9MQ0KbXNoID4NCm1zaCA+DQo='
    $s0['wrong_version']='W1RNUk9dIEJFR0lOIHY9MSBhZGRyPTAgcGxhbm5lZD0zIHJlYWRzPTMNCltUTUNdIHByb2JlOiByZWFkLW9ubHkgaWRlbnRpdHkgY2hlY2sgdHhuLWhlbGQgSU9JTitHU1RBVCtJRkNOVCAoMCBXUklURSBmcmFtZXMpDQpbVE1ST10gcmVnPTA2IHJlc3VsdD1TVUNDRVNTIHJjPTAgcmVhc29uPW9rDQpbVE1ST10gcmVnPTA2IGN0eD0xIHR4bj0xLzAgb3Blbj1BTFJFQURZX09QRU4vMCBsb2NrPTEvMS8wDQpbVE1ST10gcmVnPTA2IGZsdXNoPTEvMCBieXRlcz0wIG1zPTANCltUTVJPXSByZWc9MDYgdHg9MS8xLzAgb2ZmPTMgcmVxPTA1IDAwIDA2IDZGDQpbVE1ST10gcmVnPTA2IHR4X3dyaXR0ZW49NA0KW1RNUk9dIHJlZz0wNiBzdGFydD0xMDAgdG90YWw9MTI1IHJ4X2JlZ2luPTMgcnhfZGw9MTAzIGRvbmU9Mw0KW1RNUk9dIHJlZz0wNiByeD0xIGhkcj0xIGZ1bGw9MSBjcmM9T0sNCltUTVJPXSByZWc9MDYgdG90YWw9OCBidWY9OCBwdWI9MSBsYXRlPTAgZXhwPW4vbiBwcmVfdHJ1bmM9MCBnYXA9MA0KW1RNUk9dIHJlZz0wNiBlY2hvPU9USEVSIG1hdGNoPTAvMCB2YWw9MjAwMDAwNDANCltUTVJPXSByZWc9MDYgc2FtcGxlcyBwcmU9OCB0YWlsPTggY2FuZD04DQpbVE1ST10gcmVnPTA2IHJ4MSBvZmY9MCBuPTggaGV4PTA1IEZGIDA2IDIwIDAwIDAwIDQwIDdFDQpbVE1ST10gcmVnPTA2IHJ4VCBvZmY9MCBuPTggaGV4PTA1IEZGIDA2IDIwIDAwIDAwIDQwIDdFDQpbVE1ST10gcmVnPTA2IGNhbmQgb2ZmPTAgbj04IGhleD0wNSBGRiAwNiAyMCAwMCAwMCA0MCA3RQ0KW1RNUk9dIHJlZz0wMSByZXN1bHQ9U1VDQ0VTUyByYz0wIHJlYXNvbj1vaw0KW1RNUk9dIHJlZz0wMSBjdHg9MSB0eG49MS8wIG9wZW49QUxSRUFEWV9PUEVOLzAgbG9jaz0xLzEvMA0KW1RNUk9dIHJlZz0wMSBmbHVzaD0xLzAgYnl0ZXM9MCBtcz0wDQpbVE1ST10gcmVnPTAxIHR4PTEvMS8wIG9mZj00IHJlcT0wNSAwMCAwMSBDMQ0KW1RNUk9dIHJlZz0wMSB0eF93cml0dGVuPTQNCltUTVJPXSByZWc9MDEgc3RhcnQ9MTAzIHRvdGFsPTEyNSByeF9iZWdpbj00IHJ4X2RsPTEwNCBkb25lPTQNCltUTVJPXSByZWc9MDEgcng9MSBoZHI9MSBmdWxsPTEgY3JjPU9LDQpbVE1ST10gcmVnPTAxIHRvdGFsPTggYnVmPTggcHViPTEgbGF0ZT0wIGV4cD1uL24gcHJlX3RydW5jPTAgZ2FwPTANCltUTVJPXSByZWc9MDEgZWNobz1PVEhFUiBtYXRjaD0wLzAgdmFsPTAwMDAwMDAxDQpbVE1ST10gcmVnPTAxIHNhbXBsZXMgcHJlPTggdGFpbD04IGNhbmQ9OA0KW1RNUk9dIHJlZz0wMSByeDEgb2ZmPTAgbj04IGhleD0wNSBGRiAwMSAwMCAwMCAwMCAwMSA5QQ0KW1RNUk9dIHJlZz0wMSByeFQgb2ZmPTAgbj04IGhleD0wNSBGRiAwMSAwMCAwMCAwMCAwMSA5QQ0KW1RNUk9dIHJlZz0wMSBjYW5kIG9mZj0wIG49OCBoZXg9MDUgRkYgMDEgMDAgMDAgMDAgMDEgOUENCltUTVJPXSByZWc9MDIgcmVzdWx0PVNVQ0NFU1MgcmM9MCByZWFzb249b2sNCltUTVJPXSByZWc9MDIgY3R4PTEgdHhuPTEvMCBvcGVuPUFMUkVBRFlfT1BFTi8wIGxvY2s9MS8xLzANCltUTVJPXSByZWc9MDIgZmx1c2g9MS8wIGJ5dGVzPTAgbXM9MA0KW1RNUk9dIHJlZz0wMiB0eD0xLzEvMCBvZmY9NCByZXE9MDUgMDAgMDIgOEYNCltUTVJPXSByZWc9MDIgdHhfd3JpdHRlbj00DQpbVE1ST10gcmVnPTAyIHN0YXJ0PTEwNyB0b3RhbD0xMjUgcnhfYmVnaW49NCByeF9kbD0xMDQgZG9uZT00DQpbVE1ST10gcmVnPTAyIHJ4PTEgaGRyPTEgZnVsbD0xIGNyYz1PSw0KW1RNUk9dIHJlZz0wMiB0b3RhbD04IGJ1Zj04IHB1Yj0xIGxhdGU9MCBleHA9bi9uIHByZV90cnVuYz0wIGdhcD0wDQpbVE1ST10gcmVnPTAyIGVjaG89T1RIRVIgbWF0Y2g9MC8wIHZhbD0wMDAwMDAwMA0KW1RNUk9dIHJlZz0wMiBzYW1wbGVzIHByZT04IHRhaWw9OCBjYW5kPTgNCltUTVJPXSByZWc9MDIgcngxIG9mZj0wIG49OCBoZXg9MDUgRkYgMDIgMDAgMDAgMDAgMDAgNEMNCltUTVJPXSByZWc9MDIgcnhUIG9mZj0wIG49OCBoZXg9MDUgRkYgMDIgMDAgMDAgMDAgMDAgNEMNCltUTVJPXSByZWc9MDIgY2FuZCBvZmY9MCBuPTggaGV4PTA1IEZGIDAyIDAwIDAwIDAwIDAwIDRDDQpbVE1DXSBJT0lOID0gMHgyMDAwMDA0MCBWRVJTSU9OID0gMHgyMCAtPiBWRVJTSU9OICE9IDB4MjE6IG5vdCBUTUMyMjA5ICgweDIwPVRNQzIyMDgpDQpbVE1DXSBHU1RBVCA9IDB4MDEgcmF3PTB4MDAwMDAwMDEgcmVzZXQ9MSBkcnZfZXJyPTAgdXZfY3A9MA0KW1RNQ10gSUZDTlQgPSAwIHJhdz0weDAwMDAwMDAwIChyZWFkLW9ubHk6IHRoaXMgY29tbWFuZCB3cm90ZSBubyByZWdpc3RlcikNCltUTVJPXSByZWFkX29rPTEgaWRlbnRpdHlfb2s9MCBmaWVsZHNfb2s9MSBsaW5rPVJFQURfT0sgc2FmZV9mb3JfZW5hYmxlPTANCltUTVJPXSBFTkQgcmVhZHM9MyB3cml0ZXM9MCBleHBvcnRfZXJyPTAgb2JzZXJ2YXRpb25fY29tcGxldGU9MQ0KbXNoID4NCm1zaCA+DQo='
    $s0['gstat_high']='W1RNUk9dIEJFR0lOIHY9MSBhZGRyPTAgcGxhbm5lZD0zIHJlYWRzPTMNCltUTUNdIHByb2JlOiByZWFkLW9ubHkgaWRlbnRpdHkgY2hlY2sgdHhuLWhlbGQgSU9JTitHU1RBVCtJRkNOVCAoMCBXUklURSBmcmFtZXMpDQpbVE1ST10gcmVnPTA2IHJlc3VsdD1TVUNDRVNTIHJjPTAgcmVhc29uPW9rDQpbVE1ST10gcmVnPTA2IGN0eD0xIHR4bj0xLzAgb3Blbj1BTFJFQURZX09QRU4vMCBsb2NrPTEvMS8wDQpbVE1ST10gcmVnPTA2IGZsdXNoPTEvMCBieXRlcz0wIG1zPTANCltUTVJPXSByZWc9MDYgdHg9MS8xLzAgb2ZmPTMgcmVxPTA1IDAwIDA2IDZGDQpbVE1ST10gcmVnPTA2IHR4X3dyaXR0ZW49NA0KW1RNUk9dIHJlZz0wNiBzdGFydD0xMDAgdG90YWw9MTI1IHJ4X2JlZ2luPTMgcnhfZGw9MTAzIGRvbmU9Mw0KW1RNUk9dIHJlZz0wNiByeD0xIGhkcj0xIGZ1bGw9MSBjcmM9T0sNCltUTVJPXSByZWc9MDYgdG90YWw9OCBidWY9OCBwdWI9MSBsYXRlPTAgZXhwPW4vbiBwcmVfdHJ1bmM9MCBnYXA9MA0KW1RNUk9dIHJlZz0wNiBlY2hvPU9USEVSIG1hdGNoPTAvMCB2YWw9MjEwMDAwNDANCltUTVJPXSByZWc9MDYgc2FtcGxlcyBwcmU9OCB0YWlsPTggY2FuZD04DQpbVE1ST10gcmVnPTA2IHJ4MSBvZmY9MCBuPTggaGV4PTA1IEZGIDA2IDIxIDAwIDAwIDQwIDRGDQpbVE1ST10gcmVnPTA2IHJ4VCBvZmY9MCBuPTggaGV4PTA1IEZGIDA2IDIxIDAwIDAwIDQwIDRGDQpbVE1ST10gcmVnPTA2IGNhbmQgb2ZmPTAgbj04IGhleD0wNSBGRiAwNiAyMSAwMCAwMCA0MCA0Rg0KW1RNUk9dIHJlZz0wMSByZXN1bHQ9U1VDQ0VTUyByYz0wIHJlYXNvbj1vaw0KW1RNUk9dIHJlZz0wMSBjdHg9MSB0eG49MS8wIG9wZW49QUxSRUFEWV9PUEVOLzAgbG9jaz0xLzEvMA0KW1RNUk9dIHJlZz0wMSBmbHVzaD0xLzAgYnl0ZXM9MCBtcz0wDQpbVE1ST10gcmVnPTAxIHR4PTEvMS8wIG9mZj00IHJlcT0wNSAwMCAwMSBDMQ0KW1RNUk9dIHJlZz0wMSB0eF93cml0dGVuPTQNCltUTVJPXSByZWc9MDEgc3RhcnQ9MTAzIHRvdGFsPTEyNSByeF9iZWdpbj00IHJ4X2RsPTEwNCBkb25lPTQNCltUTVJPXSByZWc9MDEgcng9MSBoZHI9MSBmdWxsPTEgY3JjPU9LDQpbVE1ST10gcmVnPTAxIHRvdGFsPTggYnVmPTggcHViPTEgbGF0ZT0wIGV4cD1uL24gcHJlX3RydW5jPTAgZ2FwPTANCltUTVJPXSByZWc9MDEgZWNobz1PVEhFUiBtYXRjaD0wLzAgdmFsPTAwMDAwMTAwDQpbVE1ST10gcmVnPTAxIHNhbXBsZXMgcHJlPTggdGFpbD04IGNhbmQ9OA0KW1RNUk9dIHJlZz0wMSByeDEgb2ZmPTAgbj04IGhleD0wNSBGRiAwMSAwMCAwMCAwMSAwMCBBNQ0KW1RNUk9dIHJlZz0wMSByeFQgb2ZmPTAgbj04IGhleD0wNSBGRiAwMSAwMCAwMCAwMSAwMCBBNQ0KW1RNUk9dIHJlZz0wMSBjYW5kIG9mZj0wIG49OCBoZXg9MDUgRkYgMDEgMDAgMDAgMDEgMDAgQTUNCltUTVJPXSByZWc9MDIgcmVzdWx0PVNVQ0NFU1MgcmM9MCByZWFzb249b2sNCltUTVJPXSByZWc9MDIgY3R4PTEgdHhuPTEvMCBvcGVuPUFMUkVBRFlfT1BFTi8wIGxvY2s9MS8xLzANCltUTVJPXSByZWc9MDIgZmx1c2g9MS8wIGJ5dGVzPTAgbXM9MA0KW1RNUk9dIHJlZz0wMiB0eD0xLzEvMCBvZmY9NCByZXE9MDUgMDAgMDIgOEYNCltUTVJPXSByZWc9MDIgdHhfd3JpdHRlbj00DQpbVE1ST10gcmVnPTAyIHN0YXJ0PTEwNyB0b3RhbD0xMjUgcnhfYmVnaW49NCByeF9kbD0xMDQgZG9uZT00DQpbVE1ST10gcmVnPTAyIHJ4PTEgaGRyPTEgZnVsbD0xIGNyYz1PSw0KW1RNUk9dIHJlZz0wMiB0b3RhbD04IGJ1Zj04IHB1Yj0xIGxhdGU9MCBleHA9bi9uIHByZV90cnVuYz0wIGdhcD0wDQpbVE1ST10gcmVnPTAyIGVjaG89T1RIRVIgbWF0Y2g9MC8wIHZhbD0wMDAwMDAwMA0KW1RNUk9dIHJlZz0wMiBzYW1wbGVzIHByZT04IHRhaWw9OCBjYW5kPTgNCltUTVJPXSByZWc9MDIgcngxIG9mZj0wIG49OCBoZXg9MDUgRkYgMDIgMDAgMDAgMDAgMDAgNEMNCltUTVJPXSByZWc9MDIgcnhUIG9mZj0wIG49OCBoZXg9MDUgRkYgMDIgMDAgMDAgMDAgMDAgNEMNCltUTVJPXSByZWc9MDIgY2FuZCBvZmY9MCBuPTggaGV4PTA1IEZGIDAyIDAwIDAwIDAwIDAwIDRDDQpbVE1DXSBJT0lOID0gMHgyMTAwMDA0MCBWRVJTSU9OID0gMHgyMSAtPiBUTUMyMjA5ICgweDIxKSBpZGVudGl0eSBtYXRjaA0KW1RNQ10gR1NUQVQgPSAweDAwIHJhdz0weDAwMDAwMTAwIHJlc2V0PTAgZHJ2X2Vycj0wIHV2X2NwPTANCltUTUNdICEhIEdTVEFUPTB4MDAwMDAxMDAgaGFzIGJpdHMgdGhlIG1hbnVhbCBsZWF2ZXMgdW5kZWZpbmVkIChvbmx5IGJpdDAuLmJpdDIpDQpbVE1DXSBJRkNOVCA9IDAgcmF3PTB4MDAwMDAwMDAgKHJlYWQtb25seTogdGhpcyBjb21tYW5kIHdyb3RlIG5vIHJlZ2lzdGVyKQ0KW1RNUk9dIHJlYWRfb2s9MSBpZGVudGl0eV9vaz0xIGZpZWxkc19vaz0wIGxpbms9UkVBRF9PSyBzYWZlX2Zvcl9lbmFibGU9MA0KW1RNUk9dIEVORCByZWFkcz0zIHdyaXRlcz0wIGV4cG9ydF9lcnI9MCBvYnNlcnZhdGlvbl9jb21wbGV0ZT0xDQptc2ggPg0KbXNoID4NCg=='
    $s0['ifcnt_high']='W1RNUk9dIEJFR0lOIHY9MSBhZGRyPTAgcGxhbm5lZD0zIHJlYWRzPTMNCltUTUNdIHByb2JlOiByZWFkLW9ubHkgaWRlbnRpdHkgY2hlY2sgdHhuLWhlbGQgSU9JTitHU1RBVCtJRkNOVCAoMCBXUklURSBmcmFtZXMpDQpbVE1ST10gcmVnPTA2IHJlc3VsdD1TVUNDRVNTIHJjPTAgcmVhc29uPW9rDQpbVE1ST10gcmVnPTA2IGN0eD0xIHR4bj0xLzAgb3Blbj1BTFJFQURZX09QRU4vMCBsb2NrPTEvMS8wDQpbVE1ST10gcmVnPTA2IGZsdXNoPTEvMCBieXRlcz0wIG1zPTANCltUTVJPXSByZWc9MDYgdHg9MS8xLzAgb2ZmPTMgcmVxPTA1IDAwIDA2IDZGDQpbVE1ST10gcmVnPTA2IHR4X3dyaXR0ZW49NA0KW1RNUk9dIHJlZz0wNiBzdGFydD0xMDAgdG90YWw9MTI1IHJ4X2JlZ2luPTMgcnhfZGw9MTAzIGRvbmU9Mw0KW1RNUk9dIHJlZz0wNiByeD0xIGhkcj0xIGZ1bGw9MSBjcmM9T0sNCltUTVJPXSByZWc9MDYgdG90YWw9OCBidWY9OCBwdWI9MSBsYXRlPTAgZXhwPW4vbiBwcmVfdHJ1bmM9MCBnYXA9MA0KW1RNUk9dIHJlZz0wNiBlY2hvPU9USEVSIG1hdGNoPTAvMCB2YWw9MjEwMDAwNDANCltUTVJPXSByZWc9MDYgc2FtcGxlcyBwcmU9OCB0YWlsPTggY2FuZD04DQpbVE1ST10gcmVnPTA2IHJ4MSBvZmY9MCBuPTggaGV4PTA1IEZGIDA2IDIxIDAwIDAwIDQwIDRGDQpbVE1ST10gcmVnPTA2IHJ4VCBvZmY9MCBuPTggaGV4PTA1IEZGIDA2IDIxIDAwIDAwIDQwIDRGDQpbVE1ST10gcmVnPTA2IGNhbmQgb2ZmPTAgbj04IGhleD0wNSBGRiAwNiAyMSAwMCAwMCA0MCA0Rg0KW1RNUk9dIHJlZz0wMSByZXN1bHQ9U1VDQ0VTUyByYz0wIHJlYXNvbj1vaw0KW1RNUk9dIHJlZz0wMSBjdHg9MSB0eG49MS8wIG9wZW49QUxSRUFEWV9PUEVOLzAgbG9jaz0xLzEvMA0KW1RNUk9dIHJlZz0wMSBmbHVzaD0xLzAgYnl0ZXM9MCBtcz0wDQpbVE1ST10gcmVnPTAxIHR4PTEvMS8wIG9mZj00IHJlcT0wNSAwMCAwMSBDMQ0KW1RNUk9dIHJlZz0wMSB0eF93cml0dGVuPTQNCltUTVJPXSByZWc9MDEgc3RhcnQ9MTAzIHRvdGFsPTEyNSByeF9iZWdpbj00IHJ4X2RsPTEwNCBkb25lPTQNCltUTVJPXSByZWc9MDEgcng9MSBoZHI9MSBmdWxsPTEgY3JjPU9LDQpbVE1ST10gcmVnPTAxIHRvdGFsPTggYnVmPTggcHViPTEgbGF0ZT0wIGV4cD1uL24gcHJlX3RydW5jPTAgZ2FwPTANCltUTVJPXSByZWc9MDEgZWNobz1PVEhFUiBtYXRjaD0wLzAgdmFsPTAwMDAwMDAxDQpbVE1ST10gcmVnPTAxIHNhbXBsZXMgcHJlPTggdGFpbD04IGNhbmQ9OA0KW1RNUk9dIHJlZz0wMSByeDEgb2ZmPTAgbj04IGhleD0wNSBGRiAwMSAwMCAwMCAwMCAwMSA5QQ0KW1RNUk9dIHJlZz0wMSByeFQgb2ZmPTAgbj04IGhleD0wNSBGRiAwMSAwMCAwMCAwMCAwMSA5QQ0KW1RNUk9dIHJlZz0wMSBjYW5kIG9mZj0wIG49OCBoZXg9MDUgRkYgMDEgMDAgMDAgMDAgMDEgOUENCltUTVJPXSByZWc9MDIgcmVzdWx0PVNVQ0NFU1MgcmM9MCByZWFzb249b2sNCltUTVJPXSByZWc9MDIgY3R4PTEgdHhuPTEvMCBvcGVuPUFMUkVBRFlfT1BFTi8wIGxvY2s9MS8xLzANCltUTVJPXSByZWc9MDIgZmx1c2g9MS8wIGJ5dGVzPTAgbXM9MA0KW1RNUk9dIHJlZz0wMiB0eD0xLzEvMCBvZmY9NCByZXE9MDUgMDAgMDIgOEYNCltUTVJPXSByZWc9MDIgdHhfd3JpdHRlbj00DQpbVE1ST10gcmVnPTAyIHN0YXJ0PTEwNyB0b3RhbD0xMjUgcnhfYmVnaW49NCByeF9kbD0xMDQgZG9uZT00DQpbVE1ST10gcmVnPTAyIHJ4PTEgaGRyPTEgZnVsbD0xIGNyYz1PSw0KW1RNUk9dIHJlZz0wMiB0b3RhbD04IGJ1Zj04IHB1Yj0xIGxhdGU9MCBleHA9bi9uIHByZV90cnVuYz0wIGdhcD0wDQpbVE1ST10gcmVnPTAyIGVjaG89T1RIRVIgbWF0Y2g9MC8wIHZhbD0wMDAwMDEwMA0KW1RNUk9dIHJlZz0wMiBzYW1wbGVzIHByZT04IHRhaWw9OCBjYW5kPTgNCltUTVJPXSByZWc9MDIgcngxIG9mZj0wIG49OCBoZXg9MDUgRkYgMDIgMDAgMDAgMDEgMDAgRkENCltUTVJPXSByZWc9MDIgcnhUIG9mZj0wIG49OCBoZXg9MDUgRkYgMDIgMDAgMDAgMDEgMDAgRkENCltUTVJPXSByZWc9MDIgY2FuZCBvZmY9MCBuPTggaGV4PTA1IEZGIDAyIDAwIDAwIDAxIDAwIEZBDQpbVE1DXSBJT0lOID0gMHgyMTAwMDA0MCBWRVJTSU9OID0gMHgyMSAtPiBUTUMyMjA5ICgweDIxKSBpZGVudGl0eSBtYXRjaA0KW1RNQ10gR1NUQVQgPSAweDAxIHJhdz0weDAwMDAwMDAxIHJlc2V0PTEgZHJ2X2Vycj0wIHV2X2NwPTANCltUTUNdIElGQ05UID0gMCByYXc9MHgwMDAwMDEwMCAocmVhZC1vbmx5OiB0aGlzIGNvbW1hbmQgd3JvdGUgbm8gcmVnaXN0ZXIpDQpbVE1ST10gcmVhZF9vaz0xIGlkZW50aXR5X29rPTEgZmllbGRzX29rPTAgbGluaz1SRUFEX09LIHNhZmVfZm9yX2VuYWJsZT0wDQpbVE1ST10gRU5EIHJlYWRzPTMgd3JpdGVzPTAgZXhwb3J0X2Vycj0wIG9ic2VydmF0aW9uX2NvbXBsZXRlPTENCm1zaCA+DQptc2ggPg0K'
    $s0['fresh_open']='W1RNUk9dIEJFR0lOIHY9MSBhZGRyPTAgcGxhbm5lZD0zIHJlYWRzPTMNCltUTUNdIHByb2JlOiByZWFkLW9ubHkgaWRlbnRpdHkgY2hlY2sgdHhuLWhlbGQgSU9JTitHU1RBVCtJRkNOVCAoMCBXUklURSBmcmFtZXMpDQpbVE1ST10gcmVnPTA2IHJlc3VsdD1TVUNDRVNTIHJjPTAgcmVhc29uPW9rDQpbVE1ST10gcmVnPTA2IGN0eD0xIHR4bj0xLzAgb3Blbj1PUEVORURfTk9XLzAgbG9jaz0xLzEvMA0KW1RNUk9dIHJlZz0wNiBmbHVzaD0xLzAgYnl0ZXM9MCBtcz0wDQpbVE1ST10gcmVnPTA2IHR4PTEvMS8wIG9mZj0zIHJlcT0wNSAwMCAwNiA2Rg0KW1RNUk9dIHJlZz0wNiB0eF93cml0dGVuPTQNCltUTVJPXSByZWc9MDYgc3RhcnQ9MTAwIHRvdGFsPTEyNSByeF9iZWdpbj0zIHJ4X2RsPTEwMyBkb25lPTMNCltUTVJPXSByZWc9MDYgcng9MSBoZHI9MSBmdWxsPTEgY3JjPU9LDQpbVE1ST10gcmVnPTA2IHRvdGFsPTggYnVmPTggcHViPTEgbGF0ZT0wIGV4cD1uL24gcHJlX3RydW5jPTAgZ2FwPTANCltUTVJPXSByZWc9MDYgZWNobz1PVEhFUiBtYXRjaD0wLzAgdmFsPTIxMDAwMDQwDQpbVE1ST10gcmVnPTA2IHNhbXBsZXMgcHJlPTggdGFpbD04IGNhbmQ9OA0KW1RNUk9dIHJlZz0wNiByeDEgb2ZmPTAgbj04IGhleD0wNSBGRiAwNiAyMSAwMCAwMCA0MCA0Rg0KW1RNUk9dIHJlZz0wNiByeFQgb2ZmPTAgbj04IGhleD0wNSBGRiAwNiAyMSAwMCAwMCA0MCA0Rg0KW1RNUk9dIHJlZz0wNiBjYW5kIG9mZj0wIG49OCBoZXg9MDUgRkYgMDYgMjEgMDAgMDAgNDAgNEYNCltUTVJPXSByZWc9MDEgcmVzdWx0PVNVQ0NFU1MgcmM9MCByZWFzb249b2sNCltUTVJPXSByZWc9MDEgY3R4PTEgdHhuPTEvMCBvcGVuPU9QRU5FRF9OT1cvMCBsb2NrPTEvMS8wDQpbVE1ST10gcmVnPTAxIGZsdXNoPTEvMCBieXRlcz0wIG1zPTANCltUTVJPXSByZWc9MDEgdHg9MS8xLzAgb2ZmPTQgcmVxPTA1IDAwIDAxIEMxDQpbVE1ST10gcmVnPTAxIHR4X3dyaXR0ZW49NA0KW1RNUk9dIHJlZz0wMSBzdGFydD0xMDMgdG90YWw9MTI1IHJ4X2JlZ2luPTQgcnhfZGw9MTA0IGRvbmU9NA0KW1RNUk9dIHJlZz0wMSByeD0xIGhkcj0xIGZ1bGw9MSBjcmM9T0sNCltUTVJPXSByZWc9MDEgdG90YWw9OCBidWY9OCBwdWI9MSBsYXRlPTAgZXhwPW4vbiBwcmVfdHJ1bmM9MCBnYXA9MA0KW1RNUk9dIHJlZz0wMSBlY2hvPU9USEVSIG1hdGNoPTAvMCB2YWw9MDAwMDAwMDENCltUTVJPXSByZWc9MDEgc2FtcGxlcyBwcmU9OCB0YWlsPTggY2FuZD04DQpbVE1ST10gcmVnPTAxIHJ4MSBvZmY9MCBuPTggaGV4PTA1IEZGIDAxIDAwIDAwIDAwIDAxIDlBDQpbVE1ST10gcmVnPTAxIHJ4VCBvZmY9MCBuPTggaGV4PTA1IEZGIDAxIDAwIDAwIDAwIDAxIDlBDQpbVE1ST10gcmVnPTAxIGNhbmQgb2ZmPTAgbj04IGhleD0wNSBGRiAwMSAwMCAwMCAwMCAwMSA5QQ0KW1RNUk9dIHJlZz0wMiByZXN1bHQ9U1VDQ0VTUyByYz0wIHJlYXNvbj1vaw0KW1RNUk9dIHJlZz0wMiBjdHg9MSB0eG49MS8wIG9wZW49T1BFTkVEX05PVy8wIGxvY2s9MS8xLzANCltUTVJPXSByZWc9MDIgZmx1c2g9MS8wIGJ5dGVzPTAgbXM9MA0KW1RNUk9dIHJlZz0wMiB0eD0xLzEvMCBvZmY9NCByZXE9MDUgMDAgMDIgOEYNCltUTVJPXSByZWc9MDIgdHhfd3JpdHRlbj00DQpbVE1ST10gcmVnPTAyIHN0YXJ0PTEwNyB0b3RhbD0xMjUgcnhfYmVnaW49NCByeF9kbD0xMDQgZG9uZT00DQpbVE1ST10gcmVnPTAyIHJ4PTEgaGRyPTEgZnVsbD0xIGNyYz1PSw0KW1RNUk9dIHJlZz0wMiB0b3RhbD04IGJ1Zj04IHB1Yj0xIGxhdGU9MCBleHA9bi9uIHByZV90cnVuYz0wIGdhcD0wDQpbVE1ST10gcmVnPTAyIGVjaG89T1RIRVIgbWF0Y2g9MC8wIHZhbD0wMDAwMDAwMA0KW1RNUk9dIHJlZz0wMiBzYW1wbGVzIHByZT04IHRhaWw9OCBjYW5kPTgNCltUTVJPXSByZWc9MDIgcngxIG9mZj0wIG49OCBoZXg9MDUgRkYgMDIgMDAgMDAgMDAgMDAgNEMNCltUTVJPXSByZWc9MDIgcnhUIG9mZj0wIG49OCBoZXg9MDUgRkYgMDIgMDAgMDAgMDAgMDAgNEMNCltUTVJPXSByZWc9MDIgY2FuZCBvZmY9MCBuPTggaGV4PTA1IEZGIDAyIDAwIDAwIDAwIDAwIDRDDQpbVE1DXSBJT0lOID0gMHgyMTAwMDA0MCBWRVJTSU9OID0gMHgyMSAtPiBUTUMyMjA5ICgweDIxKSBpZGVudGl0eSBtYXRjaA0KW1RNQ10gR1NUQVQgPSAweDAxIHJhdz0weDAwMDAwMDAxIHJlc2V0PTEgZHJ2X2Vycj0wIHV2X2NwPTANCltUTUNdIElGQ05UID0gMCByYXc9MHgwMDAwMDAwMCAocmVhZC1vbmx5OiB0aGlzIGNvbW1hbmQgd3JvdGUgbm8gcmVnaXN0ZXIpDQpbVE1ST10gcmVhZF9vaz0xIGlkZW50aXR5X29rPTEgZmllbGRzX29rPTEgbGluaz1SRUFEX09LIHNhZmVfZm9yX2VuYWJsZT0wDQpbVE1ST10gRU5EIHJlYWRzPTMgd3JpdGVzPTAgZXhwb3J0X2Vycj0wIG9ic2VydmF0aW9uX2NvbXBsZXRlPTENCm1zaCA+DQptc2ggPg0K'
    return $s0
}
function Fixture-Responses {
    $fixtures0=@{}; $wire1=Fixture-Wire
    foreach ($fixtureName in $wire1.Keys) {$fixtures0[$fixtureName]=[Text.Encoding]::ASCII.GetString([Convert]::FromBase64String($wire1[$fixtureName]))}
    return $fixtures0
}
function Make-Response([string]$Value) {
    return [pscustomobject]@{Text=$Value; Bytes=[Text.Encoding]::ASCII.GetBytes($Value)}
}
function Expect-Rejected([string]$Name,[string]$Command,[string]$Value,[string]$Want)
{
    $caught0=$false
    try {[void](Check-UroR1Reply $Command (Make-Response $Value))} catch {
        # One-Line is reused byte-for-byte and still throws its own M0_ prefix; the six
        # legacy command checks keep the URO_ prefixes. Both are rejection evidence.
        if ($_.Exception.Message -notmatch '^(UROR1_[A-Z_]+|URO_(?:OBSERVATION_REJECTED|LINK_NOT_CONFIRMED)|M0_OBSERVATION_REJECTED):') {throw}
        if ($Want -ne '' -and $_.Exception.Message -notmatch ([regex]::Escape($Want))) {throw ('Negative control rejected for the wrong reason: '+$Name+' -> '+$_.Exception.Message)}
        $caught0=$true
    }
    if (-not $caught0) {throw ('Negative control accepted: '+$Name)}
    Note 'NEGATIVE_CONTROL' @{name=$Name;result='REJECTED';want=$Want}
}
function Expect-ResetRejected([string]$Name,[string]$Value)
{
    $caught0=$false
    try {Assert-NoReset (Make-Response $Value) $Name} catch {
        if ($_.Exception.Message -notmatch '^UROR1_CAPTURE_INCOMPLETE: reset banner') {throw}
        $caught0=$true
    }
    if (-not $caught0) {throw ('Reset control accepted: '+$Name)}
    Note 'NEGATIVE_CONTROL' @{name=$Name; result='REJECTED';want='reset banner'}
}
function Expect-ResetRejected([string]$Name,[string]$Value)
{
    $caught0=$false
    try {Assert-NoReset (Make-Response $Value) $Name} catch {
        if ($_.Exception.Message -notmatch '^UROR1_CAPTURE_INCOMPLETE: reset banner') {throw}
        $caught0=$true
    }
    if (-not $caught0) {throw ('Reset control accepted: '+$Name)}
    Note 'NEGATIVE_CONTROL' @{name=$Name; result='REJECTED';want='reset banner'}
}
function Expect-Banner([string]$Name,[string]$Text,[string]$Expected,[bool]$Continuity,[string]$WantState)
{
    $got0=Resolve-BannerGate $Text $Expected $Continuity
    if ($got0 -cne $WantState) {throw ('Banner gate differs: '+$Name+' got='+$got0+' want='+$WantState)}
    Note 'BANNER_CONTROL' @{name=$Name;state=$got0}
}
function Expect-BannerRejected([string]$Name,[string]$Text,[string]$Expected,[bool]$Continuity,[string]$Want)
{
    $caught0=$false
    try {[void](Resolve-BannerGate $Text $Expected $Continuity)} catch {
        if ($_.Exception.Message -notmatch '^UROR1_BANNER_') {throw}
        if ($_.Exception.Message -notmatch ([regex]::Escape($Want))) {throw ('Banner control rejected for the wrong reason: '+$Name+' -> '+$_.Exception.Message)}
        $caught0=$true
    }
    if (-not $caught0) {throw ('Banner control accepted: '+$Name)}
    Note 'NEGATIVE_CONTROL' @{name=$Name;result='REJECTED';want=$Want}
}
function Expect-Slice([string]$Name,[string]$Text,[string]$WantBusiness,[string]$WantStage,[int]$WantReads,[int]$WantRead,[int]$WantIdentity,[int]$WantFields)
{
    $p0=Check-UroR1Reply 'tmc_uart_probe' (Make-Response $Text)
    if ($p0.capture -cne 'COMPLETE') {throw ('Slice was not accepted as a complete capture: '+$Name)}
    if ($p0.business -cne $WantBusiness -or $p0.fail_stage -cne $WantStage -or $p0.attempted_reads -ne $WantReads) {
        throw ('Slice verdict differs: '+$Name+' got='+$p0.business+'/'+$p0.fail_stage+'/'+$p0.attempted_reads+' want='+$WantBusiness+'/'+$WantStage+'/'+$WantReads)}
    if ($p0.read_ok -ne $WantRead -or $p0.identity_ok -ne $WantIdentity -or $p0.fields_ok -ne $WantFields) {
        throw ('Slice fields differ: '+$Name+' read_ok='+$p0.read_ok+' identity_ok='+$p0.identity_ok+' fields_ok='+$p0.fields_ok)}
    if ($p0.safe_for_enable) {throw ('Slice granted enable: '+$Name)}
    Note 'SLICE_CONTROL' @{name=$Name;capture=$p0.capture;business=$p0.business;fail_stage=$p0.fail_stage;reads=$p0.attempted_reads;read_ok=$p0.read_ok;identity_ok=$p0.identity_ok;fields_ok=$p0.fields_ok;open_identity=$p0.open_identity}
    return $p0
}
try {
    if ($SelfTest) {
        if (-not (Test-Path $BootFixture -PathType Leaf)) {throw 'Missing real boot fixture'}
        if ((Get-FileHash -Algorithm SHA256 $BootFixture).Hash.ToLowerInvariant() -cne '97b37f32aa03c86b8708ae088d0ae65a8f92520f1a1ed70beb74663049321749') {throw 'Wrong boot fixture'}
        $fixtures=Fixture-Responses
        $wireFixtures=Fixture-Wire
        $slices=Fixture-TmroSlices
        $healthyText=$fixtures['tmc_uart_probe']
        $junkText=[Text.Encoding]::ASCII.GetString([Convert]::FromBase64String($slices['probe_junk']))
        $timeoutText=[Text.Encoding]::ASCII.GetString([Convert]::FromBase64String($slices['rx_timeout']))
        $openFailText=[Text.Encoding]::ASCII.GetString([Convert]::FromBase64String($slices['open_fail']))
        $txnText=[Text.Encoding]::ASCII.GetString([Convert]::FromBase64String($slices['txn_unavailable']))
        $wrongVerText=[Text.Encoding]::ASCII.GetString([Convert]::FromBase64String($slices['wrong_version']))
        $gstatHighText=[Text.Encoding]::ASCII.GetString([Convert]::FromBase64String($slices['gstat_high']))
        $ifcntHighText=[Text.Encoding]::ASCII.GetString([Convert]::FromBase64String($slices['ifcnt_high']))
        $freshOpenText=[Text.Encoding]::ASCII.GetString([Convert]::FromBase64String($slices['fresh_open']))
        $lfHealthy=$healthyText.Replace("`r`n","`n")
        $fakePort=[pscustomobject]@{Payload=[IO.File]::ReadAllBytes($BootFixture); Index=0; Calls=0; Responses=$fixtures;Wire=$wireFixtures}
        $fakePort | Add-Member ScriptProperty BytesToRead {return $this.Payload.Length-$this.Index}
        $fakePort | Add-Member ScriptMethod Read {
            param([byte[]]$b,[int]$at,[int]$count)
            $take0=[Math]::Min($count,[Math]::Min(7,$this.Payload.Length-$this.Index))
            [Array]::Copy($this.Payload,$this.Index,$b,$at,$take0); $this.Index+=$take0; return $take0
        }
        $fakePort | Add-Member ScriptMethod Write {
            param([byte[]]$b,[int]$at,[int]$count)
            $this.Calls++; $literal0=[Text.Encoding]::ASCII.GetString($b,$at,$count).TrimEnd([char]13,[char]10)
            if (-not $this.Responses.ContainsKey($literal0)) {throw 'Unexpected mock command'}
            $this.Payload=[Convert]::FromBase64String($this.Wire[$literal0]); $this.Index=0
        }
        $script:Port=$fakePort
        # -SelfTest never builds a real SerialPort, never enumerates ports/probes, never
        # touches pyOCD: the online path here is this fake object only.
        if ($script:Port -is [IO.Ports.SerialPort]) {throw 'SelfTest must not touch a real port'}
        foreach ($forbidden0 in @('motor_arm','motor_start','motor_disarm','fault_reset','tmc_config_apply CONFIRM','spm trip','spm clear_records','spm init','pin 87 1','tmc_config_show','tmc_scan','spm start 100')) {
            $blocked0=$false; try {Send-Tx $forbidden0} catch {if ($_.Exception.Message -cne 'Command outside whitelist.') {throw}; $blocked0=$true}
            if (-not $blocked0 -or $fakePort.Calls -ne 0) {throw 'Forbidden command reached mock wire'}
        }
        Begin-Reply 'attach_passive';$bootReplay=Wait-ReadOnlyProbeWindow
        if ($fakePort.Calls -ne 0 -or $bootReplay.Bytes.Length -ne 3757 -or (Get-FileHash -Algorithm SHA256 $bootReplay.Path).Hash.ToLowerInvariant() -cne '97b37f32aa03c86b8708ae088d0ae65a8f92520f1a1ed70beb74663049321749') {throw 'Boot replay mismatch'}
        $mockIdentity=Run-UroR1ReadDiag
        if ($fakePort.Calls -ne 7 -or $mockIdentity.safe_for_enable) {throw 'Healthy mock sequence differs'}
        # ---- positive controls on the REAL final-firmware slices (never hand-written) ----
        if ($mockIdentity.capture -cne 'COMPLETE' -or $mockIdentity.business -cne 'SUCCESS' -or $mockIdentity.attempted_reads -ne 3 -or $mockIdentity.read_ok -ne 1 -or $mockIdentity.identity_ok -ne 1 -or $mockIdentity.fields_ok -ne 1) {throw 'Healthy slice verdict differs'}
        Expect-Slice 'junk_stream_two_chunks' $junkText 'SUCCESS' 'NONE' 3 1 1 1
        if (([regex]::Matches($junkText,'(?m)^\[TMRO\] reg=06 rx1 off=')).Count -ne 2) {throw 'Junk slice no longer exercises the multi-chunk export'}
        Expect-Slice 'rx_entry_timeout_slice' $timeoutText 'FAIL' 'READ' 1 0 0 1
        Expect-Slice 'open_failed_slice' $openFailText 'FAIL' 'OPEN' 0 0 0 1
        Expect-Slice 'txn_unavailable_slice' $txnText 'FAIL' 'TXN' 0 0 0 1
        Expect-Slice 'wrong_version_slice' $wrongVerText 'FAIL' 'IDENTITY_MISMATCH' 3 1 0 1
        Expect-Slice 'gstat_high_bit_slice' $gstatHighText 'FAIL' 'FIELDS_OUT_OF_DOMAIN' 3 1 1 0
        Expect-Slice 'ifcnt_high_bit_slice' $ifcntHighText 'FAIL' 'FIELDS_OUT_OF_DOMAIN' 3 1 1 0
        Expect-Slice 'opened_now_slice' $freshOpenText 'SUCCESS' 'NONE' 3 1 1 1
        if ($mockIdentity.open_identity -cne 'ALREADY_OPEN' -or ([regex]::Matches($freshOpenText,'open=OPENED_NOW/0').Count) -ne 3) {throw 'Open identity of this probe is not carried by every record'}
        Expect-Slice 'lf_line_endings' $lfHealthy 'SUCCESS' 'NONE' 3 1 1 1
        $capBlocked0=$false;try {Send-Tx 'spm status'} catch {if ($_.Exception.Message -cne 'Command cap reached.') {throw};$capBlocked0=$true}
        if (-not $capBlocked0 -or $fakePort.Calls -ne 7) {throw 'Repeated probe reached wire'}
        $fakePort.Payload=[Text.Encoding]::ASCII.GetBytes($fixtures['spm status'].Replace("msh >`r`n",''));$fakePort.Index=0
        Begin-Reply 'negative_missing_prompt';$promptTimedOut=$false
        try {$unused0=Wait-Prompt 250} catch {
            if ($_.Exception.Message -cne 'Reply timeout; no retry and no cleanup.') {throw}
            $promptTimedOut=$true;$partial0=End-Reply
        }
        if (-not $promptTimedOut) {throw 'Missing prompt accepted'}
        Note 'NEGATIVE_CONTROL' @{name='missing_response_prompt';result='REJECTED';bytes=$partial0.Bytes.Length}
        # ---- banner gate controls (offline only; no SerialPort object is ever built) ----
        $bannerExpected='5.3.0 build Oct 28 2026 00:00:00'
        Expect-Banner 'banner_observed_exact' ($bannerExpected+"`r`n") $bannerExpected $false 'BANNER_MATCHED'
        Expect-Banner 'silent_window_with_readback' '' $bannerExpected $true 'BANNER_NOT_OBSERVED'
        Expect-BannerRejected 'banner_wrong_version_observed' ('5.3.0 build Sep  1 2026 08:00:00'+("`r`n")) $bannerExpected $true 'UROR1_BANNER_MISMATCH'
        Expect-BannerRejected 'banner_two_contradictory' ($bannerExpected+"`r`n"+'5.3.0 build Sep  1 2026 08:00:00'+("`r`n")) $bannerExpected $true 'UROR1_BANNER_CONTRADICTION'
        Expect-BannerRejected 'silent_window_without_readback' '' $bannerExpected $false 'UROR1_BANNER_UNRESOLVED'
        Expect-BannerRejected 'banner_observed_without_expected' ($bannerExpected+"`r`n") '' $true 'UROR1_BANNER_UNRESOLVED'
        # ---- the six legacy command checks keep their own negative controls ----
        Expect-Rejected 'macro_enabled' 'spm cap' ($fixtures['spm cap'].Replace('MOTOR_HW=0','MOTOR_HW=1')) 'four macros'
        Expect-Rejected 'safety_high_pad' 'safety_status' ($fixtures['safety_status'].Replace('pad=0','pad=1')) ''
        Expect-Rejected 'safety_no_sample' 'safety_status' ($fixtures['safety_status'].Replace('verdict=OK','verdict=NO_SAMPLE')) ''
        Expect-Rejected 'motor_armed' 'motor_status' ($fixtures['motor_status'].Replace('armed=0','armed=1')) ''
        Expect-Rejected 'motor_stop_failed' 'motor_status' ($fixtures['motor_status'].Replace('result=api-accepted','result=api-failed')) ''
        Expect-Rejected 'step_output' 'step_status' ($fixtures['step_status'].Replace('enabled=no','enabled=YES')) ''
        Expect-Rejected 'duplicate_critical_label' 'safety_state' ($fixtures['safety_state']+("`r`n")+$fixtures['safety_state']) ''
        Expect-Rejected 'nul_in_spm' 'spm cap' ($fixtures['spm cap']+[char]0) ''
        Expect-Rejected 'nul_inside_critical_field' 'safety_status' ($fixtures['safety_status'].Replace('pad=0',('pad='+[char]0+'0'))) ''
        Expect-ResetRejected 'reset_banner_inside_probe_reply' ($healthyText+"RT-Thread | Micro Kernel`r`n")
        # ---- probe: frame/identity/byte completeness (each names its own reason) ----
        Expect-Rejected 'probe_no_per_record_lines' 'tmc_uart_probe' ($healthyText -replace '(?m)^\[TMRO\] reg=[^\r\n]*\r?\n','') 'result lines'
        Expect-Rejected 'probe_drop_tx_written' 'tmc_uart_probe' ($healthyText -replace '(?m)^\[TMRO\] reg=06 tx_written=4\r?\n','') 'fact line written'
        Expect-Rejected 'probe_drop_second_prefix_chunk' 'tmc_uart_probe' ($junkText -replace '(?m)^\[TMRO\] reg=06 rx1 off=16 n=16 hex=20[^\r\n]*\r?\n','') 'chunks add up to 16'
        Expect-Rejected 'probe_chunk_n_overstates_bytes' 'tmc_uart_probe' ($healthyText.Replace('rx1 off=0 n=8 hex=05 FF 06 21 00 00 40 4F','rx1 off=0 n=16 hex=05 FF 06 21 00 00 40 4F')) 'claims n=16'
        Expect-Rejected 'probe_chunk_offset_gap' 'tmc_uart_probe' ($healthyText.Replace('rx1 off=0 n=8','rx1 off=8 n=8')) 'not contiguous'
        Expect-Rejected 'probe_empty_chunk_fabricates_zero' 'tmc_uart_probe' ($timeoutText.Replace('rx1 off=0 n=0 hex=-','rx1 off=0 n=0 hex=00')) 'claims n=0'
        Expect-Rejected 'probe_duplicate_record' 'tmc_uart_probe' ($healthyText+$healthyText.Split([char]10)[2].TrimEnd([char]13)+("`r`n")) 'result lines=4'
        Expect-Rejected 'probe_end_reads_99' 'tmc_uart_probe' ($healthyText.Replace('END reads=3','END reads=99').Replace('BEGIN v=1 addr=0 planned=3 reads=3','BEGIN v=1 addr=0 planned=3 reads=99')) 'outside the 0..3 cap'
        Expect-Rejected 'probe_begin_end_mismatch' 'tmc_uart_probe' ($healthyText.Replace('BEGIN v=1 addr=0 planned=3 reads=3','BEGIN v=1 addr=0 planned=3 reads=2')) 'BEGIN reads=2'
        $endLineOnly=[regex]::Match($healthyText,'(?m)^\[TMRO\] END [^\r\n]*').Value
        $noEndText=($healthyText -replace '(?m)^\[TMRO\] END[^\r\n]*\r?\n','')
        Expect-Rejected 'probe_end_before_begin' 'tmc_uart_probe' ($endLineOnly+"`r`n"+$noEndText) 'END frame precedes BEGIN'
        Expect-Rejected 'probe_wrong_observation_version' 'tmc_uart_probe' ($healthyText.Replace('BEGIN v=1','BEGIN v=2')) 'v=1'
        Expect-Rejected 'probe_missing_end_frame' 'tmc_uart_probe' ($healthyText.Replace('observation_complete=1','observation_complete=0')) 'observation_complete'
        Expect-Rejected 'probe_export_error' 'tmc_uart_probe' ($healthyText.Replace('export_err=0','export_err=1')) 'export buffer'
        Expect-Rejected 'probe_write_frame_claim' 'tmc_uart_probe' ($healthyText.Replace('writes=0','writes=1')) 'WRITE frame'
        Expect-Rejected 'probe_grants_enable' 'tmc_uart_probe' ($healthyText.Replace('safe_for_enable=0','safe_for_enable=1')) 'enable permission'
        Expect-Rejected 'probe_end_line_unterminated' 'tmc_uart_probe' ($healthyText.Replace('observation_complete=1'+("`r`n"),'observation_complete=1')) 'END lines=0'
        Expect-Rejected 'probe_nul_byte' 'tmc_uart_probe' ($timeoutText+[char]0) 'NUL byte'
        Expect-Rejected 'probe_line_over_print_bound' 'tmc_uart_probe' ($healthyText.Replace('tx_written=4','tx_written=4 '+'x'*120)) 'print bound'
        Expect-Rejected 'probe_notreached_fakes_rc' 'tmc_uart_probe' ($timeoutText.Replace('reg=01 result=NOT_REACHED rc=- reason=-','reg=01 result=NOT_REACHED rc=0 reason=ok')) 'numeric rc/reason'
        Expect-Rejected 'probe_notreached_fakes_timing' 'tmc_uart_probe' ($timeoutText.Replace('reg=01 timing=- (budget never opened)','reg=01 start=0 total=0 rx_begin=0 rx_dl=0 done=0')) 'unopened-budget timing line'
        Expect-Rejected 'probe_timeout_faked_success' 'tmc_uart_probe' ($timeoutText.Replace('result=FAIL','result=SUCCESS')) 'says SUCCESS with rc='
        Expect-Rejected 'probe_ioin_fail_continues' 'tmc_uart_probe' ($timeoutText.Replace('reg=01 result=NOT_REACHED','reg=01 result=FAIL')) 'early exit rule'
        Expect-Rejected 'probe_success_without_crc' 'tmc_uart_probe' ($healthyText.Replace('reg=06 rx=1 hdr=1 full=1 crc=OK','reg=06 rx=1 hdr=1 full=1 crc=NOT_REACHED')) 'crc=OK'
        Expect-Rejected 'probe_fail_line_without_reason' 'tmc_uart_probe' ($timeoutText.Replace('reason=rx-timeout-echo','reason=ok')) 'FAIL with reason=ok'
        Expect-Rejected 'probe_identity_claim_contradicts' 'tmc_uart_probe' ($wrongVerText.Replace('VERSION = 0x20 -> VERSION != 0x21: not TMC2209 (0x20=TMC2208)','VERSION = 0x21 -> TMC2209 (0x21) identity match')) 'not the top byte'
        Expect-Rejected 'probe_ioin_summary_masked_value' 'tmc_uart_probe' ($healthyText.Replace('IOIN = 0x21000040','IOIN = 0x20000040')) 'not the raw value'
        Expect-Rejected 'probe_gstat_mask_contradicts_raw' 'tmc_uart_probe' ($healthyText.Replace('GSTAT = 0x01 raw=0x00000001','GSTAT = 0x05 raw=0x00000001')) 'masked GSTAT'
        Expect-Rejected 'probe_gstat_high_bit_claimed_ok' 'tmc_uart_probe' ($gstatHighText.Replace('fields_ok=0','fields_ok=1')) 'fields_ok=1 but the raw values'
        Expect-Rejected 'probe_ifcnt_high_bit_claimed_ok' 'tmc_uart_probe' ($ifcntHighText.Replace('fields_ok=0','fields_ok=1')) 'fields_ok=1 but the raw values'
        Expect-Rejected 'probe_read_ok_contradicts_records' 'tmc_uart_probe' ($timeoutText.Replace('read_ok=0','read_ok=1')) 'read_ok=1 but the records'
        Expect-Rejected 'probe_open_failed_claims_rc0' 'tmc_uart_probe' ($openFailText.Replace('open=OPEN_FAILED/-255','open=OPEN_FAILED/0')) 'carries rc=0'
        Expect-Rejected 'probe_open_already_claims_fresh_open' 'tmc_uart_probe' ($openFailText.Replace('open=OPEN_FAILED/-255','open=ALREADY_OPEN/0')) 'nothing was attempted'
        Expect-Rejected 'probe_notreached_open_with_held_txn' 'tmc_uart_probe' ($txnText.Replace('open=OPEN_NOT_REACHED/0','open=ALREADY_OPEN/0')) 'no transaction was held'
        Expect-Rejected 'probe_refused_alongside_held_title' 'tmc_uart_probe' ($healthyText+"[TMC] probe REFUSED (-45): cfg txn unavailable or busy -> 0 bus reads`r`n") 'probe REFUSED appears although'
        Expect-Rejected 'probe_ctx_illegal_but_printed' 'tmc_uart_probe' ($healthyText.Replace('reg=06 ctx=1','reg=06 ctx=0')) 'illegal context'
        Expect-Rejected 'probe_injected_third_register' 'tmc_uart_probe' ($healthyText.Replace('reg=02 samples','reg=0A samples')) 'outside IOIN/GSTAT/IFCNT'
        Note 'OFFLINE_SELFTEST' @{result='PASS';collector='UARTReadDiagSerialR1';device_actions=0;mock_commands=7;forbidden_blocked=12;negative_controls=50;banner_controls=6;slice_controls=9;boot_fixture_bytes=3757;seventh_reply_slices='final_firmware_host_probe_stdout_real_bytes';transport='byte-identical_to_frozen_M0Serial.ps1';six_command_checks='byte-identical_to_UARTReadDiagSerial.ps1';online_mode_needs='ExecuteApprovedR1ReadDiag+ApprovalSource+ExpectedBuildBanner+ImageContinuityConfirmed+SiteConfirmed'}
        Write-Host ('URO-R1-OFFLINE-SELFTEST PASS; device_actions=0; mock_commands=7; negative_controls=50; banner_controls=6; slice_controls=9')
    } else {
        if (-not $ExecuteApprovedR1ReadDiag) {throw 'Online capture needs the explicit -ExecuteApprovedR1ReadDiag switch'}
        if (-not $ImageContinuityConfirmed -or -not $SiteConfirmed) {throw 'UROR1 site/image continuity not confirmed'}
        if ([string]::IsNullOrWhiteSpace($ApprovalSource)) {throw 'Online mode needs -ApprovalSource (where this package was approved)'}
        if ($ExpectedBuildBanner -notmatch '^5\.3\.0 build [A-Za-z]{3} +[0-9]+ 20[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$') {throw 'UROR1 needs -ExpectedBuildBanner taken from THIS round build manifest'}
        $script:Port=[IO.Ports.SerialPort]::new('COM5',115200,[IO.Ports.Parity]::None,8,[IO.Ports.StopBits]::One)
        $script:Port.Handshake=[IO.Ports.Handshake]::None;$script:Port.DtrEnable=$false;$script:Port.RtsEnable=$false
        $script:Port.ReadTimeout=100;$script:Port.WriteTimeout=1000;$script:Port.ReadBufferSize=65536
        $script:EventId='UroR1SerialError_'+[Guid]::NewGuid().ToString('N')
        Register-ObjectEvent -InputObject $script:Port -EventName ErrorReceived -SourceIdentifier $script:EventId | Out-Null
        Note 'SERIAL_OPEN_ATTEMPT' @{port='COM5';attempt=1;approval_source=$ApprovalSource}
        $script:Port.Open();Note 'SERIAL_OPEN' @{port='COM5';baud=115200;format='8N1';dtr=$false;rts=$false;expected_build_banner=$ExpectedBuildBanner}
        Begin-Reply 'attach_passive';$passive0=Wait-ReadOnlyProbeWindow
        # One attempt only. No reset, no port reopen and no second window is ever scheduled to
        # chase a banner: if none was captured, the identity of this run comes from the
        # byte-verified readback of this package, recorded as BANNER_NOT_OBSERVED.
        $bannerState=Resolve-BannerGate $passive0.Text $ExpectedBuildBanner ([bool]$ImageContinuityConfirmed)
        Note 'UROR1_BANNER_GATE' @{state=$bannerState;passive_bytes=$passive0.Bytes.Length;expected_build_banner=$ExpectedBuildBanner;image_readback_behind_this_package=[bool]$ImageContinuityConfirmed}
        $identity0=Run-UroR1ReadDiag
        Note 'UROR1_CAPTURE' @{commands=7;capture=$identity0.capture;business=$identity0.business;fail_stage=$identity0.fail_stage;attempted_reads=$identity0.attempted_reads;read_ok=$identity0.read_ok;identity_ok=$identity0.identity_ok;fields_ok=$identity0.fields_ok;write_frames=$identity0.register_write_frames;tmro_lines=$identity0.tmro_lines;link=$identity0.link;ioin=$identity0.ioin;gstat_raw=$identity0.gstat_raw;ifcnt_raw=$identity0.ifcnt_raw;open_identity=$identity0.open_identity;banner=$bannerState;safe_for_enable=$identity0.safe_for_enable;hardware_stage='URO_R1_READ_DIAG_ONLY';a0_timing='NOT_ACHIEVED'}
        Write-Host ('URO R1 CAPTURE COMPLETE; capture='+$identity0.capture+'; business='+$identity0.business+'; fail_stage='+$identity0.fail_stage+'; banner='+$bannerState+'; NO ENABLE AUTHORIZATION')
    }
} catch {
    Note 'STOP_FAILURE' @{message=$_.Exception.Message;counts=$script:Counts}
    throw
} finally {
    if ($null -ne $script:ReplyFile) {$script:ReplyFile.Dispose()}
    if ($null -ne $script:Reply) {$script:Reply.Dispose()}
    if ($null -ne $script:Port -and $script:Port -is [IO.Ports.SerialPort]) {if ($script:Port.IsOpen) {$script:Port.Close()};$script:Port.Dispose()}
    if ($null -ne $script:EventId) {Unregister-Event -SourceIdentifier $script:EventId -ErrorAction SilentlyContinue}
    $script:Rx.Dispose();$script:Ledger.Dispose()
}
