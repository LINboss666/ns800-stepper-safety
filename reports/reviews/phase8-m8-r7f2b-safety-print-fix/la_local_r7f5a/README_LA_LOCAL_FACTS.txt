本机(非 A)在 R7F5A 任务书下达前后对逻辑分析仪做的实测, 全部只到'仪器与软件通道'层面。

来源与身份
  - 被测软件: C:\Program Files\Logic\Logic.exe = Logic 2 2.4.48 (Electron, 主窗口 PID 变化过);
    更早一次截图 logic_1_2_17_connected.png 来自 C:\Program Files\Saleae Inc\Logic.exe 1.2.17,
    标题 [Connected] 只证明当时软件认到设备, 不证明任何实物输入耐压。
  - 控制通道: MCP http://127.0.0.1:10530/ (la_mcp_initialize_*.json/txt 为握手原文)。
    Automation Server 10430 当时无监听(连接被拒), 本轮未依赖它。
  - 设备: 8 路数字通道; 序列号不入公开仓库, 见 la_mcp_get_devices_sanitized.json 的掩码说明。

实测到的能力/限制(全部有对应原文件)
  1) 数字采样率只接受 25 kS/s..24 MS/s 共 13 档 => la_reject_samplerate.json 具名拒绝原文;
     任务书希望的 1 MS/s 在允许档位内。
  2) 不支持可调输入阈值电压 => la_reject_threshold.json 具名拒绝原文。因此不能靠设阈值来适配电平。
  3) 上面两条拒绝都发生在参数校验期, isError=true 且不返回 captureId => 没有建立采集会话。
  4) 导出侧通道筛选不生效: 请求 digitalChannels:[0], 实得 8 列 => la_smoke_capture_digital.csv。
     所以 CSV 的列号与信号的对应必须现场核, 不能假设导出按请求裁剪。

CONTROL_PATH_SMOKE 声明(不是测量证据)
  la_smoke_capture_digital.csv 是探头未接任何东西时的 1 秒定长采集: D0 全程 0, D1-D7 全程 1,
  两行状态无任何变化。这是浮空输入, 按项目纪律禁止当作电平事实或'采集成功'的证据。
  该次采集发生在 R7F5A 任务书下达之前, 属操作者要求的'试试能否控制仪器', 不占本轮
  logic_capture_session=1 的额度; 任务书第 58 行'不要先做试采消耗一次机会'的要求与本事实
  存在时间差, 如实登记, 不解释成已满足。

仍未核清(见 offline_r7f5a/preflight_open_items.json)
  实物输入耐压资料 / ≥100 M samples 的实际缓冲容量 / 探头印字 CH1-CH2 与 GUI 通道号的对应。
