# GUI Agent v2.0.0 开发里程碑说明

版本标识位于 `gui_agent/version.py`。本版本汇总感知、规划、双通道执行、有界恢复、小规模 LoRA 与离线效果比较，新增点击前最新 OCR 重选和鲁棒性验证。它表示工程开发里程碑，模型能力、真实桌面与应用覆盖分别验收。

## 已完成

- 观察/定位次数和时间预算、全局计数、零输入有限重规划。
- 每个动作前复核状态及窗口；点击前同时核对最新 OCR 目标与新鲜图块。
- 部分输入/提交不确定性保守停止，失败后只读诊断；OCR 与独立状态共同确认最终目标。
- 子任务依赖和执行台账，失败分类、原始证据及完整性清单。
- 本地数据审计、LoRA 训练与断点恢复、基础/微调模型对照；质量失败原样保留。
- 347 项全项目测试、20 组新增鲁棒性和 30 组恢复/HTTP 回归通过各自预期。
- 已补齐 100% 缩放下 11 种专用桌面场景；保留 13 次运行及两次历史测试失败，最新每种结果均符合预期。

## 本轮重要修复

旧版点击前可能继续使用第一次 OCR 的坐标和唯一性判断。新增目标重选后，位置变化会重定位，缺失会有限重试，重名歧义会停止。补测还修正外层 deadline 错误归因和提交回调异步导致故障注入未触发的问题，新增 5 项测试。见 [周五验证记录](../第三周周五任务验证记录.md) 与 [桌面补测](week3-friday-desktop-validation.md)。图块指标和 OCR 重选不是原子 UI 锁，不外推为任意应用已验收。

## 交付与复现

```powershell
.\.venv\Scripts\python.exe scripts/check_project.py --with-evidence
.\.venv\Scripts\python.exe run_robustness.py --scenario layout_moves
.\.venv\Scripts\python.exe scripts/validate_week3_friday.py --audit-only output/week3-friday/20261106T091722_627143Z
.\.venv\Scripts\python.exe scripts/build_v2_release.py --include-evidence
```

源码 ZIP 为 `output/deliverables/gui-agent-v2.0.0-<时间戳>.zip`，每个源码文件有 SHA-256 清单；版本记录位于 `output/releases/gui-agent-v2.0.0/<UTC时间戳>/release.json`，包含压缩包哈希、验收范围和证据索引哈希。

源码包不带虚拟环境、数据集、基础模型、adapter/checkpoint 或桌面截图。单独的 validation-evidence ZIP 包含正式实验、真实桌面原始截图/日志、历史失败、基准与哈希清单。没有自动发布到 GitHub、创建 Git tag 或替换旧交付包。

## 明确保留的限制

| 项目 | 状态 |
|---|---|
| 当前工程测试 | 离线通过，不等价于所有 GUI 任务成功 |
| 本轮真实桌面鲁棒性 | 100% 缩放下 11 种场景最新结果通过预期，规划仍为回放 |
| 真实桌面历史证据 | 此前记录只读核对；本轮另保留 13 次新运行，含 2 次历史测试失败 |
| 真实流程耗时 | 已逐例保存；正常含初始化/预热 29.954 秒，会话 5.193 秒；独立 OCR 冷/热基准仍分列 |
| 本地模型质量 | 周三 JSON 可解析性有变化，但动作结构/标注匹配仍未通过 |
| NVIDIA NIM | 真实推理待测，本地 HTTP fixture 不能替代 |
| Windows 200% 与更多应用 | 继续待测；历史 100%/125% 不代表本轮已补测 |

初次离线交付和后续桌面补测分别留档。NIM、真实模型动作质量、200% 和其他业务应用的原待测/未通过结论没有被改写成通过。
