# 桌面 GUI 智能体基础模块

当前项目已实现屏幕截图、中英文 OCR、文字目标定位、鼠标键盘控制，以及按 JSON 计划执行并确认结果的基础闭环。它是四周实习计划第一周的开发成果，后续将接入多模态大模型、Agent 规划、数据处理和微调。

最近一次周五验收通过 30 项单元测试和 8 个独立窗口桌面场景。统一离线检查及周六整理记录见 [周六任务完成记录](周六任务完成记录.md)。

提交材料：[第一周实验报告 Word](output/reports/桌面GUI智能体基础模块实验报告-第一周.docx) · [个人计划 Word](output/个人计划.docx) · [测试汇总](output/friday-test-summary.json)。仓库收录范围见 [仓库内容说明](docs/repository-content.md)。

## 开始使用

在 PowerShell 中进入本项目。现有 `.venv` 已安装开发依赖，直接使用环境内的 Python：

```powershell
# 离线回归检查，不截图、不发送鼠标键盘输入
.\.venv\Scripts\python.exe scripts/check_project.py

# 打开周五独立演示窗口，选择 NORMAL，再点击 BEGIN RUN
.\.venv\Scripts\python.exe friday_demo.py
```

实际演示请保持目标窗口在前台。窗口会定位输入入口、输入 `Hello GUI 123 中文测试`、点击提交，并在每一步后截图确认结果。Escape 或 STOP 可以请求停止。

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
plans/               示例任务计划
tests/               离线单元测试，不依赖真实桌面输入
scripts/             统一检查和源码打包工具
docs/                架构说明和交付清单
screen_ocr.py         截图与 OCR 命令行
desktop_control.py    单次鼠标键盘或文字点击命令行
run_workflow.py       多步任务执行入口
thursday_demo.py      控制模块的独立测试窗口
friday_demo.py        完整闭环与异常场景的独立窗口
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

感知层可以枚举多屏并输出物理坐标，控制层目前只验收 Windows 主屏。真实桌面测试在独立验证窗口完成，没有修改系统显示缩放或分辨率。模型调用不能被停止请求立即打断，返回后才会继续检查停止状态。

当前任务步骤来自显式 JSON 计划，尚未接入大模型自动规划。技术调研报告也尚未编写；第一周交付清单已将它列为待完成事项。
