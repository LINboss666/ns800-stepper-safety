param([string]$Collector,[string]$Wire,[string]$OutRoot)
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
$tok=$null;$err=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile($Collector,[ref]$tok,[ref]$err)
if($err.Count){throw 'Parse failed'}
foreach($d in $ast.FindAll({param($n)$n -is [System.Management.Automation.Language.FunctionDefinitionAst]},$false)){
 . ([scriptblock]::Create($d.Extent.Text))
}
Add-Type -TypeDefinition @'
using System;
using System.Threading;
public sealed class UroR2FakePort {
 public byte[] Wire; public int Pos; public bool Repeat;
 public int Chunk=4096; public int DelayMs; public int Calls;
 public int BytesToRead {get {return Pos<Wire.Length ? Wire.Length-Pos : (Repeat?Chunk:0);}}
 public int Read(byte[] b,int off,int n) {
  Calls++; if(DelayMs>0)Thread.Sleep(DelayMs);
  int k=Math.Min(n,Chunk);
  if(Pos<Wire.Length){k=Math.Min(k,Wire.Length-Pos);Array.Copy(Wire,Pos,b,off,k);Pos+=k;}
  else if(Repeat){for(int i=0;i<k;i++)b[off+i]=(byte)'b';}
  else return 0;
  return k;
 }
}
'@
[void][IO.Directory]::CreateDirectory($OutRoot)
$script:Pass=0
function Start-Case([string]$Name,[byte[]]$Data,[bool]$Repeat,[int]$Chunk,[int]$Delay) {
 $script:CaseDir=Join-Path $OutRoot $Name
 [void][IO.Directory]::CreateDirectory($script:CaseDir)
 $script:OutDir=$script:CaseDir
 $script:Rx=[IO.File]::Open((Join-Path $script:CaseDir 'session.bin'),'CreateNew','Write','Read')
 $script:Ledger=[IO.StreamWriter]::new((Join-Path $script:CaseDir 'ledger.jsonl'))
 $script:Sequence=0;$script:Reply=$null;$script:ReplyFile=$null;$script:EventId=$null
 $script:Port=[UroR2FakePort]::new();$script:Port.Wire=$Data;$script:Port.Repeat=$Repeat
 $script:Port.Chunk=$Chunk;$script:Port.DelayMs=$Delay
 Begin-Reply 'offline'
}
function Close-Case {
 if($null-ne $script:ReplyFile){$script:ReplyFile.Dispose();$script:ReplyFile=$null}
 if($null-ne $script:Reply){$script:Reply.Dispose();$script:Reply=$null}
 $script:Rx.Dispose();$script:Ledger.Dispose()
}
function Check([bool]$Good,[string]$Name) {
 if(-not $Good){throw ('FAIL '+$Name)}
 $script:Pass++;Write-Output ('PASS '+$Name)
}

Start-Case 'busy_healthy' ([Text.Encoding]::ASCII.GetBytes("[BOOT] start`r`n[MAIN] boot complete`r`n")) $true 64 2
try {
 $ready=Wait-ReadOnlyProbeWindow
 Check ($ready.Bytes.Length-gt 0 -and ([DateTime]::UtcNow-$script:LastRx).TotalMilliseconds-lt 1200) 'BUSY_HEALTHY_NO_QUIET_READY'
} finally {Close-Case}

Start-Case 'drain_bounded' ([byte[]]::new(0)) $true 4096 0
try {
 Drain-Rx
 Check ($script:Rx.Position-eq 16384 -and $script:Port.Calls-eq 4) 'CONTINUOUS_DRAIN_RETURNS_AT_16K'
} finally {Close-Case}

Start-Case 'real_warning' ([IO.File]::ReadAllBytes($Wire)) $false 4096 0
try {
 $reason='';try {$unused=Wait-ReadOnlyProbeWindow}catch{$reason=$_.Exception.Message}
 Check ($reason.StartsWith('STACK_DANGER_OBSERVED') -and $script:Rx.Position-gt 0) 'REAL_23845B_WIRE_STACK_DANGER_SAVED'
} finally {Close-Case}

Start-Case 'warning_split' ([Text.Encoding]::ASCII.GetBytes("prefix`r`nwarning: sensor stack is close to end of stack address.`r`n")) $false 1 0
try {
 $reason='';try {Drain-Rx}catch{$reason=$_.Exception.Message}
 Check ($reason.StartsWith('STACK_DANGER_OBSERVED') -and $script:Rx.Position-gt 20) 'STACK_WARNING_SPLIT_BYTEWISE'
} finally {Close-Case}

Start-Case 'overflow' ([Text.Encoding]::ASCII.GetBytes("thread:sensor stack overflow`r`n")) $false 4096 0
try {
 $reason='';try {Drain-Rx}catch{$reason=$_.Exception.Message}
 Check ($reason.StartsWith('STACK_DANGER_OBSERVED') -and $script:Rx.Position-gt 0) 'STACK_OVERFLOW_STOPS_AFTER_SAVE'
} finally {Close-Case}

Start-Case 'boot_incomplete' ([Text.Encoding]::ASCII.GetBytes("[BOOT] still starting`r`n")) $false 4096 0
try {
 $reason='';$clock=[Diagnostics.Stopwatch]::StartNew()
 try {$unused=Wait-ReadOnlyProbeWindow}catch{$reason=$_.Exception.Message}
 Check ($reason.StartsWith('BOOT_WINDOW_INCOMPLETE') -and $clock.ElapsedMilliseconds-ge 20000 -and $clock.ElapsedMilliseconds-lt 22000) 'BOOT_NOT_DONE_TIMES_OUT_WITHOUT_TX'
} finally {Close-Case}
Write-Output ('SUMMARY pass='+$script:Pass+' fail=0 serial_objects=0')
