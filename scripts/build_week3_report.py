"""Create the week three report from frozen records without rerunning experiments."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import zipfile

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output/reports'
WORK = ROOT / 'tmp/report-week3'
NAME = '桌面GUI智能体第三周实验汇报'
DOCX = OUT / (NAME + '.docx')
ZIP = OUT / (NAME + '-截图与记录.zip')
MON = 'output/week3-monday/preflight-corrected'
TUE = 'output/week3-tuesday/full-initial'
WED = 'output/week3-wednesday/comparison-initial'
THU = 'output/week3-thursday/20261106T060234_814026Z'
FRI = 'output/week3-friday/20261106T091722_627143Z'
DESK = 'output/week3-friday/desktop-validation'
BENCH = 'output/week3-friday/benchmark-isolated/20261106_172257_862887'
CHECK = 'output/project-checks/20261106_201457_908770/summary.json'


def read(path):
    return json.loads((ROOT / path).read_text(encoding='utf-8-sig'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Report:
    def __init__(self):
        self.doc = Document()
        self.figures, self.files, self.page = [], set(), 0
        section = self.doc.sections[0]
        section.page_width, section.page_height = Inches(8.5), Inches(11)
        section.top_margin, section.bottom_margin = Inches(.68), Inches(.65)
        section.left_margin, section.right_margin = Inches(.72), Inches(.72)
        section.header_distance, section.footer_distance = Inches(.28), Inches(.28)
        for name in ['Normal', 'Title', 'Subtitle', 'Heading 1', 'Heading 2', 'Caption']:
            s = self.doc.styles[name]
            s.font.name = 'Calibri'
            s._element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'), '宋体' if name == 'Normal' else '微软雅黑')
            s.font.color.rgb = RGBColor(0, 0, 0)
            s.font.underline = False
            s.font.italic = False
            ppr = s._element.find(qn('w:pPr'))
            if ppr is not None:
                for b in list(ppr.findall(qn('w:pBdr'))):
                    ppr.remove(b)
        n = self.doc.styles['Normal']
        n.font.size = Pt(11)
        n.paragraph_format.line_spacing = 1.2
        n.paragraph_format.space_after = Pt(7)
        for name, size in [('Title', 24), ('Subtitle', 12), ('Heading 1', 17), ('Heading 2', 12), ('Caption', 9)]:
            s = self.doc.styles[name]
            s.font.size = Pt(size)
            s.paragraph_format.space_after = Pt(8)
            s.paragraph_format.space_before = Pt(0 if name != 'Heading 2' else 9)
        for name in ['Heading 1', 'Heading 2']:
            self.doc.styles[name].paragraph_format.keep_with_next = True
        h = section.header.paragraphs[0]
        h.add_run('桌面 GUI 智能体  第三周实验汇报').font.size = Pt(8)
        f = section.footer.paragraphs[0]
        f.alignment = WD_ALIGN_PARAGRAPH.CENTER
        f.add_run('第三周实验记录  ·  ').font.size = Pt(8)
        field = OxmlElement('w:fldSimple'); field.set(qn('w:instr'), 'PAGE'); f._p.append(field)
        props = self.doc.core_properties
        props.title = NAME
        props.subject = '本地视觉模型训练 对照评测 有界恢复与真实桌面验证'
        props.author = ''
        props.created = datetime(2026, 11, 7, tzinfo=timezone.utc)
        props.modified = datetime(2026, 11, 7, tzinfo=timezone.utc)

    def page_start(self, title, first=False):
        if not first: self.doc.add_page_break()
        self.page += 1
        self.doc.add_paragraph(title, 'Title' if first else 'Heading 1')

    def p(self, text, small=False, bold=False):
        p = self.doc.add_paragraph()
        r = p.add_run(text); r.bold = bold
        if small:
            r.font.size = Pt(9)
            p.paragraph_format.line_spacing = 1.1
            p.paragraph_format.space_after = Pt(6)
        return p

    def h(self, text):
        self.doc.add_paragraph(text, 'Heading 2')

    def table(self, headers, rows, widths, size=9.5):
        t = self.doc.add_table(rows=1, cols=len(headers))
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        t.autofit = False
        for c, w in zip(t.columns, widths): c.width = Inches(w)
        for i, values in enumerate([headers] + list(rows)):
            cells = t.rows[0].cells if i == 0 else t.add_row().cells
            trpr = t.rows[i]._tr.get_or_add_trPr()
            trpr.append(OxmlElement('w:cantSplit'))
            if not i: trpr.append(OxmlElement('w:tblHeader'))
            for j, (c, value) in enumerate(zip(cells, values)):
                c.width = Inches(widths[j]); c.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
                c.text = str(value)
                pr = c._tc.get_or_add_tcPr()
                borders = OxmlElement('w:tcBorders')
                for edge in ['top', 'left', 'bottom', 'right']:
                    el = OxmlElement('w:' + edge)
                    for key, val in [('val', 'single'), ('sz', '4'), ('color', 'D9D9D9')]: el.set(qn('w:' + key), val)
                    borders.append(el)
                pr.append(borders)
                shd = OxmlElement('w:shd'); shd.set(qn('w:fill'), 'DCE8F3' if i == 0 else 'FFFFFF'); pr.append(shd)
                mar = OxmlElement('w:tcMar')
                for edge, val in [('top', '85'), ('bottom', '85'), ('left', '100'), ('right', '100')]:
                    e = OxmlElement('w:' + edge); e.set(qn('w:w'), val); e.set(qn('w:type'), 'dxa'); mar.append(e)
                pr.append(mar)
                for p in c.paragraphs:
                    p.paragraph_format.space_after = Pt(1)
                    p.paragraph_format.space_before = Pt(1)
                    p.paragraph_format.line_spacing = 1.12
                    if j > 0 and len(str(value)) < 14: p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    for r in p.runs:
                        r.font.size = Pt(size); r.bold = i == 0
        p = self.doc.add_paragraph()
        p.paragraph_format.space_after = Pt(2); p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.line_spacing = .5
        p.add_run().font.size = Pt(2)

    def evidence(self, *paths):
        for name in paths:
            p = ROOT / name
            if not p.is_file(): raise FileNotFoundError(p)
            self.files.add(p.relative_to(ROOT).as_posix())

    def figure(self, path, caption, kind, width=6.5, max_height=4.0):
        self.evidence(path)
        for name in ['results.json', 'state.json', 'result.json']:
            p = (ROOT / path).parent / name
            if p.is_file(): self.evidence(p.relative_to(ROOT).as_posix())
        with Image.open(ROOT / path) as im:
            width = min(width, max_height * im.width / im.height)
        p = self.doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.keep_with_next = True; p.paragraph_format.space_after = Pt(3)
        shape = p.add_run().add_picture(str(ROOT / path), width=Inches(width))
        shape._inline.docPr.set('descr', caption)
        n = len(self.figures) + 1
        cp = self.doc.add_paragraph(f'图 {n}  {caption}', 'Caption')
        cp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        self.figures.append({'number': n, 'source': path, 'caption': caption, 'kind': kind,
                             'sha256': digest(ROOT / path), 'planned_page': self.page})


def build():
    OUT.mkdir(parents=True, exist_ok=True); WORK.mkdir(parents=True, exist_ok=True)
    mon, tue, wed = read(MON+'/summary.json'), read(TUE+'/summary.json'), read(WED+'/summary.json')
    fri, project = read(FRI+'/verification.json'), read(CHECK)
    assert mon['success'] and tue['success'] and wed['success']
    assert not wed['model_quality_accepted'] and fri['full_project_tests'] == 347
    assert fri['desktop_all_passed'] and len(fri['desktop_cases']) == 11 and project['all_passed']
    r = Report()

    r.page_start('桌面 GUI 智能体第三周实验汇报', True)
    r.doc.add_paragraph('本地视觉模型训练 对照评测 有界恢复与真实桌面验证', 'Subtitle')
    r.p('项目：基于多模态大模型的桌面 GUI 智能体开发与优化。汇报日期：2026 年 11 月 7 日，时区 Australia/Sydney。本文面向项目指导与阶段验收，汇总第三周已保存的实验结果、截图和失败记录。', small=True)
    r.h('本周结论')
    r.p('本周完成了小样本视觉动作数据审计、本地 SmolVLM 的 LoRA 训练与断点恢复、基础模型和两个 adapter 的离线对照评测，以及带次数和时间上限的桌面恢复机制。系统 v2 完成 347 项回归，50 组模拟或回环 HTTP 场景符合预期；真实专用窗口 11 类场景的最新结果符合预期，其中 4 类完成任务，7 类正确停止。')
    r.p('模型动作质量尚未通过：固定测试集仅 2 条、来自 1 个任务组，三组模型的动作 schema 与标注联合命中均为 0/2。训练验证损失的小幅下降和 JSON 可解析率变化不足以说明 GUI 操作能力提升。真实桌面测试使用规划回放，不能作为模型自主规划或 NIM 推理通过的证据。')
    r.table(['验收对象', '证据与结果', '结论'], [
        ['训练链路', '真实 CUDA LoRA 更新、重载及恢复一致性', '工程通过'],
        ['动作质量', 'base / best / final 联合命中均为 0/2', '未通过'],
        ['恢复与保护', '50 组离线场景；11 类真实桌面场景', '限定范围通过'],
        ['显示缩放', '本周真实桌面为 100% / 96 DPI', '200% 待测'],
        ['真实模型闭环', '本地模型仅离线评测；桌面规划为回放', '待测'],
    ], [1.25, 4.25, 1.5])
    r.h('记录范围')
    r.p('“周一至周五”沿用任务标签，不等同实际日历日期。训练记录产生于 10 月 30 至 31 日，评测、恢复和桌面补测产生于 11 月 6 日。11 月 7 日仅整理汇报，未补造实验，也未重新触发鼠标键盘测试。截图按公开数据输入、模拟画面、真实桌面和日志图表分别标明。')
    r.p('全文结构：任务与环境 → 数据和训练 → 对照评测 → 恢复与鲁棒性 → 测试与性能 → 失败分类 → 交付与证据索引。', small=True)

    r.page_start('一 任务安排与实验环境')
    r.table(['任务标签', '完成内容', '验收记录'], [
        ['周一', '数据筛选、分组隔离、模型下载校验、一次真实更新', '21 项新增测试；累计 232'],
        ['周二', '3 epoch LoRA、最佳权重选择、受控中断与续训', '18 项新增测试；累计 250'],
        ['周三', '冻结评测方案、验证提示词、三模型配对比较', '25 项新增测试；累计 275'],
        ['周四', '预算约束、状态新鲜度、子任务台账与恢复', '45 项新增测试；累计 320'],
        ['周五', '定位复核修复、性能基准、v2 与真实桌面补测', '新增 22＋5；最终累计 347'],
    ], [0.75, 4.1, 2.15])
    r.h('计算与软件环境')
    r.table(['项目', '固定配置'], [
        ['硬件与系统', 'Windows；NVIDIA RTX 5070，约 12 GiB 显存'],
        ['训练环境', 'Python 3.13.7；PyTorch 2.8.0+cu128；独立 .venv-training'],
        ['训练依赖', 'Transformers 4.57.1；PEFT 0.17.1；Accelerate 1.10.1'],
        ['模型', 'HuggingFaceTB/SmolVLM-256M-Instruct'],
        ['模型版本', '7e3e67edbbed1bf9888184d9df282b700a323964'],
        ['数值与视觉输入', 'CUDA bf16；SDPA；图像尺寸 512；不拆分图像'],
        ['桌面补测', '1920×1080；100% 缩放；GetDpiForWindow=96'],
    ], [1.25, 5.75])
    r.p('模型 13 个文件共 517,903,046 字节，按固定版本和上游 Git/LFS 信息核验。训练环境复用既有 CUDA PyTorch，并隔离新增训练依赖。选用该小模型是为了在本机完成训练链路验证；模型主要面向英文，本实验包含中文任务，不能据此假定其具备成熟 GUI 或中文动作能力。')
    r.h('模块关系')
    r.p('数据审计与 LoRA 训练产出 adapter；离线评测独立读取冻结数据与权重。桌面执行侧由截图与 OCR、规划、目标定位、原生输入、结果确认构成，RecoverySession 为整条链路设置预算并记录子任务状态。离线模型评测不向桌面控制器派发动作。')

    r.page_start('二 数据筛选与隔离审计')
    r.p('以第二周的小规模公开来源集为起点，先按任务组和同图关系划分，再保留可映射为像素动作的记录。源集合含 76 条记录、44 个屏幕输入，本轮采用 12 条 ScreenAgent 样本，固定为训练 8 条、验证 2 条、测试 2 条，共 6 个任务组。验证和测试均未参与梯度更新。')
    r.table(['去向', '记录数', '任务组数或排除原因'], [
        ['训练', '8', '4 个任务组'], ['验证', '2', '1 个任务组'], ['固定测试', '2', '1 个任务组'],
        ['排除', '20', 'Mind2Web DOM 动作未完成像素映射验证'],
        ['排除', '12', '计划或反思内容，不是本轮动作监督'],
        ['排除', '32', 'WebArena 无可用图像'],
    ], [1.0, .8, 5.2])
    r.figure('data/week3/monday-v1/preview.png', '数据来源历史预览 图内分组标签早于最终拆分', 'public_dataset_historical_preview', max_height=3.15)
    r.p('预览图用于展示图像来源，图内旧 train 标签不代表最终划分；最终 8/2/2 以 manifest 和 split 文件为准，隔离验收不依赖预览。源历史对话、提示词及答案不作额外输入，保留既有 held-out 边界。记录 execution_eligible=false，且无独立执行后置条件；标注坐标仅适用于原图。本规模只支持流程试验。', small=True)
    r.p('来源：ScreenAgent，Apache 2.0，固定仓库修订 2312c852908dda5fbc43d903fad5929a9dec649d。冻结 manifest SHA-256：56138ae831ca3da30d0e132a4c6056853c6247b3a0f23e4024814fd485ee1b22。证据 E01。', small=True)

    r.page_start('三 周一真实训练预检')
    r.p('周一先执行一次真实视觉推理，再以单条训练样本完成 LoRA 优化器更新、梯度与显存检查、adapter 保存和新实例重载。只训练语言层 q/v LoRA，r=4、alpha=8，共 230,400 个可训练参数。基础模型冻结。')
    r.figure(MON+'/model-input.png', '周一训练预检的实际输入 公开数据截图 非本机桌面执行', 'public_dataset', max_height=3.25)
    r.table(['检查项', '原始结果'], [
        ['监督掩码', '前缀 163 token 屏蔽；assistant 含 EOS 共 63 token 参与监督'],
        ['更新数与 loss', '1 次 optimizer update；loss 2.795860'],
        ['梯度与权重', '梯度有限；裁剪前范数 1.039143；60/120 LoRA 张量变化'],
        ['显存', '峰值 allocated 887.41 MiB；reserved 956 MiB'],
        ['重载验证', 'adapter 张量精确一致；同 bf16 上下文 logits 最大差 0'],
    ], [1.3, 5.7])
    r.p('初次预检中权重重载一致，但比较 logits 时混合精度上下文不一致，最大差为 0.375，因此没有判通过。修正为相同 bf16 autocast 后最大差为 0，原失败目录保留。首次生成并未形成合格动作 JSON，训练预检通过仅证明链路可运行。证据 E02。', small=True)

    r.page_start('四 周二完整 LoRA 训练')
    r.p('本轮以固定随机种子 42 训练 3 个 epoch。训练集 8 条，每轮各访问一次，共 24 次主实验训练样本访问；micro-batch=1，梯度累计 2，共 12 次优化器更新。使用 token 加权累计，Accelerator accumulation_steps=1，避免重复除以累计步数。')
    r.table(['配置', '取值', '用途'], [
        ['LoRA', 'r 4 / alpha 8 / dropout 0.05', '语言层 q/v；230,400 参数'],
        ['优化器', 'AdamW；lr 1e-4；clip 1', 'warmup 1 步，线性衰减'],
        ['验证时点', 'step 0 / 4 / 8 / 12', '2 条 teacher-forced 验证样本'],
        ['权重选择', 'best step 8；final step 12', '按验证损失选 best，保留 final'],
    ], [1.0, 2.8, 3.2])
    r.figure(TUE+'/loss-curve.png', '真实训练与验证损失曲线 来自 train 和 validation 原始日志', 'log_chart', max_height=3.0)
    r.table(['验证 step', '0', '4', '8 最佳', '12 最终'], [['验证 loss', '3.357203', '3.343501', '3.312988', '3.313339']], [1.1, 1.475, 1.475, 1.475, 1.475])
    r.p('完整运行耗时 41.946 秒，包含加载、验证、保存、重载及生成，不是纯训练吞吐。峰值 allocated 948.71 MiB，reserved 1,116 MiB；120/120 LoRA 张量发生变化，保存重载张量精确一致、logits 最大差 0。重载后的生成仍不是合法动作 JSON。证据 E03。', small=True)

    r.page_start('五 断点恢复与可复现性')
    r.p('为验证训练恢复，另做受控停止：在全局 step 5 保存状态并正常退出，再由新进程继续剩余 7 次更新至 step 12，与完整训练对照。开启 dropout=0.05，使随机数状态恢复参与验证，而非仅比较确定性权重更新。')
    r.table(['运行', '本进程更新', '最终 global step', '耗时'], [
        ['完整训练 full-initial', '12', '12', '41.946 s'],
        ['受控停止 recovery-partial', '5', '5', '26.582 s'],
        ['新进程 recovery-resumed', '7', '12', '36.402 s'],
    ], [2.7, 1.2, 1.65, 1.45])
    r.h('逐项一致性检查')
    r.table(['恢复对象', '检查结果'], [
        ['数据游标与顺序', 'step 5 位于 epoch 1、offset 2；续训采样顺序与完整运行一致'],
        ['LoRA 权重', 'final 与 best adapter 张量均逐位一致'],
        ['优化过程', '逐步 loss、学习率、optimizer 和 scheduler 状态一致'],
        ['随机数', 'Python、NumPy、torch CPU 与 CUDA RNG 状态恢复并匹配'],
        ['检查点完整性', '配置与数据哈希核验；不可变目录；pending 完成后原子改名'],
    ], [1.25, 5.75])
    r.p('主训练是 12 次更新。额外恢复验证实际执行 5＋7 次，三次进程合计 24 次；不能将 resumed 日志中已有的历史 12 步再次算成新更新。周一的单步预检属于独立实验，也不计入周二 3 epoch。')
    r.p('检查点保存 adapter、优化器、调度器、游标和 RNG 状态，基础模型由固定版本恢复；tensor 状态按 weights_only=True 加载。本轮验证的是程序受控停止与新进程恢复，没有实际模拟断电或 OOM，不能对这两类故障作通过声明。证据 E04。')
    r.h('训练结果的解释')
    r.p('验证 loss 下降只反映在少量已固定样本上的 teacher-forced 拟合变化。它没有测量坐标正确率、动作合法率或任务完成率，因此后续必须独立生成并评分。固定测试集在周一和周二均未做模型 forward。')

    r.page_start('六 周三评测协议与指标')
    r.p('在运行生成前保存 evaluation-plan.json，固定样本、模型、提示词候选和解码参数；完成验证集选择后保存 selection.json，再读取固定测试集。评测过程不更新权重，不调用桌面控制器。')
    r.table(['阶段', '比较对象', '生成数量', '规则'], [
        ['提示词验证', 'best adapter；original 与 schema_v1', '2×2=4', '仅验证集参与选择'],
        ['固定测试', 'base / best step 8 / final step 12', '3×2=6', '相同 original 提示词配对'],
        ['统一解码', 'greedy；最多 192 new tokens', '合计 10', '种子 42；处理器与图像输入核验'],
    ], [.9, 2.9, .9, 2.3])
    r.h('评分定义')
    r.p('首先严格解析整段 JSON，拒绝 Markdown 包装、额外文本、重复键和非有限数字，不做输出修补。随后验证 actions schema，检查动作数量与顺序、类型、非坐标参数以及坐标。坐标按原图像素计算，允许误差 10 像素；拖动的起点与终点均需满足。')
    r.p('默认 button=left、clicks=1 和按键别名按预声明规则处理；输入文本按 Unicode、大小写和空格精确比较。单样本全部条件成立才计联合命中。该指标是对公开标注的一致性，不是控件框命中率，更不是执行后任务成功率。')
    r.h('提示词选择结果')
    r.p('两种提示词在验证集上均为 0/2 schema 合格和 0/2 联合命中；schema_v1 出现占位字段复制或自然语言输出。按预先声明的平局规则保留 original，未根据测试结果重写提示词。测试参数在测试前冻结，周二工程预检的 64 token 不能与本轮 192 token 直接作质量对比。')
    r.p('全轮 10 次真实视觉生成，无生成异常；总耗时 243.450 秒，包含 4 次新模型加载，不是推理性能基准。固定测试现已被查看，未来若据此调参，需要新增未见任务组，不能继续宣称它是未见测试集。证据 E05。')

    r.page_start('七 模型对照结果与失败样例')
    r.figure(WED+'/comparison-plot.png', '基础模型与两个 adapter 的固定测试结果 每组仅 2 条样本', 'log_chart', max_height=3.1)
    r.table(['模型', 'JSON 可解析', 'schema 合格', '联合命中', '坐标项命中'], [
        ['base', '1/2', '0/2', '0/2', '0/6'],
        ['best step 8', '2/2', '0/2', '0/2', '0/6'],
        ['final step 12', '2/2', '0/2', '0/2', '0/6'],
    ], [1.4, 1.4, 1.4, 1.4, 1.4])
    r.p('三组模型的动作类型、非坐标参数、样本坐标与精确匹配均为 0/2。JSON 可解析率由 1/2 变成 2/2，只说明能形成语法对象，没有满足动作协议或标注语义。10 次生成共 5 次 invalid_json、5 次 invalid_schema；其中 4 次触及 token 上限，该项与错误分类重叠，不应相加。')
    r.h('原始输出实例')
    r.p('测试 T1 的目标是 move 到 [450,394]，再从 [450,394] drag 到 [573,407]。best 的原始响应在 actions 中输出了 action="You\'re on a roll"、target.x="100"、score="30"，并附带 interactions；它描述了画面文字，却没有正确 type / point / from / to 字段，因而判 schema 不合格。', small=True)
    r.p('结论：本轮完成了真实训练与公平对照，但没有证据表明动作质量改善。样本数极少且测试仅 1 个任务组，不能报告泛化提升、显著性或真实 GUI 成功率。后续应先补充高质量动作监督和多个独立任务组，再按冻结方案复测。')

    r.page_start('八 固定测试图像与标注追溯')
    r.figure(WED+'/cases/test/base/original/01/input.png', '固定测试 T1 的实际模型输入 ScreenAgent 公开游戏截图', 'public_dataset', max_height=4.05)
    r.p('T1：2023-12-25_10-54-19-019103_translate.json；原图 1024×768；任务为“通过一局连连看游戏”。标注为一次移动与一次拖动，均在原图坐标系内。截图仅作为离线模型输入，未在本机运行游戏，也没有把动作派发到当前桌面。')
    r.table(['样例', 'move', 'drag 终点', '三模型联合结果'], [
        ['T1', '[450,394]', '[573,407]', '全部未命中'],
        ['T2', '[401,448]', '[525,336]', '全部未命中'],
    ], [.7, 1.55, 1.55, 3.2])
    r.p('每个结果目录保留 input.png 与 result.json，包含目标动作、完整提示词、原始响应、生成 token、输入哈希与评分。训练来源、权重哈希、评测计划和提示词选择也分别留档，支持核对数据泄漏和评分过程。证据 E05 的 cases/test 目录可逐例复核。', small=True)

    r.page_start('九 周四有界恢复机制')
    r.p('恢复策略在观察、定位、结果确认和整次会话四层设置上限。只有同一固定窗口、上下文或布局改变，且整个会话从未存在可能已派发输入时，才允许一次重规划。任何输入可能已送出后，不自动重规划或重放，以免重复输入与重复提交。')
    r.table(['预算', '默认值', '真实桌面实验配置'], [
        ['整次会话', '60 s', '120 s'], ['单次观察', '3 次 / 5 s', '3 次 / 15 s'],
        ['目标定位', '3 次 / 8 s', '3 次 / 30 s'], ['结果确认', '不超过 10 s 且受 step.timeout 限制', '5 s'],
        ['截图最大年龄', '2 s', '10 s'], ['全局次数', '观察 80 / 派发 12 / 重规划 1', '沿用默认次数'],
        ['重试间隔', '0.2 s', '0.2 s'],
    ], [1.25, 3.0, 2.75])
    r.p('时限使用 monotonic 时钟，在阻塞操作前后协作检查；HTTP socket timeout 被剩余预算约束，不自动重试超时或 429。它不能强制中断正在执行的 OCR、HTTP 或系统调用；Reader 初始化在会话预算外，因此不能把 session_timeout 解释为整个程序的绝对最长墙钟时间。')
    r.h('状态与台账')
    r.p('状态读取须核验来源、固定 HWND、采集时间和声明字段。临时过期可有限重试，错误窗口、格式错误与权限失败直接停止。每个子任务记录 pending、verified、failed 或 blocked，前一步未确认时后续不会继续。控制方法正常返回只计 actions_completed，不等于结果已验证。')
    r.p('可能派发标记在持久化和控制调用之前建立；即使日志写入失败，也在内存最终摘要保留。部分输入或提交后异常只允许条件满足时做只读诊断；焦点、权限、用户停止及预算保护优先，诊断看到目标成立也不能抹去派发不确定性。证据 E06。')

    r.page_start('十 恢复场景与保护行为')
    r.p('周四保存 26 组模拟场景和 4 组本地 HTTP 固定响应场景，共 30 组：9 组完成、17 组按预期失败、4 组按预期中止。HTTP fixture 只验证协议与预算，不是 NIM 或真实大模型服务。')
    r.table(['场景', '触发条件', '期望与记录'], [
        ['planning_change_once', '派发前上下文变化', '允许重规划 1 次；之后完成 3 步'],
        ['partial_input', '只输入 Hel 后异常', '停止；不补发全文；诊断目标未完成'],
        ['submitted_then_raise', '提交后抛错', '保留 failed / uncertain；不重复提交'],
        ['false_ocr', 'OCR 回显正确但独立状态错误', '结果确认失败；不进入后续步骤'],
        ['second_step_state_failure', '第二步状态不满足', '台账为 verified / failed / blocked'],
    ], [1.8, 2.2, 3.0])
    sim = THU+'/simulation/submitted_then_raise/observations/0011_recovery_diagnostic_observe1/screenshot.png'
    r.figure(sim, '周四模拟提交后异常的诊断画面 合成测试图 非真实桌面', 'simulation', max_height=3.0)
    r.p('图中目标回显可以成立，但最终任务仍不可判成功。周四最初记录没有真实桌面执行；后续周五补测另列，不改写当时的 45 项新增测试和累计 320 项记录。源代码后续修复后，旧源哈希审计可能不再匹配，应使用冻结记录追溯、以最新回归验证当前版本。', small=True)

    r.page_start('十一 周五定位复核与重名规则')
    r.p('周五先复现三个旧实现问题：目标在派发前移动或消失时仍可能使用旧坐标；重名目标新增时仍可能继续执行。这些是保留旧源码的模拟故障对照，不能写成真实桌面误点击。修复后，在派发前状态和区域检查完成后，使用最新 OCR 重新执行相同查询和目标消歧。')
    r.table(['回归例', '旧实现记录', '修复后记录'], [
        ['layout_moves', '旧中心 [150,125]；未命中', '重获 [150,190]；命中 primary'],
        ['disappears_before_click', '仍点击已空的旧位置', '有限定位后停止；零输入'],
        ['duplicate_appears', '新增歧义未阻止后续完成', 'AmbiguousTarget；零输入'],
    ], [1.8, 2.6, 2.6])
    r.h('重名目标的处理规则')
    r.p('匹配先做 NFKC 规范化、去空白和大小写折叠，默认全文相等，可显式启用 contains；再过滤低于阈值或非有限置信度。候选按框 top、left、bottom、right 和 id 排序，即先上后下、先左后右。只有一个候选才可默认命中；多个候选未声明 occurrence 时抛 AmbiguousTarget，绝不默认选首个。')
    r.p('occurrence 必须是从 1 开始的整数，不能是布尔值，超范围报 TargetError；无候选为 TargetNotFound。完全相同框的重复候选即使指定 occurrence 也拒绝；这不等于已检测所有相交框歧义。duplicate_second 明确 occurrence=2，并要求独立状态 focus_target=secondary，避免只凭 OCR 顺序认定控件身份。')
    r.p('派发前仍按相同 query、confidence 与 occurrence 重新定位。位置改变就使用新位置，候选消失则有限重试，新出现的歧义直接停止。ensure_fresh 检查的是最新观察及其确认目标，不能拿最新时间戳配旧坐标。跨观察不承诺 occurrence 能追踪同一业务控件，必须依赖独立状态确认身份。')
    r.p('周五新增 20 组鲁棒性场景和 2 项合同测试；加上周四 30 组回归共 50 组。最终复跑保存 331 张模拟截图；周五新增 20 组为 5 完成、14 正确失败、1 中止。证据 E07。', small=True)

    r.page_start('十二 真实桌面实验方法与正常案例')
    r.p('在专用 Tk 窗口 GUI Agent V2 Robustness 上补测，真实截图、CUDA OCR、DesktopController 与原生 SendInput 参与执行。只读状态由真实 Tk 控件及回调导出，并核验 HWND 与时间；规划为固定回放。故障注入均明确标记，既不模拟 OCR 性能，也不声称真实模型规划通过。')
    r.figure(DESK+'/normal-02/observations/0015_agent_goal_observe1/screenshot.png', '真实桌面 normal 最终完成 输入内容与提交回显均保留', 'real_desktop', max_height=3.7)
    r.h('双通道结果确认')
    r.p('最终目标要求 OCR 识别 SUBMITTED Hello GUI 123 中文测试，同时独立状态中的 submitted 文本精确匹配、submit_count=1、focus_target=primary。审计从 results.json 和 state.json 重新计算，不只相信摘要里的 completed 标记。该规则可阻断 OCR 将错误文字识别为正确文字造成的单通道误判。')
    r.p('环境固定为 1920×1080、100%（96 DPI），本轮未更改系统缩放。第二周曾有 100% 与 125% 实测，不能用本周复用一张 125% 历史截图的 OCR 基准代替新缩放测试；200% 仍待测。每次真实桌面均保存会话摘要、原生输入日志、最终实际控件状态、截图及状态报告。证据 E08。', small=True)

    r.page_start('十三 真实桌面逐例结果')
    r.p('按各场景最新记录验收，11 类中 4 类 completed、7 类因保护正确 failed。共执行 13 次真实桌面运行，保留 2 次历史测试失败；另有 1 次窗口尚未创建的沙箱启动失败，不计真实运行。最新 11 类共有 152 张真实截图，全部 13 次共 190 张；桌面目录 1,229 个原始文件逐个哈希。')
    rows = []
    order = ['normal','layout_shifted','layout_moves','duplicate','duplicate_second','missing','wrong_text','ocr_false_positive','stale_state','partial_input','submit_uncertain']
    sessions = {'normal':5.192648,'layout_shifted':5.285273,'layout_moves':6.516341,'duplicate':.754828,'duplicate_second':5.414909,'missing':3.187029,'wrong_text':8.390156,'ocr_false_positive':6.767902,'stale_state':.416793,'partial_input':2.949259,'submit_uncertain':4.661490}
    notes = {'normal':'三步完成；提交 1 次','layout_shifted':'布局变化后正确定位','layout_moves':'派发前移动后重新获取','duplicate':'AmbiguousTarget；零输入','duplicate_second':'明确第二个；状态 secondary','missing':'target_unavailable；零输入','wrong_text':'多出 !；未提交','ocr_false_positive':'OCR 真而状态假；未输入文本','stale_state':'StateUnavailable；零输入','partial_input':'只留下 Hel；未提交','submit_uncertain':'提交 1 次；仍 failed / uncertain'}
    counts = {'normal':3,'layout_shifted':3,'layout_moves':3,'duplicate':0,'duplicate_second':3,'missing':0,'wrong_text':2,'ocr_false_positive':1,'stale_state':0,'partial_input':2,'submit_uncertain':3}
    cases = {x['case']:x for x in fri['desktop_cases']}
    for c in order:
        x = cases[c]
        rows.append([c, '完成' if x['status']=='completed' else '保护停止', str(counts[c]), f'{sessions[c]:.3f}', f"{x['end_to_end_wall_seconds']:.3f}", notes[c]])
    r.table(['场景', '状态', '可能派发', '会话 s', '流程 s', '实际验收'], rows, [1.45,.65,.6,.65,.65,3.0], size=9)
    r.p('会话计时包括观察、规划、输入与确认；流程计时从 worker 开始，另含 Reader 初始化与首次 OCR 预热，但不含窗口创建、等待 BEGIN 及线程入口前导入。每例只执行一次或因缺陷复测，这些数字不构成多次延迟基准。可能派发计数是保守副作用标记，不等于底层每个鼠标键盘事件数。', small=True)
    r.p('每行原始目录：normal-02、layout_shifted-01、layout_moves-01、duplicate-01、duplicate_second-01、missing-01、wrong_text-02、ocr_false_positive-01、stale_state-01、partial_input-01、submit_uncertain-02。汇总索引 E08 保留全部尝试，旧失败未覆盖。', small=True)

    r.page_start('十四 真实桌面移动与重名验证')
    r.figure(DESK+'/layout_moves-01/observations/0018_agent_goal_observe1/screenshot.png', '真实桌面 layout_moves 按钮在派发前移动后仍完成单次提交', 'real_desktop', max_height=3.55)
    r.p('layout_moves 中，程序在输入前复核阶段实际移动按钮。最新 OCR 位置变化使控制器重新获取目标，随后精确输入并完成一次提交。会话 6.516 秒，可能派发 3 次，独立最终目标成立。这个结果验证了本窗口的动态几何复核。')
    r.figure(DESK+'/duplicate_second-01/observations/0015_agent_goal_observe1/screenshot.png', '真实桌面 duplicate_second 选择第二个目标并以 secondary 状态确认', 'real_desktop', max_height=2.85)
    r.p('明确第二个目标时流程完成，独立状态确认 secondary；未指定重名目标的 duplicate 则在派发前停止，原生输入为零。成功与拒绝两条分支共同验证消歧规则，未把“能点击到文字”当作目标身份验证。', small=True)

    r.page_start('十五 真实桌面部分输入保护')
    r.figure(DESK+'/partial_input-01/observations/0010_recovery_diagnostic_observe1/screenshot.png', '真实桌面 partial_input 仅输入 Hel 后注入异常 无重复输入和提交', 'real_desktop', max_height=4.1)
    r.p('该例实际发送前三个字符后抛出故障。最终控件内容为 Hel，submit_count=0，可能派发数为 2；只读诊断发现目标未完成，最终 failed / RuntimeError，input_delivery_uncertain=true。没有继续把剩余字符补进去，也没有重放整段文本。')
    r.h('失败后的控制约束')
    r.p('输入方法抛错不代表没有副作用，故先建立可能派发标记，再进入控制路径。只要已存在可能输入，恢复层就不能自动重复动作。若还满足同窗、焦点、权限和预算条件，可采集一份只读诊断；否则立即停止。')
    r.p('这类故障属于执行失败或交付不确定，不属于定位失败。报告中的 failed 是正确保护结果；它不意味着场景测试未通过，也不等于应用没有收到任何字符。证据位于 E08 的 partial_input-01，包含 actions.jsonl、final-state.json、session/summary.json 与本图同目录的状态/OCR 原始文件。')

    r.page_start('十六 提交后异常与假阳性拒绝')
    r.figure(DESK+'/submit_uncertain-02/observations/0013_recovery_diagnostic_observe1/screenshot.png', '真实桌面 submit_uncertain 已提交一次 诊断目标成立但仍保留不确定失败', 'real_desktop', max_height=3.8)
    r.p('该例仅点击一次 SUBMIT，等待真实回调计数从 0 增至 1 后注入异常。随后 OCR 与独立目标均成立，但 task_verified=false、input_delivery_uncertain=true，最终 failed / RuntimeError。控制器没有重复提交，也没有因正常回显而把不确定执行改判为成功。')
    r.h('OCR 假阳性对照')
    r.p('ocr_false_positive 保留注入前原始 OCR，再向工作报告明确注入 INPUT READY；真实控件仍没有聚焦。OCR 通道为真而独立状态为假，结果确认在 5 秒预算下失败；只有 1 次可能派发，没有输入文本。该例直接检验 OCR 错识别不能单独推动后续操作。')
    r.p('wrong_text 的真实输入多了 !，因此精确文本状态不满足，流程在提交前停止。stale_state 的状态已过期，直接 StateUnavailable、零输入。以上校验使用真实控件或回调导出，不从 OCR 结果反推出状态，避免两个通道共享同一识别错误。')

    r.page_start('十七 测试类型与数量总表')
    r.p('全项目最终 347 项自动测试通过，其中第三周新增 136 项，第二周结束时已有 211 项。下表分开列自动测试、独立场景、GPU 实验和性能采样；它们的执行对象与统计单位不同，不相加为“测试总数”。')
    r.table(['测试类型', '功能模块', '数量', '具体 case 示例'], [
        ['单元与合同测试', '周一数据和预检', '新增 21', '分组隔离、监督掩码、固定版本和输入保护'],
        ['单元与合同测试', '周二训练器', '新增 18', 'token 加权累计、游标恢复、检查点校验'],
        ['单元与协议测试', '周三评测', '新增 25', '解析 7；评分 10；冻结协议 8'],
        ['模拟集成与合同', '周四恢复', '新增 45', '场景 26；预算等 15；回环 HTTP 4'],
        ['模拟集成与合同', '周五鲁棒性', '新增 22', '鲁棒性场景 20；行为合同 2'],
        ['缺陷回归单元测试', '桌面补测修复', '新增 5', '阶段 deadline 2；回调注入时序 3'],
        ['全项目回归', '既有与本周所有模块', '最终 347', '包括上述 136；不是各日累计值之和'],
        ['离线系统场景', '恢复和鲁棒性', '50 组', '26 周四模拟＋4 HTTP＋20 周五模拟'],
        ['真实桌面系统测试', '专用窗口', '11 类 / 13 次', '4 完成、7 保护停止；2 历史失败另存'],
        ['真实 GPU 实验', '训练与离线生成', '1＋3＋10', '1 单步预检；3 训练进程；10 次配对生成'],
        ['性能采样', 'CUDA OCR', '3 冷 / 15 热', '3 新进程，每进程首次后再测 5 次'],
    ], [1.35, 1.45, 1.0, 3.2], size=9.3)
    r.h('通过标准与复核')
    r.p('每个场景预先定义应完成、应失败或应中止，再核对错误码、派发上限、状态内容和证据完整性。全项目记录还包括计划校验、依赖检查与历史桌面核对；这些辅助检查不另计为 347 项测试。模型质量评分独立为 0/2，不能被工程回归通过所覆盖。证据 E09。', small=True)

    r.page_start('十八 OCR 冷热启动性能')
    r.p('正式基准由 3 个独立新 Python 进程组成，每个进程首次推理后使用同一 Reader 和同一图片测 5 次热推理，共 15 次。每次调用后同步 GPU；输入是已有受控窗口的 1050×700 截图，CUDA、canvas_size=1600、batch_size=1、workers=0。')
    stats = fri['benchmark']['statistics']
    labels = [('import_ms','依赖导入'),('reader_init_ms','Reader 初始化'),('first_inference_ms','首次推理'),('cold_ready_ms','进程内冷启动至首结果'),('warm_inference_ms','热推理'),('process_wall_ms','完整工作进程墙钟')]
    r.table(['测量项', 'n', '均值 ms', '中位数 ms', 'P95 ms'], [[label,str(stats[key]['n']),f"{stats[key]['mean_ms']:.2f}",f"{stats[key]['median_ms']:.2f}",f"{stats[key]['p95_ms']:.2f}"] for key,label in labels], [2.5,.5,1.3,1.35,1.35])
    r.h('测量边界')
    r.p('冷启动指新进程 worker 入口到首次 OCR 结果，包含导入、图片读取、Reader 初始化和首次推理；模型文件及操作系统缓存已存在，不包含模型下载、电脑重启冷缓存或解释器入口前启动。完整进程墙钟 28,483.01 ms 还含热采样和清理，不能写成冷启动。')
    r.p('热推理指同 Reader 首次推理后重复处理相同图片，平均 1,049.42 ms；它不能代表不断变化的桌面截图或端到端任务延迟。3 次冷采样的 nearest-rank P95 等于最大值，仅作描述，样本量不足以稳定估计尾延迟。')
    r.h('实验有效性与记录')
    r.p('最初一批性能采样与项目回归重叠，原始结果保留但从正式基准剔除；随后单独运行本表批次，避免并行负载干扰。排除依据是运行条件，不是挑选更快结果。输入文件 SHA-256 为 ce0117eff1114e3701fe67c9c8090d45c96534847d4b5e2c9f6dd3730c251838。')
    r.p('这张输入来自第二周 125% 缩放记录，本轮只做固定图片 OCR 性能测量，没有验证当前桌面在 125% 下的坐标映射。本周真实桌面只有 100%，200% 缩放保持待测。证据 E10。')

    r.page_start('十九 失败分类与修复记录')
    r.p('周五新增 20 组离线场景中，5 组完成；其余 15 组按预期失败或中止。分类为：定位失败 5、结果确认失败 4、状态不可用 2、执行失败 2、权限不足 1、用户中止 1。该分布仅对应这 20 组，不是全部真实桌面或模型输出的错误分布。')
    r.table(['分类', 'case 示例', '处理与证据'], [
        ['定位失败', 'missing / duplicate / 新增歧义', '有限重试或直接消歧失败；零输入'],
        ['结果确认失败', 'wrong_text / ocr_false_positive', 'OCR 与状态双检；失败不进入提交'],
        ['状态不可用', 'stale_state / 错误来源或窗口', '新鲜度及 HWND 保护；有限重试或停止'],
        ['执行失败', 'partial_input / submit_uncertain', '保留可能输入与不确定性；不重放'],
        ['权限不足', '显式 PermissionError 场景', '立即停止；保存原因链，不伪装定位超时'],
        ['用户中止', '中止信号', '终止会话，不继续诊断或重试'],
    ], [1.1, 2.45, 3.45], size=9.3)
    r.h('历史问题及复测')
    r.table(['历史记录', '原因', '修复与复测'], [
        ['周一 preflight-initial', '重载比较的混合精度上下文不同', '统一 bf16；logits 最大差 0'],
        ['周五 regression-before', '派发前复核仍用旧 OCR 目标', '重选最新目标；移动恢复，缺失与歧义零输入'],
        ['normal-01 启动失败', '沙箱 Tcl init.tcl 加载失败；尚无窗口', '授权交互进程运行 normal-02；无系统防护改动'],
        ['wrong_text-01 测试失败', '保护生效但 deadline 错归 observation_timeout', '传递阶段错误码；-02 为 verification_timeout'],
        ['submit_uncertain-01 测试失败', 'UI 回调未更新时读取计数，注入未触发', '一次点击后只读等待回调；-02 保留 failed/uncertain'],
    ], [1.65, 2.55, 2.8], size=9.1)
    r.p('两次历史真实桌面测试失败分别是错误归因和 fixture 时序问题，不是两次重复提交。注入修复同时验证延迟、无回调和多回调；无回调或多回调明确判 fixture 失败，绝不重点击。旧源码、before 说明和原目录均保留。', small=True)

    r.page_start('二十 系统交付与后续工作')
    r.p('系统 v2 将感知、定位、原生输入、双通道确认和有界恢复串联，完成当前受控范围的工程验收。已推送的第三周代码与验证交付基线为 main 分支 fb2ad8beebc7cf563c92e78f32b4017f184fe810；本汇报基于该批记录整理，不在文内冒充新增模型或桌面验证。')
    r.table(['产物', '版本与校验'], [
        ['源码 ZIP', 'gui-agent-v2.0.0-20261106_202512_277219.zip\n170 个源码文件；454,736 字节'],
        ['源码 SHA-256', '7ae3f66a8aaaa2b3b772b4d0d3619375cfbd245ed5281c66448ed0ab6ea768f4'],
        ['v2 验证证据 ZIP', 'gui-agent-v2.0.0-validation-evidence-20261106T092513_239543Z.zip\n3,147 个文件；19,259,054 字节'],
        ['证据 SHA-256', '5ca69d09ad66a69804d3ff411f01b840f50600f7903023a164991e017b2bdf53'],
    ], [1.45, 5.55])
    r.h('仍未完成的验收')
    r.p('一是模型动作质量：三模型联合命中均为 0/2，需要扩充和清洗动作监督、增加独立任务组，再重新冻结训练与评测方案。二是真实模型规划闭环与 NVIDIA NIM：目前桌面依赖回放，没有真实服务质量验收。三是 Windows 200% 缩放及更多业务应用：本周只验证 100% 专用窗口，不能扩大结论。')
    r.p('恢复机制的协作时限不具备硬中断能力，断电、OOM 和广泛权限环境也未实际覆盖。后续应先建立新增场景的明确预期，再开展多轮性能测量和跨环境回归，保留所有失败尝试及原因。')
    r.h('报告及记录包')
    r.p('随报告提供本周截图与记录包，按原仓库相对路径组织，附 REPORT-MANIFEST.json 与 SHA256SUMS.json。记录包含选用的周一至周三日志、完整 v2 验证证据以及逐日使用说明；不含模型权重、虚拟环境和 API 密钥。训练权重仍按原始本地路径与已保存哈希追溯，本记录包不是离线重训环境。')

    r.page_start('附录一 证据索引')
    r.p('以下路径均相对于项目根目录 D:\\study\\实习，也对应随附记录 ZIP 中的路径。日期以 Australia/Sydney 解释，目录内 UTC 时间戳不强行改名。')
    index = [
        ('E01','数据审计','data/week3/monday-v1/manifest.json'),
        ('E02','单步训练及重载',MON+'/summary.json'),
        ('E03','完整训练及损失',TUE+'/summary.json'),
        ('E04','续训验证','output/week3-tuesday/20261030T131044_700421Z/verification.json'),
        ('E05','冻结评测与原始响应',WED+'/summary.json'),
        ('E06','初次恢复验证',THU+'/verification.json'),
        ('E07','最终离线与桌面汇总',FRI+'/verification.json'),
        ('E08','真实桌面逐次记录',DESK+'/normal-02/summary.json'),
        ('E09','最终全项目回归',CHECK),
        ('E10','独立 OCR 性能',BENCH+'/summary.json'),
        ('E11','v2 发布清单','output/releases/gui-agent-v2.0.0/20261106T092512_331916Z/release.json'),
    ]
    r.table(['编号', '内容', '主要文件'], [[a,b,c] for a,b,c in index], [.5,1.55,4.95], size=9)
    r.p('E02 同目录含 training-step.json、batch-audit.json、reload-verification.json；E03 同目录含 train.jsonl、validation.jsonl、metrics.csv 与 loss-curve.png。E05 同目录含 evaluation-plan.json、selection.json、validation.jsonl、test.jsonl、cases 与 evidence-manifest.json。', small=True)
    r.p('E08 是入口示例；其他 12 次真实运行和 normal-01 的启动失败位于同一 desktop-validation 根目录，完整清单由 E07 的 desktop_attempts、desktop_historical_failed_attempts 和 desktop_launch_failures 保存。', small=True)
    r.p('截图索引及每张图的源文件 SHA-256 保存在 REPORT-MANIFEST.json。正文中的公开数据截图、合成图与真实桌面图分别记录 kind，便于复核来源，不混算截图数量。', small=True)
    for _,_,p in index: r.evidence(p)

    r.page_start('附录二 使用与验证复现')
    r.p('在项目根目录打开 PowerShell。训练与模型命令使用 .venv-training，系统验证使用原 .venv。先核对已有记录，再按逐日使用说明执行需要 GPU 或前台桌面的步骤；模型训练和桌面运行不是生成本报告的前提。')
    r.h('只读检查已有验证索引')
    for command in [
        'Get-Content output/week3-monday/verification.json -Encoding utf8',
        'Get-Content output/week3-tuesday/verification.json -Encoding utf8',
        'Get-Content output/week3-wednesday/verification.json -Encoding utf8',
        'Get-Content output/week3-friday/verification.json -Encoding utf8',
    ]: r.p(command, small=True)
    r.h('复核最终固定批次')
    r.p('.\\.venv\\Scripts\\python.exe scripts/validate_week3_friday.py --audit-only output/week3-friday/20261106T091722_627143Z', small=True)
    r.p('该审计核对已保存的来源哈希、预期状态、派发次数和原始证据，不重新操作桌面。若未来改动源文件导致哈希不同，应保留原批次并建立新验证记录，不覆盖旧证据。')
    r.h('逐日操作说明与实现入口')
    r.table(['任务', '文档', '主要实现'], [
        ['周一', 'docs/week3-monday.md', '数据准备及 training preflight'],
        ['周二', 'docs/week3-tuesday.md', 'LoRA trainer 与 checkpoint'],
        ['周三', 'docs/week3-wednesday.md', '冻结评测、解析与评分'],
        ['周四', 'docs/week3-thursday.md', 'gui_workflow/recovery.py'],
        ['周五', 'docs/week3-friday.md', 'run_robustness.py；gui_tasks/desktop_faults.py'],
        ['桌面补测', 'docs/week3-friday-desktop-validation.md', '真实控件、输入日志及历史缺陷复测'],
    ], [.7,3.3,3.0], size=9.2)
    r.p('本汇报引用的所有数值来自已冻结原始记录。报告生成脚本 scripts/build_week3_report.py 仅负责组织文本、嵌图和打包，不调用模型、OCR、桌面输入或网络 API。')

    r.doc.save(DOCX)
    manifest = {'title': NAME, 'date': '2026-11-07', 'timezone': 'Australia/Sydney',
                'docx': DOCX.relative_to(ROOT).as_posix(), 'docx_sha256': digest(DOCX),
                'planned_pages': r.page, 'figures': r.figures, 'experiments_rerun': False,
                'source_commit': 'fb2ad8beebc7cf563c92e78f32b4017f184fe810',
                'model_quality_accepted': False, 'full_project_tests': 347}
    (WORK/'report-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    bundle(r, manifest)
    print(json.dumps({'docx':str(DOCX),'zip':str(ZIP),'pages_planned':r.page,'figures':len(r.figures)},ensure_ascii=True))


def bundle(r, manifest):
    # Include the already audited v2 evidence verbatim, then add earlier-week records.
    previous = ROOT/'output/deliverables/gui-agent-v2.0.0-validation-evidence-20261106T092513_239543Z.zip'
    selected = set(r.files)
    for pattern in ['docs/week3*.md','configs/week3*.json','*第三周*说明.md','*第三周*记录.md']:
        selected.update(p.relative_to(ROOT).as_posix() for p in ROOT.glob(pattern) if p.is_file())
    for folder in ['output/week3-monday','output/week3-tuesday', WED]:
        for p in (ROOT/folder).rglob('*'):
            if not p.is_file(): continue
            if any(part.startswith(('adapter','best_adapter','checkpoint-')) for part in p.relative_to(ROOT/folder).parts[:-1]): continue
            if p.suffix.lower() in {'.json','.jsonl','.csv','.md','.png','.svg','.txt'}:
                selected.add(p.relative_to(ROOT).as_posix())
    for pattern in ['data/week3/monday-v1/*.json','data/week3/monday-v1/*.jsonl']:
        selected.update(p.relative_to(ROOT).as_posix() for p in ROOT.glob(pattern))
    selected.add(DOCX.relative_to(ROOT).as_posix())
    selected.add('scripts/build_week3_report.py')
    hashes = {}
    with zipfile.ZipFile(ZIP,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        with zipfile.ZipFile(previous) as prior:
            for name in prior.namelist():
                if name.endswith('/'): continue
                content = prior.read(name)
                z.writestr(name,content)
                hashes[name] = hashlib.sha256(content).hexdigest()
        for name in sorted(selected):
            if name in hashes: continue
            content = (ROOT/name).read_bytes()
            z.writestr(name,content); hashes[name] = hashlib.sha256(content).hexdigest()
        manifest['evidence_files'] = len(hashes)
        z.writestr('REPORT-MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
        z.writestr('SHA256SUMS.json',json.dumps(hashes,ensure_ascii=False,indent=2))
    with zipfile.ZipFile(ZIP) as z:
        assert z.testzip() is None
        for name, expected in hashes.items(): assert hashlib.sha256(z.read(name)).hexdigest() == expected
    (WORK/'bundle-verification.json').write_text(json.dumps({'files':len(hashes),'bytes':ZIP.stat().st_size,
        'sha256':digest(ZIP),'all_hashes_verified':True},indent=2),encoding='utf-8')


if __name__ == '__main__': build()
