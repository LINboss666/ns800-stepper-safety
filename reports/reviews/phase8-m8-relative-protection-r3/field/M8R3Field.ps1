<#
  M8R3Field.ps1 —— NS800 M8-R3 现场候选包入口(本轮离线: 不建立任何真实串口/DAP 对象)

  三个入口是**真的分流**(三个互斥参数集, 不能同时给):
    -SelfTest   离线自检: 本脚本 ParseFile 语法 + 执行器静态串口纪律 + needles 回产品源码证明 +
                --validate-plan 的结构门 + Python 场景自检(含 7 项定向检出) +
                三个档案(healthy/trip/no_trigger)各跑一次 --dispatch(假端口 + 假动作后端) +
                两条 PS 侧负控(无令牌的 real 传输必须拒; 被判据挡住的阶段必须 NOT_RUN)。
    -Precheck   只读前置核对: 交付件存在性、镜像字节/散列独立复算并与 build_manifest 与 plan
                三方对账、额度表都是整数、两个提交都是仓库里的 commit 对象、产品侧改动文件的
                sha 等于计划申报值、rows.json 声称的板级来源文件确实存在。零写动作。
    -RunField   执行**一次**批准计划: 直接调 m8r3_serial_chain.py --dispatch —— 与自检**同一个
                入口、同一条现场停点策略** —— 打印实际调用与额度序列, 再按 success 口径判:
                任何 STOP / NOT_RUN / terminal=UNKNOWN / 清理错误 / 预留耗尽 / 证据失败 => 非零返回。

  R3 相对 R2 的实质变化: 本脚本**不再自己实现阶段循环与停点**(那是 R2 的做法: strict 路径主动停在
  成功下载之后, 下一次又从 S1 开始, 所以自检的 full 路径证明不了交付版 RunField 能整包运行)。
  调度与判据只有一份, 在 Python 执行器里; PS 只做参数门、证据落盘、退出码与独立复算。
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
    [ValidateSet('stub', 'real')]
    [string]$Transport = 'stub',
    [Parameter(ParameterSetName = 'RunField')]
    [ValidateSet('sim', 'manual', 'real')]
    [string]$Backend = 'sim',
    [Parameter(ParameterSetName = 'RunField')]
    [ValidateSet('healthy', 'trip', 'no_trigger', 'all')]
    [string]$Profile = 'all',
    [Parameter(ParameterSetName = 'RunField')]
    [string]$Port = 'COM5',
    [Parameter(ParameterSetName = 'RunField')]
    [string]$ApprovalToken = '',
    [Parameter(ParameterSetName = 'RunField')]
    [string]$ExpectedToken = '',
    [Parameter(ParameterSetName = 'RunField')]
    [string]$UidFile = '',

    [string]$OutRoot = '',
    [string]$RepoRoot = ''
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$script:Self = $PSScriptRoot
$script:EntryName = $PSCmdlet.ParameterSetName   # 注意: PS 变量名大小写不敏感, 局部 \$entry 会顶掉它
$script:Checks = 0
$script:Failures = New-Object System.Collections.ArrayList
$script:Py = $null
$script:Root = $null
$script:OutDir = $null
$script:RunId = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ') + '-' + $PID
$script:Chain = Join-Path $script:Self 'm8r3_serial_chain.py'
$script:SelftestCase = Join-Path $script:Self 'm8r3_field_selftest.py'
$script:PlanPath = Join-Path $script:Self 'plan.json'
$script:RowsPath = Join-Path $script:Self 'rows.json'
$script:NeedlesPath = Join-Path $script:Self 'needles.json'
$script:BspRel = 'rt-thread-master\rt-thread-master\bsp\novosns\ns800\ns800rt7p65-nssinepad'

$script:Trans = New-Object System.Collections.ArrayList   # 逐行缓冲, 收口时按 UTF-8 落盘
function Say([string]$m) { [void]$script:Trans.Add($m); Write-Host $m }

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
    throw 'PYTHON_NOT_FOUND'
}

function Get-RepoRootOrThrow {
    if ($script:Root) { return $script:Root }
    $r = if ($RepoRoot) { $RepoRoot } else { (Resolve-Path (Join-Path $script:Self '..\..\..\..')).Path }
    if (-not (Test-Path -LiteralPath (Join-Path $r '.git'))) { throw "REPO_ROOT_MISSING_GIT:$r" }
    $script:Root = $r
    return $r
}

