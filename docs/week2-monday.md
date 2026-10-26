# 第二周周一 数据采集与预处理完成记录

完成日期：2026 年 10 月 27 日，Australia/Sydney。目录名含 `T…Z` 的时间戳为 UTC，与本地日期可能不同。

配套文档：[任务使用说明](../第二周周一任务使用说明.md)（命令、参数、数据读取与排错） · [任务验证记录](../第二周周一任务验证记录.md)（用例、数量、证据与实际样本截图）。

已完成三种官方来源的小样本采集、统一记录格式、图片预处理、动作转换、缺陷隔离、去重、按任务划分及完整性校验。本次共 76 条记录、44 张真实公开截图，作为接口开发样本使用；没有全量下载数据集，也未训练模型或执行样本中的桌面操作。

## 实际样本与划分

| 来源 | 实际获取内容 | 记录 | 截图 | 训练 | 验证 | 独立评测 |
|---|---|---:|---:|---:|---:|---:|
| ScreenAgent | 6 个训练会话，各前 4 条正样本，含计划、动作、反思 | 24 | 24 | 16 | 8 | 0 |
| Multimodal-Mind2Web | 训练集 5 个位置，各取 4 行，实际覆盖 7 个任务 | 20 | 20 | 19 | 1 | 0 |
| WebArena | 从官方配置文件的 812 个任务中均匀选取 32 个配置 | 32 | 0 | 0 | 0 | 32 |
| 合计 | 76 条统一记录 | 76 | 44 | 35 | 9 | 32 |

按记录类型计：动作记录 32 条、计划 6 条、反思 6 条、评测任务 32 条。同一动作记录可能包含连续点击、输入和按键，动作标签数量不等于记录数量。本次有效样本全部通过结构校验，没有拒绝或完全重复记录；异常与重复分支另由测试覆盖。

