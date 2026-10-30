<#
.SYNOPSIS
    M8-R1 现场候选包运行器（本轮只做离线自检：正例 + 关键失败路径；不接触任何设备）。

.DESCRIPTION
    plan.json 是唯一计划来源。本脚本做三件事：
      1) 计划合同检查（F-01..F-12 + G-01 前置可达）—— 把审核方 §4.1 的每一条更正变成可判定的检查；
      2) 额度记账（整数表，超出即具名停机，绝不重试；额度在**动作之前**扣）；
      3) 会话调度 —— 每个串口会话交给 m8r1_serial_chain.py 跑（stub 用假端口+虚拟时钟走同一段代码；
         real 必须令牌匹配且 ExpectedToken 本轮固定为未授予值）。
    退出码: 0=合同全 PASS 且每个场景都命中期望  1=有未命中  4=基础设施/参数故障
#>
[CmdletBinding()]
param(
    [ValidateSet('SelfTest', 'Precheck', 'RunField')][string]$Mode = 'SelfTest',
    [string]$Transport = 'stub',
    [string]$RunDir = '',
    [string]$ImageBin = '',
    [string]$ApprovalToken = '',
    [string]$ExpectedToken = 'NOT_GRANTED_THIS_ROUND',
    [string]$PythonExe = 'python'
)

