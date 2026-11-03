# R7F1 A 复审：保留本轮 C 修复，补两处小改后再构建

审核对象：远端 feature/m8-sg-adc-relative-protection 的 adeeedc6f05bf5c9cd0f3b41023b64a53fb134aa（基线 ed8d7f4）。
日期：2026-11-03。此复审及附件没有硬件授权，未产生设备动作。

## 裁定

本轮确实修改了嵌入式产品 C：启动期间仅挡后台 SG 读取，首次启动自检返回后释放；自检获得本次调用自己的 TMC 失败原因；三处已证截断的打印已拆短。这些改动保留，不退回重做 Python。

新 BIN e5b51b6b810f215e284ea6317a4b1bef6b3faddc6f1ac9f8947804f261fa5732（292628 B）构建身份核对通过，但暂不作为下一次上板候选。需要的收尾只有下面两项，附件已给出可直接应用的两文件补丁。

## 1. main 栈报告漏算祖先帧，完整入口已超 2048 B 静态预算

入库调用图 rt-thread-motor-m1-callgraph.htm 已直接给出：

| 符号 | 本函数帧 | 含后续调用的 Max Depth |
|---|---:|---:|
| main_thread_entry | 16 B | 2076 B |
| $Super$$main（应用 main） | 40 B | 2060 B |
| supervisor_boot | 48 B | 2020 B |
| safety_startup_selftest | 56 B | 1220 B |

main_thread_entry → $Super$$main → supervisor_boot 是当前 ARM 工具链与 RT-Thread 的真实入口关系，src/components.c:186–204 也明确调用 $Super$$main。报告中的 main=756 B 对应启动 RT-Thread 的重定向入口，不能代替调度之后的 main 线程预算。

因此 2048−2020=28 B 只是遗漏两层调用者的计算。完整线程入口为 2076 B，比配置的 2048 B 多 28 B。自检单独子树 1220 B 也不等于整条启动路径；加上 16+40+48 后为 1324 B，原配置下相应静态差值是 724 B。

边界：2076 B 是调用图中的已知静态路径，包含调度栈检查及其诊断打印分支；不是证明上一场实际走满该深度，更不是证明已经发生现场栈溢出。未解析间接调用、上下文保存等也不能据此宣称已覆盖。即使它是旧问题，当前候选也不能按“余量 28 B”签收。

最小处理：RT_MAIN_THREAD_STACK_SIZE 从 2048 调到 4096。当前已知入口深度相对新配置的差值为 2020 B，仍须保留静态分析的边界。此目标启用堆分配 main 线程栈，主要增加 2048 B 运行时堆占用，不应硬写成 ZI 必增 2048 B；资源变化取实际新构建。无需重写 SPI 配置加载或重构启动线程。

## 2. 新公开的 used 字段在提前返回路径漏记耗时

tmc2209_read_register_stat() 清零 local 后交给既有 tmc_read_reg()。在 tmc_read_reg_obs() 的取锁失败分支，reason/rc 已设置，却在更新 st->used 的正常出口之前返回。

A 用现有 BTB06 同一输入复跑：
- 原件：rc=-23，reason=lock-busy，tx=0，rx=0，flush=0，used=0。
- 附件补丁：其余值不变，used=20。
- 夹具的取锁确实推进了 20 tick，然后失败；原件的 0 是漏记，不是“没有等待”。

补丁只在预算已建立的上下文拒绝、懒打开拒绝/失败、取锁失败出口补记 rt_tick_get()−b->start。它不增加等待、不改超时、不改变返回码、不吞错误、不引入共享状态；字段单位仍为 tick。预算尚未建立就失败的路径保持初始化值，不读取未初始化起点。

## 附件怎么用

补丁 R7F1_A_minimal_followup.patch 仅修改：
- rtconfig.h：main 栈 2048 → 4096。
- applications/tmc2209.c：以上四处提前返回时补记 used。

已在 adeeedc 上通过 git apply --check；A 的验证在仓库外副本完成，未改用户远端或仓库产品工作树。