ScreenAgent 的截图和鼠标键盘标注适合桌面数据格式验证；Multimodal-Mind2Web 的正候选是 DOM 元素，保留其语义标签及原始 CSS 矩形；WebArena 公开任务配置没有对应的演示截图和动作序列，因此只进入评测文件。来源分别为 [ScreenAgent 官方仓库](https://github.com/niuzaisheng/ScreenAgent)、[Multimodal-Mind2Web 官方数据卡](https://huggingface.co/datasets/osunlp/Multimodal-Mind2Web)、[WebArena 官方配置](https://github.com/web-arena-x/webarena/blob/main/config_files/test.raw.json)。

## 产物位置

| 内容 | 路径 |
|---|---|
| 原始数据与来源清单 | `data/week2/raw/20261026T145007Z/` |
| 处理后的数据 | `data/week2/processed/20261026T145603_897824Z/` |
| 机器可读完成摘要 | `output/week2-monday/20261026T145603_897824Z/summary.json` |
| 校验与测试证据 | 同上目录的 `verification.json` |
| 数据规范 | `docs/gui-data.schema.json` 与 `gui_data/schema.py` |
| 采集及处理入口 | `scripts/prepare_week2_data.py` |
| 适配器与处理管线 | `gui_data/adapters.py`、`gui_data/pipeline.py` |
| 新增测试 | `tests/test_data.py` |

处理目录包含 `records.jsonl`、`train.jsonl`、`validation.jsonl`、`evaluation_tasks.jsonl`、`rejected.jsonl`、`duplicates.jsonl`、`images/`、`preview.png` 和 `manifest.json`。后两份 JSONL 在本次实际样本中为空，表示没有被拒绝或去重的记录。

原始数据及派生样本由 Git 忽略，保存在本机；代码、规范、来源说明和不含原始样本的数量摘要可以纳入仓库。源码打包工具已加入 `gui_data/`。本次没有上传数据或推送仓库。

## 统一记录格式

| 字段 | 用途 |
|---|---|
| `schema_version`、`id` | 格式版本 1.0 与稳定记录标识 |
| `source` | 数据集名称、固定版本、原始划分、来源链接、许可标签、原始文件与哈希 |
| `group_id` | ScreenAgent 会话或 Mind2Web 任务；供分组划分 |
| `component_id` | 同任务、同图记录组成的连通分量；供防泄漏检查 |
| `record_type` | action、plan、reflection、task |
| `instruction` | 清理首尾空白后的任务指令 |
| `observation.image` | 图片路径、RGB 尺寸、原始路径与哈希、缩放比例、像素哈希；任务配置为 null |
| `observation.context` | 来源上下文或当前动作之前的历史；不放当前或未来动作答案 |
| `label` | 动作、计划、反思或评测答案；与模型输入分开 |
| `verification` | 独立执行后状态标签是否可用；本次均为 false，不编造成功证据 |
| `quality` | 坐标状态与质量提示，包括未验证的 DOM 视觉定位 |
| `split` | train、validation、evaluation |
| `execution_eligible` | 固定 false；离线数据不是可直接发送到桌面的控制计划 |

JSON Schema 描述文件结构；运行时使用 Python 校验器，并额外核对坐标范围、图片尺寸、哈希及跨文件划分一致性。当前动作词表覆盖实际取得的点击、输入、按键、移动、拖拽、计划和反思；还支持格式明确的滚动、等待等转换。未知动作拒绝处理，不静默丢弃。

模型输入应只取 `instruction` 和 `observation`。训练答案位于 `label`，评测参考答案也只位于 `label.evaluation`。ScreenAgent 的来源提示保留为不可信数据上下文，不能作为应用的系统指令或直接执行。

## 清洗与预处理规则

1. 先验证下载文件与派生原始行的 SHA-256。原始文件损坏时中止，避免把传输损坏误算成标注质量问题。
2. 检查任务指令、会话或任务 ID、图片存在性、图片解码、声明尺寸及动作字段。问题记录进入 `rejected.jsonl`，保留来源、错误码与原因。
3. 图片转为 RGB，最长边默认不超过 1600，不放大小图；保留原图以及实际 `scale_xy`。ScreenAgent 图像内坐标同步转换，非法点拒绝使用，不做边界夹取。带非默认 EXIF 方向的图片拒绝自动旋转，以免坐标错位。
4. 中文及键盘输入内容逐字符保留，包括有意义的空格与换行；只对任务指令清理首尾空白。拖拽起点来自同一动作序列此前的移动位置，缺少起点不猜测。
5. Mind2Web 只保留目标动作之前的历史，排除当前及未来动作；完整 HTML、负候选和带签名的图片 URL 留在原始目录，不复制进规范化模型输入。
6. DOM/CSS 矩形明确标为 `source_css_rect_unverified`，不自动转换为图片点击点或物理桌面坐标。多个正候选全部保留，不任意选一个。
7. 先按任务与原始解码图片哈希建立连通分量，再去重，最后按固定种子 42 划分；确保去重不会抹掉任务间的同图联系。本次 13 个分量，3 个进入验证集。同任务与相同图片均不跨集合，但这不是网站或领域留出评估。
8. WebArena 全部配置保持 evaluation，不进入 train 或 validation。三个来源均不补造缺失截图、动作或独立控件状态。

## 复现命令

在项目根目录使用已有虚拟环境，无需安装新依赖：

```powershell
# 获取一份新的有大小限制的官方小样本，再完成预处理；需要网络
.\.venv\Scripts\python.exe scripts/prepare_week2_data.py --fetch

# 使用现有原始数据离线重新处理，输出到新的时间戳目录
.\.venv\Scripts\python.exe scripts/prepare_week2_data.py --raw data/week2/raw/20261026T145007Z

# 只检查已经生成的数据、图片、哈希与划分，无网络或桌面输入
.\.venv\Scripts\python.exe scripts/prepare_week2_data.py --validate data/week2/processed/20261026T145603_897824Z

# 新增数据模块测试
.\.venv\Scripts\python.exe -m unittest tests.test_data -v

# 全项目回归、示例计划、依赖及历史证据检查
.\.venv\Scripts\python.exe scripts/check_project.py --with-evidence
```

原始清单固定了 ScreenAgent 与 WebArena 的 Git 提交。Mind2Web 保存仓库版本、原始行响应哈希，并检查每张缓存图片 URL 中的版本与仓库版本一致。远端更新后重新 `--fetch` 可能取得新版本；严格复现本次处理应使用已留存的原始目录。指定输出目录已存在时拒绝覆盖。

## 实际验证与测试

| 测试类型 | 模块 | 数量 | 实际结果 |
|---|---|---:|---|
| 单元 | 三源适配、坐标、标签及输入校验 | 15 | 全部通过 |
| 单元与集成 | 去重、任务与同图划分、文件完整性 | 8 | 全部通过 |
| 全项目回归 | 原有 60 项加本次 23 项 | 83 | 全部通过，新增项不重复计数 |
| 真实数据完整性 | 76 条记录与 44 张图片 | 1 批 | 哈希、尺寸、分区与文件一致性全部通过 |
| 真实数据复现 | 同一原始清单再次执行管线 | 1 次 | 六份 JSONL 与图片文件哈希完全一致 |

具体覆盖：NaN/越界坐标拒绝；拖拽缺少起点拒绝；空任务与空计划分类报错；键盘中文空格换行保留；DOM 矩形不冒充桌面坐标；原始 HOVER 不改写成 CLICK；当前与未来动作不进入历史；同图与同任务不跨划分；去重保留任务连接；缺图隔离；原始与派生文件篡改可检测。测试夹具是合成数据，与 76 条公开真实样本明确分开。

自动日志：`output/project-checks/20261027_020101_200949/`。计划结构、依赖检查与 8 份旧版桌面证据核对也通过；本任务没有重新执行桌面系统场景。

已查看接触表及一个 ScreenAgent 原始动作截图：例中 `(487,193)` 位于网页搜索框内。该抽查不代表全部标签的语义准确率；Mind2Web 的视觉定位尚未人工逐一确认。

## 来源许可与当前边界

ScreenAgent 的许可文件区分代码 MIT、数据 Apache-2.0、模型许可，本次记录的是数据部分。Multimodal-Mind2Web 数据卡标为 openrail，不用原始 Mind2Web 的 CC-BY 标签替代；WebArena 任务来自 Apache-2.0 仓库。原始声明随采集保留，详见 [来源登记](data-sources.json)。[ScreenAgent 许可](https://github.com/niuzaisheng/ScreenAgent/blob/main/LICENSE)、[Multimodal-Mind2Web 数据卡](https://huggingface.co/datasets/osunlp/Multimodal-Mind2Web/blob/main/README.md)、[WebArena 许可](https://github.com/web-arena-x/webarena/blob/main/LICENSE)。

本批是开发样本，验证集很小，不用于宣称模型泛化或任务成功率。部分网页截图很长，缩到 1600 后文字会变小；原图保留，后续进行模型训练前应选择高分辨率输入或视口切片并重新建立坐标对应。Mind2Web 当前适合动作语义与历史结构验证，不能直接当作已确认的像素定位真值。WebArena 的真实执行还需部署其自托管环境。

周二可基于本记录格式接入模型接口，将指令、图像与历史作为输入；模型生成的新动作仍需经过现有窗口保护和独立结果确认，不能回放离线截图中的旧坐标。
