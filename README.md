# 桌面 GUI 智能体基础模块

当前项目已实现屏幕截图、中英文 OCR、文字目标定位、鼠标键盘控制，以及按 JSON 计划执行并确认结果的基础闭环；第二周增加数据处理，后续继续接入模型接口、任务规划和微调。

第二周周一的数据处理已完成：三种官方来源共 76 条小样本、44 张截图，统一为训练 35 条、验证 9 条、独立评测任务 32 条；新增 23 项数据测试，全项目 83 项回归通过。数据规范、复现命令和处理结果见 [周一完成记录](docs/week2-monday.md)。

2026 年 10 月 26 日修订增加 OCR 与真实控件状态的双重确认、失败分类和 OCR 冷热启动基准；60 项离线测试通过。新版真实桌面验证覆盖 100% 下五个场景和 125% 下正常三步流程，六组最终均符合预期。125% 首次测试发现状态发布故障，已修复并保留失败证据；200% 按要求保留待测，系统已恢复 100%。详见 [测试章节](docs/testing.md)。

提交材料：[修订版实验报告 Word](output/reports/桌面GUI智能体基础模块实验报告-修订版.docx) · [原第一周报告](output/reports/桌面GUI智能体基础模块实验报告-第一周.docx) · [个人计划 Word](output/个人计划.docx)。仓库收录范围见 [仓库内容说明](docs/repository-content.md)。

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

当前任务步骤来自显式 JSON 计划，尚未接入大模型自动规划。技术调研报告也尚未编写；第一周交付清单已将它列为待完成事项。
