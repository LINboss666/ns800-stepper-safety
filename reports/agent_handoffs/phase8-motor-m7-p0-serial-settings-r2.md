# NS800 M7 P0 串口设置 R2 交接

2026-10-30。任务 `phase8-motor-m7-p0-serial-settings-r2`。
执行依据：`p0_settings_r2_unzip/01_Review_and_P0_Serial_Settings_R2_Task.md`（59ea99f4…3de4，zip 36877503…29c0，清单 33/33 全对）。
本轮**全部离线**：设备对象 0、COM5 打开 0、下载 0、目标 Flash 读 0、DAP 0、复位 0、电机 0、配置写 0、读数 0。
详细逐条记录见 `reports/reviews/phase8-motor-m7-p0-serial-settings-r2/run_record.md` 与同目录 `identities.json`。

## 1. 基线（§6 第 0 步）

本地 HEAD 与远端分支同为 **8bade29**，工作树干净。任务书基线 280ca81，之后多的四个提交
（换口 4fbb11e、历史读法 e93971d、单 CR b3c07a3、采集器读路径 8bade29）**全部只动 `reports/` 下证据与旧交接**；
产品/测试/固件目录差异 0。冻结引用只读复算一致（main 1b3757ee、phase7 6d3fbc55、phase8 1696c59f、tag→e40a9b95）。
未回退、未 rebase、未改任何历史证据；前两轮失败件与 62 B 污染原件原样在库。

## 2. 精确补丁与身份（§6 第 1–2 步）

仓外导出 HEAD 规范 blob → `git -c core.autocrlf=false apply --check`（rc 0）→ `apply`（rc 0）→ 逐字节比对 → 拷回。
补丁 10011 B / c5793e35…aa15，只改两个脚本，未“补丁后再贴全文件”，未改 Git 配置。

| 件 | 修前 | 修后 | 期望 | 结果 |
|---|---|---|---|---|
| p0_voltage_observation.py | 0e02ccfc…5467 / 23374 | **c98bb712…a726 / 23430** | c98bb712…a726 / 23430 | 相等，且与包内 candidate 全等 |
| verify_p0_candidate.py | 2cf87b1d…a8c7 / 21742 | **99a449f6…8a45 / 26674** | 99a449f6…8a45 / 26674 | 相等，且与包内 candidate 全等 |

计算器 5c7a2340…63ad、BIN e614a075…f7da/266244、AXF 2205c7e7…d584/1688696 一字未变；
C/.h/HAL/内核/宏/工程/镜像变更 0；未重编、未重烧；板上载荷仍是 M7-R1 那份。

## 3. 离线自检（§6 第 3 步）

proof 由**固定 24ab5a7 Git 对象**导出（board-proof.json 10116 B/e8df121f…4246；boot.b64 4508 B/46b53e93…dd46 → 3381 B/bc47dc02…14fa）。
HEAD 里那份被勘误更新过的 board.json（1fb1a49c…e246）没有替换固定对象——按 §6.3 明令。

| 命令 | rc | 计数 | 与云端记录 |
|---|---|---|---|
| `--selftest` | 0 | 37/37 | 归一 CRLF 后逐字节相等 |
| `--selftest-continuation` | 0 | 11/11 | 同上 |
| `--selftest-static-port`（本机默认 cp936） | **1** | **8/9** | **不等**，见第 4 节 |
| `--selftest-static-port`（`PYTHONUTF8=1`） | 0 | 9/9 | 逐字节相等 |

导入采集器后 `serial`、`pyocd` 不在 `sys.modules`，三条路径确实零传输对象。原始 stdout/stderr/rc 全部入库，失败件未删。

## 4. 本轮新查出的第三处错误：R2-LOC（不擅自改，交审核裁决）

`verify_p0_candidate.py:447` 用 `ast.parse(Path(m.__file__).read_text())` 解析采集器源码而未指定 encoding，
于是跟随主机码页。本机 cp936、采集器源码是含中文的 UTF-8，第 1759 字节 `0x8e` 抛 `UnicodeDecodeError('gbk'…)`。
云端码页 UTF-8 所以同字节候选 9/9；这就是“云端全绿≠本机全绿”的又一次实例。

- 属**验证器夹具/主机可移植性**缺陷，不是采集器产品逻辑，也不是把判据放宽或收紧。
- 为把“性质”与“夹具”分开，另写了一份显式 UTF-8 的等价静态检查 `check_static_port_substance.py`：
  对本机 Git 索引 blob 导出的候选判 rc 0（`serial.Serial` 调用点 1 处、`timeout=Constant(0)`、
  `write_timeout=Name('WRITE_SECONDS')`、全模块 protected 属性 Store 赋值 0 处）；
  对**修前** blob 判 rc 1（`P0C09B: timeout must be the literal 0`），说明该检查会失败、不是恒绿。
