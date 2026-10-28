from pathlib import Path
import argparse
import hashlib
import re

OLD_SHA = '2e9c8ede8f75e7b513dbd4f2c3091189309454879e98404ba7ec64bbb80f2568'

DRAIN = r'''function Assert-StackSafe {
    # Inspect saved bytes; never discard a diagnostic before deciding to stop.
    $dangerView=[Text.Encoding]::ASCII.GetString($script:Reply.ToArray())
    if ($dangerView -match '(?i)(?:warning:\s+[^\r\n]{1,48}\s+stack is close|thread:[^\r\n]{1,48}\s+stack overflow)') {
        throw 'STACK_DANGER_OBSERVED; saved raw bytes, no further command.'
    }
}
function Drain-Rx {
    $buf = [byte[]]::new(4096)
    $drainLeft=16384
    # A busy producer must not trap this call beyond the outer deadline loop.
    while ($script:Port.BytesToRead -gt 0 -and $drainLeft -gt 0) {
        $want=[Math]::Min($buf.Length,[Math]::Min($drainLeft,$script:Port.BytesToRead))
        $n = $script:Port.Read($buf,0,$want)
        if ($n -le 0) { throw 'Serial read made no progress.' }
        Persist-Chunk $buf $n
        $drainLeft-=$n
        Assert-StackSafe
    }
    if ($null -ne $script:EventId) {
        $ev = @(Get-Event -SourceIdentifier $script:EventId -ErrorAction SilentlyContinue)
        if ($ev.Count) { Note 'SERIAL_ERROR' @($ev | ForEach-Object {$_.SourceEventArgs.EventType.ToString()}); throw 'Serial error event.' }
    }
}
'''

READY = r'''function Wait-ReadOnlyProbeWindow {
    $sw=[Diagnostics.Stopwatch]::StartNew()
    while ($sw.ElapsedMilliseconds -lt 20000) {
        Drain-Rx
        $view=[Text.Encoding]::ASCII.GetString($script:Reply.ToArray())
        $bootSeen=($view -match 'RT-Thread|\[BOOT\]')
        $bootDone=($view -match '\[MAIN\] boot complete')
        # This only schedules the first status query. It does not accept a reply.
        # Banner/image identity and every command response retain their own gates.
        if ($sw.ElapsedMilliseconds -ge 2000 -and ((-not $bootSeen) -or $bootDone)) {
            Note 'READ_ONLY_PROBE_READY' @{boot_seen=$bootSeen; boot_done=$bootDone; startup_prompt_required=$false; quiet_required=$false}
            return End-Reply
        }
        [Threading.Thread]::Sleep(5)
    }
    throw 'BOOT_WINDOW_INCOMPLETE; no command sent and no retry.'
}
'''

def make(old_path, out_path):
    raw=Path(old_path).read_bytes()
    assert len(raw)==93797 and hashlib.sha256(raw).hexdigest()==OLD_SHA, 'Wrong frozen R1 collector'
    text=raw.decode('ascii')
    for name, replacement in [('Drain-Rx', DRAIN), ('Wait-ReadOnlyProbeWindow', READY)]:
        pattern=rf'(?ms)^function {re.escape(name)} \{{\n.*?^\}}\n'
        text,n=re.subn(pattern,lambda _:replacement,text)
        assert n==1,(name,n)
    start=text.index('[CmdletBinding()]')
    text=('# ASCII source. URO-R2: bounded drain, no passive quiet prerequisite.\n'
          '# Stack danger is never ignored. Response/business/banner gates are retained.\n'
          '# ExecuteApprovedR1ReadDiag is the retained CLI name, not an old authorization.\n'
          +text[start:])
    data=text.encode('ascii')
    assert b'\r' not in data
    Path(out_path).write_bytes(data)
    print(f'R2 bytes={len(data)} sha256={hashlib.sha256(data).hexdigest()}')

if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('frozen_r1');ap.add_argument('new_r2')
    a=ap.parse_args();make(a.frozen_r1,a.new_r2)