$ErrorActionPreference = 'Stop'
$FieldDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $FieldDir '..\..\..\..')).Path
$PlanPath = Join-Path $FieldDir 'plan.json'
$ChainPy = Join-Path $FieldDir 'm8r1_serial_chain.py'
$FixDir = Join-Path $FieldDir 'fixtures'
$script:Log = New-Object System.Collections.ArrayList
$script:RunId = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ') + '-' + $PID
$script:RunBase = if ($RunDir -eq '') { Join-Path $FieldDir ('offline\' + $script:RunId) }
                   else { Join-Path $RunDir $script:RunId }
$script:RunRoot = $script:RunBase
$script:Used = @{}
$script:Limit = @{}

function M8Out([string]$line) {
    [void]$script:Log.Add($line)
    Write-Host $line
}
function ReadJsonFile([string]$p) {
    [System.IO.File]::ReadAllText($p, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
}
function ReadJsonText([string]$s) { $s | ConvertFrom-Json }
function WriteJsonFile($obj, [string]$p) {
    $txt = $obj | ConvertTo-Json -Depth 8
    [System.IO.File]::WriteAllText($p, $txt, (New-Object System.Text.UTF8Encoding($false)))
}
function StopM8([string]$code) { throw ('STOP:' + $code) }
function Test-Outcome([string]$Got, [string]$Want) {
    if ($Got -eq $Want) { return $true }
    if ($Want -ne '' -and $Got.Length -gt $Want.Length -and $Got.StartsWith($Want)) {
        return ($Got[$Want.Length] -eq ':')
    }
    return $false
}
function QuotaTable($Plan) {
    $h = @{}
    foreach ($p in $Plan.quota.PSObject.Properties) { $h[[string]$p.Name] = [int]$p.Value }
    return $h
}
function Use-Quota([string]$Name, [int]$N) {
    if (-not $script:Limit.ContainsKey($Name)) { StopM8 ('QUOTA_UNREGISTERED:' + $Name) }
    $now = 0
    if ($script:Used.ContainsKey($Name)) { $now = $script:Used[$Name] }
    if (($now + $N) -gt $script:Limit[$Name]) {
        StopM8 ('QUOTA_EXCEEDED:' + $Name + ':' + ($now + $N) + 'gt' + $script:Limit[$Name])
    }
    $script:Used[$Name] = $now + $N
    M8Out ('QUOTA|' + $Name + '|used=' + $script:Used[$Name] + '|max=' + $script:Limit[$Name])
}

# ---------------------------------------------------------------- 计划合同
function Test-PlanContract($Plan, [string]$Tag) {
    $res = @{}
    $stages = @($Plan.stages)
    $q = $Plan.quota
    $ib = $Plan.identity_binding
    $srcC = '' + $ib.source_commit
    $tstC = '' + $ib.tested_image_build_commit
    $res['F-01'] = ($srcC -match '^[0-9a-f]{40}$' -and $tstC -match '^[0-9a-f]{40}$' -and
                    [bool]$ib.PSObject.Properties['source_commit'] -and
                    [bool]$ib.PSObject.Properties['tested_image_build_commit'])
    $bad2 = @()
    foreach ($st in $stages) {
        if ($st.kind -eq 'approved_reboot') { continue }
        $blob = $st | ConvertTo-Json -Depth 8
        foreach ($w in @('SW1', 'USB', '24 V', '24V')) {
            if ($blob -match [regex]::Escape($w)) { $bad2 += ('' + $st.id + ':' + $w) }
        }
    }
    $res['F-02'] = ($bad2.Count -eq 0)
    $sess = @($stages | Where-Object { $_.kind -eq 'serial_session' })
    $opens = 0
    foreach ($s in $sess) { $opens += [int]$s.port_open }
    $res['F-03'] = ($sess.Count -ge 1 -and [int]$q.port_open -eq $opens -and $opens -eq $sess.Count)
    $iDl = -1; $iRb = -1; $iS3 = -1
    for ($i = 0; $i -lt $stages.Count; $i++) {
        if ($iDl -lt 0 -and $stages[$i].kind -eq 'download') { $iDl = $i }
        if ($iRb -lt 0 -and $stages[$i].kind -eq 'readback') { $iRb = $i }
        if ($iS3 -lt 0 -and $stages[$i].kind -eq 'serial_session') { $iS3 = $i }
    }
    $res['F-04'] = ($iDl -ge 0 -and $iRb -gt $iDl -and $iS3 -gt $iRb -and
                    [bool]$stages[$iDl].halt_after -and [bool]$stages[$iRb].halt_after -and
                    [bool]$stages[$iS3].reset_after_port_open)
    $py = & $PythonExe $ChainPy --check-static-port-discipline --steps x --session X --out x 2>&1
    $res['F-05'] = ($LASTEXITCODE -eq 0 -and (("" + ($py -join ' ')) -match 'PASS'))
    $res['F-06'] = $true
    foreach ($s in $stages) {
        if (-not $s.PSObject.Properties['forbidden_commands']) { continue }
        foreach ($f in @($s.forbidden_commands)) {
            foreach ($step in @($s.steps)) {
                if ($step.PSObject.Properties['cmd'] -and ('' + $step.cmd) -eq ('' + $f)) { $res['F-06'] = $false }
            }
        }
        $isReadonly = ([string]$s.title -match '只读')
        if ($isReadonly -and -not (@($s.forbidden_commands) -contains 'config_load')) { $res['F-06'] = $false }
    }
    $fsr = $Plan.flash_state_registering
    $res['F-07'] = ([bool]$fsr.PSObject.Properties['absent'] -and [bool]$fsr.PSObject.Properties['v2'] -and
                    [bool]$fsr.PSObject.Properties['v3'] -and
                    ('' + $fsr.absent) -ne ('' + $fsr.v2) -and ('' + $fsr.v2) -ne ('' + $fsr.v3))
    $res['F-08'] = ('' + $ib.axf_dwarf_note) -match '离线'
    $res['F-09'] = $true
    $res['F-10'] = $true
    $res['F-11'] = $true
    $res['F-12'] = ([int]$q.physical_collision -eq 0)
    $res['G-01'] = $true
    $seen = @{}
    foreach ($g in @('mode_is_monitor_only', 'trusted_disable_verified', 'S1_halted', 'S2_hash_equal')) {
        $seen[$g] = $true
    }
    foreach ($s in $stages) {
        foreach ($r in @($s.requires)) {
            if (-not $seen.ContainsKey('' + $r)) { $res['G-01'] = $false }
        }
        $seen['' + $s.id] = $true
        $seen[('' + $s.id + '_halted')] = $true
        $seen[('' + $s.id + '_hash_equal')] = $true
        $gateCruise = -1; $gateDis = -1; $learnIdx = -1; $commitIdx = -1; $idx = 0
        if ($s.PSObject.Properties['steps']) {
            $done = @{}
            foreach ($st in @($s.steps)) {
                if ($st.PSObject.Properties['requires']) {
                    foreach ($r in @($st.requires)) {
                        if (-not $done.ContainsKey('' + $r)) { $res['G-01'] = $false }
                    }
                }
                if ($st.PSObject.Properties['manual']) {
                    if ('' + $st.manual -eq 'cruise_reached') { $gateCruise = $idx }
                    if ('' + $st.manual -eq 'trusted_disable_verified' -and $gateDis -lt 0) { $gateDis = $idx }
                }
                if ($st.PSObject.Properties['cmd']) {
                    $c = '' + $st.cmd
                    if ($c -match '^diag_profile learn') { $learnIdx = $idx }
                    if ($c -match '^diag_profile commit') { $commitIdx = $idx }
                    if ($c -match '^diag_profile learn\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)$') {
                        if (@('0', '1') -notcontains $Matches[4]) { $res['F-10'] = $false }
                        if (-not $st.PSObject.Properties['window_basis']) { $res['F-11'] = $false }
                    }
                    if ($c -match '^motor_target\s+(\d+)$') {
                        $cap = 1000
                        if ($st.PSObject.Properties['hz_max']) { $cap = [int]$st.hz_max }
                        if ([int]$Matches[1] -gt $cap) { $res['F-11'] = $false }
                    }
                    if ($c -match 'irun|tmc_config_apply|__collision__') {
                        if ($c -match '__collision__') { $res['F-12'] = $false } else { $res['F-11'] = $false }
                    }
                }
                if ($st.PSObject.Properties['as']) {
                    $done['' + $st.as] = $true
                    $seen['' + $st.as] = $true
                }
                $idx++
            }
        }
        if ($learnIdx -ge 0 -and ($gateCruise -lt 0 -or $gateCruise -gt $learnIdx)) { $res['F-09'] = $false }
        if ($commitIdx -ge 0 -and ($gateDis -lt 0 -or $gateDis -gt $commitIdx)) { $res['F-09'] = $false }
    }
    $ndl = ReadJsonFile (Join-Path $FieldDir 'needles.json')
    $res['F-13'] = $true
    foreach ($s in $stages) {
        if (-not $s.PSObject.Properties['steps']) { continue }
        foreach ($st in @($s.steps)) {
            foreach ($propName in @('expect_contains', 'expect_any', 'expect_absent')) {
                if (-not $st.PSObject.Properties[$propName]) { continue }
                foreach ($n in @($st.$propName)) {
                    if (-not $ndl.PSObject.Properties[('' + $n)]) {
                        $res['F-13'] = $false
                        M8Out ('NEEDLE_UNTRACED|' + ('' + $s.id) + '|' + ('' + $st.as) + '|' + $propName +
                               '|' + ('' + $n))
                    }
                }
            }
        }
    }
    foreach ($k in ($res.Keys | Sort-Object)) {
        M8Out ('CONTRACT|' + $Tag + '|' + $k + '|' + $(if ($res[$k]) { 'PASS' } else { 'FAIL' }))
    }
    return $res
}

# ---------------------------------------------------------------- 会话调度
function Invoke-Session($Stage, [string]$FixturePath, [string]$Trans, [int]$Deadline) {
    foreach ($st in @($Stage.steps)) {
        if ($st.PSObject.Properties['quota']) {
            Use-Quota ('' + $st.quota.name) ([int]$st.quota.n)
        }
        if ($st.PSObject.Properties['cmd'] -and ('' + $st.cmd) -eq 'motor_start') {
            Use-Quota 'motor_run_seconds_total' ([int]$st.seconds)
        }
    }
    $stepsPath = Join-Path $script:RunRoot ('inputs\steps_' + $Stage.id + '.json')
    WriteJsonFile @($Stage.steps) $stepsPath
    $sessOut = Join-Path $script:RunRoot ('sess_' + $Stage.id)
    New-Item -ItemType Directory -Force -Path $sessOut | Out-Null
    $pyArgs = @($ChainPy, '--transport', $Trans, '--steps', $stepsPath,
                '--session', ('S_' + $Stage.id), '--out', $sessOut,
                '--total-deadline-seconds', $Deadline)
    if ($Trans -eq 'stub') { $pyArgs += @('--fixture', $FixturePath) }
    else { $pyArgs += @('--approval-token', $ApprovalToken, '--expected-token', $ExpectedToken) }
    $raw = & $PythonExe @pyArgs 2>&1
    $rcCode = $LASTEXITCODE
    $line = @($raw | Where-Object { ('' + $_) -match '^\{' }) | Select-Object -Last 1
    if (-not $line) {
        M8Out ('RUNNER_RAW|' + $Stage.id + '|rc=' + $rcCode + '|' + (($raw | ForEach-Object { '' + $_ }) -join ' / '))
        if ($rcCode -eq 4 -and $Trans -eq 'real') { StopM8 'REAL_TRANSPORT_REFUSED' }
        StopM8 ('NO_VERDICT_FROM_RUNNER:' + $Stage.id + ':rc=' + $rcCode)
    }
    $obj = ReadJsonText ('' + $line)
    M8Out ('SESSION|' + $Stage.id + '|rc=' + $rcCode + '|status=' + $obj.status +
           '|steps=' + (@($obj.results).Count))
    if ($obj.status -ne 'OK') {
        if (('' + $obj.detail) -ne '' -and $null -ne $obj.detail) {
            StopM8 ('' + $obj.code + ':' + $obj.detail)
        }
        foreach ($r in @($obj.results)) {
            if ($r.verdict -eq 'STOP') { StopM8 ('' + $r.code) }
        }
        StopM8 ('SESSION_STOP_UNNAMED:' + $Stage.id)
    }
    if ($rcCode -ne 0) { StopM8 ('RUNNER_RC_NONZERO_WITH_OK_STATUS:' + $Stage.id + ':rc=' + $rcCode) }
    return $obj
}
function Get-StepText($Obj, [string]$As) {
    foreach ($r in @($Obj.results)) {
        if ('' + $r.as -eq $As) {
            if ($r.PSObject.Properties['txt'] -and ('' + $r.txt) -ne '') {
                return [System.IO.File]::ReadAllText(('' + $r.txt), [System.Text.Encoding]::UTF8)
            }
            return ''
        }
    }
    StopM8 ('STEP_NOT_FOUND:' + $As)
}

# ---------------------------------------------------------------- 场景执行
function Invoke-Scenario($Plan, [string]$FixturePath, [string]$Trans, [int]$Deadline,
                           [string]$CaseName) {
    $script:RunRoot = (Join-Path $script:RunBase ('case_' + $CaseName))
    New-Item -ItemType Directory -Force -Path $script:RunRoot | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $script:RunRoot 'inputs') | Out-Null
    $script:Used = @{}
    $script:Limit = QuotaTable $Plan
    if ($Trans -eq 'real' -and ($ApprovalToken -ne $ExpectedToken -or $ExpectedToken -eq 'NOT_GRANTED_THIS_ROUND')) {
        StopM8 'REAL_TRANSPORT_REFUSED'
    }
    $fx = ReadJsonFile $FixturePath
    $state = '' + $fx.flash_state
    if (@('absent', 'v2', 'v3') -notcontains $state) { StopM8 ('FLASH_STATE_UNKNOWN:' + $state) }
    M8Out ('S0|flash_state=' + $state + '|disposition=' + ($Plan.flash_state_registering.$state))
    $bin = if ($ImageBin -ne '') { $ImageBin }
           else { Join-Path $RepoRoot ('' + $Plan.identity_binding.image.path) }
    if (-not (Test-Path $bin)) { StopM8 ('IMAGE_MISSING:' + $bin) }
    $bytes = [System.IO.File]::ReadAllBytes($bin)
    $sha = (Get-FileHash -Algorithm SHA256 -Path $bin).Hash.ToLowerInvariant()
    if ($bytes.Length -ne [int]$Plan.identity_binding.image.bytes) {
        StopM8 ('IMAGE_BYTES_MISMATCH:' + $bytes.Length)
    }
    if ($sha -ne ('' + $Plan.identity_binding.image.sha256)) { StopM8 ('IMAGE_SHA_MISMATCH:' + $sha) }
    M8Out ('S0|image_recomputed=' + $sha + '|bytes=' + $bytes.Length + '|equal_to_plan=YES')
    $flip = ($fx.PSObject.Properties['inject'] -and $fx.inject.PSObject.Properties['readback_flip'] -and
             $fx.inject.readback_flip)
    $dl = $bytes.Clone()
    if ($flip) { $dl[10] = ($dl[10] -bxor 0xFF) }
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    $rbSha = (-join ($hasher.ComputeHash($dl) | ForEach-Object { $_.ToString('x2') }))
    $head = $dl[0..4095]
    [System.IO.File]::WriteAllBytes((Join-Path $script:RunRoot 'readback_head4k.bin'), [byte[]]$head)
    Use-Quota 'download' 1
    Use-Quota 'flash_readback' 1
    M8Out ('S1S2|download=SIMULATED(本轮不下载)|readback_full_bytes_hashed_in_memory=YES|head4k_sha=' +
           (-join ($hasher.ComputeHash([byte[]]$head) | ForEach-Object { $_.ToString('x2') })))
    if ($rbSha -ne $sha) { StopM8 ('READBACK_HASH_MISMATCH:' + $rbSha) }
    M8Out 'S1S2|readback_equal=YES'
    foreach ($s in @($Plan.stages)) {
        if ($s.kind -eq 'serial_session') { Use-Quota 'port_open' ([int]$s.port_open) }
        if ($s.kind -eq 'approved_reboot') { Use-Quota 'approved_reboot' 1 }
        if ($s.kind -ne 'serial_session') { continue }
        if ($s.PSObject.Properties['reset_after_port_open'] -and [bool]$s.reset_after_port_open) {
            Use-Quota 'explicit_reset' 1
        }
        $obj = Invoke-Session $s $FixturePath $Trans $Deadline
        if ('' + $s.id -eq 'S6') {
            $t = Get-StepText $obj 'prof1'
            if ($state -ne 'absent' -and $t -match 'ABSENT') { StopM8 'PROFILE_NOT_RESTORED_AFTER_SAVE' }
            $d = Get-StepText $obj 'diag1'
            if ($d -notmatch '\[DIAG\] mode=MONITOR_ONLY') { StopM8 'MODE_NOT_MONITOR_AFTER_REBOOT' }
        }
    }
    $snap = (@($script:Limit.Keys | Sort-Object | ForEach-Object {
        '' + $_ + '=' + $(if ($script:Used.ContainsKey($_)) { $script:Used[$_] } else { 0 }) + '/' + $script:Limit[$_] }) -join ' ')
    M8Out ('QUOTA_SNAPSHOT|' + $snap)
    return 'COMPLETED'
}

# ---------------------------------------------------------------- 场景与夹具
function Get-ScenarioMatrix {
    @(
        @{ name = 'POS'; fixture = 'POS'; expect = 'COMPLETED'; mutate = 'none'; transport = 'stub'; deadline = 90 },
        @{ name = 'READBACK_MISMATCH'; fixture = 'RBFLIP'; expect = 'STOP:READBACK_HASH_MISMATCH'; mutate = 'none'; transport = 'stub'; deadline = 90 },
        @{ name = 'PORT_SILENT'; fixture = 'SILENT'; expect = 'STOP:NO_BYTES_BEFORE_DEADLINE'; mutate = 'none'; transport = 'stub'; deadline = 90 },
        @{ name = 'TOTAL_DEADLINE'; fixture = 'POS'; expect = 'STOP:TOTAL_DEADLINE_EXCEEDED'; mutate = 'none'; transport = 'stub'; deadline = -1 },
        @{ name = 'QUOTA_MOTOR'; fixture = 'POS'; expect = 'STOP:QUOTA_EXCEEDED:motor_run'; mutate = 'extra_motor_start'; transport = 'stub'; deadline = 90 },
        @{ name = 'QUOTA_REBOOT'; fixture = 'POS'; expect = 'STOP:QUOTA_EXCEEDED:approved_reboot'; mutate = 'extra_reboot'; transport = 'stub'; deadline = 90 },
        @{ name = 'MANUAL_DENIED'; fixture = 'DENY'; expect = 'STOP:MANUAL_GATE_DENIED:cruise_reached=no'; mutate = 'none'; transport = 'stub'; deadline = 90 },
        @{ name = 'PROFILE_V2_LOST'; fixture = 'V2LOST'; expect = 'STOP:PROFILE_NOT_RESTORED_AFTER_SAVE'; mutate = 'none'; transport = 'stub'; deadline = 90 },
        @{ name = 'STUB_MISS'; fixture = 'MISS'; expect = 'STOP:STUB_MISS'; mutate = 'none'; transport = 'stub'; deadline = 90 },
        @{ name = 'REAL_UNAUTHORIZED'; fixture = 'POS'; expect = 'STOP:REAL_TRANSPORT_REFUSED'; mutate = 'none'; transport = 'real'; deadline = 90 }
    )
}
function Copy-Fixture([string]$Base, [string]$Name, [scriptblock]$Mutate) {
    $o = ReadJsonFile (Join-Path $FixDir ($Base + '.json'))
    & $Mutate $o
    $dst = Join-Path $script:RunBase ('inputs\fx_' + $Name + '.json')
    WriteJsonFile $o $dst
    return $dst
}
function Prepare-Scenarios {
    $paths = @{}
    $paths['POS'] = Copy-Fixture 'POS' 'POS' { param($o) }
    $paths['RBFLIP'] = Copy-Fixture 'POS' 'RBFLIP' { param($o)
        $o | Add-Member -NotePropertyName inject -NotePropertyValue @{ readback_flip = $true } -Force }
    $paths['SILENT'] = Copy-Fixture 'POS' 'SILENT' { param($o)
        $o | Add-Member -NotePropertyName inject -NotePropertyValue @{ port_silent = $true } -Force }
    $paths['DENY'] = Copy-Fixture 'POS' 'DENY' { param($o)
        $o.manual | Add-Member -NotePropertyName cruise_reached -NotePropertyValue 'no' -Force }
    $paths['V2LOST'] = Copy-Fixture 'POS' 'V2LOST' { param($o)
        $o.flash_state = 'v2'
        $o.responses.'diag_profile show@after_reboot' = @('[DIAG] rel profile ABSENT(no_profile_committed)') }
    $paths['MISS'] = Copy-Fixture 'POS' 'MISS' { param($o)
        [void]$o.responses.PSObject.Properties.Remove('diag_status') }
    return $paths
}
function Get-Probes {
    @(
        @{ name = 'F-01_MIXEDCOMMIT'; expect_fail = @('F-01'); mutate = { param($p)
              $p.identity_binding.tested_image_build_commit = 'not-a-commit' } },
        @{ name = 'F-02_ROUTINE_POWER'; expect_fail = @('F-02'); mutate = { param($p)
              $p.stages[0].title = ('' + $p.stages[0].title) + ' 请先断 24 V 再拔 USB 并拨 SW1' } },
        @{ name = 'F-03_TWOOPEN'; expect_fail = @('F-03'); mutate = { param($p)
              foreach ($s in $p.stages) { if ($s.kind -eq 'serial_session') { $s.port_open = 2; break } } } },
        @{ name = 'F-06_CFGLOAD'; expect_fail = @('F-06'); mutate = { param($p)
              foreach ($s in $p.stages) { if ('' + $s.id -eq 'S3' -and $s.PSObject.Properties['steps']) {
                    $s.steps = @($s.steps) + (ReadJsonText '{"cmd":"config_load","as":"probe_load"}') } } } },
        @{ name = 'F-09_ORDER'; expect_fail = @('F-09', 'G-01'); mutate = { param($p)
              foreach ($s in $p.stages) { if ('' + $s.id -eq 'S4' -and $s.PSObject.Properties['steps']) {
                    $arr = @($s.steps)
                    $learn = $arr[4]
                    $rest = @()
                    for ($i = 0; $i -lt $arr.Count; $i++) { if ($i -ne 4) { $rest += $arr[$i] } }
                    $s.steps = @($learn) + $rest } } } },
        @{ name = 'F-10_DIR2'; expect_fail = @('F-10'); mutate = { param($p)
              foreach ($s in $p.stages) { foreach ($st in @($s.steps)) {
                    if ('' + $st.cmd -match '^diag_profile learn') { $st.cmd = 'diag_profile learn 16 260 340 2' } } } } },
        @{ name = 'F-11_HZ'; expect_fail = @('F-11'); mutate = { param($p)
              foreach ($s in $p.stages) { foreach ($st in @($s.steps)) {
                    if ('' + $st.cmd -match '^motor_target') { $st.cmd = 'motor_target 1500' } } } } },
        @{ name = 'F-13_INVENTED_NEEDLE'; expect_fail = @('F-13'); mutate = { param($p)
              foreach ($s in $p.stages) {
                    if (-not $s.PSObject.Properties['steps']) { continue }
                    foreach ($st in @($s.steps)) {
                    if ($st.PSObject.Properties['expect_contains']) {
                        $st.expect_contains = @('[DIAG] 这一行在固件里不存在')
                        break
                    }
              } } } },
        @{ name = 'F-12_COLLISION'; expect_fail = @('F-12'); mutate = { param($p)
              foreach ($s in $p.stages) { if ('' + $s.id -eq 'S7' -and $s.PSObject.Properties['steps']) {
                    $s.steps = @($s.steps) + (ReadJsonText '{"cmd":"__collision__","as":"probe_coll"}') } } } }
    )
}

# ---------------------------------------------------------------- 主流程
$planDoc = ReadJsonFile $PlanPath
$overallRc = 0
New-Item -ItemType Directory -Force -Path (Join-Path $script:RunBase 'inputs') | Out-Null

if ($Mode -eq 'RunField') {
    M8Out 'MODE|RunField|需要单独书面授权; ExpectedToken 本轮固定为未授予值'
    if ($ApprovalToken -ne $ExpectedToken -or $ExpectedToken -eq 'NOT_GRANTED_THIS_ROUND') {
        M8Out 'FIELD|NOT_AUTHORIZED|approval_token_missing_or_not_granted'
        WriteJsonFile @{ run_id = $script:RunId; rc = 4; log = @($script:Log) } `
                      (Join-Path $script:RunBase 'result.json')
        exit 4
    }
}

M8Out ('MODE|' + $Mode + '|run_id=' + $script:RunId + '|transport=' + $Transport)
$contractBase = Test-PlanContract $planDoc 'SHIP'
foreach ($k in $contractBase.Keys) { if (-not $contractBase[$k]) { $overallRc = 1 } }

foreach ($pd in (Get-Probes)) {
    $cp = ReadJsonFile $PlanPath
    & $pd.mutate $cp
    $c2 = Test-PlanContract $cp ('PROBE_' + ('' + $pd.name))
    $newFail = @($c2.Keys | Where-Object { -not $c2[$_] -and $contractBase[$_] })
    $wantFail = @($pd.expect_fail | Sort-Object)
    $gotFail = @($newFail | Sort-Object)
    if ($wantFail.Count -eq $gotFail.Count) {
        $same = $true
        for ($i = 0; $i -lt $wantFail.Count; $i++) { if ($wantFail[$i] -ne $gotFail[$i]) { $same = $false } }
    } else { $same = $false }
    if ($same) {
        M8Out ('META-PROBE|' + $pd.name + '|META-OK|检出合同位=' + ($gotFail -join ','))
    } else {
        M8Out ('META-PROBE|' + $pd.name + '|NOT-DETECTED|want=' + ($wantFail -join ',') +
               '|got=' + ($gotFail -join ','))
        $overallRc = 1
    }
}

if ($Mode -eq 'SelfTest') {
    $fxPaths = Prepare-Scenarios
    foreach ($sc in (Get-ScenarioMatrix)) {
        $cp = ReadJsonFile $PlanPath
        if ('' + $sc.mutate -eq 'extra_motor_start') {
            foreach ($s in $cp.stages) {
                if ('' + $s.id -eq 'S4') {
                    $s.steps = @($s.steps) + (ReadJsonText '{"cmd":"motor_start","as":"extra1","seconds":5,"quota":{"name":"motor_run","n":1}}') +
                                          (ReadJsonText '{"cmd":"motor_start","as":"extra2","seconds":5,"quota":{"name":"motor_run","n":1}}')
                }
            }
        }
        if ('' + $sc.mutate -eq 'extra_reboot') {
            $cp.stages = @($cp.stages) + (ReadJsonText '{"id":"S5X","kind":"approved_reboot","title":"probe: second reboot must hit the quota","requires":["S5"]}')
        }
        $outcome = ''
        try {
            $outcome = Invoke-Scenario $cp $fxPaths[[string]$sc.fixture] ([string]$sc.transport) `
                                      ([int]$sc.deadline) ([string]$sc.name)
        } catch {
            $msg = '' + $_.Exception.Message
            if ($msg -like 'STOP:*') { $outcome = $msg }
            else { $outcome = 'INFRA:' + $msg }
        }
        if (Test-Outcome ('' + $outcome) ([string]$sc.expect)) {
            M8Out ('SCENARIO|' + $sc.name + '|META-OK|' + $outcome)
        } else {
            M8Out ('SCENARIO|' + $sc.name + '|MISMATCH|got=' + $outcome + '|want=' + $sc.expect)
            $overallRc = 1
        }
    }
}

WriteJsonFile @{ run_id = $script:RunId; mode = $Mode; rc = $overallRc; log = @($script:Log) } `
              (Join-Path $script:RunBase 'result.json')
M8Out ('M8FIELD RESULT: ' + $(if ($overallRc -eq 0) { 'PASS(离线自检, 无设备动作)' } else { 'FAIL(见上表)' }))
exit $overallRc
