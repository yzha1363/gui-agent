# 桌面 GUI 智能体基础模块

当前项目已实现屏幕截图、中英文 OCR、文字目标定位、鼠标键盘控制，以及按 JSON 计划执行并确认结果的基础闭环；第二周增加数据处理、LangChain 模型接口、多步规划与执行集成，后续继续真实环境验收和微调。

第二周周一的数据处理已完成：三种官方来源共 76 条小样本、44 张截图，统一为训练 35 条、验证 9 条、独立评测任务 32 条；新增 23 项数据测试，全项目 83 项回归通过。数据规范、复现命令和处理结果见 [周一完成记录](docs/week2-monday.md)。

第二周周二已实现 LangChain Core 1.7.0 调用链、图片与历史输入、HTTP/回放接口及单步候选校验。含中断状态修复的 35 项新增测试、7 组集成验证和全项目 118 项回归通过；HTTP 使用本地模拟服务验证。已选择 NVIDIA NIM，首次真实请求读超时后被中断，真实推理仍待测，见 [NIM 排查记录](docs/nim-troubleshooting.md) 与 [周二完成记录](docs/week2-tuesday.md)。离线体验：`.\.venv\Scripts\python.exe agent_cli.py --demo`。

第二周周三增加子任务依赖、整份动作校验、有序动作表与 workflow 候选导出。新增 30 项测试、11 组集成验证及全项目 148 项回归通过；该阶段导出候选，后续实时执行见周四记录，真实模型质量待测。离线体验：`.\.venv\Scripts\python.exe agent_cli.py --mode plan --demo`，详见 [周三完成记录](docs/week2-wednesday.md)。

第二周周四已实现实时观察、规划、窗口/状态复核、逐步执行与调用方最终目标确认，新增 37 项测试、全项目 185 项测试及 14 组模拟/HTTP 场景通过。退出游戏后，同一代码的真实三步流程和 OCR 假阳性保护场景均符合预期；此前五次正常流程失败保留，具体影响组件未确认。规划使用回放，NIM 实际推理待测。模拟体验：`.\.venv\Scripts\python.exe run_agent.py --demo`，详见 [周四完成记录](docs/week2-thursday.md)。

第二周周五补齐打开浏览器、搜索、文件、本地消息、关闭应用五类受控任务，新增 26 项测试，全项目 211 项回归、10 组模拟与 10 组真实桌面场景均符合预期。独立 Edge 使用本地页面和测试收件箱；规划仍为回放。运行 `.\.venv\Scripts\python.exe run_basic_tasks.py --with-protection`，真实测试显式加 `--desktop`，详见 [周五完成记录](docs/week2-friday.md)。

第三周周一完成 12 条视觉动作数据的 8/2/2 固定划分，以及本地 SmolVLM 的真实视觉推理、一次 LoRA 更新与保存重载。新增 21 项测试、全项目 232 项回归通过，单步训练峰值分配约 887 MiB。基础模型本次未生成合法动作 JSON，动作质量仍待评估；NIM 独立待测。详见 [周一完成记录](docs/week3-monday.md)。

第三周周二已完成 3 轮小规模 LoRA 训练，共 12 次更新，以及第 5 步停止后跨进程恢复的对照；恢复后的 adapter、损失与优化器等状态和连续训练完全一致。新增 18 项测试、全项目 250 项回归通过。验证损失从 3.357203 变为 3.313339，重载后实际生成仍不是合法动作 JSON，效果比较待周三。详见 [周二完成记录](docs/week3-tuesday.md)。

2026 年 10 月 26 日修订增加 OCR 与真实控件状态的双重确认、失败分类和 OCR 冷热启动基准；60 项离线测试通过。新版真实桌面验证覆盖 100% 下五个场景和 125% 下正常三步流程，六组最终均符合预期。125% 首次测试发现状态发布故障，已修复并保留失败证据；200% 按要求保留待测，系统已恢复 100%。详见 [测试章节](docs/testing.md)。

当前汇报：[第一至第二周实验汇报 Word](output/reports/桌面GUI智能体实验汇报-第一至第二周.docx) · [配套截图与原始记录包](output/reports/桌面GUI智能体实验汇报-第一至第二周-截图与记录.zip)。汇报共 17 页，嵌入 13 张已有实验图，包含测试数量、冷热 OCR、真实缩放、双重确认、失败修复及待测范围；记录包保留逐步证据与 SHA-256 清单。

历史提交材料：[修订版实验报告 Word](output/reports/桌面GUI智能体基础模块实验报告-修订版.docx) · [原第一周报告](output/reports/桌面GUI智能体基础模块实验报告-第一周.docx) · [个人计划 Word](output/个人计划.docx)。仓库收录范围见 [仓库内容说明](docs/repository-content.md)。

