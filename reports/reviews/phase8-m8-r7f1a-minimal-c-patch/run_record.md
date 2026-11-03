# M8-R7F1-A 最小补丁跟修轮 · 执行记录

日期：2026-11-03。基线：`feature/m8-sg-adc-relative-protection` @ `adeeedc`（上一轮 R7F1 交付头）。
输入：云端 A 的 `NS800_R7F1_A_Review_and_Minimal_C_Patch_20261103.zip`
（zip 原字节 sha256 `4d5f4664c51468ea957f90f10e547653fe32bd987b9ce2ed3a082b798f9b6b1b`，
裁定=保留 R7F1 的 C 修复，只做 §1/§2 两项小改后再构建；该复审件本身不含硬件授权）。
设备动作：**0**。本文件不含任何硬件批准，也不代替操作者回答现场安全门。

## 1. 做了什么

| 项 | 内容 | 来源 |
|---|---|---|
| §1 | `rtconfig.h:118` `RT_MAIN_THREAD_STACK_SIZE` 2048 → **4096** | A 的补丁 `host/reviewer/R7F1_A_minimal_followup.patch` |
| §1+ | `.config:194` `CONFIG_RT_MAIN_THREAD_STACK_SIZE` 2048 → **4096** | 操作者本轮指令（"上一份补丁漏了同步 .config"），**不在 A 的补丁里**，单独一次单行替换 |
| §2 | `tmc2209.c` 四个提前返回各补 `st->used = rt_tick_get() - b->start`（1123 BAD_CONTEXT / 1147 静止门 / 1156 懒打开失败 / 1182 取锁失败）；`tmc_budget_open` 失败那条（:1104）不写，不读未初始化起点 | 同一补丁 |
| 步骤1 | `leaf_main.c`：BTB06 断言 `st.used == 本次夹具推进 tick` 且 `== TMC_BUD_XFER_LOCK_MS(20)`；新增 BTB11/BTB12 覆盖两条 `NO_DEVICE` 提前返回；保留 reason/rc/零 UART 字节断言 | 复审步骤 1 |
| 步骤1 | `run_stop_priority_s2.sh`：`G_tmc_boot_*` 追加 BTB11/BTB12，`S2_EXPECT_target` 68→70、`host_ovr` 143→145（BTB01..BTB10 的 id 与数量逐字未动） | 台账同步 |
| 步骤3 | 单目标 `Rebuild All` 一次并归档 BIN/AXF/MAP/调用图/日志 | 复审步骤 3 |

补丁是原样 `git apply`（先 `--check` 通过） applied 在 adeeedc 之上；`.config` 用字节级单行替换（CRLF 原样，前后同为 52103 B），没有重复套整份补丁。

## 2. `.config` 同步的机械核对（为什么这一步不是多余）

- `RT_USING_HEAP` 下主线程栈走 `rt_thread_create`（`src/components.c:218-221`），静态数组那条在
  `#ifndef RT_USING_HEAP`（`:172-177`）里 —— 所以只改 `rtconfig.h` 就能生效于本轮镜像；
  但 `.config` 是 menuconfig 的真源，留着 2048 的话下一次 `scons --menuconfig` 会把 `rtconfig.h` 生成回去。
- 同步后逐条比对：`rtconfig.h` 中能在 `.config` 找到同名 `CONFIG_` 的**带值项 21 个，值不一致 0 个**；
  `RT_MAIN_THREAD_STACK_SIZE=4096`、`RT_CONSOLEBUF_SIZE=128` 两侧一致。
- 未动任何其它 CONFIG 项。

## 3. used 断言不是空转：修前/修后同输入对照

把 `SRC_APP` 指到仓外的 adeeedc `tmc2209.c` 快照，只跑 `tmc_boot`（`host/r7f1a_btb_prepatch.txt`）：

```
target   /tmc_boot | NOT-PASS 判FAIL=1 必需=12/12
host_ovr /tmc_boot | NOT-PASS 判FAIL=1 必需=12/12
CASE|BTB06-...|FAIL|rc=-23 reason=lock-busy(4) tx=0 rx=0 flush=0 used=0 本次夹具推进=20 tick(必须相等且等满 20) ...
S2 RUNNER RESULT: FAIL (rc=1)
```

修后同一套（`host/r7f1a_s2_full.txt`、`host/tmc_boot_target_out.txt`）：`used=20 本次夹具推进=20` PASS，
BTB11 `rc=-12 used=0 推进=0`、BTB12 `rc=-255 used=0 推进=0 open 注入剩余=0` 均 PASS。

如实登记一处机制更正：包装函数 `tmc2209_read_register_stat()` 先把局部结构 `memset` 成 0 再回拷，
所以修前 `used` 读到的是 **0，不是 0xA5 残值**（A 在自己环境里看到的也是 0）。因此真正能当突变锚点的
只有 BTB06（这一路确实等了 20 tick）；BTB07/BTB11/BTB12 三条不等锁，修前修后都是 0，它们的增量是
"这条分支第一次有了自己的用例身份（rc/reason/零线上字节/零取锁）"，不是 used。这一点写进了
`leaf_main.c` 的注释，不留在口头。

## 4. 本轮跑的链

