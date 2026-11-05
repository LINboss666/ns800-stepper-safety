# 主机探针说明

四例使用仓库既有 rel_main.c 夹具直接包含当前产品 C。输入为合成帧，虚拟时钟 10 ms；没有任何设备动作。

运行：
`python run_recovery_ramp_probe.py --repo-root <仓库根> --output-dir <仓外输出目录> --cc gcc`

当前结果：cases=4、failures=0、build_rc=0、run_rc=0。GCC 对既有 rel_main.c:351 报 1 条 misleading-indentation，完整输出保留；没有改夹具或加抑制来宣称 0 warning。

Linux 首次编译因既有 shim_rel 无条件包含 windows.h 而失败，first_attempt_probe_build.* 保留该次输出。随后仅提供空的 windows.h 兼容头（该夹具未使用 Win32 API），不改产品、tick、锁或采样合同；Windows 下 runner 使用系统头。

首次能编译后的探针断言有两处初始化错误，initial_probe_* 保留：尚未先进入窗口就设置模拟锁存，首帧窗口计数复位影响观察；单源负例未先建立 ADC 身份，导致初次 ADC 值被当新样本。修订为先正常喂一帧，再建立合成事故并清恢复计数，最终四例通过。这里修的是探针，不是产品代码。

探针只说明引擎回落、事故标志保留及 ramp/CRUISE 的判据行为；没有执行真实 fault_reset，也不据此宣称 Safety 层复核或物理停机验证。

