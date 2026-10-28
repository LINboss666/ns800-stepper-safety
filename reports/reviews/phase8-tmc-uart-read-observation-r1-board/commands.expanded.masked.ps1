# commands.expanded.masked.ps1 - masked public copy of the private runner (lossless=false)
# Replacement rules (declared, string-level):
#   <DRIVE>ns800-r1-board            -> <LOCAL-ROOT>
#   <DRIVE>ns800                       -> <REPO-ROOT>
#   <DRIVE>Keil_v5                       -> <TOOL-ROOT>
#   <DRIVE>Users\<user-name>\AppData\Local\Programs -> <TOOL-ROOT>
#   any remaining drive-qualified path token -> <LOCAL-PATH-MASKED>
#   DAP UID value never appears here (private file, mode 600, out of repo)
# Original with real paths stays out of repo at <LOCAL-ROOT>/logs/s1_s6_commands.ps1;
# the two files are line-for-line identical apart from the substitutions above.
# This copy exists for review of parameters and order only. Without the user's approval of
# the whole package, no step in it may be executed.
# s1_s6_commands.ps1 —— URO-R1 板级包的展开命令(脱敏公开副本: 路径已代号化, UID 值不出现)。
# 本文件只承载"已绑定好的参数与顺序"; 未获用户对整包的批准前不得运行任何设备步骤。
# 每一步都重新直算散列(不缓存上一步的结论), 判据不满足即 throw, 不自动续跑。
# 用法: powershell -NoProfile -ExecutionPolicy Bypass -File s1_s6_commands.ps1 -Step <Precheck|S2|S3|S4|S5>
#       -Step Precheck 只做本地只读核对, 零设备动作。
[CmdletBinding()]
param(
    [ValidateSet('Precheck', 'S2', 'S3', 'S4', 'S5')]
    [string]$Step = 'Precheck',
    [string]$ApprovalReference = ''
)
$ErrorActionPreference = 'Stop'

# ---- §6 变量绑定(本地已知路径) ----
$Uv4Exe        = '<TOOL-ROOT>\UV4\UV4.exe'
$PythonExe     = '<TOOL-ROOT>\Python\Python312\python.exe'
$TaskRoot      = '<LOCAL-ROOT>'
$DownloadProject = '<LOCAL-ROOT>\download-r1\download-b.uvprojx'
$PayloadAxf      = '<LOCAL-ROOT>\download-r1\dl\rt-thread-a0.axf'
$FrozenCollector = '<REPO-ROOT>\reports\reviews\phase8-tmc-uart-read-observation-r1\UARTReadDiagSerialR1.ps1'
$BinAccepted   = '<REPO-ROOT>\reports\firmware\phase8-tmc-uart-read-observation-r1\measure-a0.bin'
$Manifest      = '<REPO-ROOT>\reports\firmware\phase8-tmc-uart-read-observation-r1\build_manifest.json'
$FlashLog      = '<LOCAL-ROOT>\logs\s2_flash.log'
$ReadbackPath  = '<LOCAL-ROOT>\readback\r1-measure-a0-268340.bin'
$CaptureOutDir = '<LOCAL-ROOT>\capture\r1_run1'          # 必须不存在, 由采集器自己新建
$PyOcdWork     = '<LOCAL-ROOT>\pyocd-work-r1'
$UidPrivate    = '<LOCAL-ROOT>\logs\dap_uid_private.txt'
$ExpectedBanner = '5.3.0 build Oct 28 2026 02:23:31'
$Exp = @{
    axf   = @{ bytes = 1693804; sha = 'a96889c47c9433b132dc4eeecf46be287c13dc1cff11fc0003e6cca10826846f' }
    bin   = @{ bytes = 268340;  sha = 'a48400c881c539db539876cfd38f3aceac2a78cc7ed0d0f9655c7edafbb19a1e' }
    ps1   = @{ bytes = 93797;   sha = '2e9c8ede8f75e7b513dbd4f2c3091189309454879e98404ba7ec64bbb80f2568' }
    mani  = @{ bytes = 16057;   sha = '075bd3ff6eace662a8f4809a4b5810eeddd04997a77f590b2bbef0519db5d2e3' }
}
$ReadbackLenHex = '0x41834'