## 开始使用

在 PowerShell 中进入本项目。现有 `.venv` 已安装开发依赖，直接使用环境内的 Python：

```powershell
# 离线回归检查，不截图、不发送鼠标键盘输入
.\.venv\Scripts\python.exe scripts/check_project.py

# 打开周五独立演示窗口，选择 NORMAL，再点击 BEGIN RUN
.\.venv\Scripts\python.exe friday_demo.py
```

实际演示请保持目标窗口在前台。窗口会定位输入入口、输入 `Hello GUI 123 中文测试`、点击提交，并在每一步后截图确认结果。Escape 或 STOP 可以请求停止。

新版每步必须同时满足 OCR 回显与独立控件状态；演示程序在主线程导出输入框内容、焦点及提交状态，运行器读取的状态必须新鲜且绑定同一 HWND。接口、重名候选排序和失败分类见 [双重确认与重名目标规则](docs/verification-and-target-rules.md)。

```powershell
# CLI 需要先启动演示程序，提供独立状态导出，并在倒计时后切到目标窗口
.\.venv\Scripts\python.exe run_workflow.py --plan plans/friday_demo.json --device cuda --state-file output/demo-state.json

# 保存每条离线用例、实际结果和模块数量
.\.venv\Scripts\python.exe scripts/test_revision.py

# 同图测量 3 次新进程冷启动及每进程 10 次热推理
.\.venv\Scripts\python.exe benchmark_ocr.py
```

查看基本截图及识别功能：

```powershell
.\.venv\Scripts\python.exe screen_ocr.py --device cuda
```

结果保存在 `output/` 下的独立时间戳目录中；原图、标注图、OCR 结果和执行日志不会相互覆盖。

## 新环境安装

