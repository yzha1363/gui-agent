# 第三周周五：真实桌面补充验证

执行日期：2026 年 11 月 6 日（Australia/Sydney）。用户先要求离线交付，随后要求完成对应验证；本记录补齐此前待测的 11 种专用窗口场景。

**11 种场景的最新一次运行均符合预期：4 种正常完成、7 种正确保护停止。** 共保留 13 次真实桌面运行、2 次历史测试失败，以及 1 次尚未创建窗口的沙箱启动失败。不是 11 次任务全部成功，也没有丢弃失败记录。全部窗口已关闭。

## 环境与范围

- 实测 Windows 100% 缩放（GetDpiForWindow=96）、1920×1080，未改变系统缩放。
- 只操作 `GUI Agent V2 Robustness` 专用 Tk 窗口，实际鼠标/键盘输入经 DesktopController 与原生 SendInput 路径，截图经真实屏幕捕获。
- OCR 使用本地 CUDA Reader；独立状态来自真实 Tk 控件及提交回调，经 HWND 和时间新鲜度约束，不从 OCR 反推。
- 规划仍是固定回放。故障场景明确构造或注入，不能计为真实模型/NIM 推理效果，也不外推为任意业务应用通过。
- 桌面实验采用明确配置：会话 120 秒、观察 15 秒、定位 30 秒、结果确认 5 秒、截图年龄 10 秒；次数预算沿用原策略。原来更严格的默认配置没有被改写。

## 逐例结果与耗时

以下使用每种场景时间最新的记录作为当前验收结果，完整尝试仍在索引的 desktop_attempts 中。

| 场景 | 实际最终状态/错误 | 可能派发数 | 恢复会话耗时 | 含初始化/预热的流程耗时 | 验收 |
|---|---|---|---|---|---|
| normal | completed | 3 | 5.193 s | 29.954 s | OCR＋状态最终目标通过，提交 1 次 |
| layout_shifted | completed | 3 | 5.285 s | 12.018 s | 初始布局改变后命中正确控件 |
| layout_moves | completed | 3 | 6.516 s | 10.897 s | 点击前移动，重新获取新位置后完成 |
| duplicate | failed / AmbiguousTarget | 0 | 0.755 s | 5.047 s | 未指定重名目标，零输入停止 |
| duplicate_second | completed | 3 | 5.415 s | 9.757 s | focus_target=secondary，提交 1 次 |
| missing | failed / target_unavailable | 0 | 3.187 s | 7.424 s | 定位/重规划有上限，无输入 |
| wrong_text | failed / verification_timeout | 2 | 8.390 s | 12.743 s | 实际文本多出 `!`，未提交 |
| ocr_false_positive | failed / verification_timeout | 1 | 6.768 s | 11.081 s | OCR 假阳性被独立状态拒绝 |
| stale_state | failed / StateUnavailable | 0 | 0.417 s | 3.889 s | 过期导出拒绝，零输入 |
| partial_input | failed / RuntimeError | 2 | 2.949 s | 7.342 s | 仅 `Hel`，没有重复输入或提交 |
| submit_uncertain | failed / RuntimeError | 3 | 4.661 s | 9.070 s | 已提交一次，保留不确定性，不再提交 |

会话计时由 RecoverySession 记录，包含逐步观察/规划/输入/确认；流程计时从 worker 开始，另含 Reader 初始化和首次 OCR 预热，但不含窗口创建、等待 BEGIN 和线程入口前的导入。两者不能与独立的 OCR 冷/热基准混用。每例只运行一次或因问题复测，没有预设多次性能采样，不据此宣称吞吐或延迟提升。

正式复核：[验证索引](../output/week3-friday/verification.json)，批次 `output/week3-friday/20261106T091722_627143Z/`。最新 11 例保存 152 张真实截图，全部 13 次运行共 190 张；桌面目录 1,229 个原始文件逐个计算 SHA-256。图块预点击图另存，未将模拟截图混入真实计数。

## 真实截图与双重确认

### 正常三步和最终目标

![真实窗口：正常流程完成](../output/week3-friday/desktop-validation/normal-02/observations/0015_agent_goal_observe1/screenshot.png)

最终 OCR 识别到 `SUBMITTED Hello GUI 123 中文测试`；独立 submitted 文本、submit_count=1 和 focus_target=primary 同时满足。审计从原始 results.json 和 state.json 重新计算最终目标，不仅读取 summary 中的成功标记。[会话](../output/week3-friday/desktop-validation/normal-02/session/summary.json) · [最终实际控件状态](../output/week3-friday/desktop-validation/normal-02/final-state.json)。