function Get-FieldOutRoot {
    if ($script:OutDir) { return $script:OutDir }
    $base = if ($OutRoot) { $OutRoot } else {
        Join-Path (Get-RepoRootOrThrow) 'reports/reviews/phase8-m8-relative-protection-r3/offline' }
    $leaf = "psrun-$($script:RunId)-$($script:EntryName)"
    $d = Join-Path $base $leaf
    Say ('OUT_DIR_TRIED|base=[' + $base + '] leaf=[' + $leaf + ']')
    New-Item -ItemType Directory -Path $d -Force | Out-Null
    $script:OutDir = $d
    return $d
}

function Invoke-Py([string[]]$argl) {
    # 权威退出码来自进程本身; 输出先落文件再按 UTF-8 读(直接接管道会被 GBK 解码成乱码)
    $py = Resolve-Python
    # python 的 stdout 被重定向到文件时按**本机 locale**(这台机器是 cp936)编码, 而下面按 UTF-8 读。
    # 不钉死就得到"归档本身是合法 UTF-8, 但里面的中文全是替换符"的静默损坏(本轮真实踩到, 旧运行
    # 目录里的 console_* 与 pyout-* 就是这样)。
    $env:PYTHONIOENCODING = 'utf-8'
    $dir = Get-FieldOutRoot
    $n = (Get-ChildItem -LiteralPath $dir -Filter 'pyout-*.txt' -ErrorAction SilentlyContinue).Count + 1
    $tmp = Join-Path $dir ("pyout-{0}-{1:d2}.txt" -f $script:RunId, $n)
    $err = $tmp + '.err'
    $p = Start-Process -FilePath $py -ArgumentList $argl -NoNewWindow -PassThru -Wait `
        -RedirectStandardOutput $tmp -RedirectStandardError $err
    $lines = @()
    if (Test-Path -LiteralPath $tmp) {
        $lines = [System.IO.File]::ReadAllLines($tmp, [System.Text.Encoding]::UTF8)
    }
    $errtext = @()
    if (Test-Path -LiteralPath $err) {
        $errtext = [System.IO.File]::ReadAllLines($err, [System.Text.Encoding]::UTF8)
    }
    # 判据不是"看起来像中文"而是**替换符不得出现**: 出现了就是编码链断了, 归档不可用
    $joined = ($lines -join "`n") + "`n" + ($errtext -join "`n")
    if ($joined.Contains([char]0xFFFD)) {
        $bad = @($lines | Where-Object { $_.Contains([char]0xFFFD) } | Select-Object -First 1)
        throw ('PYTHON_OUTPUT_NOT_UTF8|file=' + $tmp + '|first_bad_line=' + ($bad -join ''))
    }
    return [pscustomobject]@{ Rc = $p.ExitCode; Lines = @($lines); Err = @($errtext); Out = $tmp }
}

function Write-EvidenceNew([string]$name, $text) {
    # 独占创建: 证据一旦落盘就不覆盖(重导出要换 run_id, 不是删旧的)
    $p = Join-Path (Get-FieldOutRoot) $name
    if (Test-Path -LiteralPath $p) { throw "EVIDENCE_PREEXISTING:$p" }
    [System.IO.File]::WriteAllLines($p, @($text), (New-Object System.Text.UTF8Encoding($false)))
    return $p
}

function Flush-Transcript() {
    # 归档不依赖本机 console codepage: Say 的每一行由 .NET 按 UTF-8 落盘, host/ 的字段归档取这份字节副本。
    # (把 PS 进程 stdout 直接重定向成归档, 中文会按 cp936 走一遍 => "合法 UTF-8 的乱码/替换符"。)
    Write-EvidenceNew 'transcript_console.txt' @($script:Trans) | Out-Null
}

function Split-Summary([string]$sum) {
    $kv = @{}
    foreach ($part in ($sum -split '\|')[1..99]) {
        $i = $part.IndexOf('=')
        if ($i -gt 0) { $kv[$part.Substring(0, $i)] = $part.Substring($i + 1) }
    }
    return $kv
}

