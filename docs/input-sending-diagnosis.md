# 真实鼠标输入失败的补充排查

日期：2026 年 10 月 27 日（Australia/Sydney）。**退出游戏后同一代码的真实三步流程与 OCR 假阳性保护均符合预期，底层影响组件尚未确认。** 下文保留退出前的失败诊断。

用户在本机 PowerShell 运行后，`20261027_175059_026436/summary.json` 同样出现 `Mouse SendInput accepted 1/2 events`。因此，先前“可能仅与自动执行会话有关”的推测未得到本机对照支持。

## 已核对与修改

本进程为 64 位 Python，INPUT 为 40 字节、MOUSEINPUT 为 32 字节、KEYBDINPUT 为 24 字节，union 偏移 8；未发现明显的结构大小问题。结构成员与对齐参考 [Microsoft MOUSEINPUT 定义](https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-mouseinput)。

已把鼠标和 Unicode 按下/释放改为逐事件发送，每次请求 1 个并要求返回 1；按下后释放失败时仅做释放清理，不重复按下，不自动切换后端。新增中文、UTF-16 代理对和按键释放失败测试。

修改后的真实重测 `20261027_175330_409425` 在第一个事件就失败：`Mouse down SendInput accepted 0/1 event`。这表明拆分发送并未解决当前真实输入故障，不能把批量发送当作已确认根因。

## 零事件诊断

Microsoft 将 SendInput 返回值定义为成功插入的输入事件数，见 [SendInput 官方文档](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput)。本轮工具调用 `SendInput(0, NULL, 40)` 时却取得非零值 `1020941952`；Python 与 .NET 探针均观察到该数值，沙箱外 Python 也复现。

用户已在同一本机终端复现该数值。这不代表发送了这么多输入事件，也不能据此认定系统损坏、权限拒绝或特定软件拦截。零长度调用不能代替有效输入测试，诊断脚本已改为记录 inconclusive，而不是以非零探针值作为整体失败依据。没有修改系统保护、权限或第三方软件设置。

只读命令：

```powershell
.\.venv\Scripts\python.exe scripts/diagnose_input.py
```

它请求 0 个输入事件，不移动鼠标、不打字，不读取模型密钥。输出 zero_event_return、win32_last_error、结构大小及记录位置；退出码 0 仅表示诊断成功运行。即使探针返回 0，也不能证明真实点击可以工作。

本轮固定诊断：[diagnostic.json](../output/week2-thursday/input-diagnostic/20261027T065938_806069Z/diagnostic.json)。本机失败：[summary.json](../output/week2-thursday/desktop-normal/20261027_175059_026436/summary.json)。逐事件失败：[summary.json](../output/week2-thursday/desktop-normal/20261027_175330_409425/summary.json)。

## 验证状态

当前 37 项本阶段测试、全项目 185 项测试及 14 组模拟/HTTP 验证通过；最新两类真实桌面场景均符合预期，NIM 实际模型仍待测。旧失败记录未改写。发送数量检查与双通道确认继续保留，不将历史发送失败当作成功。

## 有效事件错误码与独立工具对照

在捕获失败瞬间的 GetLastError 后，`20261027_180733_632796` 记录实际鼠标按下返回 0/1，Win32 error=87。87 的官方含义为 ERROR_INVALID_PARAMETER（参数错误），见 [Microsoft 错误码表](https://learn.microsoft.com/en-us/windows/win32/debug/system-error-codes--0-499-)。它不是本轮已证实的权限拒绝，也不能仅凭错误码定位具体软件。

项目之外的标准电脑操作工具点击同一个独立测试窗口的 BEGIN RUN 时，也报 `SendInput sent 0 of 1 events; GetLastError=87`，未成功启动手动对照流程。这表明错误不限于项目的 Python 调用。空白诊断窗口已关闭，未修改用户的游戏或其他窗口。

当前 DLL 路径为 System32 的 USER32.dll，转发到 System32 的 win32u.dll；这只是路径检查，不等于验证了全部系统组件或排除了所有拦截。环境为 Console 会话，远程会话标志为 false。

窗口清单同时显示 VALORANT 正在运行。用户随后正常退出游戏，进程检查确认游戏进程已不在，再用相同代码复测。没有关闭防护服务、修改安全设置或绕过输入限制。

有效事件失败：[summary.json](../output/week2-thursday/desktop-normal/20261027_180733_632796/summary.json)。用户本机零事件记录：[diagnostic.json](../output/week2-thursday/input-diagnostic/20261027T070312_261208Z/diagnostic.json)。

## 退出游戏后的真实对照

正常流程 [summary.json](../output/week2-thursday/desktop-normal/20261027_193238_195187/summary.json)：completed、task_verified=true，3 次动作均正常返回，每步和最终目标的 OCR/独立控件状态均通过；中文输入完整，未改变运行代码或输入后端。

假阳性保护 [summary.json](../output/week2-thursday/desktop-false-ocr/20261027_193356_948040/summary.json)：主动注入 OCR 成功文本，实际控件为 WRONG STATE；OCR=true、state=false，任务正确以 verification_timeout 停止，仅一次点击。此处任务 failed 是预期保护结果，用例通过。

退出后的 [零事件诊断](../output/week2-thursday/input-diagnostic/20261027T083328_572813Z/diagnostic.json) 为 zero_event_return=0、win32_last_error=87，仍是 inconclusive。真实有效输入结果才是验收依据，不将零事件探针解释为系统健康检查。

退出前后对照支持故障与游戏运行时的输入环境有关，但本次是单机、顺序对照，未分离所有环境变量，不能确定为游戏或某个防护组件的具体机制。正式记录、两类真实截图及全部旧尝试见 [周四验证记录](../第二周周四任务验证记录.md)。
