# Phase 8-F2d-R4 交接记录（Agent → 独立复审）

日期：2026-10-19（北京时间，跨零点）。分支 `phase8/tmc-drive-config`。
基线 `8edfd8b86d7793230fb7bcdf448b1211a0632ef5`（F2d-R3，远端回读一致）。最终提交号只出现在推送后的交付消息里：本报告在提交之前生成。
交付方式：GitHub 直接审查，**不生成 ZIP**。

范围严格限定在 F2d-R3 复审 §4（R3-NEW-01/P1）与 §5（R3-NEW-02/P2）：只动主机验证工具的路径处理、
哈希清单工具、`tests/host` 说明、R4 审查文本与阶段文档。**`applications/` 与 `project.uvprojx`
相对基线差异为空**；三道宏仍 `RT_FALSE`；未烧录、未复位、未操作 COM5、未写真实 TMC、未接电机、
未输出 STEP。

## R3-NEW-01：工作目录保护前置

- 三个入口（normal / negative / meta）在任何 `rm` / `mkdir` / 输出重定向 / 编译**之前**调用
  `env.sh::require_outside_repo`：路径规范化（`cygpath` + `realpath`，处理 `.`/`..`，Windows
  大小写不敏感）后按**目录成分**判断，拒绝空值、文件系统根、仓库根、仓内子目录、以及包住仓库的
  祖先目录；无效覆盖值直接非零退出，不静默改写路径。
- `build_and_run.sh` 默认 `OUT_DIR` 从仓内 `tests/host/_out` 改到仓库外（R3 的 README 声称"一律仓外"
  与默认值不符，属我方文档/实现不一致，已在 BSP README 显式勘误）。
- **只删自己标过的目录**：工作区与每个用例输出目录都写哨兵文件 `.ns800_host_work`；目录存在但没有
  哨兵 => 拒绝 `rm -rf` 并保留内容。该规则不依赖链接可见性。
- 能力边界如实记录：本机 MSYS 把目录软链表现为普通目录（`test -L` 为假，`realpath`/`pwd -P` 均不解析），
  所以"经由链接绕回仓内"不声称被词法守卫覆盖；实际兜底是哨兵式删除。
- 回归 `tests/host/test_path_guard.sh` 只在 `mktemp -d` 新建的一次性沙盒（内含假 git 仓库与 `KEEP`
  标记文件）里跑，断言"拒绝发生在破坏之前"：仓库根、仓内子目录、祖先目录、`..` 落回仓内、文件系统根、
  空值全部被拒且标记文件仍在、目录未被创建；同前缀兄弟目录与含空格路径不被误拒；无哨兵的已存在仓外目录
  不被删除。**没有拿真实项目目录或用户数据试删除保护。**

## R3-NEW-02：哈希清单字节语义

- R3 清单本机自校 11/11，但按远端提交对象复算为 **10/11**，唯一失配 `tests/host/probe_errcodes.c`：
  清单值是工作树 CRLF 字节（1062 B），远端 blob 是 LF（1030 B，实际 `09812e88…`）。
  CRLF 转 LF 后内容一致 —— 不是探针逻辑变化，也不是生产驱动缺陷。
- 新增 `tests/host/make_source_manifest.sh`：清单以 **Git blob 原始字节**为准（先 `git add`，
  从索引 `git cat-file blob ':<path>'` 取），工作树字节不同则**另列带标签的一段**仅供本地对照；
  推送后 `--verify origin/<分支> <清单>` 对远端提交对象逐项复算，索引与远端结果分别报告。
- 撤回 R3 中"probe_errcodes.c 与 R2 原工件逐字节相同"的表述，改为"CRLF 规范化为 LF 后一致"；
  `host_main.c` 与两份 shim 仍是逐字节相同。纠正记录见 `r3_hash_manifest_correction.txt`，
  R3 的历史清单原样保留未删改。

## 原链重跑（入库脚本，判据未放宽）