- 最小修正是 `read_text(encoding='utf-8')` 一处。**我没有改候选字节**：接受条件绑定那两个散列，自行动它就变成未披露的逻辑改动。
  两条路请择一：(a) 认 `PYTHONUTF8=1` ＋ 独立等价检查为本轮 Windows 口径；(b) 重发一版只改 encoding 的验证器候选重新逐字节接受。
- 同类无 encoding 写法还在 `verify_p0_candidate.py:90/239/262`（读 ledger.jsonl）。本轮这些路径没报错，
  只说明其内容在本机可被 cp936 解码，**不**证明长期安全，列为后续只读项，未顺手改。
- 生产采集链不受此影响：采集器所有文本 I/O 均显式 `encoding='utf-8', newline='\n'`（第 53/63 行），UID 读 `ascii`，DMM 走 `json.loads(bytes)`。

## 5. pySerial v3.5 API 桩对照（§6 第 4 步）

仓外按包结构运行，并先把包内 candidate 换成**本机 Git blob 导出**再跑，避免只验“包里那份”：

```text
UPSTREAM_SAME_VALUE_SETTERS  SetCommState = 3
before_one_query             SetCommState = 5   raw_read 64   state_validator_accepted false
after_one_query              SetCommState = 0   raw_read 64   state_validator_accepted true
```

5→0 复现，与云端 `upstream35_setcommstate_probe.txt` 归一后逐字节相等；默认码页与 UTF-8 模式两跑一致。
未加载真实 Win32 串口 DLL、未构造 `serial.Serial`、未开端口、未模拟物理位错。
本机 pySerial **3.5**，只读核对：包内 `serialutil.py`/`serialwin32.py` 与 `site-packages/serial/` 下同名文件**逐字节相同**，
所以被证明的 setter 就是本机会跑的那份。未联网、未安装、未改动已装库。

## 6. 源码事实登记与一次归因更正

`m5_field_serial.py:92` 只在打开时设 `timeout=0.05`，第 96–112 行读循环从不写端口设置属性。
所以“干净取样都来自 PowerShell、Python 本身可疑”的按语言归因**作废**：M5 的干净字节同样出自 Python/pySerial。
真正的判别量是**打开之后是否还写串口设置属性**，按脚本索引，不按编程语言。DTR/RTS 各对照均未动，均用库默认。

前四轮工装结论保持不变且不被本轮改写：不是 USB 口、不是并发打印者、不是单 CR 终止符、不是每块 fsync，
损坏全部发生在第一次 `read()` 之内且 `d1 95 f5` 逐字节重现。旧工装之所以没看见 R2-PORT，是因为假端口把设置属性当普通变量放行。

## 7. 已证 / 未证

已证：修前一次正常查询触发 5 次 `SetCommState`、修后 0 次；同一条 64 B 健康响应由拒转受；交付字节与 §5 表一致。
未证：**板上 62 B 污染是否由打开后重配置造成**——那要同板对照才有资格写“支持 R2-PORT 为原因”，
且即便成立也只排除到这一层，不等于排除其余物理机制。
P0 仍未取得电压数据；`CONSOLE_STREAM_INTEGRITY_UNRESOLVED` 继续开放；
P1 METHOD_NOT_READY、P2 NOT_AUTHORIZED、示波器/A0 暂缓；不做精度认证、不产 MEASURED 系数、不外推相电流 RMS。

## 8. 现场部分：本轮不执行

任务书 §7.4 那句话**尚未被用户签署**，而用户把现场条件定为“仅在该 R2 包已有明确批准时执行”。
因此本包停在离线交付，不打开 COM5、不发送任何命令。旧几轮的 COM5 额度已耗尽，不结转、不自动续。
一旦获批，按 §7 一包内完成：首条 `safety_state` 严格通过就在同一次串口会话继续其余 P0 步骤，不再另起单条诊断轮，
不增加“换 PowerShell 试试”“在线改 DTR/波特率/timeout”“去掉 fsync”这类分支。

## 9. 远端回读

