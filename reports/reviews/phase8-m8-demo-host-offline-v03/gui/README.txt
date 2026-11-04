GUI 桌面实测取证说明（V0.3，本机 Windows / Python 3.12.5 / Tk 8.6，缩放 100%）
================================================================

一、怎么产生的（归档脚本与实际执行命令一致，路径全部走参数）

1) 反例复跑（单独执行，原始 stdout+stderr 原样落盘到 host/negative_controls.txt）：

   python tools/demo_negative_controls.py \
     --pkg <仓库外解包目录>/V0_3/NS800_Demo_Offline_V0_3 --repo <仓库根> --box <仓库外工作区>/neg

2) 离线复核集中取证（21 项判据，全部 PASS 才返回 0）：

   python tools/demo_review_checks.py --zip <V0.3 附件> --zip-prev <V0.2 附件> \
     --zip-prev2 <V0.1 附件> --repo <仓库根> --box <仓库外工作区> --out host

3) 桌面实测（一次真实启动 Tk 窗口，探针只调用应用自己的按钮回调，不改应用源码）：

   powershell -NoProfile -ExecutionPolicy Bypass -File tools/demo_gui_shot.ps1 \
     -PkgDir <仓库外解包目录>/V0_3/NS800_Demo_Offline_V0_3 -Probe tools/demo_gui_probe.py \
     -Frame 3 -Dump gui/frame3_0x18.json -Out gui/frame3_0x18.png -HoldSec 20
   （同一命令 -Frame 6 产出 gui/frame6_0x8.*）

   截图取法：GetClientRect + ClientToScreen + Graphics.CopyFromScreen，只截客户区。
   不用 PrintWindow：本机实测它会把内容整体下移约一个标题栏高度（约 29 px），曾据此误判"来源行被裁"。

二、判据（不靠肉眼，取自控件几何与 Tk 变量；JSON 为 UTF-8 + LF 直写，不经控制台，避免 GBK 乱码）

两份 dump 的 client 均为 [1180, 820]，四个回放按钮 bbox 与来源行都在客户区内：
  播放全部记录 [24,716,148,767] / 暂停回放 [158,716,256,767] / 下一步记录 [266,716,377,767] /
  回到启动记录 [387,716,511,767]，all_control_buttons_inside = true（底部留白 53 px）。

第 3/7 步（frame3_0x18）：
  状态块  故障码 0 | IRQ CLOSED | arm 门  0x18
  参数行  本段记录未提供驱动参数；回放配置回包后显示。
  来源行  历史 R7F4 · 第 3/7 步 · 1128 B · SHA256 f0550d313164… · 当前未连接设备
  原始回包页 sha256 = f0550d3131642cd02408c88adc67f6fcb064a27ccba6ceec9c60b82e9cf0c3b5
    == 仓库原件 02_mot_pre.rx.bin 的磁盘散列（host/archive_recompute.json），1084 字符 / 1128 B。

第 6/7 步（frame6_0x8）：
  状态块  故障码 0 | IRQ CLOSED | arm 门  0x8
  参数行  固件设置值：IRUN 20 · IHOLD 8 · DLY 4 · TPD 8 · VSENSE 1 （电流未实测）
  来源行  历史 R7F4 · 第 6/7 步 · 1080 B · SHA256 fa69aa5cf5a1… · 当前未连接设备
  原始回包页 sha256 = fa69aa5cf5a11917ee3b00f06e2822ee66979c1aae576f3f1eb4a0bd85ff2215
    == 仓库原件 05_mot_post.rx.bin 的磁盘散列，1036 字符 / 1080 B。

三个"待联调/待验证"按钮在两帧里 state 都是 disabled，界面没有把未验证功能做成可点。

三、边界

- 覆盖范围只有本机一台 Windows、100% 缩放、默认 1180x820 客户区；其他 DPI/字体/系统缩放组合未测。
- 探针走的是应用自己的 play/pause/step/reset 回调与它自己的变量，没有替换任何数据源。
- 这两帧仍是历史回放的显示结果，不构成当前板端状态或任何硬件动作的证据；本轮设备动作 0。
