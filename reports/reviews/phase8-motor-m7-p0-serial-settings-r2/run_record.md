# NS800 M7 P0 串口设置 R2 —— 本地精确应用与离线复验记录

任务名 `phase8-motor-m7-p0-serial-settings-r2`。2026-10-30。
被执行的交付：`p0_settings_r2_unzip/01_Review_and_P0_Serial_Settings_R2_Task.md`（SHA-256 59ea99f4…3de4）。
本轮全部为离线；真实设备对象 0，COM5 未打开。

## 1. 基线核对（§6 第 0 步）

- 本地 HEAD 与远端 `debug/tmc-uart-rootcause-20261028` 均为 **8bade29677dd3f2395cfd92ce28b9cd7206c89e2**；工作树干净。
- 任务书基线是 280ca81。`280ca81..8bade29` 共 18 个文件差异，全部在 `reports/` 下：
  四次只读对照实验的证据（`4fbb11e` 换口、`e93971d` 历史读法、`b3c07a3` 单 CR、`8bade29` 采集器读路径复现）
  与旧交接 `phase8-motor-m7-p0-serial-continuation.md/.json` 的追加节。
  **产品/测试/固件目录差异 0**，C/.h/HAL/内核/工程/镜像一字未动，未重编未重烧。
- 冻结引用只读复算：main=1b3757ee、phase7=6d3fbc55、phase8=1696c59f、tag v0.7→e40a9b95，与任务书 refs.json 一致，未改。
- 未回退、未 rebase、未覆盖任何历史证据；两次失败轮次与 62 B 污染原件原样保留。

## 2. 精确补丁应用（§6 第 1 步）

规范 LF 做法（仓库配置未改）：把 HEAD 两个规范 blob 导出到仓外 `D:\p0r2_work\apply\`，
在仓外 `git -c core.autocrlf=false apply --check`（rc 0）后 `apply`（rc 0），逐字节比对后再拷回仓库。

| 文件 | 修前 blob | 修后 | 任务书 §5 期望 | 一致 |
|---|---|---|---|---|
| p0_voltage_observation.py | 0e02ccfc…5467 / 23374 | c98bb712…a726 / 23430 | c98bb712…a726 / 23430 | 是 |
| verify_p0_candidate.py | 2cf87b1d…a8c7 / 21742 | 99a449f6…8a45 / 26674 | 99a449f6…8a45 / 26674 | 是 |

补丁只改这两个文件；工作树两文件 CR 字节均为 0；Git 索引 blob 散列即上表“修后”。
补丁本身 10011 B / c5793e35…aa15，与任务书一致。未“补丁后再粘贴全文件”。

固定输入复核：计算器 54267 B / 5c7a2340…63ad 未变；板上 BIN 266244 B / e614a075…f7da、AXF 1688696 B / 2205c7e7…d584 未变；
固件输入仍 5b564eb。

## 3. 离线自检（§6 第 3 步，Windows Python 3.12.5）

proof 由**固定 24ab5a7 Git 对象**导出：board-proof.json 10116 B / e8df121f…4246；boot.b64 4508 B / 46b53e93…dd46，
解回 3381 B / bc47dc02…14fa。HEAD 里那份被勘误更新过的 board.json（1fb1a49c…e246）**没有**用来替换固定对象。

| 命令 | rc | 结果 | 与云端记录 |
|---|---|---|---|
| `--selftest` | 0 | 37/37 | 逐字节相等（仅 CRLF→LF 归一） |
| `--selftest-continuation --prior-board-json … --prior-boot-b64 …` | 0 | 11/11 | 逐字节相等（同上） |
| `--selftest-static-port`（本机默认码页 cp936） | **1** | **8/9**，P0C09 失败 | **不相等**，见第 4 节 |
| `--selftest-static-port`（PYTHONUTF8=1） | 0 | 9/9 | 逐字节相等 |
| `PYTHONUTF8=1` 重跑 37 与 11 | 0 / 0 | 37/37、11/11 | 稳定 |

原始 stdout/stderr 全部留存：`selftest37_windows.*`、`selftest11_windows.*`、`static9_windows.*`（失败件，未删）、
`static9_windows_utf8mode.*`、`selftest37_windows_utf8mode.*`、`selftest11_windows_utf8mode.*`、`selftest_rcs.txt`。
Windows Python 标准输出重定向带 CRLF（56/13/12/10/11/4 个 CR），与云端 LF 记录归一后一致——这是既有已登记的取数惯例，不是判据差异。
入库件按映射表统一转成 LF（`stdout_crlf_normalisation.json` 逐个记原字节数、CR 数、原散列、归一后散列），
这样 `core.autocrlf` 不可能在无人察觉时改掉已提交文本件的字节；目录内 `SHA256SUMS.txt` 逐件登记最终 blob 散列，
该清单故意不含自身散列（避免自引用身份）。真正的串口原始字节仍须走 base64＋`-text` 保护，本轮没有取任何设备字节。
导入采集器模块后 `serial`、`pyocd` 均不在 `sys.modules`：三条自检路径确实不构造任何传输对象。

## 4. 新发现的第三处软件错误（本地复验才暴露，登记为 R2-LOC）

`verify_p0_candidate.py:447` 在 P0C09 里用 `ast.parse(Path(m.__file__).read_text())` 解析采集器源码，
**没有指定 encoding**，于是跟随主机码页。本机 `locale.getpreferredencoding(False)=cp936`，
采集器源码是 UTF-8 且含中文（`api-accepted(驱动接受关断, 非波形证明)`），在第 1759 字节 0x8e 处抛
`UnicodeDecodeError: 'gbk' codec can't decode byte 0x8e`。云端码页为 UTF-8，所以同字节候选在云端 9/9、在本机 8/9。