脚本与证据以 **01f0e429…d1079af** 提交推送后重新 `git fetch`，一切复算走**远端 blob**：
两脚本散列、计算器、BIN/AXF、固定 24ab5a7 的 proof、62 B 污染件 b64、以及证据清单 25 条全部 0 失配。
再把远端 blob 的三个脚本导出到仓外结构重跑：原生码页 37/37、11/11、static **8/9**；`PYTHONUTF8=1` 下 37/37、11/11、**9/9**
——远端字节与本机候选表现完全一致，说明 R2-LOC 属主机码页，不是某份副本漂移。原始件见 `…_from_remote_blob_…` 六件。

## 10. 停点

```text
P0_SETTINGS_R2_PATCH_APPLIED_BYTE_EXACTLY(c98bb712/23430, 99a449f6/26674)
P0_SETTINGS_R2_APPLIED_AND_PUSHED(01f0e42) / REMOTE_BLOB_READBACK_MATCHES
OFFLINE_37_AND_11_NATIVE / STATIC_9_ONLY_UNDER_PYTHONUTF8 / R2_LOC_DISCLOSED_NOT_SELF_FIXED
PYSERIAL_3_5_UPSTREAM_SOURCES_IDENTICAL_TO_INSTALLED / SETCOMMSTATE_5_TO_0
M5_FIXED_TIMEOUT_SOURCE_FACT_REGISTERED / LANGUAGE_ATTRIBUTION_RETRACTED
SOFTWARE_CALL_PATH_PROVEN / PHYSICAL_CAUSE_NOT_CLOSED / VOLTAGE_DATA_NOT_OBTAINED
NO_C_OR_FIRMWARE_CHANGE / NO_KEIL / NO_FLASH / DEVICE_ACTIONS=[] / COM5_OPENS=0
AWAITING_R2_LOC_DECISION_AND_A_SEPARATE_USER_APPROVAL_FOR_THE_FIELD_PACKAGE
```

## 11. 裁决与单行补正（追加，上文不改）

审核方选 **(b)**：只补验证器那一行，随后直接接回原 P0 包，不再开完整返修轮，也不单开 GO 登记。
并明确定性为"这是我提供的验证器漏写编码，属于测试工具问题"，另引 Python 官方文档区分默认编码与显式编码。

[verify_p0_candidate.py:447](file:///D:/ns800/reports/reviews/phase8-motor-m7-current-calibration-r2/verify_p0_candidate.py#L447)：

```text
-        tree=ast.parse(Path(m.__file__).read_text())
+        tree=ast.parse(Path(m.__file__).read_text(encoding='utf-8'))
```

从远端 ee6eb33 的规范 blob 导出、在仓外做这一处字节替换、逐行 diff 证明全文件仅第 447 行不同，
得 **26690 B / 08559720b607a7cf503ff3ccf8daf787495ef8619ff3c3e1b3ee70b6f889887e**，与审核方给的新身份一致；
采集器仍 23430 B / c98bb712…a726，计算器/镜像/固件输入未动。第 90/239/262 行那三处 `read_text()` 不在授权范围，未顺手改。

**默认码页复跑**（实测 `utf8_mode=0`、`locale.getpreferredencoding=False`=`cp936`，不依赖 `PYTHONUTF8=1`）：
37/37 rc 0、11/11 rc 0、**static 9/9 rc 0**，三份 stdout 与云端记录逐字节相等；
API 桩对着交付采集器重跑仍 **5→0**；独立编码安全检查仍 rc 0。
原生 8/9 失败件、`PYTHONUTF8=1` 9/9 件、两次板级失败与 62 B base64 **全部原样保留**。
来源等级并列声明：审核方的 CP936 模型结论属云端离线；本节是本机 Windows 默认码页的真实执行；两者都不冒充板级结果。

## 12. 停点

```text
R2_LOC_FIXED_BY_THE_APPROVED_SINGLE_LINE(verifier 08559720/26690; collector unchanged c98bb712/23430)
DEFAULT_CODE_PAGE_CP936_UTF8MODE_0 / 37+11+9_ALL_GREEN_RC0 / OUTPUTS_EQUAL_CLOUD_RECORDS / API_5_TO_0_RE-RUN
NO_C_OR_FIRMWARE_CHANGE / NO_KEIL / NO_FLASH / DEVICE_ACTIONS=[] / COM5_OPENS=0
FIELD_PACKAGE_SCOPE_UNCHANGED_AND_NOT_APPROVED_YET
```

现场范围沿用原 R2 包（一次 COM5、七次查询、四个电压读数，首条严格通过后同会话继续；零烧录/DAP/复位/电机）。
你这句回执里没有该包的新现场批准，所以我没有打开串口——要执行请给 §7.4 那句批准文字。