function Get-DispatchVerdict($res) {
    # 只**汇总**执行器自己打印的东西, 不在 PS 里另做判定: 判定口径只有 Python 那一份
    $sum = $res.Lines | Where-Object { $_ -like 'DISPATCH_SUMMARY|*' } | Select-Object -Last 1
    $stages = @($res.Lines | Where-Object { $_ -match '^DISPATCH\|stage=' })
    if (-not $sum) {
        $refused = (@($res.Lines) + @($res.Err)) -join ' '
        return [pscustomobject]@{ Rc = $res.Rc; Status = 'NO_SUMMARY'; Executed = 0
            NotRunCount = 0; Summary = ''; Stages = $stages; Unknown = @(); CleanupErrors = @()
            CleanupOverruns = @(); EvidenceFailures = @(); QuotaAuditFailures = @()
            NotRunLines = @($stages | Where-Object { $_ -match '\|status=NOT_RUN' })
            QuotaLeft = ''; QuotaUsed = ''; Profile = ''; Recorded = ''; Ledger = ''
            RefusedText = $refused }
    }
    $kv = Split-Summary $sum
    return [pscustomobject]@{
        Rc = $res.Rc; Status = $kv['status']; Executed = [int]$kv['stages_executed']
        NotRunCount = [int]$kv['stages_not_run']; Summary = $sum; Stages = $stages
        Unknown = @($res.Lines | Where-Object { $_ -match '"terminal":\s*"UNKNOWN"' })
        CleanupErrors = @($res.Lines | Where-Object { $_ -match '"cleanup_errors":\s*\[\s*"' })
        CleanupOverruns = @($res.Lines | Where-Object { $_ -match '"cleanup_overruns":\s*\[\s*"' })
        EvidenceFailures = @($res.Lines | Where-Object { $_ -match 'EVIDENCE_(OPEN|WRITE)_FAILED' })
        QuotaAuditFailures = @($res.Lines | Where-Object { $_ -match '^QUOTA_AUDIT_FAIL' })
        NotRunLines = @($stages | Where-Object { $_ -match '\|status=NOT_RUN' })
        QuotaLeft = $kv['quota_left']; QuotaUsed = $kv['quota_used']; Profile = $kv['profile']
        Recorded = ($res.Lines | Where-Object { $_ -match '"recorded":' } | Select-Object -Last 1)
        Ledger = $kv['ledger']; RefusedText = '' }
}

function Assert-DispatchOk($v, [string]$which) {
    $ok = ($v.Rc -eq 0) -and ($v.Status -eq 'OK') -and ($v.NotRunCount -eq 0) -and
          ($v.Unknown.Count -eq 0) -and ($v.CleanupErrors.Count -eq 0) -and
          ($v.CleanupOverruns.Count -eq 0) -and ($v.EvidenceFailures.Count -eq 0) -and
          ($v.QuotaAuditFailures.Count -eq 0)
    $detail = ('status={0} rc={1} executed={2} not_run={3} unknown={4} cleanup_err={5} ' +
               'reserve_breach={6} evidence_fail={7} quota_fail={8}') -f $v.Status, $v.Rc,
               $v.Executed, $v.NotRunCount, $v.Unknown.Count, $v.CleanupErrors.Count,
               $v.CleanupOverruns.Count, $v.EvidenceFailures.Count, $v.QuotaAuditFailures.Count
    Check ("DISPATCH_" + $which) $ok $detail | Out-Null
    return $ok
}

function Invoke-Dispatch([string]$profile, [string]$transport, [string]$backend, [string]$tag,
                         [string]$planPath, [string]$approval, [string]$expected, [string]$uid) {
    $out = Join-Path (Get-FieldOutRoot) ('dispatch-' + $tag + '-' + $profile)
    $argl = @($script:Chain, '--dispatch', '--transport', $transport, '--backend', $backend,
              '--profile', $profile, '--plan', $planPath, '--out', $out,
              '--repo-root', (Get-RepoRootOrThrow), '--port', $Port)
    if ($approval) { $argl += @('--approval-token', $approval) }
    if ($expected) { $argl += @('--expected-token', $expected) }
    if ($uid) { $argl += @('--uid-file', $uid) }
    $res = Invoke-Py $argl
    $v = Get-DispatchVerdict $res
    Write-EvidenceNew ("console_" + $tag + "_" + $profile + ".txt") (
        @($res.Lines) + @('STDERR>') + @($res.Err)) | Out-Null
    return $v
}

