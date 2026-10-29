# M7 编译期横幅勘误（独立复审 §5.2）

日期：2026-10-29。本文**撤回** M7 轮关于"镜像不含编译期横幅"的结论，并给出可独立复算的依据。

## 1. 原结论（错误，保留原文以便核对）

`reports/reviews/phase8-motor-m7-current-calibration/build_notes.md` §6 与
`reports/firmware/phase8-motor-m7-current-calibration/build_manifest.json` 的 `banner_note` 写过：

> 本轮实测：三个 BIN 与三个 AXF 里**都找不到**编译期日期时间串；`__DATE__` 在
> `bsp/novosns/ns800/` 下只出现在一个 markdown 文件里，不在任何源文件。因此这三枚镜像没有
> 可回取的 build banner。…… M5/M6 的 `build_manifest.json` 里记有 `banner: "build Oct 29 2026 …"`，
> 该值用同一 pattern 扫 M5 归档镜像为 0 命中，即**不能从已归档镜像字节复现**。

## 2. 错在哪（方法缺陷，不是镜像缺陷）

1. **搜索范围错了**：`__DATE__` / `__TIME__` 的实际使用点在 `src/kservice.c:161-162`，
   在 BSP 目录 `bsp/novosns/ns800/` **之外**。我只在 BSP 目录下 grep，于是得出"不在任何源文件"。
2. **搜索形态错了**：那一行是
   `rt_kprintf(" / | \\     %d.%d.%d build %s %s\n", …, RT_VERSION, RT_SUBVERSION, RT_REVISION, __DATE__, __TIME__);`
   —— 日期与时间是**两个独立的字符串池对象**，不保证紧邻。我用的是"日期紧跟时间"的整串 pattern，
   在字符串池里必然 0 命中。实测池内顺序还常常是**时间在低地址、日期在高地址**（下表）。

这两个缺陷叠加，使我把"我的搜索没命中"当成了"字节里不存在"。同一 pattern 扫 M5/M6 得 0 命中
也因此**不能**当成"M5/M6 manifest 的 banner 值不可复现"——那条并列事实同样作废。

## 3. 本轮自己的字节级复核（零基偏移，不是 Flash 地址）

对**已归档**的 BIN 直接在字节流里找形如 `%b %d %Y` 与 `%H:%M:%S` 的 ASCII 串，
两种 pattern 各自独立、不要求相邻：

| 归档 BIN | bytes | SHA-256（前 12） | 日期串 / 偏移 | 时间串 / 偏移 |
|---|---|---|---|---|
| M7 `rt-thread.bin` | 265388 | `1bf17ed8bc45` | `Oct 29 2026` / 259816 | `18:46:15` / 259807 |
| M7 `rt-thread-measure-a0.bin` | 275644 | `8307cffdcaa1` | `Oct 29 2026` / 270047 | `18:46:28` / 270059 |
| M7 `rt-thread-motor-m1.bin` | 265492 | `4717a718b840` | `Oct 29 2026` / 259925 | `18:46:41` / 259916 |
| M5 `default.bin` | 259468 | `fdab581e63cc` | `Oct 29 2026` / 253910 | `00:38:29` / 253922 |
| M5 `measure-a0.bin` | 269724 | `17c6f191dbf8` | `Oct 29 2026` / 264159 | `00:38:43` / 264150 |
| M5 `rt-thread-motor-m1.bin`（板上最后已证） | 259580 | `01d3e5ab1ad2` | `Oct 29 2026` / 254019 | `00:38:59` / 254031 |
| M6 `default.bin` | 259468 | `ccfae5e37cd0` | `Oct 29 2026` / 253910 | `11:20:17` / 253922 |
| M6 `measure-a0.bin` | 269724 | `6458c8417471` | `Oct 29 2026` / 264159 | `11:20:33` / 264150 |
| M6 `rt-thread-motor-m1.bin` | 259580 | `a1ba8226c5d0` | `Oct 29 2026` / 254028 | `11:20:45` / 254019 |

九枚 BIN **每枚各 1 次日期命中、1 次时间命中**（`date_count=time_count=1`），
并且 M7 三枚的日期/时间与独立复审 `results/banner_probe.json` 逐条相等；
M5 motor（板上那枚 `01d3e5ab…64bc`）的 254019 / 254031 也与复审表一致。
M5/M6 manifest 里的 banner 值**可以**从其归档镜像字节复现，与复审 §5.2 的结论相同。

复算入口（不需要新工具，任何按字节读文件的语言都能做）：
读 BIN 全部字节 → `re.finditer(rb"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d\d \d{4}", b)`
与 `re.finditer(rb"\d\d:\d\d:\d\d", b)`，报告 `m.start()`（**零基文件偏移**）。
不要用"日期+时间必须紧邻"的联合 pattern，那正是本次漏检的形态。

## 4. 来源等级必须分开写

| 说法 | 来源等级 | 本轮状态 |
|---|---|---|
| "归档字节恢复的构建戳" | 由已归档 BIN 的字节 + 上表偏移推出 | **有**（本轮实测，见 §3） |
| "板上实读的启动文本" | 串口读到的那一行 banner 文本 | **没有**（本轮 HARDWARE_ACTIONS=[]，未接板） |

两者的差别不是措辞：前者只证明"我们保存的这枚镜像里有这个戳"，后者才证明"板上跑的就是它"。
未来的下载身份包必须分别登记，不得用前者替代后者。M7-R1 的 P0 已把这一点写成
`board_candidate.md` §2.1 的 A2/A4 两步。

## 5. 不改写的历史件

按既有纪律，M5/M6 的 manifest、build_notes 与镜像**不重做、不回填**：本轮不因为补文档而重建旧镜像，
也不宣布旧的 SHA 被推翻——六件 M7 BIN/AXF 的 SHA 仍然与 M7 manifest 全等，这一点复审也确认了。
被更正的三处 M7 证据只做**追加勘误指针**（原文保留）：

- `reports/firmware/phase8-motor-m7-current-calibration/build_manifest.json` → 新增 `banner_erratum`
  字段，`banner_note` 原文与 `NOT_EMBEDDED_IN_IMAGE_BYTES` 值都留在原地，标注已撤回。
- `reports/reviews/phase8-motor-m7-current-calibration/build_notes.md` §6 → 追加"已撤回"标记与指针。
- `reports/agent_handoffs/phase8-motor-m7-current-calibration.md/.json` → 对应条目并列保留 + 勘误指针。

## 6. FINSH_THREAD_STACK_SIZE（同一条 §5.2 的附带项）

`rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad/rtconfig.h:125`：

```c
#define FINSH_THREAD_STACK_SIZE    4096
```

这是**源码事实**，可以补。栈**高水位**本轮仍未实测（无设备、无 map 级实测依据），保持
`NOT_MEASURED`；不得把"配置成 4096"写成"实测够用"。归档 Keil 日志的 CRLF→LF 与
`lossless=false` 上一轮已如实披露，本轮不因此重跑构建或重造 stdout。