| 链 | 结果 | 证据 |
|---|---|---|
| S2 全量（22 组 × 2 变体） | `PASS (rc=0)`，target=70 / host_ovr=145，新两组真执行无 SKIP | `host/r7f1a_s2_full.txt` |
| S2 所选 3 组（tmc_boot/read_obs/boot_print） | `PASS (rc=0)`，用例=47 | `host/r7f1a_s2_targeted.txt` |
| V1/V2（真 `tmc2209.c` + `motor.c`） | `RUNNER RESULT: PASS (rc=0)` | `host/r7f1a_v1v2.txt` |
| line_width_check | `LINE_WIDTH_RESULT PASS (bad=0)` | `host/r7f1a_lwc.txt` |
| 修前对照（SRC_APP 快照） | 按预期 FAIL，且只 FAIL 在被测缺陷上 | `host/r7f1a_btb_prepatch.txt` |
| A 自己的独立复跑 | 原件 90/90、补丁件 80/80（tmc_boot+read_obs 两变体），SD 因 Linux 无 windows.h 未计通过 | `host/reviewer/` |

未重跑 SD/IO/S1/M8 及历史所有组，理由见 manifest `host_tests.not_rerun_and_why`（本轮产品改动只落在
`tmc2209.c` 的四个提前返回与两个配置常量，这三条链不编真实读函数，主机链也不读 `rtconfig.h`）。

## 5. 构建

```
C:\Keil_v5\UV4\UV4.exe -r project.uvprojx -t rt-thread-motor-m1-candidate -j0 -o
    D:/ns800/reports/reviews/phase8-m8-r7f1a-minimal-c-patch/build/rt-thread-motor-r7f1a.log
UV4_EXIT=0   0 Error(s), 0 Warning(s).   Build Time Elapsed: 00:00:10
Program Size: Code=205922 RO-data=86186 RW-data=2192 ZI-data=85596
```

- 对 adeeedc 那枚：`Code +56 / RO +0 / RW +0 / ZI +0`；`BIN 292628 → 292684`（+56 = Code 增量）。
- **main 栈 2048→4096 一点静态 RAM 都没动**：堆分配路径（`RT_USING_HEAP`）使 ZI 不变，代价是运行时堆 +2048 B；
  堆区间固定 `board.h:30-31` = `0x8000` = 32768 B。堆峰值离线测不到，登记为现场可见事实。
- 新镜像：BIN `b4243886818b6b2cf2e30d10864272225e1b0e66c8435c8a50bb449053e153bf`（292684 B）；
  AXF `ea4f8dd1f337796fe824cc262b8fc27f13920a7c260d1a27737b065369124d16`；
  MAP `757701a509e659fcc00df55309920455badb15434e34867a3bc9f0ec34100f53`；
  调用图 `f97857ab587a8db2b881f1c9dae58e5b723c9b40f4c7f9d4d166f06a8fcd38b1`。四件入库副本 = 构建输出本体（manifest 里逐条 true）。

## 6. 栈结论（换根后的数，取自入库的两份调用图 htm，未重编）

- `main_thread_entry` Max Depth = **2076 B**（adeeedc 与本轮**同为 2076**，差 0）；
  链 = `main_thread_entry → $Super$$main → supervisor_boot → project_config_boot → … → ns_flash_read`。
- 修前 2076 / 预算 2048 = **超出 28 B**（A 复审 §1 的结论，本轮以入库件复算确认）；
  修后 2076 / 预算 4096 = **余量 2020 B**。
- `safety_startup_selftest` 子树 1220 B、`sensor_thread_entry` 1332 B、`system_selftest` 1340 B 两版全等（差 0）。
- 边界：Max Depth 是 armlink 静态下界（不解析函数指针、不含异常/ISR 帧与上下文保存），"余量 2020 B"不等于"不会溢出"。

## 7. 上一轮两处说法作废（按复审要求更正，旧文件不改写字节）

上一轮已入库的 `phase8-m8-r7f1-startup-c-fix/` 里，`run_record.md` §3 与交接件中的这两条**不再成立**，
以本件与 `reports/agent_handoffs/phase8-m8-r7f1a-minimal-c-patch.md` 为准；旧文件保持原字节是故意的
（它们的 SHA256SUMS 与 manifest 绑的就是那些字节，改了清单就自证失效）：

1. "main 栈瓶颈在 supervisor_boot 的 SPI 线 2020 B，余量只有 28 B" —— 错在漏算 `main_thread_entry`
   与 `$Super$$main` 两层调用者，正确根是 `main_thread_entry`，值 2076 B。
2. "sensor 在 stage 7–11 已经半帧进入 SG，所以门挡不住在途读" —— **不适用于冷启动**：门在静态初始化时
   就关闭，先于 sensor 线程创建。保留的那条限制只有"运行中 `system_selftest` 重入不受门约束"。
3. "`[MOT]   注:` 行实测 126 B，在 127 线内但丢换行" —— 后半句错。该源码字面量含换行共 126 B，
   格式化容量 127 B 足以容纳它加终止 NUL，发送长度就是 126，不会因为这里产生裸 NUL 或丢换行；
   它超过本轮 120 B 目标，但不是"已截断"，也不属于任务书三条修复对象。