| 项 | 实际 |
|---|---|
| 正常 V1（三宏 FALSE） | HOST TEST SUMMARY: CHECK_PASS=25 CHECK_FAIL=0 BAD_SELFTEST=0 MISSING_CASE=0 |
| 正常 V2（仅主机覆盖） | HOST TEST SUMMARY: CHECK_PASS=22 CHECK_FAIL=0 BAD_SELFTEST=0 MISSING_CASE=0 |
| 负例+回归 | 行数 13：OK=13 NOT-DETECTED=0 INFRA-FAIL=0 |
| 路径守卫沙盒回归 | PASS=34 FAIL=0 |
| rc 台账样例 | `N1_only_V1_injected_fail	class=OK	runner_proc_rc=1	runner_reported_rc=1	expected_rc=1	V1=RAN/0/1 V2=RAN/0/0` |

| 元负例组 | 判定 |
|---|---|
| M1_blinded_fd7b | META-OK |
| M1_control_unblinded | CONTROL-OK |
| M2_stubbed_compile | META-OK |
| M2_control_real_compiler | CONTROL-OK |
| M3_runner_exit_zero_lost | META-OK |
| M3_control_unmodified | CONTROL-OK |
| M4_selection_semantics | META-OK |
| M5_wrong_reason_guard | META-OK |

M3 出口码丢失原因行：`INFRA-FAIL | N1_only_V1_injected_fail | rc 真实=0 自报=1 期望=1 | V1=RAN/0/1 V2=RAN/0/0 (期望 V1=RAN/0/1 V2=RAN/0/0)  runner.proc_rc:期望=1 实际=0  runner.reported_rc:期望=0 实际=1`

Keil：本轮未重建（固件与 Keil 工程 0 改动），沿用 F2d-R2 原始 0 Error / 0 Warning 日志并在证据里显式标注。

## 失败与偏差（不掩盖）

1. 哨兵规则第一次生效时，把**上一轮遗留、没有哨兵文件**的默认工作区判为他人现场并拒绝删除，
   整链以 rc=4 终止。这是设计行为而非新缺陷；人工确认该目录只含本工具产物后删除，再以新工作区重跑。
2. **M3 场景插线错误（实跑检出）**：被复制到仓外的 runner 副本自己 `git rev-parse` 找不到仓库根，
   在写状态回执之前就退出，于是 M3 拿到的是"缺 `runner_status.txt`"而不是预期的出口码失配。
   元负例框架按"缺矩阵/空矩阵不得记 META-OK"把这条不合格场景判成 `META-LEAK` 且整体非零
   （**没有放行**）——但根因在我方：`run_negatives.sh` 现在显式把 `REPO_ROOT` 传给 runner；
   修好后 M3 稳定给出 `rc 真实=0 自报=1 期望=1` 与两处 runner rc 失配。
3. `test_path_guard.sh` 首版写了 `local a=1 b=$a`，同一语句内引用尚未赋值的变量，在 `set -u` 下报
   `unbound variable`；拆成两条 `local` 后正常。
4. `env.sh` 一次编辑留下悬空 `|| {`（语法错误），守卫函数因此未定义；回归里"拒绝原因必须包含
   GUARD-REJECT"的断言把这种**因意外错误而恰好非零**的假拒绝抓了出来 —— 只看退出码是不够的。
5. 上一轮记下的三个通用陷阱仍然有效：patch 回环的临时目录必须在仓库外；`core.autocrlf` 会造成假失配；
   Windows `tar` 解不开中文名归档。

## 未验证 / 明确不做

真机全部 NOT TESTED（UART 时序与超时、优先级继承、栈高水位、停机时延上界、`rt_pwm_disable` 返回值、
ENN 电压、STEP 波形、真实寄存器读写）；本轮未重建 Keil；经由目录软链/junction 绕回仓内的词法识别在
本机不可见（由哨兵式删除兜住）。RISK-03 / RISK-03a / RISK-04 继续开放。不新增元负例种类，不扩大到
F2e/F3，不修改任何生产行为或硬件宏。

## 下一步

等待 ChatGPT Work 独立复审；通过前不进 F2e/F3。上板取证与三道宏翻转属另一次授权。
