# 第三周周一 数据审计与训练前置验证

任务标签为“第三周周一”，实际执行日期为 2026 年 10 月 30 日（Australia/Sydney）。已完成小规模视觉动作数据准备、本地模型与配置准备、真实视觉推理、一次 LoRA 优化器更新、显存检查及 adapter 保存重载。新增 21 项离线测试、全项目 232 项回归通过。

**训练前置验证通过；模型动作质量尚未通过验收。** 基础模型本次生成不是合法动作 JSON，不能计为 GUI 任务完成，也不能据一次更新宣称微调效果提升。NIM 联调独立保留待测。

配套：[使用说明](../第三周周一任务使用说明.md) · [验证记录](../第三周周一任务验证记录.md) · [机器可读索引](../output/week3-monday/verification.json)。

## 数据结果

| 项目 | 实际结果 |
|---|---|
| 处理来源 | 第二周正式批次 76 条记录、44 张截图 |
| 入选 | 12 条 ScreenAgent 动作记录，原始截图像素坐标 |
| 训练 / 验证 / 固定评测 | 8 / 2 / 2 条，各集合的任务与图片不交叉 |
| 排除 | 20 条未确认 DOM 视觉定位，12 条计划/反思，32 条无视觉演示任务 |
| 输入与标签 | 仅指令和图像进入用户输入；动作数组仅作 assistant 目标，原始来源提示不进入输入 |
| 数据状态 | SHA-256、坐标、分区内容、旧验证集隔离均通过；同源重建逐文件一致 |

固定目录为 `data/week3/monday-v1/`，包含 JSONL、来源与排除清单、图片、预览和 manifest。先用全部视觉记录连接任务/同图关系，再筛选，防止排除计划/反思时丢失分组联系；旧验证组不进入训练，并分别留作验证和固定评测。

这是最小开发样本，不是完整 GUI 训练集或代表性基准。标签不含独立执行后状态，execution_eligible=false；不能把截图中的旧坐标直接发送到真实桌面。

## 本地模型与单步结果

选择 [SmolVLM-256M-Instruct 官方模型](https://huggingface.co/HuggingFaceTB/SmolVLM-256M-Instruct/tree/7e3e67edbbed1bf9888184d9df282b700a323964) 作为小规模训练前置模型，固定提交 `7e3e67edbbed1bf9888184d9df282b700a323964`，13 个文件共 517,903,046 字节，逐个核对官方 LFS/Git 对象哈希。模型卡声明主要语言为英语，当前样本包含中文，因此本轮不承诺动作理解效果；该选择用于验证训练链路。

| 项目 | 实测 |
|---|---|
| 环境 | Python 3.13.7；RTX 5070；PyTorch 2.8.0+cu128 |
| 独立训练依赖 | Transformers 4.57.1、PEFT 0.17.1、Accelerate 1.10.1 |
| 训练设置 | bfloat16、SDPA、单图 512、batch=1、恰好 1 次更新 |
| LoRA | 语言模型 q_proj/v_proj；r=4、alpha=8；230,400 个可训练参数 |
| 标签掩码 | 163 个提示/图像 token 不计算损失，监督 63 个 assistant token（含 EOS） |
| 损失与梯度 | loss=2.795860；裁剪前梯度范数 1.039143，有限且非零 |
| 权重变化 | 120 个 LoRA 张量中 60 个变化；冻结参数无梯度 |
| 训练阶段显存 | 峰值分配 887.41 MiB、峰值预留 956.00 MiB |
| 保存重载 | LoRA 张量逐个完全相同；同精度探针 logits 最大差异 0 |

显存是该进程在一次训练步骤中的 PyTorch 统计，含驻留模型与输入，不等于整机占用，也不代表多步训练峰值。首次视觉生成 96 个 token 约 18.53 秒，单步训练约 0.30 秒；计时范围不同，且没有热推理基准，不能相互当作速度对照。

本轮新建 `.venv-training`，通过 `.pth` 引用原环境的 CUDA 包，训练依赖安装在新环境；没有替换原 `.venv` 的 torch。运行模型时只使用本地文件，未上传图片或调用 NIM，未发送桌面输入。

## 产物

| 路径 | 内容 |
|---|---|
| `configs/week3-monday.json` | 固定模型、数据、精度、LoRA 和单步配置 |
| `gui_training/data.py` | 训练数据筛选、冻结划分、复制与审计 |
| `gui_training/vlm.py` | assistant-only 批次、真实推理、单步更新与重载检查 |
| `validate_training.py` | 本地 GPU 前置验证入口 |
| `scripts/setup_training_env.py` | 独立训练环境准备 |
| `scripts/download_week3_model.py` | 固定版本下载与官方对象哈希核对 |
| `scripts/prepare_week3_data.py` | 数据构建/审计入口 |
| `scripts/validate_week3_monday.py` | 离线测试、数据重建及已有 GPU 证据审计 |
| `output/week3-monday/preflight-corrected/` | 基础输出、批次掩码、训练日志、adapter 与重载结果 |

首次 GPU 尝试的 adapter 权重一致，但混合精度检查上下文不同，logits 最大差异 0.375，整体被判失败。统一两侧 bf16 autocast 后复测通过；首次记录保留。周二可在本次环境和数据上开发多步训练，仍需扩大数据与正式评估。
