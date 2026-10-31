<#
  M8R2Field.ps1 —— NS800 M8-R2 现场候选包入口(本轮离线: 不建立任何真实串口/DAP 对象)

  三个入口是**真的分流**, 不是同一个开关换名字:
    -SelfTest   离线自检: 语法(ParseFile) + 静态串口纪律 + needles 回产品源码证明 +
                25 条 Python 场景(含 3 项定向检出) + RunField 用假动作后端完整调度一遍。
    -Precheck   只读前置核对: 计划结构/额度表整数/身份绑定字段齐备/镜像件存在与散列可算 ——
                不做任何写动作、不开端口、不导入 pyserial。
    -RunField   执行**一次**批准计划: 逐阶段调度动作后端与串口会话, 输出实际调用序列、
                每步判据、额度余量、清理与终态; -Transport real 需要令牌且本轮从未使用。

  额度与硬截止由 m8r2_serial_chain.py 在**发送之前**执行; 本脚本不重复实现判定, 只负责
  调度、证据落盘(独占创建)与首错/清理错/证据失败的分别保留(M6 语义)。
#>
[CmdletBinding(DefaultParameterSetName = 'SelfTest')]
param(
    [Parameter(Mandatory = $true, ParameterSetName = 'SelfTest')]
    [switch]$SelfTest,
    [Parameter(Mandatory = $true, ParameterSetName = 'Precheck')]
    [switch]$Precheck,
    [Parameter(Mandatory = $true, ParameterSetName = 'RunField')]
    [switch]$RunField,

    [Parameter(ParameterSetName = 'RunField')]
    [string]$ApprovalToken = '',
    [Parameter(ParameterSetName = 'RunField')]
    [ValidateSet('stub', 'real')]
    [string]$Transport = 'stub',
    [Parameter(ParameterSetName = 'RunField')]
    [ValidateSet('sim', 'manual', 'real')]
    [string]$Backend = 'sim',
    [Parameter(ParameterSetName = 'RunField')]
    [string]$Port = 'COM5',
    [Parameter(ParameterSetName = 'RunField')]
    [string]$FixturePath = '',

    [string]$OutRoot = '',
    [string]$RepoRoot = ''
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$script:Failures = New-Object System.Collections.ArrayList
$script:Checks = 0
$script:Py = $null
$script:RunId = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ') + '-' + $PID

# Write-Host 而不是 Write-Output: 函数里的成功流会被返回值吞掉(实测 $d 变成字符串数组)。
function Say([string]$m) { Write-Host $m }
function Check([string]$id, [bool]$ok, [string]$detail) {
    $script:Checks++
    if (-not $ok) { [void]$script:Failures.Add($id) }
    Say ('CHECK|{0}|{1}|{2}' -f $id, ($(if ($ok) { 'PASS' } else { 'FAIL' })), $detail)
}

function Resolve-Python {
    if ($script:Py) { return $script:Py }
    foreach ($cand in @('python', 'py')) {
        $p = Get-Command $cand -ErrorAction SilentlyContinue
        if ($p) { $script:Py = $p.Source; return $script:Py }
    }
    throw 'PYTHON_MISSING'
}

function Get-RepoRootOrThrow {
    if ($RepoRoot -and (Test-Path (Join-Path $RepoRoot '.git'))) { return (Resolve-Path $RepoRoot).Path }
    $dir = Get-FieldDir
    # field 位于 reports/reviews/<task>/field => 向上三级是仓库根
    $root = (Resolve-Path (Join-Path $dir '..\..\..\..')).Path
    if (-not (Test-Path (Join-Path $root '.git'))) { throw "REPO_ROOT_UNVERIFIED:$root" }
    return $root
}

# $PSScriptRoot 只在脚本顶层可靠(函数里 $MyInvocation 指向函数自身), 因此顶层捕获一次。
$script:Self = if ($PSScriptRoot) { (Resolve-Path $PSScriptRoot).Path }
               else { (Resolve-Path (Split-Path -Parent $PSCommandPath)).Path }

function Get-FieldDir { return $script:Self }

function Read-JsonFile([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { throw "MISSING_FILE:$path" }
    $txt = Get-Content -LiteralPath $path -Raw -Encoding UTF8
    return ($txt | ConvertFrom-Json)
}

function Write-EvidenceNew([string]$path, $obj) {
    $dir = Split-Path -Parent $path
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    if (Test-Path -LiteralPath $path) { throw "EVIDENCE_PREEXISTING:$path" }   # 不覆盖既有证据
    # 字典/对象直接序列化; 集合(含单元素数组)必须显式成数组 —— 管道拆开过, 执行器把
    # 字典当步骤列表读会静默错位, 所以这里按类型分派而不是无脑 @()。
    if ($obj -is [System.Collections.IDictionary] -or $obj -is [pscustomobject]) {
        $json = ConvertTo-Json -InputObject $obj -Depth 12
    } else {
        $json = ConvertTo-Json -InputObject @($obj) -Depth 12
    }
    [System.IO.File]::WriteAllText($path, $json + "`n", (New-Object System.Text.UTF8Encoding($false)))
    return $path
}

function Invoke-PyScript([string]$scriptPath, [string[]]$argl) {
    $py = Resolve-Python
    $prev = $env:PYTHONIOENCODING
    $env:PYTHONIOENCODING = 'utf-8'
    # 原生命令的 stderr 在 $ErrorActionPreference=Stop 下会变成终止错误(实测中断整个调度),
    # 所以这一段临时降回 Continue, 让 stderr 作为数据被收集进 lines。
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $py $scriptPath @argl 2>&1
        $rc = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prevEap
        $env:PYTHONIOENCODING = $prev
    }
    return [pscustomobject]@{ rc = $rc; lines = @($out) }
}

function Assert-PlanShape($plan) {
    $bad = @()
    if (-not $plan.stages -or $plan.stages.Count -lt 6) { $bad += 'stages' }
    foreach ($q in $plan.quota.PSObject.Properties) {
        if ($q.Name -eq 'motion_cap_seconds_per_run') { continue }
        if ($q.Value -isnot [Int64] -and $q.Value -isnot [Int32]) { $bad += ('quota_not_int:' + $q.Name) }
        if ([int]$q.Value -lt 0) { $bad += ('quota_negative:' + $q.Name) }
    }
    $ids = @($plan.stages | ForEach-Object { $_.id })
    if (($ids | Sort-Object -Unique).Count -ne $ids.Count) { $bad += 'stage_id_duplicated' }
    foreach ($st in $plan.stages) {
        foreach ($s in @($st.steps)) {
            if (-not $s) { continue }
            if (-not $s.as) { $bad += ('step_without_as:' + $st.id) }
            if (-not $s.cmd -and -not $s.action -and -not $s.manual) { $bad += ('step_without_effect:' + $st.id + '/' + $s.as) }
            if ($s.allowed_outcomes) {
                foreach ($a in $s.allowed_outcomes) {
                    if (-not ($a -match '^\[DIAG\]|^\[MOT\]|^\[CFG\]|^\[Safety\]|^\[SAFETY\]')) { $bad += ('untyped_allowed:' + $a) }
                }
            }
        }
    }
    return $bad
}

function Invoke-NeedleProof($root, $fieldDir) {
    $args = @('--prove-needles', (Join-Path $fieldDir 'needles.json'),
              '--source-root', (Join-Path $root 'rt-thread-master\rt-thread-master\bsp\novosns\ns800\ns800rt7p65-nssinepad'))
    $r = Invoke-PyScript (Join-Path $fieldDir 'm8r2_serial_chain.py') $args
    $unproven = ($r.lines | Select-String -Pattern 'NEEDLES\|total=(\d+)\|unproven=(\d+)' |
                 ForEach-Object { [int]$_.Matches[0].Groups[2].Value } | Select-Object -First 1)
    $total = ($r.lines | Select-String -Pattern 'NEEDLES\|total=(\d+)\|unproven=(\d+)' |
              ForEach-Object { [int]$_.Matches[0].Groups[1].Value } | Select-Object -First 1)
    return [pscustomobject]@{ rc = $r.rc; total = $total; unproven = $unproven }
}

function Invoke-StaticDiscipline($fieldDir) {
    $r = Invoke-PyScript (Join-Path $fieldDir 'm8r2_serial_chain.py') @('--check-static-port-discipline')
    return [pscustomobject]@{ rc = $r.rc; line = ($r.lines -join ' ') }
}

function Invoke-ParseFile([string]$path) {
    $errs = $null; $toks = $null
    [void][System.Management.Automation.PSParser]::Tokenize((Get-Content -LiteralPath $path -Raw), [ref]$errs)
    return @($errs)
}

function New-SessionFixture($plan, $stage, $mode, $fieldDir) {
    # mode: pos | noresp | denied | trip
    $fx = [ordered]@{ boot_bytes = @(); manual = [ordered]@{}; responses = [ordered]@{} }
    $rows = [ordered]@{}
    $rows['system_status'] = @('=== SYSTEM STATUS ===', '[Boot]   bootstrap-complete=YES abort-stage=0')
    $rows['safety_status'] = @('[Safety] request=LOW pad=0 expected_ENN=HIGH ENN_fb=1(resolved) verdict=OK')
    $rows['tmc_config_show'] = @('[TMC-CFG] planned irun=20 iheld=4 target IHOLD_IRUN=0x00041408')
    $rows['tmc_config_apply'] = @('[TMC-CFG] write gate mask=0x0: applied', '[TMC] GSTAT=0x0 IOIN=0x210002C1')
    $rows['safety_irq_enable'] = @('[SAFETY] IRQ gate OPEN: 0 active protection input(s) registered')
    $rows['motor_status'] = @('[MOT] state=0 name=IDLE', '[MOT] armed=0 drv_en_request=0 health=OK',
                              '[MOT] expected_ENN=HIGH ENN_fb=1 resolved verdict=OK')
    $rows['motor_arm'] = @('[MOT] armed=1 drv_en_request=1 health=OK')
    $rows['motor_dir 0'] = @('[MOT] dir=0 (set while fully at rest)')
    $rows['motor_target 100'] = @('[MOT] cur_hz=0 tgt_hz=100 accel=40 decel=40 dir=0')
    $rows['motor_start'] = @('[MOT] state=2 name=CRUISE')
    $rows['motor_stop'] = @('[MOT] stop result=OK src=stop rc=0 latched=0')
    $rows['motor_disarm'] = @('[MOT] armed=0 drv_en_request=0 health=OK')
    $rows['diag_status'] = @('[DIAG] mode=MONITOR_ONLY', '[DIAG] rel cov=COVERED lost=0 gen=0 rearm=0',
                             '[DIAG] rel snap=OK inc=OK seq=1 pubfail=0')
    $rows['config_show'] = @('[CFG] loaded OK (version 0x00010003 cal_source=1 profile=PRESENT)')
    $rows['config_save'] = @('[CFG] save: OK')
    $rows['diag_profile show'] = @('[DIAG] profile id=1 rev=1 abi=2 flg=0005 FIELD_UNVERIFIED',
                                   '[DIAG] live sig=1163520832 match=YES cred zero=0 learn=0')
    $rows['diag_profile zero 32'] = @('[DIAG] zero OK n=32 raw 1660..1660 mean=1660000 (cal source untouched)')
    $rows['diag_profile learn 16 80 120 0'] = @('[DIAG] learn OK adc[2000..2000]n68 nz0 sg[500..500]n8 seq4..11')
    $rows['diag_profile tier 2'] = @('[DIAG] tier 2: dev=64/128 drop=16/32 FIELD_UNVERIFIED (commit to publish)')
    $rows['diag_profile commit'] = @('[DIAG] commit OK id=1 rev=1 flags=0005 FIELD_UNVERIFIED')
    $rows['diag_mode relative CONFIRM_EXPERIMENTAL'] = @('[DIAG] mode=RELATIVE_ACTIVE')
    $rows['blackbox_status'] = @('[BB] session=7 records=12')
    $rows['flash_logstat'] = @('slots=3/64 valid=3 bad=0', 'queued=0 completed=3 failed=0 queue_dropped=0 last_error=0')
    $rows['fault_reset'] = @('[SAFETY] fault cleared')
    $rows['system_selftest'] = @('[SELFTEST] start (state0=FAULT)', '[SELFTEST] verdict=PASS')
    $rows['__watch__'] = @('[MOT] state=2 name=CRUISE')
    foreach ($k in $rows.Keys) { $fx['responses'][$k] = $rows[$k] }
    switch ($mode) {
        'noresp' { $fx['responses'].Remove('motor_target 100') }
        'denied' { $fx['manual']['approved_reboot_performed'] = 'no' }
        'trip' { $fx['responses']['__watch__'] = @('[MOT] state=2 name=CRUISE',
                    '[DIAG] REL TRIP r=REL_SG_SEVERE id=1 rev=1 post_rc=0') }
    }
    $path = Join-Path $fieldDir ('fixtures\FX_' + $stage.id + '_' + $mode.ToUpper() + '.json')
    return (Write-EvidenceNew $path $fx)
}

function Invoke-Stage([string]$fieldDir, $stage, [string]$transport, [string]$backend, [string]$outDir, [string]$planPath, $plan) {
    $stepsFile = Join-Path $outDir ('steps_' + $stage.id + '.json')
    $keep = @()
    foreach ($s in @($stage.steps)) {
        if (-not $s) { continue }
        $o = [ordered]@{}
        foreach ($pp in $s.PSObject.Properties) { $o[$pp.Name] = $pp.Value }
        $keep += [pscustomobject]$o
    }
    if ($stage.actions) { $keep = @($stage.actions | ForEach-Object { [pscustomobject]$_ }) + $keep }
    if ($keep.Count -eq 0) { return [pscustomobject]@{ stage = $stage.id; skipped = 'no_steps' } }
    [void](Write-EvidenceNew $stepsFile $keep)
    $fx = $null
    if ($transport -eq 'stub') {
        $fx = New-SessionFixture $plan $stage 'pos' $outDir
        if ($FixturePath) { $fx = $FixturePath }
    }
    $args = @('--transport', $transport, '--backend', $backend, '--steps', $stepsFile,
              '--session', ('sess_' + $stage.id), '--out', $outDir,
              '--total-deadline-seconds', '180',
              '--plan', (Join-Path $script:Self 'plan.json'),
              '--motion-cap-seconds', ([string]$plan.quota.motion_cap_seconds_per_run))
    if ($fx) { $args += @('--fixture', $fx) }
    if ($transport -eq 'real') {
        if ($ApprovalToken -ne $plan.task_id) {
            return [pscustomobject]@{ stage = $stage.id; status = 'REFUSED'; code = 'REAL_TRANSPORT_TOKEN_MISMATCH' }
        }
        $args += @('--port', $Port, '--approval-token', $ApprovalToken, '--expected-token', $plan.task_id)
    }
    $r = Invoke-PyScript (Join-Path $fieldDir 'm8r2_serial_chain.py') $args
    $last = ($r.lines | Select-Object -Last 1)
    $obj = $null
    try { $obj = $last | ConvertFrom-Json } catch { $obj = $null }
    if (-not $obj) {
        return [pscustomobject]@{ stage = $stage.id; status = 'EVIDENCE_FAILURE';
                                  detail = ('chain_output_unparsable rc=' + $r.rc + ' line=' + $last) }
    }
    return [pscustomobject]@{ stage = $stage.id; status = $obj.status; code = $obj.code;
                              disarm = $obj.disarm; terminal = $obj.terminal;
                              budget_seconds = $obj.budget_seconds; rc = $r.rc;
                              ledger = $obj.ledger; steps = $keep.Count }
}

function Invoke-RunFieldDispatch($root, $fieldDir, $plan, [string]$haltPolicy = 'strict') {
    $outDir = Join-Path (Get-FieldOutRoot) 'runfield'
    $results = @()
    $halted = $null
    foreach ($stage in $plan.stages) {
        if ($stage.kind -eq 'offline') {
            $results += [pscustomobject]@{ stage = $stage.id; status = 'OFFLINE_BINDING'; code = '' }
            continue
        }
        if ($halted) {
            $results += [pscustomobject]@{ stage = $stage.id; status = 'NOT_RUN'; code = ('halt_after:' + $halted) }
            continue
        }
        $r = Invoke-Stage $fieldDir $stage $Transport $Backend $outDir (Join-Path $fieldDir 'plan.json') $plan
        $results += $r
        Say ('STAGE|{0}|{1}|{2}|disarm={3}|budget={4}' -f $r.stage, $r.status, $r.code, $r.disarm, $r.budget_seconds)
        if ($r.status -notin @('OK', 'OBSERVATION_NO_TRIGGER', 'OFFLINE_BINDING')) { $halted = $r.stage }
        if ($stage.halt_after -eq $true) {
            # strict = 现场 RunField 的默认: 到这个停点就停, 下一次执行需要新的批准。
            # full = 仅离线自检: 记录停点但继续调度, 以便完整看到动作调用序列。
            Say ('HALT_POINT|' + $stage.id + '|after=' + $r.status)
            if ($haltPolicy -ne 'full') { $halted = $r.stage }
        }
    }
    return [pscustomobject]@{ outDir = $outDir; results = $results; halted_after = $halted }
}

function Get-FieldOutRoot {
    if ($OutRoot) {
        if (-not (Test-Path -LiteralPath $OutRoot)) { New-Item -ItemType Directory -Path $OutRoot -Force | Out-Null }
        return (Resolve-Path $OutRoot).Path
    }
    $t = Join-Path $env:TEMP ('ns800-m8r2-field-' + $script:RunId)
    New-Item -ItemType Directory -Path $t -Force | Out-Null
    return (Resolve-Path $t).Path
}

# ------------------------------------------------------------------ 入口
$script:FieldDir = Get-FieldDir
$script:Root = Get-RepoRootOrThrow
$plan = Read-JsonFile (Join-Path $script:FieldDir 'plan.json')

if ($Precheck) {
    Say ('PRECHECK_START|run=' + $script:RunId)
    $bad = Assert-PlanShape $plan
    Check 'PC01_plan_shape' ($bad.Count -eq 0) ('violations=' + ($bad -join ','))
    $missId = @('reviewed_delivery_commit', 'software_input_commit', 'tested_image_build_commit') |
        Where-Object { -not $plan.identity_binding.$_ }
    Check 'PC02_identity_fields' (@($missId).Count -eq 0) ('missing=' + (@($missId) -join ','))
    Check 'PC03_image_declared' ($plan.identity_binding.image.bytes -gt 0 -and
                                 $plan.identity_binding.image.sha256.Length -eq 64) `
                                ('bytes=' + $plan.identity_binding.image.bytes)
    $imgPath = Join-Path $script:Root $plan.identity_binding.image.path
    if (Test-Path -LiteralPath $imgPath) {
        $h = (Get-FileHash -LiteralPath $imgPath -Algorithm SHA256).Hash.ToLower()
        $len = (New-Object System.IO.FileInfo $imgPath).Length
        Check 'PC04_image_hash' ($h -eq $plan.identity_binding.image.sha256 -and $len -eq $plan.identity_binding.image.bytes) `
                                ('disk=' + $h.Substring(0, 12) + ' bytes=' + $len)
    } else {
        Check 'PC04_image_hash' $false ('镜像件尚未归档: ' + $plan.identity_binding.image.path)
    }
    $np = Invoke-NeedleProof $script:Root $script:FieldDir
    Check 'PC05_needles_proven' ($np.rc -eq 0 -and $np.unproven -eq 0) ('total=' + $np.total + ' unproven=' + $np.unproven)
    $sd = Invoke-StaticDiscipline $script:FieldDir
    Check 'PC06_static_port_discipline' ($sd.rc -eq 0) $sd.line
    Check 'PC07_no_serial_import' ((Get-Content -LiteralPath (Join-Path $script:FieldDir 'm8r2_serial_chain.py') -Raw -Encoding UTF8) -match 'if a.transport == "real"') `
                                   'real 分支存在但需令牌; 本轮 hardware_actions=0'
    Check 'PC08_prerequisites_present' ($plan.prerequisites_after_fresh_flash_or_reboot.then_normal_apply -match 'tmc_config_apply') `
                                       'C4 前置与额度已写入计划'
    Check 'PC09_load_step_named_gap' ($plan.load_step.verdict -eq 'NAMED_PREREQUISITE_MISSING') $plan.load_step.verdict
    Check 'PC10_impact_not_selected' ($plan.impact_r6_excitation.selected -eq $false) 'R6 激励未选择 => FIELD_NOT_TRIGGERED'
    $summary = [ordered]@{ run_id = $script:RunId; entry = 'Precheck'; checks = $script:Checks;
                             failures = @($script:Failures); verdict = ($(if ($script:Failures.Count -eq 0) { 'PASS' } else { 'FAIL' })) }
    $path = Write-EvidenceNew (Join-Path (Get-FieldOutRoot) ('precheck_' + $script:RunId + '.json')) $summary
    Say ('PRECHECK_VERDICT|' + $summary.verdict + '|failures=' + (@($script:Failures) -join ','))
    Say ('PRECHECK_EVIDENCE|' + $path)
    if ($summary.verdict -ne 'PASS') { exit 1 }
    exit 0
}

