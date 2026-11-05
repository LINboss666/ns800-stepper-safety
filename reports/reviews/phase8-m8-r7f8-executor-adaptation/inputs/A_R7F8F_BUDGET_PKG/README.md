# R7F8F预算裁定补充

这是A对6f2b12f交接§3两条静态红项的确定裁定，继续同一R7F8F，不是新一轮纯离线任务。

1. 先读 `R7F8F_A_BUDGET_RULING.md`。
2. 将 `step_total_windows.json` 的38步总窗并入现有plan；同时落实“可信零速结束运动计时、arm计时独立保留”及显式总窗的窄适配。
3. 本包 `budget_check.py` 可直接运行，独立复算窗口、命令顺序和参照字节身份。`PROOF_budget.json`是A本地结果：17条算术／身份检查、FAIL=0；**不是执行器已改好或offline_gate已全绿的证明**。
4. `reference_plan_6f2b12f.json` / `reference_rows_6f2b12f.json` 为裁定基准原字节，不能当作已应用裁定的可执行计划。保留旧A包，不覆写其原件或清单。
5. `R7F8_LIVING_DRAFT.md`是本次更新的持续草案正文；合并到仓库的活草案，旧输入件不回改。
6. 补充相关正／负例、重建verify、整包与离线门、同源变更表和交接，再按原流程核远端blob。全过后用同一任务取得一次现场批准，直接接上机，不再等额外的软件签章。

复算：`python budget_check.py`；也可加 `--out <仓外输出.json>`。

固定边界不变：DIR1/300Hz、运行到可信零速12秒上限、arm至可信禁能18秒、会话90秒含7秒收口，一个COM5会话、一个start、不重试。未发生设备动作；polarity_measured/core_protection_verified保持false。
