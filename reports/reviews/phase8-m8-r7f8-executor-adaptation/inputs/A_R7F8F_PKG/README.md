# R7F8F 合并任务包

先读 `R7F8F_REPAIR_AND_FIELD_TASK.md`：这是返修后直接接一次实机的范围；不是另一份纯离线任务。再读 `A_REVIEW_R7F8E.md` 和 `proof/A_counterexamples.json`。

`tools/a_counterexamples.py` 是参数化复现探针，依赖被审轮的原矩阵工具。用法：

```text
python tools/a_counterexamples.py --repo <仓库根> --work <仓外目录>
```

默认测试仓库内R7F8E派生执行器；`--executor <派生脚本>`可指向返修副本。脚本只用stub/sim，不打开COM。它输出观察结果，不把“脚本rc0”本身当返修通过；修前预期出现本包列出的错误OK，修后逐条按任务书判定首次STOP及截止行为，另补完整产品形状的正向例。旧POS的简化DONE夹具在严格修复后可以因缺候选被拒，此时另用完整正向夹具证明正常链。

`proof/replayed_*.txt` 是A在仓外副本复跑B原脚本的原文。包含本地临时工作路径的工具输出仅属环境记录；可执行工具不硬编码该路径。`proof/A_counterexamples_initial_incomplete_cleanup.json`保留探针最初遗漏清理回包的结果，修正后的主结果为`A_counterexamples.json`。

现场动作尚未发生；批准模板不是批准原件。操作者一次批准后，agent完成返修和全部离线门便直接执行同包现场段，无需再发一份纯离线签收申请。

`SHA256SUMS.txt`列除自身外的全部交付文件。历史原件、冻结旧执行器、产品源码和固件均不在本包修改范围。