function Get-Sha1([string]$Path) {
    if (-not (Test-Path $Path -PathType Leaf)) { throw ('缺失文件: ' + $Path) }
    return (Get-FileHash -Algorithm SHA256 $Path).Hash.ToLowerInvariant()
}
function Assert-Identity([string]$Name, [string]$Path, $Want) {
    $len = (Get-Item $Path).Length
    $got = Get-Sha1 $Path
    if ($len -ne $Want.bytes -or $got -cne $Want.sha) {
        throw ($Name + ' 身份不符: bytes=' + $len + '(期望 ' + $Want.bytes + ') sha=' + $got + '(期望 ' + $Want.sha + ')')
    }
    Write-Output ($Name + ' 身份一致 bytes=' + $len + ' sha256=' + $got)
}
function Read-DapUid() {
    if (-not (Test-Path $UidPrivate -PathType Leaf)) { throw '缺少 DAP UID 私有依据文件' }
    $line = Select-String -Path $UidPrivate -Pattern '^dap_uid=([0-9A-Fa-f]{20,64})$' | Select-Object -First 1
    if (-not $line) { throw 'DAP UID 私有依据文件形状不符(不猜、不枚举探针)' }
    return $line.Matches[0].Groups[1].Value
}

# ---- 所有步骤共用的只读前置 ----
Assert-Identity '待下载 BIN' $BinAccepted $Exp.bin
Assert-Identity '构建 manifest' $Manifest $Exp.mani
Assert-Identity '冻结采集器' $FrozenCollector $Exp.ps1
Assert-Identity '下载副本载荷 AXF' $PayloadAxf $Exp.axf
foreach ($d in @($PyOcdWork)) {
    $stray = @(Get-ChildItem -Force $d | Where-Object { $_.Name -in @('pyocd.yaml', 'pyocd.yml', 'pyocd_user.py') })
    if ($stray.Count) { throw ('pyOCD 工作目录存在隐式配置: ' + ($stray.Name -join ',')) }
}
Write-Output ('pyOCD 工作目录无隐式配置: ' + $PyOcdWork)

