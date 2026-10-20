# CUDA 环境配置

项目大纲要求 Python 3.10+、PyTorch 2.2+，有 NVIDIA GPU 时配置 CUDA 加速；推荐显存 8GB 以上。大纲没有指定 CUDA 版本。

本项目使用独立虚拟环境 `.venv`：Python 3.13.7、PyTorch 2.8.0+cu128、torchvision 0.23.0+cu128、CUDA 12.8 运行时。硬件为 NVIDIA GeForce RTX 5070，显存约 12GB，现有驱动版本 617.14。

## 使用

在 PowerShell 中进入项目目录，然后使用环境内的 Python：

```powershell
cd D:\study\实习
.\.venv\Scripts\python.exe .\verify_cuda.py
```

也可以在当前 PowerShell 会话启用环境：

```powershell
. .\activate-cuda.ps1
python .\verify_cuda.py
```

如本机脚本策略限制执行，直接使用 `.venv\Scripts\python.exe` 即可。IDE 中选择 `D:\study\实习\.venv\Scripts\python.exe` 为解释器。系统原有 Python 的 PyTorch 是 CPU 版，运行项目时必须选择这个虚拟环境。

## 配置范围

已采用 PyTorch 官方 CUDA 12.8 二进制包，它包含 CUDA 运行时和 cuDNN，可以执行 PyTorch GPU 推理和训练。未安装系统级 CUDA Toolkit / nvcc；编译自定义 CUDA 扩展时才需要另行安装 Toolkit 和 C++ 编译工具链。`nvidia-smi` 显示的 CUDA 13.4 是驱动支持信息，项目实际运行时以 `torch.version.cuda` 为准。

本次配置针对大纲第一周的 CUDA 加速环境。后续 OCR、模型、Agent 框架和微调依赖应结合实际选用的项目再安装，不能将 CUDA 验证通过视为所有后续模型均已验证。

## 复现与检查

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install torch==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu128
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe .\verify_cuda.py
```

`verify_cuda.py` 检查 GPU 矩阵乘法与 CPU 结果一致、梯度反向传播、cuDNN 卷积及 torchvision CUDA NMS。成功后写入 `cuda-verification.json`。已安装依赖的精确版本见 `requirements-cuda-lock.txt`。

参考：[PyTorch 官方安装版本表](https://pytorch.org/get-started/previous-versions/)、[Blackwell 和 CUDA 12.8 支持](https://pytorch.org/blog/pytorch-2-7/)、[PyTorch 二进制包的 CUDA 运行时说明](https://discuss.pytorch.org/t/pytorch-finds-cuda-despite-nvcc-not-found/166754/2)。