### 点击前目标移动

![真实窗口：目标移动后的完成画面](../output/week3-friday/desktop-validation/layout_moves-01/observations/0018_agent_goal_observe1/screenshot.png)

程序在输入前复核阶段实际移动按钮；最新 OCR 位置改变触发重新获取，之后完成精确输入和单次提交。[会话与恢复台账](../output/week3-friday/desktop-validation/layout_moves-01/session/summary.json)。

### 部分输入与提交不确定

![真实窗口：部分输入仅留下 Hel](../output/week3-friday/desktop-validation/partial_input-01/observations/0010_recovery_diagnostic_observe1/screenshot.png)

这里只发送前三个测试字符后注入异常；输入框实际为 `Hel`，提交计数为 0，不重复发送全文。[最终控件状态](../output/week3-friday/desktop-validation/partial_input-01/final-state.json)。

![真实窗口：提交后注入异常的只读诊断](../output/week3-friday/desktop-validation/submit_uncertain-02/observations/0013_recovery_diagnostic_observe1/screenshot.png)

只点击一次 SUBMIT，确认实际回调计数从 0 变为 1 后注入错误。诊断中 OCR 与独立目标均成立，但 task_verified=false、input_delivery_uncertain=true；没有将正常回显单独当作成功，也没有重复提交。[会话/诊断](../output/week3-friday/desktop-validation/submit_uncertain-02/session/summary.json) · [原生输入日志](../output/week3-friday/desktop-validation/submit_uncertain-02/actions.jsonl)。

OCR 假阳性场景保留原始 OCR 报告，再向工作报告显式注入 INPUT READY；独立控件仍未聚焦。实际结果为验证失败，输入次数为 1，未输入文本。[双通道证据](../output/week3-friday/desktop-validation/ocr_false_positive-01/session/cycle-01/workflow/summary.json) · [注入前原始 OCR](../output/week3-friday/desktop-validation/ocr_false_positive-01/raw-ocr)。

## 发现的问题、修复与复测

| 历史尝试 | 实际问题 | 修复 | 复测 |
|---|---|---|---|
| normal-01，窗口创建前 | 沙箱进程无法加载已安装的 Tcl init.tcl，未创建窗口/未派发输入 | 在已授权的交互会话运行专用程序，未改动系统或防护设置 | normal-02 创建窗口并通过；不计 normal-01 为真实桌面运行 |
| wrong_text-01 | 错误文本确实未提交，但外层验证 deadline 到期被归类为 observation_timeout | 向观察层传递当前阶段的 deadline 错误码，区分验证、定位和独立观察时限 | wrong_text-02 为 verification_timeout，保护行为不变 |
| submit_uncertain-01 | UI 回调尚未更新计数，条件注入没有触发，程序正常完成；这是 fixture 时序失败 | 定位真实 SUBMIT 控件，点击一次后只读等待回调增加 1，再抛人工错误；无回调或多回调则明确判 fixture 失败 | submit_uncertain-02 正确注入并保持 failed/uncertain，提交计数 1 |

两次历史测试失败分别是错误归因问题和注入未触发，不能称为两次重复提交。原始目录、旧源代码及说明保留在 desktop-validation/timeout-fix、submit-injection-fix 中。

新增 5 项测试：两项阶段 deadline 归因边界，以及延迟回调、无回调、多回调三项注入检查。相关 72 项测试通过，全项目 **347 项**回归、计划校验、依赖检查和历史桌面核对通过。[全项目结果](../output/project-checks/20261106_201457_908770/summary.json)。

## 复核与交付

```powershell
.\.venv\Scripts\python.exe scripts/validate_week3_friday.py --audit-only output/week3-friday/20261106T091722_627143Z
.\.venv\Scripts\python.exe scripts/build_v2_release.py --include-evidence
```

审计保留每次尝试，逐例重算状态、派发次数、控件文本及提交数，并核对所有桌面原始文件哈希；验收按每种场景最新记录判断，同时单列历史未通过记录。源码包与配套证据 ZIP 分开生成，旧的离线交付版本不覆盖。

本次补齐了专用窗口在 100% 下的受控执行与故障保护，不包含真实模型规划质量、NIM 或 200% 缩放。这些仍保持原来的待测/未通过结论。
