# ASCII source. URO read-diagnosis collector: seven read-only commands, same order as M0.
# The transport layer (Note / Save-New / Begin-Reply / Persist-Chunk / Drain-Rx / End-Reply /
# Wait-Prompt / Send-Tx / Cmd / Need / Idle-Status / Wait-ReadOnlyProbeWindow / One-Line /
# Make-Response / Fixture-Responses), the global setup block and the finally block are copied
# BYTE FOR BYTE out of the accepted M0Serial.ps1 (24064 B / sha256
# 2131af0ef32b0d9acb3a2a0489d47b90e26968e2fedd7c4ee4624fede0687e3f); this generator verifies
# that after writing the file, so no second serial layer was hand-written.
# Changed this round: mode switch and parameters; the 7th reply checker (a captured read
# timeout is now the wanted diagnostic: capture=COMPLETE judged apart from business=FAIL);
# the two 7th-reply fixtures (taken from this round's host probe of the final firmware);
# the build-banner binding (required parameter instead of the frozen M0 date); a no-reset
# check; and the offline selftest set. Online mode additionally needs -ApprovalSource.
[CmdletBinding()]
param(
    [switch]$SelfTest,
    [switch]$ExecuteApprovedUroReadDiag,
    [Parameter(Mandatory=$true)][string]$OutDir,
    [string]$BootFixture,
    [string]$ExpectedBuildBanner,
    [string]$ApprovalSource,
    [switch]$ImageContinuityConfirmed,
    [switch]$SiteConfirmed
)
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
if ($SelfTest.IsPresent -eq $ExecuteApprovedUroReadDiag.IsPresent) {throw 'Select exactly one mode.'}
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
function Assert-NoReset($Response,[string]$Label)
{
    # No-reset guard: if the board rebooted mid-capture the boot banner appears again inside
    # this reply, so the reply is not trustworthy. Original bytes are already flushed; stop.
    if ($Response.Text -match 'RT-Thread \| Micro Kernel' -or $Response.Text -match '5\.3\.0 build ' -or $Response.Text -match '(?m)^\[BOOT\]') {
        throw ('URO_OBSERVATION_REJECTED: reset banner inside reply: ' + $Label)
    }
}
function Check-UroReply([string]$Command,$Response)
{
    $text0=$Response.Text
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
        'tmc_uart_probe' {
            # URO (task book 7.1): a read timeout on the 7th command is now the diagnostic we
            # want fully captured. The bytes were flushed first
            # (Persist-Chunk flushes every chunk); two verdicts are judged apart: frame
            # completeness (capture) and the business result - never merged into one PASS.
            # Stage semantics are read offline; this script adds no new decision gate.
            [void](One-Line $text0 '[TMRO] BEGIN' '(?m)^\[TMRO\] BEGIN v=[0-9]+ addr=[0-9]+ planned=3 reads=[0-9]+\r?$')
            $end0=One-Line $text0 '[TMRO] END' '(?m)^\[TMRO\] END reads=([0-9]+) writes=([0-9]+) export_err=([0-9]+) observation_complete=([0-9]+)\r?$'
            if ($end0.Groups[2].Value -cne '0') {throw 'URO_OBSERVATION_REJECTED: probe reported a WRITE frame'}
            if ($end0.Groups[3].Value -cne '0') {throw 'URO_OBSERVATION_REJECTED: probe export buffer overflowed'}
            if ($end0.Groups[4].Value -cne '1') {throw 'URO_OBSERVATION_REJECTED: observation not complete'}
            if ($text0 -match 'EXPORT_ERROR') {throw 'URO_OBSERVATION_REJECTED: EXPORT_ERROR line present'}
            if ($text0 -match '\x00') {throw 'URO_OBSERVATION_REJECTED: NUL byte in probe response'}
            $tmroAll=[regex]::Matches($text0,'(?m)^\[TMRO\]').Count
            $tmroOk=[regex]::Matches($text0,'(?m)^\[TMRO\] [^\r\n]*\r?$').Count
            if ($tmroAll -ne $tmroOk) {throw 'URO_OBSERVATION_REJECTED: TMRO line not terminated cleanly'}
            $ident0=One-Line $text0 '[TMRO] identity=' '(?m)^\[TMRO\] identity=(READ_OK|NOT_PROVEN) link=(READ_OK|UNPROVEN) safe_for_enable=([01])\r?$'
            if ($ident0.Groups[3].Value -cne '0') {throw 'URO_OBSERVATION_REJECTED: probe granted enable permission'}
            $attempted=[int]$end0.Groups[1].Value
            $link0=$ident0.Groups[2].Value
            if ($attempted -eq 0) {[void](One-Line $text0 '[TMC] probe REFUSED' '(?m)^\[TMC\] probe REFUSED \(-?[0-9]+\)[^\r\n]*\r?$')}
            if ($attempted -lt 3 -or $ident0.Groups[1].Value -cne 'READ_OK') {
                # Failure fully observed: require that one FAIL record to exist (content is
                # read by a human later); never guess success fields.
                # A record label repeats (one line per field per record), so the FAIL record is
                # counted with a plain regex instead of One-Line's "exactly one" rule.
                if ($attempted -ge 1 -and ([regex]::Matches($text0,'(?m)^\[TMRO\] reg=[0-9A-F]{2} result=FAIL rc=-?[0-9]+ reason=[a-z\-]+\r?$').Count -lt 1)) {
                    throw 'URO_OBSERVATION_REJECTED: no per-record FAIL line'
                }
                return [pscustomobject]@{ioin=$null; version=0; gstat=$null; ifcnt=$null; register_write_frames=0; safe_for_enable=$false; capture='COMPLETE'; business='FAIL'; attempted_reads=$attempted; link=$link0; tmro_lines=$tmroAll}
            }
            $io=One-Line $text0 '[TMC] IOIN =' '(?m)^\[TMC\] IOIN = 0x([0-9A-F]{8}) VERSION = 0x21 -> [^\r\n]+\r?$'
            $ioValue=[Convert]::ToUInt32($io.Groups[1].Value,16)
            if (($ioValue -shr 24) -ne 0x21) {throw 'URO_LINK_NOT_CONFIRMED: inconsistent IOIN identity'}
            $gs=One-Line $text0 '[TMC] GSTAT =' '(?m)^\[TMC\] GSTAT = 0x([0-9A-F]{2}) reset=([01]) drv_err=([01]) uv_cp=([01])\r?$'
            $gValue=[Convert]::ToUInt32($gs.Groups[1].Value,16)
            if ($gValue -gt 7 -or ($gValue -band 1) -ne [int]$gs.Groups[2].Value -or (($gValue -shr 1) -band 1) -ne [int]$gs.Groups[3].Value -or (($gValue -shr 2) -band 1) -ne [int]$gs.Groups[4].Value) {throw 'URO_LINK_NOT_CONFIRMED: inconsistent GSTAT fields'}
            $ic=One-Line $text0 '[TMC] IFCNT =' '(?m)^\[TMC\] IFCNT = ([0-9]+)(?: [^\r\n]*)?\r?$'
            $ifValue=[UInt32]::Parse($ic.Groups[1].Value)
            if ($ifValue -gt 255) {throw 'URO_LINK_NOT_CONFIRMED: IFCNT out of range'}
            [void](One-Line $text0 '[TMC] probe:' '(?m)^\[TMC\] probe: [^\r\n]*txn-held IOIN\+GSTAT\+IFCNT \(0 [^\r\n]*WRITE [^\r\n]*\)\r?$')
            return [pscustomobject]@{ioin=$io.Groups[1].Value; version=33; gstat=$gValue; ifcnt=$ifValue; register_write_frames=0; safe_for_enable=$false; capture='COMPLETE'; business='SUCCESS'; attempted_reads=$attempted; link=$link0; tmro_lines=$tmroAll}
        }
        default {throw 'URO_OBSERVATION_REJECTED: no checker for command'}
    }
}
function Run-UroReadDiag
{
    $commResult=$null
    foreach ($literal in @('spm status','spm cap','safety_state','safety_status','motor_status','step_status','tmc_uart_probe')) {
        $commandReply=Cmd $literal
        Assert-NoReset $commandReply $literal
        $parsed0=Check-UroReply $literal $commandReply
        if ($literal -ceq 'tmc_uart_probe') {$commResult=$parsed0}
        Note 'URO_COMMAND_CHECKED' @{command=$literal; result='ACCEPT'; raw_path=[IO.Path]::GetFileName($commandReply.Path)}
    }
    foreach ($literal in $script:Limits.Keys) {if (-not $script:Counts.ContainsKey($literal) -or $script:Counts[$literal] -ne 1) {throw 'URO command count differs'} }
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
    $wire0['tmc_uart_probe']='W1RNUk9dIEJFR0lOIHY9MSBhZGRyPTAgcGxhbm5lZD0zIHJlYWRzPTMNCltUTUNdIHByb2JlOiByZWFkLW9ubHkgaWRlbnRpdHkgY2hlY2sgdHhuLWhlbGQgSU9JTitHU1RBVCtJRkNOVCAoMCBXUklURSBmcmFtZXMpDQpbVE1ST10gcmVnPTA2IHJlc3VsdD1TVUNDRVNTIHJjPTAgcmVhc29uPW9rDQpbVE1ST10gcmVnPTA2IGN0eD0xIHR4bj0xLzAgb3Blbj1BTFJFQURZX09QRU4vMCBsb2NrPTEvMS8wDQpbVE1ST10gcmVnPTA2IGZsdXNoPTEvMCBieXRlcz0wIG1zPTANCltUTVJPXSByZWc9MDYgdHg9MS8xLzAgb2ZmPTMgcmVxPTA1IDAwIDA2IDZGDQpbVE1ST10gcmVnPTA2IHN0YXJ0PTIwMyB0b3RhbD0xMjUgcnhfYmVnaW49MyByeF9kbD0xMDMgZG9uZT0zDQpbVE1ST10gcmVnPTA2IHJ4PTEgaGRyPTEgZnVsbD0xIGNyYz1PSw0KW1RNUk9dIHJlZz0wNiB0b3RhbD04IGJ1Zj04IHB1Yj0xIGxhdGU9MCBleHA9MC8wDQpbVE1ST10gcmVnPTA2IGVjaG89T1RIRVIgbWF0Y2g9MCB0cnVuYz0wIHZhbD0yMTAwMDA0MA0KW1RNUk9dIHJlZz0wNiByeDEgbj04IDA1IEZGIDA2IDIxIDAwIDAwIDQwIDRGDQpbVE1ST10gcmVnPTA2IHJ4VCBuPTggMDUgRkYgMDYgMjEgMDAgMDAgNDAgNEYNCltUTVJPXSByZWc9MDYgY2FuZCBuPTggMDUgRkYgMDYgMjEgMDAgMDAgNDAgNEYNCltUTVJPXSByZWc9MDEgcmVzdWx0PVNVQ0NFU1MgcmM9MCByZWFzb249b2sNCltUTVJPXSByZWc9MDEgY3R4PTEgdHhuPTEvMCBvcGVuPUFMUkVBRFlfT1BFTi8wIGxvY2s9MS8xLzANCltUTVJPXSByZWc9MDEgZmx1c2g9MS8wIGJ5dGVzPTAgbXM9MA0KW1RNUk9dIHJlZz0wMSB0eD0xLzEvMCBvZmY9NCByZXE9MDUgMDAgMDEgQzENCltUTVJPXSByZWc9MDEgc3RhcnQ9MjA2IHRvdGFsPTEyNSByeF9iZWdpbj00IHJ4X2RsPTEwNCBkb25lPTQNCltUTVJPXSByZWc9MDEgcng9MSBoZHI9MSBmdWxsPTEgY3JjPU9LDQpbVE1ST10gcmVnPTAxIHRvdGFsPTggYnVmPTggcHViPTEgbGF0ZT0wIGV4cD0wLzANCltUTVJPXSByZWc9MDEgZWNobz1PVEhFUiBtYXRjaD0wIHRydW5jPTAgdmFsPTAwMDAwMDAxDQpbVE1ST10gcmVnPTAxIHJ4MSBuPTggMDUgRkYgMDEgMDAgMDAgMDAgMDEgOUENCltUTVJPXSByZWc9MDEgcnhUIG49OCAwNSBGRiAwMSAwMCAwMCAwMCAwMSA5QQ0KW1RNUk9dIHJlZz0wMSBjYW5kIG49OCAwNSBGRiAwMSAwMCAwMCAwMCAwMSA5QQ0KW1RNUk9dIHJlZz0wMiByZXN1bHQ9U1VDQ0VTUyByYz0wIHJlYXNvbj1vaw0KW1RNUk9dIHJlZz0wMiBjdHg9MSB0eG49MS8wIG9wZW49QUxSRUFEWV9PUEVOLzAgbG9jaz0xLzEvMA0KW1RNUk9dIHJlZz0wMiBmbHVzaD0xLzAgYnl0ZXM9MCBtcz0wDQpbVE1ST10gcmVnPTAyIHR4PTEvMS8wIG9mZj00IHJlcT0wNSAwMCAwMiA4Rg0KW1RNUk9dIHJlZz0wMiBzdGFydD0yMTAgdG90YWw9MTI1IHJ4X2JlZ2luPTQgcnhfZGw9MTA0IGRvbmU9NA0KW1RNUk9dIHJlZz0wMiByeD0xIGhkcj0xIGZ1bGw9MSBjcmM9T0sNCltUTVJPXSByZWc9MDIgdG90YWw9OCBidWY9OCBwdWI9MSBsYXRlPTAgZXhwPTAvMA0KW1RNUk9dIHJlZz0wMiBlY2hvPU9USEVSIG1hdGNoPTAgdHJ1bmM9MCB2YWw9MDAwMDAwMDANCltUTVJPXSByZWc9MDIgcngxIG49OCAwNSBGRiAwMiAwMCAwMCAwMCAwMCA0Qw0KW1RNUk9dIHJlZz0wMiByeFQgbj04IDA1IEZGIDAyIDAwIDAwIDAwIDAwIDRDDQpbVE1ST10gcmVnPTAyIGNhbmQgbj04IDA1IEZGIDAyIDAwIDAwIDAwIDAwIDRDDQpbVE1DXSBJT0lOID0gMHgyMTAwMDA0MCBWRVJTSU9OID0gMHgyMSAtPiBUTUMyMjA5ICgweDIxKSBpZGVudGl0eSBtYXRjaA0KW1RNQ10gR1NUQVQgPSAweDAxIHJlc2V0PTEgZHJ2X2Vycj0wIHV2X2NwPTANCltUTUNdIElGQ05UID0gMCAocmVhZC1vbmx5OiB0aGlzIGNvbW1hbmQgd3JvdGUgbm8gcmVnaXN0ZXIpDQpbVE1ST10gaWRlbnRpdHk9UkVBRF9PSyBsaW5rPVJFQURfT0sgc2FmZV9mb3JfZW5hYmxlPTANCltUTVJPXSBFTkQgcmVhZHM9MyB3cml0ZXM9MCBleHBvcnRfZXJyPTAgb2JzZXJ2YXRpb25fY29tcGxldGU9MQ0KbXNoID4NCm1zaCA+DQo='
    return $wire0
}
function Fixture-TimeoutProbeWire { return 'W1RNUk9dIEJFR0lOIHY9MSBhZGRyPTAgcGxhbm5lZD0zIHJlYWRzPTENCltUTUNdIHByb2JlOiByZWFkLW9ubHkgaWRlbnRpdHkgY2hlY2sgdHhuLWhlbGQgSU9JTitHU1RBVCtJRkNOVCAoMCBXUklURSBmcmFtZXMpDQpbVE1ST10gcmVnPTA2IHJlc3VsdD1GQUlMIHJjPS0xMTcgcmVhc29uPXJ4LXRpbWVvdXQtZWNobw0KW1RNUk9dIHJlZz0wNiBjdHg9MSB0eG49MS8wIG9wZW49QUxSRUFEWV9PUEVOLzAgbG9jaz0xLzEvMA0KW1RNUk9dIHJlZz0wNiBmbHVzaD0xLzAgYnl0ZXM9MCBtcz0wDQpbVE1ST10gcmVnPTA2IHR4PTEvMS8wIG9mZj0zIHJlcT0wNSAwMCAwNiA2Rg0KW1RNUk9dIHJlZz0wNiBzdGFydD0xMDAgdG90YWw9MTI1IHJ4X2JlZ2luPTMgcnhfZGw9MTAzIGRvbmU9MTAzDQpbVE1ST10gcmVnPTA2IHJ4PTEgaGRyPTAgZnVsbD0wIGNyYz1OT1RfUkVBQ0hFRA0KW1RNUk9dIHJlZz0wNiB0b3RhbD0wIGJ1Zj0wIHB1Yj0wIGxhdGU9MCBleHA9MC8xDQpbVE1ST10gcmVnPTA2IGVjaG89Tk9ORSBtYXRjaD0wIHRydW5jPTAgdmFsPTAwMDAwMDAwDQpbVE1ST10gcmVnPTA2IHJ4MSBuPTAgDQpbVE1ST10gcmVnPTA2IHJ4VCBuPTAgDQpbVE1ST10gcmVnPTA2IGNhbmQgbj0wIA0KW1RNUk9dIHJlZz0wMSByZXN1bHQ9Tk9UX1JFQUNIRUQgcmM9LSByZWFzb249LQ0KW1RNUk9dIHJlZz0wMSBjdHg9MCB0eG49MS8wIG9wZW49QUxSRUFEWV9PUEVOLzAgbG9jaz0wLzAvMA0KW1RNUk9dIHJlZz0wMSBmbHVzaD0wLzAgYnl0ZXM9MCBtcz0wDQpbVE1ST10gcmVnPTAxIHR4PTAvMC8wIG9mZj0wIHJlcT0NCltUTVJPXSByZWc9MDEgdGltaW5nPS0gKGJ1ZGdldCBuZXZlciBvcGVuZWQpDQpbVE1ST10gcmVnPTAxIHJ4PTAgaGRyPTAgZnVsbD0wIGNyYz1OT1RfUkVBQ0hFRA0KW1RNUk9dIHJlZz0wMSB0b3RhbD0wIGJ1Zj0wIHB1Yj0wIGxhdGU9MCBleHA9MC8wDQpbVE1ST10gcmVnPTAxIGVjaG89VU5LTk9XTiBtYXRjaD0wIHRydW5jPTAgdmFsPTAwMDAwMDAwDQpbVE1ST10gcmVnPTAxIHJ4MSBuPTAgDQpbVE1ST10gcmVnPTAxIHJ4VCBuPTAgDQpbVE1ST10gcmVnPTAxIGNhbmQgbj0wIA0KW1RNUk9dIHJlZz0wMiByZXN1bHQ9Tk9UX1JFQUNIRUQgcmM9LSByZWFzb249LQ0KW1RNUk9dIHJlZz0wMiBjdHg9MCB0eG49MS8wIG9wZW49QUxSRUFEWV9PUEVOLzAgbG9jaz0wLzAvMA0KW1RNUk9dIHJlZz0wMiBmbHVzaD0wLzAgYnl0ZXM9MCBtcz0wDQpbVE1ST10gcmVnPTAyIHR4PTAvMC8wIG9mZj0wIHJlcT0NCltUTVJPXSByZWc9MDIgdGltaW5nPS0gKGJ1ZGdldCBuZXZlciBvcGVuZWQpDQpbVE1ST10gcmVnPTAyIHJ4PTAgaGRyPTAgZnVsbD0wIGNyYz1OT1RfUkVBQ0hFRA0KW1RNUk9dIHJlZz0wMiB0b3RhbD0wIGJ1Zj0wIHB1Yj0wIGxhdGU9MCBleHA9MC8wDQpbVE1ST10gcmVnPTAyIGVjaG89VU5LTk9XTiBtYXRjaD0wIHRydW5jPTAgdmFsPTAwMDAwMDAwDQpbVE1ST10gcmVnPTAyIHJ4MSBuPTAgDQpbVE1ST10gcmVnPTAyIHJ4VCBuPTAgDQpbVE1ST10gcmVnPTAyIGNhbmQgbj0wIA0KW1RNQ10gSU9JTiByZWFkIGZhaWxlZCAoLTExNykgLT4gaWRlbnRpdHkgTk9UIGNvbmZpcm1lZCAoY2hpcCBkYW1hZ2UgTk9UIGltcGxpZWQpDQpbVE1ST10gaWRlbnRpdHk9Tk9UX1BST1ZFTiBsaW5rPVVOUFJPVkVOIHNhZmVfZm9yX2VuYWJsZT0wDQpbVE1ST10gRU5EIHJlYWRzPTEgd3JpdGVzPTAgZXhwb3J0X2Vycj0wIG9ic2VydmF0aW9uX2NvbXBsZXRlPTENCm1zaCA+DQptc2ggPg0K' }
function Fixture-Responses {
    $fixtures0=@{}; $wire1=Fixture-Wire
    foreach ($fixtureName in $wire1.Keys) {$fixtures0[$fixtureName]=[Text.Encoding]::ASCII.GetString([Convert]::FromBase64String($wire1[$fixtureName]))}
    return $fixtures0
}
function Make-Response([string]$Value) {
    return [pscustomobject]@{Text=$Value; Bytes=[Text.Encoding]::ASCII.GetBytes($Value)}
}
function Expect-Rejected([string]$Name,[string]$Command,[string]$Value)
{
    $caught0=$false
    try {[void](Check-UroReply $Command (Make-Response $Value))} catch {
        # One-Line is reused byte-for-byte from the frozen collector and still throws its own
        # M0_OBSERVATION_REJECTED prefix; both prefixes count as rejection evidence.
        if ($_.Exception.Message -notmatch '^(URO_(OBSERVATION_REJECTED|LINK_NOT_CONFIRMED)|M0_OBSERVATION_REJECTED):') {throw}
        $caught0=$true
    }
    if (-not $caught0) {throw ('Negative control accepted: '+$Name)}
    Note 'NEGATIVE_CONTROL' @{name=$Name; result='REJECTED'}
}
function Expect-ResetRejected([string]$Name,[string]$Value)
{
    $caught0=$false
    try {Assert-NoReset (Make-Response $Value) $Name} catch {
        if ($_.Exception.Message -notmatch '^URO_OBSERVATION_REJECTED: reset banner') {throw}
        $caught0=$true
    }
    if (-not $caught0) {throw ('Reset control accepted: '+$Name)}
    Note 'NEGATIVE_CONTROL' @{name=$Name; result='REJECTED'}
}
try {
    if ($SelfTest) {
        if (-not (Test-Path $BootFixture -PathType Leaf)) {throw 'Missing real boot fixture'}
        if ((Get-FileHash -Algorithm SHA256 $BootFixture).Hash.ToLowerInvariant() -cne '97b37f32aa03c86b8708ae088d0ae65a8f92520f1a1ed70beb74663049321749') {throw 'Wrong boot fixture'}
        $fixtures=Fixture-Responses
        $wireFixtures=Fixture-Wire
        $timeoutText=[Text.Encoding]::ASCII.GetString([Convert]::FromBase64String((Fixture-TimeoutProbeWire)))
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
        $mockIdentity=Run-UroReadDiag
        if ($fakePort.Calls -ne 7 -or $mockIdentity.version -ne 33 -or $mockIdentity.safe_for_enable) {throw 'Healthy mock sequence differs'}
        if ($mockIdentity.capture -cne 'COMPLETE' -or $mockIdentity.business -cne 'SUCCESS' -or $mockIdentity.attempted_reads -ne 3) {throw 'Healthy slice verdict differs'}
        # The two 7th-reply slices must yield two SEPARATE verdicts: a complete observation
        # is not a successful link (task book 9 forbids merging the two conclusions).
        $timeoutParsed=Check-UroReply 'tmc_uart_probe' (Make-Response $timeoutText)
        if ($timeoutParsed.capture -cne 'COMPLETE' -or $timeoutParsed.business -cne 'FAIL') {throw 'Timeout slice must be capture COMPLETE with business FAIL'}
        if ($timeoutParsed.attempted_reads -ne 1 -or $timeoutParsed.register_write_frames -ne 0 -or $timeoutParsed.safe_for_enable -or $timeoutParsed.link -cne 'UNPROVEN') {throw 'Timeout slice fields differ'}
        Note 'URO_SLICE_VERDICTS' @{healthy='COMPLETE/SUCCESS';timeout='COMPLETE/FAIL';timeout_reads=$timeoutParsed.attempted_reads;tmro_lines=$timeoutParsed.tmro_lines}
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
        Expect-ResetRejected 'reset_banner_inside_probe_reply' ($fixtures['tmc_uart_probe']+"RT-Thread | Micro Kernel`r`n")
        Expect-Rejected 'macro_enabled' 'spm cap' ($fixtures['spm cap'].Replace('MOTOR_HW=0','MOTOR_HW=1'))
        Expect-Rejected 'safety_high_pad' 'safety_status' ($fixtures['safety_status'].Replace('pad=0','pad=1'))
        Expect-Rejected 'safety_no_sample' 'safety_status' ($fixtures['safety_status'].Replace('verdict=OK','verdict=NO_SAMPLE'))
        Expect-Rejected 'motor_armed' 'motor_status' ($fixtures['motor_status'].Replace('armed=0','armed=1'))
        Expect-Rejected 'motor_stop_failed' 'motor_status' ($fixtures['motor_status'].Replace('result=api-accepted','result=api-failed'))
        Expect-Rejected 'step_output' 'step_status' ($fixtures['step_status'].Replace('enabled=no','enabled=YES'))
        Expect-Rejected 'wrong_version' 'tmc_uart_probe' ($fixtures['tmc_uart_probe'].Replace('0x21 ->','0x20 ->'))
        Expect-Rejected 'inconsistent_ioin' 'tmc_uart_probe' ($fixtures['tmc_uart_probe'].Replace('0x21000040','0x20000040'))
        Expect-Rejected 'ifcnt_256' 'tmc_uart_probe' ($fixtures['tmc_uart_probe'].Replace('IFCNT = 0 (','IFCNT = 256 ('))
        Expect-Rejected 'gstat_inconsistent' 'tmc_uart_probe' ($fixtures['tmc_uart_probe'].Replace('GSTAT = 0x01','GSTAT = 0x05'))
        Expect-Rejected 'bare_read_failed_without_tmro' 'tmc_uart_probe' ('[TMC] IOIN read failed (-117)'+("`r`n")+'msh >'+("`r`n"))
        Expect-Rejected 'tmro_missing_end_frame' 'tmc_uart_probe' ($fixtures['tmc_uart_probe'].Replace('observation_complete=1','observation_complete=0'))
        Expect-Rejected 'tmro_export_error' 'tmc_uart_probe' ($fixtures['tmc_uart_probe'].Replace('export_err=0','export_err=1'))
        Expect-Rejected 'probe_write_frame_claim' 'tmc_uart_probe' ($fixtures['tmc_uart_probe'].Replace('writes=0','writes=1'))
        Expect-Rejected 'probe_grants_enable' 'tmc_uart_probe' ($fixtures['tmc_uart_probe'].Replace('safe_for_enable=0','safe_for_enable=1'))
        Expect-Rejected 'timeout_slice_faked_success' 'tmc_uart_probe' ($timeoutText.Replace('result=FAIL','result=SUCCESS'))
        Expect-Rejected 'tmro_end_line_unterminated' 'tmc_uart_probe' ($fixtures['tmc_uart_probe'].Replace('observation_complete=1'+("`r`n"),'observation_complete=1'))
        Expect-Rejected 'nul_in_probe_response' 'tmc_uart_probe' ($timeoutText+[char]0)
        Expect-Rejected 'nul_in_spm' 'spm cap' ($fixtures['spm cap']+[char]0)
        Expect-Rejected 'nul_inside_critical_field' 'safety_status' ($fixtures['safety_status'].Replace('pad=0',('pad='+[char]0+'0')))
        Expect-Rejected 'duplicate_critical_label' 'safety_state' ($fixtures['safety_state']+("`r`n")+$fixtures['safety_state'])
        Note 'OFFLINE_SELFTEST' @{result='PASS';collector='UARTReadDiagSerial';device_actions=0;mock_commands=7;forbidden_blocked=12;negative_controls=23;boot_fixture_bytes=3757;seventh_reply_slices='host_generated_TMRO_healthy_and_timeout';transport='byte-identical_to_frozen_M0Serial.ps1';online_mode_needs='ExecuteApprovedUroReadDiag+ApprovalSource+ExpectedBuildBanner+ImageContinuityConfirmed+SiteConfirmed'}
        Write-Host ('URO-OFFLINE-SELFTEST PASS; device_actions=0; mock_commands=7; negative_controls=23; forbidden_blocked=12')
    } else {
        if (-not $ExecuteApprovedUroReadDiag) {throw 'Online capture needs the explicit -ExecuteApprovedUroReadDiag switch'}
        if (-not $ImageContinuityConfirmed -or -not $SiteConfirmed) {throw 'URO site/image continuity not confirmed'}
        if ([string]::IsNullOrWhiteSpace($ApprovalSource)) {throw 'Online mode needs -ApprovalSource (where this package was approved)'}
        if ($ExpectedBuildBanner -notmatch '5\.3\.0 build [A-Za-z]{3} +[0-9]+ 20[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}') {throw 'URO needs -ExpectedBuildBanner taken from this round build manifest'}
        $script:Port=[IO.Ports.SerialPort]::new('COM5',115200,[IO.Ports.Parity]::None,8,[IO.Ports.StopBits]::One)
        $script:Port.Handshake=[IO.Ports.Handshake]::None;$script:Port.DtrEnable=$false;$script:Port.RtsEnable=$false
        $script:Port.ReadTimeout=100;$script:Port.WriteTimeout=1000;$script:Port.ReadBufferSize=65536
        $script:EventId='UroSerialError_'+[Guid]::NewGuid().ToString('N')
        Register-ObjectEvent -InputObject $script:Port -EventName ErrorReceived -SourceIdentifier $script:EventId | Out-Null
        Note 'SERIAL_OPEN_ATTEMPT' @{port='COM5';attempt=1;approval_source=$ApprovalSource}
        $script:Port.Open();Note 'SERIAL_OPEN' @{port='COM5';baud=115200;format='8N1';dtr=$false;rts=$false;expected_build_banner=$ExpectedBuildBanner}
        Begin-Reply 'attach_passive';$passive0=Wait-ReadOnlyProbeWindow
        Need $passive0.Text ([regex]::Escape($ExpectedBuildBanner))
        $identity0=Run-UroReadDiag
        Note 'URO_CAPTURE' @{commands=7;capture=$identity0.capture;business=$identity0.business;attempted_reads=$identity0.attempted_reads;write_frames=$identity0.register_write_frames;tmro_lines=$identity0.tmro_lines;link=$identity0.link;safe_for_enable=$identity0.safe_for_enable;hardware_stage='URO_READ_DIAG_ONLY';a0_timing='NOT_ACHIEVED'}
        Write-Host ('URO CAPTURE COMPLETE; capture='+$identity0.capture+'; business='+$identity0.business+'; NO ENABLE AUTHORIZATION')
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
