# M8-R7F1 产品 C 修复轮 · 执行记录

日期：2026-11-03。基线：`feature/m8-sg-adc-relative-protection` @ `ed8d7f4a895ac95e0339bfc7c5eeae72f322c0a6`。
任务书：云端 A 的 `NS800_R7F1_A_Startup_C_Fix_20261103.md`（离线 C 修复 + 一次单目标构建）。
设备动作：**0**（未下载、未回读、未复位、未开串口、未 apply、未 config_save、未起转/学习/保存）。
本文件不含任何硬件批准，也不代替操作者回答现场安全门。

## 1. 六个条目各自落在哪里

| 任务书 | 做了什么 | 判定它的东西 |
|---|---|---|
| §1 用现有 C 设施覆盖启动先后与失败分支 | 在既有 leaf 链（真实 `tmc2209.c` + 真实 UART 传输 + 真 pthread）新增 `tmc_boot` 组 BTB01–BTB10；在既有并发链新增 `boot_print` 组 BPT01–BPT05 | `host/leaf_tmc_boot_target.txt`、`host/leaf_tmc_boot_host_ovr.txt`、`host/conc_boot_print_*.txt`，均在 `host/s2_full_after.txt` 的 22 组台账内 |
| §2 逐调用失败原因 | `tmc2209.h/.c` 暴露 `tmc_xfer_stat_t`(48 B) + `tmc2209_read_register_stat()`；`safety_state.c` 自检必需项④在传输锁释放后的普通线程上下文打两行（rc+reason / tx,rx,flush,used） | BTB06–BTB10（reason 与阶段分离、两次调用互不继承）、BPT04（两行形状）、`build/stack_callgraph_r7f1.txt` §3（帧实测 48 B，没搬 188 B 数组） |
| §3 最小修复启动顺序 | `sensor_service` 的 SG 启动门：默认关闭，只压 SG 这一路的 UART 读；`supervisor.c` 在 stage 11 自检返回后释放（PASS/FAIL 都释放） | SD13a/b/c（只静默 SG、IMU/ADC/快照照跑、关闭帧沿用"非分频帧"语义、释放后恢复分频）+ BTB01/03 的 A/B |
| §4 拆三处已证截断的长打印 | `motor.c` `[MOT] init OK` → 2 行；`safety_state.c` `[SS] STEP:` → 3 行（成功）/4 行（故障）；`[SS] ==> FAULT_LATCHED` → 4 行 | `host/line_width_after.txt`（15 站点，带真实 UTF-8 实参上界）+ `--selfcheck`（4 条修前原样仍命中）+ BPT01–BPT04（动态逐行量宽）+ BPT05（裁剪模型元负控） |
| §5 只跑受影响的链 | S2 全 22 组 × 2 变体、SD、IO、S1、M8、V1/V2、line_width | `host/*_after.txt` 七份 + `host/iterations/` 里的中途失败原文 |
| §6 单目标构建一次 | `UV4 -r -t rt-thread-motor-m1-candidate`，0 Error 0 Warning | `host/../build/rt-thread-motor-r7f1.log`、`reports/firmware/phase8-m8-r7f1-startup-c-fix/build_manifest.json` |

没有新增派生器、就绪门或多层登记脚本；两份检查器（`host/line_width_check.py` 复用 R6 判据函数、`host/gen`类脚本本轮无）只有一份拷贝在 `host/`。

## 2. 构建（本轮唯一的设备无关生产动作）

```
C:\Keil_v5\UV4\UV4.exe -r project.uvprojx -t rt-thread-motor-m1-candidate -j0 -o
    D:/ns800/reports/reviews/phase8-m8-r7f1-startup-c-fix/build/rt-thread-motor-r7f1.log
UV4_EXIT=0
```

- `0 Error(s), 0 Warning(s).` / `Using Compiler 'V6.24' (armclang)` / `Build Time Elapsed: 00:00:12`
- `Program Size: Code=205866 RO-data=86186 RW-data=2192 ZI-data=85596`
- 对照上一轮同目标（R6A，已证在板那枚）：`Code=205594 RO-data=85994 RW-data=2188 ZI-data=85592`
  → **Code +272 / RO +192 / RW +4 / ZI +4**；BIN `292164 → 292628`（+464 = Code+RO，逐字节对账）。