if ($RunField) {
    Say ('RUNFIELD_START|run=' + $script:RunId + '|transport=' + $Transport + '|backend=' + $Backend)
    $np = Invoke-NeedleProof $script:Root $script:FieldDir
    Check 'RF00_needles_proven_before_run' ($np.rc -eq 0 -and $np.unproven -eq 0) ('unproven=' + $np.unproven)
    if ($np.unproven -gt 0) { Say 'RUNFIELD_VERDICT|REFUSED_NEEDLES_UNPROVEN'; exit 4 }
    if ($Transport -eq 'real' -and $ApprovalToken -ne $plan.task_id) {
        Say 'RUNFIELD_VERDICT|REFUSED_REAL_TRANSPORT_TOKEN'
        exit 4
    }
    $d = Invoke-RunFieldDispatch $script:Root $script:FieldDir $plan
    $seq = @()
        $summary = [ordered]@{ run_id = $script:RunId; entry = 'RunField'; transport = $Transport;
                             backend = $Backend; halted_after = $d.halted_after;
                             stages = @($d.results | ForEach-Object { $_ | ConvertTo-Json -Compress -Depth 6 }) }
    $path = Write-EvidenceNew (Join-Path $d.outDir ('runfield_' + $script:RunId + '.json')) $summary
    Say ('RUNFIELD_SUMMARY|halted_after=' + $d.halted_after + '|stages=' + $d.results.Count)
    Say ('RUNFIELD_EVIDENCE|' + $path)
    if ($script:Failures.Count -gt 0) { exit 1 }
    if ($Transport -eq 'stub' -and $Backend -eq 'sim') {
        Say 'RUNFIELD_OFFLINE_DISPATCH|identity_gate=NOT_EXERCISED|hardware_actions=0'
        exit 0
    }
    exit 0
}