function Assert-ParseFile([string]$path, [string]$id) {
    $errs = $null
    $tokens = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errs)
    $n = if ($errs) { @($errs).Count } else { 0 }
    Check $id ($n -eq 0 -and @($tokens).Count -gt 400)
          ("errors=$n tokens=$(($tokens).Count) file=$(Split-Path $path -Leaf)") | Out-Null
    return ($n -eq 0)
}

function Get-RowsProvenanceFiles($rowsDoc) {
    # rows.json 里声称的板级来源文件必须真在仓库里(来源可核, 不是一句注释)
    $found = New-Object System.Collections.Generic.List[string]
    function Walk($node) {
        if ($null -eq $node) { return }
        if ($node -is [string]) {
            foreach ($mo in [regex]::Matches($node, 'reports/[^\s,;()\"]+\.(?:txt|bin|b64|json)')) {
                [void]$found.Add($mo.Value)
            }
            return
        }
        if ($node -is [System.Collections.IEnumerable]) {
            foreach ($x in $node) { Walk $x }
            return
        }
        foreach ($p in $node.PSObject.Properties) { Walk $p.Value }
    }
    Walk $rowsDoc.commands
    Walk $rowsDoc._board_sources
    return $found
}

# --------------------------------------------------------------------------- 入口: -Precheck
if ($Precheck) {
    $root = Get-RepoRootOrThrow
    $plan = Get-Content -LiteralPath $script:PlanPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $rows = Get-Content -LiteralPath $script:RowsPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $ib = $plan.identity_binding
    Check 'PRE_FILES' (@(@($script:Chain, $script:SelftestCase, $script:PlanPath, $script:RowsPath,
                          $script:NeedlesPath) | Where-Object { -not (Test-Path -LiteralPath $_) }).Count -eq 0) `
          '执行器/自检/计划/行表/needles 五件齐全' | Out-Null
    Check 'PRE_COMMITS_SHAPE' ($ib.software_input_commit -match '^[0-9a-f]{40}$' -and
                               $ib.tested_image_build_commit -match '^[0-9a-f]{40}$' -and
                               $ib.reviewed_delivery_commit -match '^[0-9a-f]{40}$')
          ("sw=$($ib.software_input_commit) img_build=$($ib.tested_image_build_commit)") | Out-Null
    $neg = @($plan.quota.PSObject.Properties | Where-Object { $_.Value -lt 0 })
    Check 'PRE_QUOTA_INTEGERS' ($neg.Count -eq 0)
          ("keys=$($plan.quota.PSObject.Properties.Name.Count) negative=$($neg.Count)") | Out-Null

    $imgFull = Join-Path $root ($ib.image.path -replace '/', '\')
    if (Test-Path -LiteralPath $imgFull) {
        $sha = (Get-FileHash -LiteralPath $imgFull -Algorithm SHA256).Hash.ToLower()
        $len = (Get-Item -LiteralPath $imgFull).Length
        Check 'PRE_IMAGE_RECALC_EQ_PLAN' ($sha -eq $ib.image.sha256 -and $len -eq [int]$ib.image.bytes) `
              ("bytes=$len sha=$($sha.Substring(0,16))") | Out-Null
        $man = Get-Content -LiteralPath (Join-Path $root ($ib.image.manifest -replace '/', '\')) `
              -Raw -Encoding UTF8 | ConvertFrom-Json
        $imgEntry = $null
        foreach ($t in $man.targets) {
            foreach ($n in $t.images.PSObject.Properties) {
                if ($n.Name -eq (Split-Path $ib.image.path -Leaf)) { $imgEntry = $n.Value }
            }
        }
        Check 'PRE_IMAGE_EQ_MANIFEST' ($imgEntry -and $imgEntry.sha256 -eq $sha -and [int]$imgEntry.bytes -eq $len) `
              ("manifest_bytes=$($imgEntry.bytes) manifest_sha=$(($imgEntry.sha256).Substring(0,16))") | Out-Null
        Check 'PRE_MANIFEST_CODE_COMMIT' ($man.code_commit -eq $ib.tested_image_build_commit) `
              ("manifest.code_commit=$($man.code_commit)") | Out-Null
    } else {
        Check 'PRE_IMAGE_RECALC_EQ_PLAN' $false "IMAGE_ABSENT:$($ib.image.path)" | Out-Null
    }

    foreach ($f in @('software_input_commit', 'tested_image_build_commit', 'reviewed_delivery_commit')) {
        $c = $ib.$f
        $o = (& git -C $root rev-parse "$($c)^{commit}") 2>$null
        Check ("PRE_COMMIT_EXISTS_" + $f) ($LASTEXITCODE -eq 0 -and $o -eq $c) "rev-parse=$o want=$c" | Out-Null
    }
    $prodRel = $ib.build_input_proof.product_file_changed_this_round -replace '/', '\'
    $prodFull = Join-Path $root (Join-Path $script:BspRel $prodRel)
    $prodSha = (Get-FileHash -LiteralPath $prodFull -Algorithm SHA256).Hash.ToLower()
    Check 'PRE_PRODUCT_SHA_EQ_PATCH_OUTPUT' ($prodSha -eq $ib.build_input_proof.product_file_sha256) `
          "$prodRel sha=$($prodSha.Substring(0,16))" | Out-Null

    $refs = Get-RowsProvenanceFiles $rows
    $miss = @($refs | Where-Object { -not (Test-Path -LiteralPath (Join-Path $root ($_ -replace '/', '\'))) })
    Check 'PRE_ROWS_PROVENANCE_FILES' (@($refs).Count -ge 10 -and $miss.Count -eq 0) `
          ("refs=$(($refs).Count) missing=$($miss.Count) $(($miss | Select-Object -First 3) -join ',')") | Out-Null

    $v1 = Invoke-Py @($script:Chain, '--check-static-port-discipline')
    Check 'PRE_STATIC_PORT_DISCIPLINE' ($v1.Rc -eq 0) (($v1.Lines -join ' ') -replace '^.{0,0}', '') | Out-Null
    $v2 = Invoke-Py @($script:Chain, '--prove-needles', $script:NeedlesPath, '--source-root',
                      (Join-Path $root $script:BspRel))
    Check 'PRE_NEEDLES' ($v2.Rc -eq 0 -and (($v2.Lines -join ' ') -notmatch 'unproven=[1-9]')) `
          (($v2.Lines | Select-Object -Last 1) -join ' ') | Out-Null
    $v3 = Invoke-Py @($script:Chain, '--validate-plan', '--plan', $script:PlanPath, '--repo-root', $root)
    Check 'PRE_VALIDATE_PLAN' ($v3.Rc -eq 0) (($v3.Lines | Select-Object -Last 1) -join ' ') | Out-Null

    $summary = [ordered]@{ run_id = $script:RunId; entry = 'Precheck'; checks = $script:Checks;
                           failures = @($script:Failures); hardware_actions = @();
                           note = '只读: 未开端口、未导入 pyserial、未调用任何下载/复位/回读工具' }
    Write-EvidenceNew 'precheck_summary.json' ($summary | ConvertTo-Json -Depth 6) | Out-Null
    Say ('PRECHECK_SUMMARY|checks={0}|failures={1}' -f $script:Checks, $script:Failures.Count)
    Flush-Transcript
    if ($script:Failures.Count) { exit 1 }
    exit 0
}

# --------------------------------------------------------------------------- 入口: -RunField
if ($RunField) {
    $root = Get-RepoRootOrThrow
    if ($Transport -eq 'real' -and (-not $ApprovalToken -or -not $UidFile)) {
        Say 'RUNFIELD_REFUSED|real_transport_needs_approval_token_and_uid_file'
        exit 4
    }
    $profiles = if ($Profile -eq 'all') { @('healthy', 'trip', 'no_trigger') } else { @($Profile) }
    foreach ($pf in $profiles) {
        $v = Invoke-Dispatch $pf $Transport $Backend ("run-$Backend") $script:PlanPath `
                             $ApprovalToken $ExpectedToken $UidFile
        Say ('PROFILE|' + $pf + '|status=' + $v.Status + '|executed=' + $v.Executed +
             '|not_run=' + $v.NotRunCount)
        foreach ($s in $v.Stages) { Say ('  ' + $s) }
        if ($v.Recorded) { Say ('  ' + ($v.Recorded -replace '^\s*', '')) }
        Assert-DispatchOk $v $pf | Out-Null
        if ($Transport -eq 'stub') {
            # 假端口/假动作后端的通过只证明离线控制流: 这里显式声明它**不是**设备结论
            Say ('FIELD_PROOF|' + $pf + '|NO(transport=stub simulated_bytes_never_reach_real_identity_gate)')
        }
    }
    $summary = [ordered]@{ run_id = $script:RunId; entry = 'RunField'; transport = $Transport;
                           backend = $Backend; profiles = $profiles; checks = $script:Checks;
                           failures = @($script:Failures)
                           hardware_actions = @() }
    Write-EvidenceNew 'runfield_summary.json' ($summary | ConvertTo-Json -Depth 6) | Out-Null
    Say ('RUNFIELD_SUMMARY|checks={0}|failures={1}|transport={2}|backend={3}|hardware_actions=0' -f `
         $script:Checks, $script:Failures.Count, $Transport, $Backend)
    Flush-Transcript
    if ($script:Failures.Count) { exit 1 }
    exit 0
}

# --------------------------------------------------------------------------- 入口: -SelfTest
Assert-ParseFile (Join-Path $script:Self 'M8R3Field.ps1') 'ST_PARSE_FILE_SELF' | Out-Null

foreach ($pf in @('healthy', 'trip', 'no_trigger')) {
    $v = Invoke-Dispatch $pf 'stub' 'sim' 'stub' $script:PlanPath '' '' ''
    $exp = if ($pf -eq 'trip') { 12 } else { 9 }
    Assert-DispatchOk $v ('ST_' + $pf) | Out-Null
    Check ("ST_" + $pf + "_STAGE_COUNT") ($v.Executed -eq $exp -and $v.NotRunCount -eq 0)
          ("executed=$($v.Executed) expected=$exp not_run=$($v.NotRunCount)") | Out-Null
    Check ("ST_" + $pf + "_RECORDED_IDENTITY") ($v.Recorded -match '"id":\s*"7"' -and
                                                $v.Recorded -match '"flags":\s*"0010"' -and
                                                $v.Recorded -match '"match":\s*"YES"')
          (($v.Recorded -replace '^.*"recorded": ', '') -replace '"results.*', '') | Out-Null
    if ($pf -eq 'trip') {
        Check 'ST_TRIP_ARM_REFUSAL_IS_NAMED' (@($v.Stages | Where-Object {
              $_ -match 'stage=S10\|status=REFUSED' }).Count -eq 1)
              '锁存态再武装在 S10 具名拒绝(计划申报的 expected_terminal), 该会话到此结束' | Out-Null
        Check 'ST_TRIP_FORENSICS_RAN' (@($v.Stages | Where-Object {
              $_ -match 'stage=S9\|status=OK' }).Count -eq 1)
              'S9 按实际新增 session 取证(增大不成立即 SESSION_NOT_INCREASED)' | Out-Null
    }
    Say ('FIELD_PROOF|' + $pf + '|NO(transport=stub backend=sim simulated_bytes_never_reach_real_identity_gate)')
}

# 执行器侧只读门
$v1 = Invoke-Py @($script:Chain, '--check-static-port-discipline')
Check 'ST_STATIC_PORT_DISCIPLINE' ($v1.Rc -eq 0) (($v1.Lines -join ' ')) | Out-Null
$v2 = Invoke-Py @($script:Chain, '--prove-needles', $script:NeedlesPath, '--source-root',
                  (Join-Path (Get-RepoRootOrThrow) $script:BspRel))
Check 'ST_NEEDLES_PROVEN' ($v2.Rc -eq 0) (($v2.Lines | Select-Object -Last 1) -join ' ') | Out-Null
$v3 = Invoke-Py @($script:Chain, '--validate-plan', '--plan', $script:PlanPath,
                  '--repo-root', (Get-RepoRootOrThrow))
Check 'ST_VALIDATE_PLAN' ($v3.Rc -eq 0) (($v3.Lines | Select-Object -Last 1) -join ' ') | Out-Null

# Python 场景自检(55 条, 含 7 项定向检出)
$st = Invoke-Py @($script:SelftestCase)
$sumLine = ($st.Lines | Where-Object { $_ -like 'SUMMARY|*' } | Select-Object -Last 1)
$okSt = ($st.Rc -eq 0 -and $sumLine -match 'fail=0' -and $sumLine -match 'preserved_r2_identities=25' `
         -and $sumLine -match 'negative_injections=[1-9]')
Check 'ST_PYTHON_SELFTEST' $okSt ($sumLine -join ' ') | Out-Null
Write-EvidenceNew 'console_python_selftest.txt' (@($st.Lines) + @('STDERR>') + @($st.Err)) | Out-Null
$failRows = @($st.Lines | Where-Object { $_ -match '\|FAIL\|' })
Check 'ST_PYTHON_NO_FAIL_ROWS' ($failRows.Count -eq 0) ("fail_rows=$($failRows.Count)") | Out-Null

# 负控 1: 真传输但没令牌/没 UID => 执行器必须拒, 一条命令都不发
$nv = Invoke-Dispatch 'healthy' 'real' 'real' 'neg-no-token' $script:PlanPath '' '' ''
$refText = ($nv.RefusedText -replace '\s+', ' ')
if ($refText.Length -gt 90) { $refText = $refText.Substring(0, 90) }
Check 'ST_NEG_REAL_WITHOUT_TOKEN' ($nv.Rc -ne 0 -and $nv.Stages.Count -eq 0 -and
                                   $nv.RefusedText -match 'REAL_TRANSPORT_REFUSED')
      ('rc=' + $nv.Rc + ' stages=' + $nv.Stages.Count + ' text=' + $refText) | Out-Null

# 负控 2: 把某个阶段的判据改成不可能成立 => 该阶段 STOP, 后续一律 NOT_RUN(不被算成已执行)
$negdir = Join-Path (Get-FieldOutRoot) 'neg-stagemutate'
New-Item -ItemType Directory -Path $negdir -Force | Out-Null
$raw = [System.IO.File]::ReadAllText($script:PlanPath, [System.Text.Encoding]::UTF8)
$hits = ([regex]::Matches($raw, '"r":\s*"OK"')).Count
$mut = $raw -replace '"r":\s*"OK"', '"r": "PS_MUTANT_IMPOSSIBLE"'
Check 'ST_NEG_MUTATE_ANCHOR_ONCE' ($hits -eq 1 -and $mut -match 'PS_MUTANT_IMPOSSIBLE') `
      ("anchor_hits=$hits") | Out-Null
[System.IO.File]::WriteAllText((Join-Path $negdir 'plan.json'), $mut, (New-Object System.Text.UTF8Encoding($false)))
Copy-Item -LiteralPath $script:RowsPath -Destination (Join-Path $negdir 'rows.json') -Force
$nv2 = Invoke-Dispatch 'healthy' 'stub' 'sim' 'stagemutate' (Join-Path $negdir 'plan.json') '' '' ''
Check 'ST_NEG_STAGE_FAILURE_HALTS' ($nv2.Rc -ne 0 -and $nv2.Status -eq 'STOP' -and
                                    $nv2.NotRunCount -gt 0 -and $nv2.Executed -lt 12)
      ("rc=$($nv2.Rc) status=$($nv2.Status) executed=$($nv2.Executed) not_run=$($nv2.NotRunCount)") | Out-Null

$summary = [ordered]@{ run_id = $script:RunId; entry = 'SelfTest'; checks = $script:Checks;
                       failures = @($script:Failures); hardware_actions = @();
                       python_selftest_summary = $sumLine }
Write-EvidenceNew 'selftest_summary.json' ($summary | ConvertTo-Json -Depth 6) | Out-Null
Say ('SELFTEST_SUMMARY|checks={0}|failures={1}|python={2}' -f $script:Checks,
     $script:Failures.Count, $sumLine)
Flush-Transcript
if ($script:Failures.Count) { exit 1 }
exit 0