本地 agent 从当前头先检查再应用补丁，保留用户已有改动。之后：
1. 在既有 BTB06 断言 used 等于本次脚本实际推进的 tick，不能只把它打印出来；保留 reason/rc/零 UART 字节断言。补充相关提前失败路径的耗时断言即可，无需新测试框架。
2. 运行受影响的既有 TMC C 测试。A 已预跑附件版本 tmc_boot 与 read_obs，两变体合计 80 项均通过。没有改其他模块，不要求重跑历史所有组。
3. 对最终 C 字节构建一次同一 motor candidate；归档 BIN/AXF/MAP/调用图和真实构建日志，重新登记 rtconfig.h 及 tmc2209.c 输入身份。以 main_thread_entry 作为 main 栈分析根；不再用重定向 main 或 supervisor_boot 子树代替整线程。
4. 更新本轮交接中的栈结论、镜像身份及下面两条文字说明。一次精简交付即可，无需新增派生器、批准框架或为自己的新头追加收口轮次。

上轮“只构建一次”限制的是该轮，不禁止发现具体 C 问题后构建修订候选；也无需为了对照而额外重编历史基线。A 环境没有本地 Windows Keil，附件是主机验证过的源补丁，未冒称已构建新固件。

## 两条文字说明直接更正，不增加 C 工作

- “sensor 在 stage7–11 已经半帧进入 SG，所以门挡不住在途读”不适用于本次冷启动：门在静态初始化时已关闭，先于 sensor 线程创建。本门明确仍不覆盖运行中的 system_selftest 重入，这条限制保留。
- 未改的 [MOT] 注释行按源码计算是 126 B，已包含换行；格式化容量 127 B 足以容纳它及终止 NUL，发送长度为 126，不会因本处产生裸 NUL 或丢换行。它仍超过本轮采用的 120 B 目标，但不是三处截断修复的对象；无需顺带修改，不能把“126 B”直接等同为“已截断”。

现场打印覆盖也须按可达分支说：若新候选正常进入 READY，自然不会打印 FAULT_LATCHED/故障收口分支；未出现不代表该故障分支已被现场验证，更不需要为了看它而额外触发故障。

## A 的独立验证与限制

- 原提交：tmc_boot 10 + boot_print 5 + read_obs 30，两变体合计 90/90，rc=0。
- 附件产品 C 副本：tmc_boot 10 + read_obs 30，两变体合计 80/80，rc=0；BTB06 used=0 → 20。
- 新归档 BIN/AXF/MAP/调用图、构建日志、7 份源 blob 与声明的编译磁盘字节重建：19 项身份核对，0 失败。CRLF 声明按实际声明重建后比对；这不等于 A 能直接证明本地 Keil 进程读过哪些文件。
- SD 独立复跑因 Linux 无 windows.h 未能编译，未计为通过，也不据此判产品失败；本次不扩展平台适配工作。原交付的 SD 记录可保留。
- 主机编译适配仅 -mthreads → -pthread。所有 C 测试都是带桩的真实产品函数验证，不能替代板上 RT-Thread 调度。
- 没有设备操作、没有 Keil 重编、没有新的现场批准。板上最后已证镜像仍为 5ae4110d…；product_field_fix 仍未验证。

两项小修及新候选身份核对完，再安排一次限定范围的启动现场诊断：下载/定长回读/复位并抓启动流，观察真实 reason、READY/fault 和可达短打印。此时无需 arm/start/学习/保存/清日志，不把旧额度结转。

## 证据

完整路径均相对 adeeedc 仓库根：
- reports/firmware/phase8-m8-r7f1-startup-c-fix/rt-thread-motor-m1-callgraph.htm
- reports/firmware/phase8-m8-r7f1-startup-c-fix/build_manifest.json
- reports/reviews/phase8-m8-r7f1-startup-c-fix/build/stack_callgraph_r7f1.txt
- applications 目录为 rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad/applications/
- src/components.c 为 rt-thread-master/rt-thread-master/src/components.c

ZIP 中另附 A 的原件/补丁测试摘要与逐项输出，供直接比较，不含设备原始 UID。

