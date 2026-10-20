# 第一周开发成果与交付清单

本清单对应压缩后第一周，即原大纲第 1 至 2 周。已经完成的开发主要来自周三至周五，周六补充代码说明、检查与交付整理。尚未完成的调研工作单列，不能把开发模块验收当作第一周全部完成。

| 项目 | 状态 | 主要文件或证据 |
|---|---|---|
| Python 与 CUDA 环境 | 已配置并验证 | CUDA环境配置说明.md、verify_cuda.py |
| 实时截图与区域截图 | 已实现并验证 | gui_perception/pipeline.py、周三任务验证记录.md |
| 中英文 OCR 与边界框标注 | 已实现并验证 | screen_ocr.py、output/wednesday-validation/ |
| 文字目标筛选与物理坐标 | 已实现并验证 | gui_control/targets.py、周四任务验证记录.md |
| 鼠标、键盘和 Unicode 输入 | 已实现并验证 | gui_control/controller.py、gui_control/unicode_input.py |
| 截图到执行再到确认的闭环 | 已实现并验证 | gui_workflow/、plans/friday_demo.json |
| 错误处理及停止行为 | 已验证固定场景 | 周五任务验证记录.md、output/friday-test-summary.json |
| 单元测试与测试记录 | 30 项测试及 8 个桌面场景已通过 | tests/、周五任务测试清单.md |
| 统一入口与架构说明 | 周六已补充 | README.md、docs/architecture.md |
| 可复用检查与交付工具 | 周六已补充 | scripts/check_project.py、scripts/package_project.py |
| GUI Agent 技术调研报告 | 待完成 | 需比较 Ui-TARS、Claude Computer Use 和 ScreenAgent |
| 真实多屏与系统缩放切换测试 | 尚未进行 | 当前只在主屏实测，其他尺寸使用可控图片验证 |
| Git 仓库与远程备份 | 本地仓库与远程私有仓库已建立 | yzha1363/gui-agent；以远程 main 分支为同步结果 |

## 交付方式

源码包包含三个核心模块、入口脚本、示例计划、测试、运行依赖文件和说明文档；选择 include-evidence 时附带周五汇总、各场景的 JSON 摘要和动作日志，以便核对动作次数。模型、虚拟环境、临时文件和实际桌面截图保留在本机，不进入源码包。

包内 JSON 摘要中的项目绝对路径转换为相对路径。摘要中引用的详细观察目录仍保留在本机，压缩包不包含那些目录里的图片和 OCR 明细；因此交付包适合代码审阅和复现，不是全部运行数据备份。

`requirements-project-lock.txt` 是完整虚拟环境快照，`requirements.txt` 是运行依赖入口，`requirements-cuda.txt` 指定 CUDA 版 PyTorch。这次只检查现有环境，没有验证一次全新的安装。

## 后续事项

1. 补齐技术调研报告，确认后续 Agent 规划和模型接口的技术路线。
2. 周日复盘本周成果、问题和第二周优先级。
3. 第二周从公开数据样本处理、模型调用接口和基础规划框架开始。
4. 在实际桌面软件中逐步扩大验证范围，单独记录多屏、缩放和动态界面的行为。

大模型训练、LoRA 微调、性能全面评估和作品集整理属于后续阶段，尚未在当前开发中完成。
