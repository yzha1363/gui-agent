# NVIDIA NIM 首次联调排查记录

排查日期：2026 年 10 月 27 日（Australia/Sydney）。模型：`meta/llama-3.2-11b-vision-instruct`，服务根地址：`https://integrate.api.nvidia.com/v1`。

## 实际异常与结论

本次运行目录为 `output/week2-tuesday/20261026T165017_535245Z/`。读取 VS Code 终端看到的异常顺序是：

1. 等待 HTTPS 响应时出现 `TimeoutError: The read operation timed out`。
2. 随后在连接清理过程中出现 `KeyboardInterrupt`，程序被中断。

[原始 summary.json](../output/week2-tuesday/20261026T165017_535245Z/summary.json) 记录耗时 120.315928 秒、请求 1 次，未得到有效模型响应。中断未被捕获，导致它虽有结束时间，状态仍残留为 running。该原始记录保留不改写；不能用它判断程序仍在执行。

没有取得 401/403 等 HTTP 响应，不能据此认定密钥错误；同样没有模型内容返回，不能判断图片理解或 JSON 输出是否正确。

用户随后确认：该模型请求量过大，请求持续在服务端队列等待。本次原因据此记录为“模型服务拥挤、排队等待导致客户端读超时”；归因来源为用户在服务端侧的观察反馈，本地日志对应的错误仍是读超时，未收到 HTTP 429。后续收到有效模型响应后，再完成真实模型联调验收。

## 本次修复与检查

- `GuiAgent.run()` 捕获 KeyboardInterrupt，保存 `status:aborted` 和 `error.code:user_aborted`，不重试请求。
- CLI 被中断时返回退出码 130，保留 summary 路径，不再直接打印整段未处理异常栈。
- 新增 2 项测试，分别验证中断落盘和 CLI 退出码。当前 Agent 共 35 项测试、全项目 118 项测试及 7 组集成验证均通过。
- [全项目检查](../output/project-checks/20261027_035422_332316/summary.json) · [固定集成证据](../output/week2-tuesday/20261026T165441_668296Z/verification.json)。集成服务仍是本地模拟服务。

另做一次不带密钥的 GET `https://integrate.api.nvidia.com/v1/models`，返回 HTTP 200，耗时约 0.397 秒。该检查未上传截图、未发送模型推理请求，仅说明当时模型目录接口可访问，不证明目标模型正常推理或鉴权成功。普通工具沙箱的联网限制与用户终端中的读超时是两件不同的事。

## 下一次运行

待服务端排队缓解后，在已设置 `GUI_AGENT_API_KEY` 的同一 PowerShell 终端中再次运行之前的图片测试命令。每次使用新输出目录，不自动重试，避免进一步增加排队请求。若出现新的 HTTP 错误，按其状态码继续定位。

正常未中断的 socket 超时仍记录为 failed / model_timeout；手动中断为 aborted / user_aborted。真实模型联调继续保留待测，不能将本次超时或本地模拟验证计为推理通过。