本机验证环境为 Windows、Python 3.13.7、PyTorch 2.8.0+cu128、CUDA 12.8 运行时和 RTX 5070。大纲的最低版本要求为 Python 3.10+、PyTorch 2.2+，其他版本组合尚未在本机验收。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-cuda.txt
.\.venv\Scripts\python.exe -m pip install wheel
.\.venv\Scripts\python.exe -m pip install -r requirements.txt --no-build-isolation
.\.venv\Scripts\python.exe verify_cuda.py
.\.venv\Scripts\python.exe scripts/check_project.py
```

先安装 CUDA 版 PyTorch，再安装通用运行依赖。`requirements-project-lock.txt` 是现有完整虚拟环境的版本快照，包含此前制作文档时安装的工具；运行本项目使用 `requirements.txt` 即可，无需安装全部文档工具。CUDA 驱动和运行时的区别见 [CUDA 环境配置说明](CUDA环境配置说明.md)。

EasyOCR 模型保存在 `models/easyocr/`，首次在新环境运行识别时会下载。源码交付包不附带虚拟环境或模型，安装说明是复现步骤，本轮没有另外创建全新环境重装。

## 目录与入口

```text
gui_perception/       截图、DPI 感知、OCR、坐标转换与标注
gui_control/          文字筛选、窗口检查、鼠标键盘与 Unicode 输入
gui_workflow/         JSON 计划校验、观察、动作执行和结果确认
gui_data/             三源小样本采集、格式适配、清洗、分组划分与完整性校验
gui_agent/            LangChain 框架、模型接口、单步候选及子任务/动作计划编译
data/week2/           本地原始数据和派生数据，不纳入 Git
plans/               示例任务计划
tests/               离线单元测试，不依赖真实桌面输入
scripts/             统一检查和源码打包工具
docs/                架构说明和交付清单
screen_ocr.py         截图与 OCR 命令行
desktop_control.py    单次鼠标键盘或文字点击命令行
run_workflow.py       多步任务执行入口
thursday_demo.py      控制模块的独立测试窗口
friday_demo.py        完整闭环与异常场景的独立窗口
benchmark_ocr.py      固定输入的进程冷启动与 Reader 热推理基准
agent_cli.py          已有截图/数据样本的模型调用和离线回放，不发送桌面输入
run_agent.py          模拟闭环、真实窗口预览与显式执行，含独立最终目标确认
models/              本地 OCR 模型，不纳入源码包
output/              选定实验记录、报告和交付包；普通桌面截图留在本机
tmp/                 文档生成和临时检查文件
```

## 进一步查看

| 内容 | 文档 |
|---|---|
| 从截图到执行结果的模块关系 | [架构说明](docs/architecture.md) |
| 功能、验证证据和未完成事项 | [第一周交付清单](docs/week1-delivery.md) |
| 截图、OCR 与坐标约定 | [周三使用说明](周三任务使用说明.md) |
| 点击、输入、滚动和拖拽 | [周四使用说明](周四任务使用说明.md) |
| JSON 计划、条件确认与失败处理 | [周五使用说明](周五任务使用说明.md) |
| 已运行的桌面场景和结果目录 | [周五验证记录](周五任务验证记录.md) |
| 第二周数据采集、清洗、格式与测试 | [周一完成记录](docs/week2-monday.md) |
| 第二周周一运行命令、产物与常见问题 | [周一任务使用说明](第二周周一任务使用说明.md) |
| 第二周周一用例、实测结果与样本截图 | [周一任务验证记录](第二周周一任务验证记录.md) |
| 第二周周二框架选型、接口与完成范围 | [周二完成记录](docs/week2-tuesday.md) |
| 第二周周二运行方式、服务配置与排错 | [周二任务使用说明](第二周周二任务使用说明.md) |
| 第二周周二测试分类、用例与输入图片 | [周二任务验证记录](第二周周二任务验证记录.md) |
| 第二周周三任务拆解、动作表示与候选编译 | [周三完成记录](docs/week2-wednesday.md) |
| 第二周周三规划运行、输出与参数 | [周三任务使用说明](第二周周三任务使用说明.md) |
| 第二周周三测试分类、用例与输入图 | [周三任务验证记录](第二周周三任务验证记录.md) |
| 第二周周四集成、模拟与真实测试边界 | [周四完成记录](docs/week2-thursday.md) |
| 第二周周四运行、目标配置与复测 | [周四任务使用说明](第二周周四任务使用说明.md) |
| 第二周周四测试数量、用例、真实截图与失败对照 | [周四任务验证记录](第二周周四任务验证记录.md) |
| 第二周周五五类任务与实现边界 | [周五完成记录](docs/week2-friday.md) |
| 第二周周五模拟、真实桌面与故障运行 | [周五任务使用说明](第二周周五任务使用说明.md) |
| 第二周周五测试数量、分类、截图与原始记录 | [周五任务验证记录](第二周周五任务验证记录.md) |
| 第三周时间表与验收要求 | [第三周任务安排](docs/week3-plan.md) |
| 第三周周一数据与本地模型验证 | [周一完成记录](docs/week3-monday.md) |
| 第三周周一环境、命令与产物 | [周一任务使用说明](第三周周一任务使用说明.md) |
| 第三周周一真实训练结果及失败对照 | [周一任务验证记录](第三周周一任务验证记录.md) |
| 第三周周二多样本训练与恢复验证 | [周二完成记录](docs/week3-tuesday.md) |
| 第三周周二训练、保存、恢复命令 | [周二任务使用说明](第三周周二任务使用说明.md) |
| 第三周周二实测损失、测试数量与失败分析 | [周二任务验证记录](第三周周二任务验证记录.md) |

## 检查与打包

```powershell
# 回归测试、计划结构校验、依赖检查，并保存检查日志
.\.venv\Scripts\python.exe scripts/check_project.py

# 额外核对本地保存的周五桌面结果和动作次数；不重新操作桌面
.\.venv\Scripts\python.exe scripts/check_project.py --with-evidence

# 打包代码、配置、文档和测试
.\.venv\Scripts\python.exe scripts/package_project.py

# 同时附带周五 JSON 结果摘要与动作日志；不附带截图
.\.venv\Scripts\python.exe scripts/package_project.py --include-evidence
```

检查报告位于 `output/project-checks/`，源码包位于 `output/deliverables/`。包内 `manifest.json` 记录每个文件的大小和 SHA-256；打包后会重新读取压缩包检查完整性和哈希。

## 当前范围

感知层可以枚举多屏并输出物理坐标，控制层目前只验收 Windows 主屏。本次通过系统显示设置切换 100% 与 125%，实测窗口分别为 96 与 120 DPI，分辨率均为 1920×1080；双重确认在两档正常流程通过。四类异常仅在 100% 验收；200% 尚待测。模型调用不能被停止请求立即打断，返回后才会继续检查停止状态。

当前已把模型规划接口与实时观察、执行、独立目标反馈连接；周四受控闭环和周五五类基础任务及故障保护已验证。周五为 100% 缩放、1920×1080 的独立 Edge 与测试启动器，搜索和文件在本地协作页面完成，消息只写测试收件箱，不代表任意业务应用已验收。NIM 模型质量、更多应用与缩放待测。技术调研报告尚未编写；第一周交付清单已将它列为待完成事项。