switch ($Step) {
    'Precheck' {
        Write-Output 'Precheck: 只读身份/目录核对完成, 零设备动作。'
        Write-Output ('下载工程存在=' + (Test-Path $DownloadProject -PathType Leaf) +
                      ' 采集 OutDir 尚未创建=' + (-not (Test-Path $CaptureOutDir)) +
                      ' 回读文件尚未创建=' + (-not (Test-Path $ReadbackPath)))
        break
    }
    'S2' {
        # 一次 Keil 下载(既有流程, 含工具自身正常初始化行为)。UV4 rc 不单独作判据。
        if ($ApprovalReference -eq '') { throw 'S2 需要 -ApprovalReference 绑定用户实际批准原文' }
        & $Uv4Exe -f $DownloadProject -t rt-thread -j0 -o $FlashLog
        $log = Get-Content -Raw $FlashLog
        foreach ($p in @('Load "' + $PayloadAxf.Replace('\', '\\'), 'Erase Done.', 'Programming Done.', 'Verify OK.')) {
            if ($log -notlike ('*' + $p + '*')) { throw ('下载日志缺少判据: ' + $p) }
        }
        if ($log -match '(mass\s*erase|unlock|option\s*bytes|OTP)') { throw '下载日志出现越界操作字样, 立即停' }
        Write-Output 'S2 通过: 实际 Load 本轮新副本载荷 + Erase/Programming/Verify OK。'
        break
    }
    'S3' {
        # pyOCD 会话 #1: 唯一定长回读(0x08000000 起 0x41834 = 268340 B)。Halted, 不恢复。
        $DapUid = Read-DapUid
        if (Test-Path $ReadbackPath) { throw ('回读目标是本轮新文件, 已存在即停: ' + $ReadbackPath) }
        $ReadbackForward = $ReadbackPath.Replace('\', '/')
        Push-Location $PyOcdWork
        try {
            $ReadArgs = @(
                '-m', 'pyocd', 'commander', '--no-config',
                '-t', 'cortex_m', '-u', $DapUid, '-f', '1000000',
                '-O', 'auto_unlock=false',
                '-O', 'connect_mode=halt',
                '-O', 'resume_on_disconnect=false',
                '-O', 'cache.enable_memory=false',
                '-O', 'cache.read_code_from_elf=false',
                '-O', 'scan_all_aps=false',
                '-c', 'status',
                '-c', ('savemem 0x08000000 ' + $ReadbackLenHex + ' ' + $ReadbackForward),
                '-c', 'status',
                '-c', 'exit'
            )
            & $PythonExe @ReadArgs *>&1 | Tee-Object -FilePath '<LOCAL-ROOT>\logs\s3_readback_pyocd.log'
        } finally { Pop-Location }
        if (-not (Test-Path $ReadbackPath -PathType Leaf)) { throw '回读文件未产生' }
        Assert-Identity '板上回读' $ReadbackPath $Exp.bin
        $cmp = @(Compare-Object (Get-Content $BinAccepted -Encoding Byte -ReadCount 0) (Get-Content $ReadbackPath -Encoding Byte -ReadCount 0))
        if ($cmp.Count) { throw ('回读与接受 BIN 逐字节比对差异 ' + $cmp.Count + ' 处') }
        Write-Output 'S3 通过: 长度/SHA/逐字节 cmp 一致 => 本次 ImageContinuityConfirmed 的依据成立。'
        break
    }
    'S4' {
        # pyOCD 会话 #2: 开串口前唯一一次显式 SYSRESETREQ(取代旧 board_candidate 的 S6 条件复位)。
        $DapUid = Read-DapUid
        Push-Location $PyOcdWork
        try {
            $StartArgs = @(
                '-m', 'pyocd', 'commander', '--no-config',
                '-t', 'cortex_m', '-u', $DapUid, '-f', '1000000',
                '-O', 'auto_unlock=false',
                '-O', 'connect_mode=attach',
                '-O', 'resume_on_disconnect=false',
                '-O', 'cache.enable_memory=false',
                '-O', 'cache.read_code_from_elf=false',
                '-O', 'scan_all_aps=false',
                '-c', 'reset sysresetreq',
                '-c', 'status',
                '-c', 'exit'
            )
            & $PythonExe @StartArgs *>&1 | Tee-Object -FilePath '<LOCAL-ROOT>\logs\s4_reset_pyocd.log'
        } finally { Pop-Location }
        $rl = Get-Content -Raw '<LOCAL-ROOT>\logs\s4_reset_pyocd.log'
        if ($rl -notmatch 'Resetting target' -or $rl -notmatch 'Running') { throw 'S4 未实证复位与 Running' }
        if ($rl -match '(?i)error|traceback|failed') { throw 'S4 原始输出含真实错误, 不追加 resume/reset, 不建第三次 DAP' }
        Write-Output 'S4 通过: 复位 + Running 已由原文实证。此后才允许启动采集器。'
        break
    }
    'S5' {
        # 唯一一次 COM5 打开由冻结采集器自己完成; 不先用串口助手/其它程序试开。
        if ($ApprovalReference -eq '') { throw 'S5 需要 -ApprovalReference 绑定用户实际批准原文' }
        if (Test-Path $CaptureOutDir) { throw ('OutDir 必须是全新路径, 已存在即停: ' + $CaptureOutDir) }
        & 'powershell.exe' -NoProfile -ExecutionPolicy Bypass `
            -File $FrozenCollector `
            -ExecuteApprovedR1ReadDiag `
            -OutDir $CaptureOutDir `
            -ApprovalSource $ApprovalReference `
            -ExpectedBuildBanner $ExpectedBanner `
            -ImageContinuityConfirmed `
            -SiteConfirmed
        Write-Output ('采集器 rc=' + $LASTEXITCODE + ' (台账/原字节在 ' + $CaptureOutDir + ')')
        break
    }
}
