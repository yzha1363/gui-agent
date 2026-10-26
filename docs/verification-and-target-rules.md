# 双重确认与重名目标规则

## 动作结果必须同时满足两条证据

每一步必须填写 `expect.text` 和非空 `expect.state`。成功公式为 `OCR.met AND state.met`。OCR 来自新截图；独立状态来自应用控件本身，禁止复用 OCR 文本充当第二条证据。状态缺失、过期、窗口身份不符、任一字段不符，都不能进入 `verified`。

演示程序通过 Tk 主线程读取真实控件状态，并原子导出 `output/demo-state.json`。运行器校验时间戳不超过 2 秒、HWND 与当前目标窗口相同，再逐字段比较：

| 步骤 | OCR 条件 | 独立控件条件 |
|---|---|---|
| 激活输入框 | INPUT READY | 输入框确实获得焦点，状态变量为 INPUT READY |
| 输入文字 | Hello GUI 123 中文测试 | `entry.get()` 与预期逐字符相等 |
| 提交 | SUBMITTED Hello GUI 123 中文测试 | 提交状态变量与预期逐字符相等，提交按钮可用 |

控件字符串采用精确比较，区分大小写与空格；布尔与整数不互相替代。OCR 的 NFKC、忽略空白与大小写规则只用于视觉识别通道。截图前后独立状态或窗口边界发生变化时，这一观察作废，重新取样，不重发动作。

每次观察保存原始截图、OCR JSON、`state.json`；步骤摘要的 `verification.ocr`、`verification.state.checks` 分别保存期望、实际值和判定结果。`dispatch_started` 记录可能部分发送，`action_sent` 记录调用返回，只有两个通道都满足才能 `verified`。

这是受控演示应用的测试适配器，不能自动读取任意第三方软件的输入框。接入其他应用时需要提供 UI Automation、原生控件或该应用的状态接口；不得将窗口标题不变当作所有输入操作均成功的证据。没有适配器时拒绝执行，而非退回 OCR 单通道。

命令行运行前先启动演示程序，并保持测试窗口前台：

```powershell
.\.venv\Scripts\python.exe run_workflow.py --plan plans/friday_demo.json --device cuda --state-file output/demo-state.json
```

`false_ocr` 场景是显式故障注入：点击后保留实际未获得焦点的输入框，并给后续 OCR 结果增加高置信度 INPUT READY。图片保持真实；注入项在 results.json 标记 `fault_injection`。预期 OCR 为真、独立状态为假、流程超时失败，且只发送一次点击。它不代表 EasyOCR 在该截图上真实发生了该识别错误。

## 重名目标的处理顺序

1. 首先由精确窗口标题和首次锁定的 HWND 限定目标窗口；仅识别当前客户区，其他窗口的同名控件不进入候选集。同标题但 HWND 改变也终止。
2. 文本执行 Unicode NFKC、移除空白、casefold。默认完整文本匹配；只有明确设置 `contains: true` 才做包含匹配。归一化会把 `Ａ` 与 `A`、部分空格差异视为同名，必须按重名处理。
3. 排除置信度不达阈值、非有限置信度。坐标必须有限、框具有正面积、完整位于当前截图中；非法匹配框拒绝使用。不是选择置信度最高者。
4. 对剩余候选按物理框 `(top, left, bottom, right, 字符串形式的id)` 升序排列。严格按框的上边界先排，不做视觉行聚类；同一行高度轻微不同可能改变序号。序号只对应本帧候选集。
5. 无候选时为 `TargetNotFound`，仅重截图寻找，最多 `lookup_attempts` 次；超过上限记录 `target_unavailable`。多个候选且没有 `occurrence` 时立即 `AmbiguousTarget`，不点击、不以刷新碰运气消歧。
6. `occurrence` 是从 1 开始的正整数，不能为布尔值、0、负数或越界。配置为 2 表示选择本帧排序后的第二个有效候选。候选中存在完全相同的框时，不论是否指定序号都拒绝，因为几何位置无法区分 OCR 重复检测。部分重叠框仍会作为多个候选，不自动合并。
7. 选择后保存目标 ID、文字、置信度、框和点击中心；真正点击前再次检查窗口、遮挡和目标补丁。窗口移动或补丁灰度平均差超过 12 时重新观察及排序；不能沿用旧帧的坐标。动态页面无法保证 occurrence 代表同一个语义控件，调用方应先缩小应用内任务范围或增加专门的语义适配器。
8. 一旦开始派发动作，就不自动重放。后续只检查结果，避免重名、超时或部分发送导致重复操作。

## 失败分类

| category | 典型 code | 含义与后续处理 |
|---|---|---|
| localization_failure | AmbiguousTarget、target_unavailable、TargetError | 文字缺失、重名或坐标不可靠；派发前停止 |
| execution_failure | RuntimeError、其他输入异常 | 可能部分执行；保留 dispatch_started，不重放 |
| permission_denied | PermissionError、WinError 5 | 已确认访问被拒绝；停止，检查进程权限是否一致 |
| window_guard_failure | FocusLost | 目标失焦或 HWND 变化；停止 |
| verification_failure | verification_timeout、condition_already_satisfied | 双通道未同时成立，或动作前已经满足预期 |
| state_unavailable | StateUnavailable | 状态文件缺失、过期或属于其他窗口 |
| user_aborted | StopRequested、KeyboardInterrupt、FailSafeException | Esc、STOP 或故障保险触发 |

不把所有 SendInput 失败都标为权限不足：Windows 可能不提供明确的拒绝原因，未知异常仍保留原始 code/message，归入执行失败。权限分类已覆盖注入 PermissionError 的单元用例；是否存在真实管理员权限边界需要独立系统测试证明。