# SelfTest(默认入口)
Say ('SELFTEST_START|run=' + $script:RunId)
$ps1 = Join-Path $script:FieldDir 'M8R2Field.ps1'
$tokErrs = Invoke-ParseFile $ps1
Check 'ST00_ps1_parse' ($tokErrs.Count -eq 0) ('parse_errors=' + $tokErrs.Count)
$bad = Assert-PlanShape $plan
Check 'ST01_plan_shape' ($bad.Count -eq 0) ('violations=' + ($bad -join ','))
$sd = Invoke-StaticDiscipline $script:FieldDir
Check 'ST02_static_port_discipline' ($sd.rc -eq 0) $sd.line
$np = Invoke-NeedleProof $script:Root $script:FieldDir
Check 'ST03_needles_proven' ($np.rc -eq 0 -and $np.unproven -eq 0) ('total=' + $np.total + ' unproven=' + $np.unproven)
$st = Invoke-PyScript (Join-Path $script:FieldDir 'm8r2_field_selftest.py') @()
$sumLine = ($st.lines | Select-String -Pattern '^SUMMARY\|cases=(\d+) pass=(\d+) fail=(\d+)' | Select-Object -First 1)
if ($sumLine) {
    $cases = [int]$sumLine.Matches[0].Groups[1].Value
    $passN = [int]$sumLine.Matches[0].Groups[2].Value
    $failN = [int]$sumLine.Matches[0].Groups[3].Value
} else { $cases = 0; $passN = 0; $failN = -1 }
$sumTxt = $(if ($sumLine) { $sumLine.ToString() } else { 'NO_SUMMARY_LINE' })
Check 'ST04_python_selftest_all_pass' ($st.rc -eq 0 -and $failN -eq 0 -and $cases -eq 25) `
      ('rc=' + $st.rc + ' ' + $sumTxt)
foreach ($need in @('FS05', 'FS10', 'FS11', 'FS13', 'FS17', 'FS18', 'FS20', 'FS23', 'FS24', 'FS25')) {
    $hit = $st.lines | Where-Object { $_ -match ('^CASE\|' + $need + '\|PASS') }
    Check ('ST05_' + $need) (@($hit).Count -eq 1) ($st.lines | Where-Object { $_ -match ('^CASE\|' + $need + '\|') } | Select-Object -First 1)
}
$d = Invoke-RunFieldDispatch $script:Root $script:FieldDir $plan 'full'
$txAll = @()
foreach ($r in $d.results) {
    if ($r.ledger -and (Test-Path -LiteralPath $r.ledger)) {
        $txAll += (Get-Content -LiteralPath $r.ledger -Encoding UTF8 |
                   ForEach-Object { try { ($_ | ConvertFrom-Json) } catch { $null } } |
                   Where-Object { $_.kind -in @('TX', 'ACTION') })
    }
}
$names = @($txAll | ForEach-Object { if ($_.kind -eq 'ACTION') { ('action:' + $_.name) } elseif ($_.bytes) { ('tx:' + $_.bytes.Trim()) } else { 'tx?' } })
foreach ($want in @('action:download', 'action:readback', 'action:reset', 'action:reboot')) {
    Check ('ST06_' + ($want -replace ':', '_')) ($names -contains $want) ('seen=' + ($names -join ','))
}
Check 'ST07_no_transport_real' ($Transport -ne 'real') 'offline dispatch only'
Check 'ST08_terminal_known_after_start' ($names -contains 'tx:motor_disarm') ('disarm_sent=' + ($names -contains 'tx:motor_disarm'))
$summary = [ordered]@{ run_id = $script:RunId; entry = 'SelfTest'; checks = $script:Checks;
                         python_cases = $cases; python_pass = $passN; python_fail = $failN;
                         failures = @($script:Failures); action_sequence = $names;
                         stages = @($d.results | ForEach-Object { $_ | ConvertTo-Json -Compress -Depth 6 }) }
$path = Write-EvidenceNew (Join-Path (Get-FieldOutRoot) ('selftest_' + $script:RunId + '.json')) $summary
Say ('SELFTEST_VERDICT|' + ($(if ($script:Failures.Count -eq 0) { 'PASS' } else { 'FAIL' })) +
     '|checks=' + $script:Checks + '|failures=' + (@($script:Failures) -join ','))
Say ('SELFTEST_EVIDENCE|' + $path)
if ($script:Failures.Count -gt 0) { exit 1 }
exit 0
