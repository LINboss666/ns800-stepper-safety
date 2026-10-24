# sp_conc_main.c 的构造源（S2 并发/身份夹具）

`tests/host/sp_conc_main.c` 是**自包含的夹具源**，随本轮一起提交、可直接审；本目录三个脚本
是它的生成过程（任务书 §9："修后脚本构造源应可审"）：

| 顺序 | 脚本 | 做什么 |
|---|---|---|
| 1 | `gen_sp_conc_ports.py` | 取 `ps_main.c` 的端口层（到 `#include "stop_priority.c"` 之前）为基础，把"单线程 IPC + 掩码位"整块换成真 pthread 模型：真实阻塞的递归所有权互斥量、全局独占短临界区（等价 PRIMASK 屏蔽期的不可抢占）、被屏蔽期间排队并在窗口结束后由同一线程交付的 ISR、可注入的取锁错误与 event 发送失败 |
| 2 | `gen_sp_conc_hooks.py` | 装四个显式接缝（钩子点）：`rt_pwm_disable`（窗口内，只许入队一次中断）、`permit_confirm_read`、`tmc2209_config_integrity_check_for`、`safety_drv_feedback_confirm`（自检真实经过的慢步骤）；并加序列位/栅栏与 spawn/join |
| 3 | `gen_sp_conc_body.py` | 追加 C01..C21 用例、`CASE|id|PASS|detail` / `SKIP|id|why` / `PRE-FAIL|what` / `SUMMARY|...` 台账与按组 `main(argc, argv)` |

重生成（结果与已提交的 `sp_conc_main.c` 逐字节一致，本机已用 `cmp` 验证）：

```
python tests/host/sp_conc_gen/gen_sp_conc_ports.py
python tests/host/sp_conc_gen/gen_sp_conc_hooks.py
python tests/host/sp_conc_gen/gen_sp_conc_body.py
```

执行入口是 `tests/host/run_stop_priority_s2.sh`（逐组一个进程、逐变体登记必需 id 与允许的
SKIP、墙钟只作死锁守卫）。产品文件不因本夹具而改；夹具需要的端口全部落在
`tests/host/shim_ps/` 与本夹具源内。

行尾：生成器在运行时把**自身内嵌的锚点/替换文本**与目标文本统一按 LF 比对，因此无论 LF 还是
CRLF 检出都能生成同一份夹具（本机对两种检出各验一次，产物逐字节一致）。锚点没命中时会显式
报 `ANCHOR MISSING`，不会静默生成内容缺失的夹具。