- 这是**验证器夹具**的缺陷，不是采集器产品逻辑，也不是判据被放宽；P0C09 想证的性质本身成立，见下。
- 独立、显式 UTF-8 的等价检查 `check_static_port_substance.py` 对**本机 Git 索引 blob 导出的候选**判 rc 0：
  `serial.Serial` 调用点 1 处、`timeout=Constant(0)`、`write_timeout=Name('WRITE_SECONDS')`、
  全模块 protected 属性 Store 赋值 0 处（`static_port_substance.stdout.txt`）。
  同一脚本对**修前** blob 判 rc 1（`P0C09B: timeout must be the literal 0`），证明该检查会失败、不是恒绿（`…_negative_control.txt`）。
- 最小修正只需 `read_text(encoding='utf-8')`。**本轮未自行改动候选字节**：任务书 §5/§6 的接受条件绑定那两个散列，
  自行动它就把“精确字节接受”变成了未披露的逻辑改动。
- 同类写法还存在于 `verify_p0_candidate.py:90/239/262`（无 encoding 读 `ledger.jsonl`）。本轮这些路径未报错，
  只说明其内容在本机可被 cp936 解码，不证明长期安全；列为后续只读项，未顺手改。
- 采集器自身的文本 I/O 不受此影响：`save_json` 与 ledger 打开均为 `encoding='utf-8', newline='\n'`（脚本第 53/63 行），
  DMM 与 UID 读取分别用 `json.loads(bytes)` 与 `encoding='ascii'`。所以 R2-LOC 不阻断现场采集链的正确性，只使验证器在本机不可用。

## 5. pySerial v3.5 API 桩对照（§6 第 4 步）

仓外按包目录结构运行 `checks/probe_pyserial35_reconfiguration.py`，
并把包内 `candidate/p0_voltage_observation.py` 换成**本机 Git blob 导出**（c98bb712…a726）后再跑，避免只验“包里那份”。

```text
UPSTREAM_SAME_VALUE_SETTERS  SetCommState_calls = 3
before_one_query             SetCommState_calls = 5   raw_read=64  state_validator_accepted=false
after_one_query              SetCommState_calls = 0   raw_read=64  state_validator_accepted=true
```

5→0 复现，且与云端 `results/upstream35_setcommstate_probe.txt` 逐字节相等（CRLF 归一后）；
默认码页与 `PYTHONUTF8=1` 两种模式输出一致、rc 均为 0（`upstream35_probe_default_locale.*`、`upstream35_probe.*`）。
探针未加载真实 Win32 串口 DLL、未构造 `serial.Serial`、未打开端口、未模拟物理位错。

本机已安装 pySerial **3.5**，只读核对：包内 `pyserial35/serialutil.py`（3c84f8c7…17c0a）与
`serialwin32.py`（17681ea9…bbe00）与 `site-packages/serial/` 下同名文件**逐字节相同**。
也就是说被证明的那份 setter 行为就是本机会执行的那份。未联网、未安装、未改动已安装库。

## 6. 源码事实登记与一次归因更正

`reports/reviews/phase8-motor-m5-fault-log/m5_field_serial.py`：
第 92 行 `serial.Serial(port, baud, timeout=0.05)` 仅在打开时设一次；第 96–112 行读取循环里
只有 `read(max(1, in_waiting))`，**循环中不再写 timeout/write_timeout**。
因此“干净取样都来自 PowerShell、Python 本身有问题”的按语言归因作废——
M5 的干净字节同样出自 Python/pySerial 工具；差异在**是否在打开后反复写设置属性**，按脚本索引而不按编程语言归类。
DTR/RTS 默认值在本对照中不变（M5 与当前都用库默认）。

## 7. 状态与边界

- 软件调用路径已证：修前一次正常查询触发 5 次 `SetCommState`，修后 0 次；帧界修后同一条 64 B 健康响应由拒转受。
- **物理根因仍未关闭**：板上 62 B 污染是否由打开后重配置造成，须同板对照才有资格写“支持 R2-PORT 为原因”。
- 本轮未执行任何现场动作，额度全 0；旧几轮 COM5 额度已耗尽且不结转。
- 任务书 §7 的现场包需要用户对 §7.4 那句话的明确批准；当前没有该批准，故本文件不代表批准，也**不进入现场**。
- P0 仍只到 VOLTAGE_OBSERVATION_ONLY 这一层；未取电压数据，不产 MEASURED 系数，不动阈值，不外推相电流 RMS。
  P1 仍 METHOD_NOT_READY，P2 仍 NOT_AUTHORIZED，示波器/A0 仍暂缓。
- 未重跑未改的 C 回归、未编 Keil；M6 等其它候选未顺手批量修（可只读列出同类写法留后续）。

## 8. 待裁决的一点

精确补丁的字节接受条件成立（两散列相等、镜像与计算器不变、远端字节一致）。
但 §6 的“Windows 37＋11＋9 通过”在本机**只在 UTF-8 模式下**成立，默认码页下 P0C09 因夹具缺陷失败。
两条可走的路，由审核方择一，不自行认定：
(a) 接受 `PYTHONUTF8=1` 作为本轮运行口径＋独立等价检查（已有 rc 0 与负控）；
(b) 由审核方发一版仅把 `read_text()` 改成 `read_text(encoding='utf-8')` 的验证器候选，重新逐字节接受。