- 归因（两版 map 的 Object Totals 差分，见 `build/stack_callgraph_r7f1.txt` §4）：
  `tmc2209.o` Code+132、`safety_state.o` Code+88 RO+177、`sensor_service.o` Code+28 **RW+4**、
  `motor.o` Code+16 RO+15、`supervisor.o` Code+4、`(incl. Padding)` Code+4 **ZI+4**。
  RW+4 的唯一具名来源是 `.data.s_sg_startup_gated`（4 B）；ZI+4 是链接器填充，没有任何具名 `.bss` 目标变化。
- 新镜像散列（BIN 292628 B）：`e5b51b6b810f215e284ea6317a4b1bef6b3faddc6f1ac9f8947804f261fa5732`。
  **这枚镜像没有在任何硬件上验证过**；板上仍是 R6A 的 `5ae4110d…`，本轮没下载。

## 3. 栈深核对（构建前置，任务书 §2 的"别把 188 B 搬到启动线程栈"）

- `safety_startup_selftest` 子树 Max Depth = **1220 B**（最深链已穿过新的 `tmc2209_read_register_stat`），
  对 `RT_MAIN_THREAD_STACK_SIZE=2048` 静态余量 828 B。反汇编帧 = `PUSH {r4,lr}` + `SUB sp,sp,#0x30` = 56 B。
- `supervisor_boot` 子树 = **2020 B**，最深链是 SPI 配置加载线（`ns_flash_read → rt_spi_send_then_recv → rt_mutex_take`），
  不经过本轮改的 IOIN 读；余量只有 28 B。本轮只构建一次，**没有修前对照数**，
  所以"本轮没让它变紧"只由"那段代码一行未改"支撑，不由实测差分支撑 —— 已在证据文件里这么写。
- Max Depth 是 armlink 的**静态下界**（解析不了函数指针、不含异常/ISR 帧），不得当上界。
- 只读门状态的访问器 `sensor_service_sg_startup_gated()` 在本目标无人调用，被链接器移除；
  门生效靠 `s_collect → s_sg_startup_gated` 那条边（map 已列）。如实登记，不声称两个入口都进了镜像。

## 4. 中途失败原文（不删不改，都在 `host/iterations/`）

`S2` 台账在本轮出现过两类中途红：`s2_btb_v1..v3`（夹具自身计数错：把后台 flush 的那次读算进前台、
`health` 判据写成恒等式）与 `s2_bpt_v2..v8`（窗口行数上限 24 溢出、gcc 变参从右到左求值导致明细里读到 -1/脏值、
`bpt_reset()` 位置让 `wire_and_boot()` 自己的打印污染分支缺失判据、被捕获行的换行打断按行解析的台账）。
另有 `s2_full_v1`（`unused variable 'j2'` → 构建非 0 warning 被 runner 判 INFRA-FAIL rc=4）、
`sd_v1`（SD13c 期望 25 帧后 `fresh_sg=1`，第 25 帧不是分频帧）、`m8_v1`（新默认关门的 18 条 REL 失败）、
`lwc_run/lwc_selfcheck`（FIXED 表漏了 `!! STEP` 两条的第 2/3 个 `%s`，量出 133/96 B 的红）。
全部是夹具/检查器/登记的错，最终定稿见 `host/*_after.txt`；产品侧的修前失败形状由 BTB01/03 的 A/B
与 line_width 的 4 条 KNOWN_TOO_LONG 承担。

## 5. 台账计数

- S2：`S2_EXPECT_target=68`、`S2_EXPECT_host_ovr=143`（新组 `tmc_boot` 10 条、`boot_print` 5 条在两变体都真执行，
  无 SKIP）；派生过程写在 `tests/host/run_stop_priority_s2.sh` 的注释里，未新增登记层。
- SD：`COMMON_CASES` 增 SD13a/b/c。
- 未重跑：历史数千条清单、全部 Python 自检、未触及的运动/学习链（任务书 §5）。
