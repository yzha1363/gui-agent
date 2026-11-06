# 第二周周五 基础任务测试

对应四周日历第二周周五的五类任务：打开浏览器、搜索、打开文件、发送消息、关闭应用，以及整理结果和修复关键问题。

本轮新增可重复的基础任务测试入口和独立本地应用适配器。26 项新增测试、全项目 211 项回归、10 组模拟场景和 10 组真实桌面场景均符合预期；真实正常任务完成 5/5，另外五组是预期被拒绝的故障保护。规划使用固定回放，NIM 实际推理仍待测。

配套：[使用说明](../第二周周五任务使用说明.md) · [验证记录与真实截图](../第二周周五任务验证记录.md) · [验证索引](../output/week2-friday/verification.json)。

## 实现范围

| 任务 | 实际执行范围 | 独立验收证据 |
|---|---|---|
| 打开浏览器 | Agent 点击测试启动器的 OPEN BROWSER，启动独立配置的 Edge 本地页面 | 本轮唯一标题、HWND、PID，实际窗口仍存在 |
| 搜索 | 在真实 Edge 页面中聚焦、输入 GUI Agent、搜索本地目录 | 应用保存的查询与实际目录匹配结果 |
| 打开文件 | 在真实 Edge 页面点击 OPEN FILE，读取本轮生成的 sample.txt | 实际读取的内容与 SHA-256 |
| 发送消息 | 在真实 Edge 页面输入并提交至本地测试收件箱 | inbox.json 中恰好一条记录，内容与输入一致 |
| 关闭应用 | Agent 点击测试启动器的 CLOSE APP，向本轮 Edge 窗口发送 WM_CLOSE | 同一 HWND/PID 对应的窗口已消失 |

上述每项还必须通过 OCR 回显确认，不能仅凭输入调用返回或界面提示判成功。启动/关闭任务观察的是持续存活的测试启动器；并未扩展 AgentSession 为任意跨窗口代理。搜索和打开文件通过协作测试页面完成，不代表公共搜索引擎、资源管理器、Office 或任意通信软件已验收。

## 文件与运行

| 文件 | 用途 |
|---|---|
| `gui_tasks/basic.py` | 五类任务契约、固定规划、本地应用状态、模拟执行与证据审计 |
| `gui_tasks/browser.py` | 本地 HTTP 页面、独立 Edge、受控生命周期窗口和真实观察 |
| `run_basic_tasks.py` | 默认模拟，显式 desktop 才使用真实鼠标键盘 |
| `tests/test_basic_tasks.py` | 26 项任务、文件、消息、HTTP 与窗口身份检查 |
| `scripts/validate_week2_friday.py` | 重跑离线场景、重新核对已保存的真实双通道证据、保留历史尝试 |

```powershell
.\.venv\Scripts\python.exe run_basic_tasks.py --with-protection
.\.venv\Scripts\python.exe run_basic_tasks.py --desktop --with-protection
```

不新增 Python 依赖；真实模式需要已安装 Microsoft Edge 和可用的 OCR 环境。页面服务只绑定 127.0.0.1，每轮有独立地址与浏览器配置，不使用个人浏览器配置。消息只写本地测试收件箱。

本轮真实环境为 96 DPI / 100% 缩放、1920×1080，仅各场景一次正式运行。耗时字段是会话耗时，排除了浏览器准备和 OCR Reader 初始化，不作为 OCR 冷热启动或稳定成功率基准；已有 [OCR 基准入口](../benchmark_ocr.py) 继续单独使用。125% 本周未补测，200% 与真实 NIM 继续待测。
