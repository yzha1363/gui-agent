# 第二周周四：闭环集成

日期：2026 年 10 月 27 日（Australia/Sydney）。对应“打通指令、截图、规划、操作与反馈，开发命令行交互，调试系统闭环”。

已完成闭环实现、命令行入口和验证：新增 37 项测试、全项目 185 项测试与 14 组模拟/HTTP 场景通过。用户退出游戏后，同一代码的真实三步流程及 OCR 假阳性保护场景均符合预期；每步和最终目标均保留 OCR 与独立控件状态证据。真实规划使用回放，NIM 实际推理仍待测。

配套文档：[使用说明](../第二周周四任务使用说明.md) · [验证记录](../第二周周四任务验证记录.md) · [验证索引](../output/week2-thursday/verification.json)。

退出游戏前的五次 Agent 正常流程失败及旧固定计划对照均保留。退出后未修改运行代码即通过，支持故障与游戏运行时的输入环境有关，但未定位具体组件；零事件探针不能代替有效输入验收，见 [补充诊断](input-sending-diagnosis.md)。

## 实现流程

```mermaid
flowchart LR
    G[调用方指令与最终目标] --> O[真实截图 OCR + 独立控件状态]
    O --> P[LangChain 多步规划]
    P --> C[重查窗口身份、位置与状态]
    C --> W[WorkflowRunner 逐步执行]
    W --> D[每步新 OCR + 独立状态确认]
    D --> F[再次观察并核对调用方最终目标]
    F --> R[摘要、反馈和失败分类]
```

| 文件 | 用途 |
|---|---|
| `gui_agent/session.py` | AgentSession：观察、规划、上下文检查、执行、可信最终目标与反馈 |
| `gui_agent/simulation.py` | 显式模拟应用、OCR、控制与假时钟；不访问桌面 |
| `run_agent.py` | 模拟演示、真实窗口规划预览及显式 execute 入口 |
| `friday_demo.py --agent-replay` | 独立窗口下集成规划回放与真实 OCR/控制的测试入口 |
| `docs/agent-goal.example.json` | 调用方提供的最终 OCR 与独立状态要求 |
| `gui_control/unicode_input.py` | 新增检查返回数量的鼠标 SendInput，部分发送后只做释放清理 |
| `tests/test_session.py` | 37 项闭环、异常、CLI 与鼠标发送检查测试 |
| `scripts/validate_week2_thursday.py` | 14 组模拟/HTTP 场景及已有真实测试的审计 |

沿用已有依赖，无新增安装。模型输入增加实际 OCR、独立状态和调用方最终目标；周二单步与周三纯规划入口继续保留。

## 关键行为

- 实际执行要求调用方提供最终 goal；模型自己写的步骤全部通过，也不能覆盖不同的调用方目标。
- 模型返回后重新检查停止状态、HWND、窗口区域和独立状态；变化则停止，避免使用过期上下文。
- 每个点击仍从新 OCR 定位，执行前重拍目标区域；每步都由现有运行器检查 OCR 与真实状态。
- 模型表达 done 不等于成功；只有独立最终目标已成立才可报告完成。
- 预览模式不派发动作；模型或动作失败不自动重试。中断和部分输入都保留记录。
- Thursday 的 Windows 点击路径检查指针实际位置及 SendInput 接收数量；失败不切回旧后端补发。既有非 Agent 演示仍保留原鼠标路径作历史对照。

## 快速验证

```powershell
# 完全模拟，不请求模型，不占用鼠标键盘
.\.venv\Scripts\python.exe run_agent.py --demo
```

该例会执行模拟的“聚焦 → 输入 → 提交 → 最终确认”，`environment:simulation`、`desktop_input_sent:false`。它证明软件链路可运行，不代表真实桌面或真实模型验收。

真实复测命令、成功截图和历史失败现场见配套文档。当前索引为 `offline_all_passed:true`、`full_project_passed:true`、`desktop_validation:passed`、`all_passed:true`，表示所列用例符合预期，不表示历史每次尝试成功。真实测试仅覆盖本轮 100% 缩放独立窗口；200% 与 NIM 实际推理仍待测。
