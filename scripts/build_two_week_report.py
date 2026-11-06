"""Build the two-week report from frozen evidence; no experiments are rerun."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import zipfile

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Pt, RGBColor

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output/reports'
WORK = ROOT / 'tmp/report-two-weeks'
NAME = '桌面GUI智能体实验汇报-第一至第二周'
DOCX = OUT / (NAME + '.docx')
FRI = 'output/week2-friday/20261027T113745_094152Z'
THU = 'output/week2-thursday/20261027T083523_487770Z'
WED = 'output/week2-wednesday/20261027T045037_365812Z'
TUE = 'output/week2-tuesday/20261026T165441_668296Z'
MON = 'output/week2-monday/20261026T145603_897824Z'
CHECK = 'output/project-checks/20261027_223734_623723'
BENCH = 'output/revision/benchmark/20261026_203843_946808'
NORMAL = 'output/week2-thursday/desktop-normal/20261027_193238_195187'
FALSE = 'output/week2-thursday/desktop-false-ocr/20261027_193356_948040'
FULL = 'output/week2-friday/desktop-full'
OPEN = 'output/week2-friday/desktop-open-verified/open_browser-none'
CLOSE = 'output/week2-friday/desktop-close-final-cua/close_app-none'


def read(path):
    return json.loads((ROOT / path).read_text(encoding='utf-8-sig'))


class Report:
    def __init__(self):
        self.doc = Document()
        self.figures, self.files, self.page = [], set(), 0
        section = self.doc.sections[0]
        section.page_width, section.page_height = Cm(21), Cm(29.7)
        section.top_margin, section.bottom_margin = Cm(1.6), Cm(1.6)
        section.left_margin, section.right_margin = Cm(1.8), Cm(1.8)
        section.header_distance, section.footer_distance = Cm(.6), Cm(.65)
        styles = self.doc.styles
        for name in ['Normal', 'Title', 'Subtitle', 'Heading 1', 'Heading 2', 'Caption']:
            s = styles[name]
            s.font.name = 'Calibri'
            s._element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'), '宋体' if name == 'Normal' else '微软雅黑')
            s.font.color.rgb = RGBColor(0, 0, 0)
            ppr = s._element.find(qn('w:pPr'))
            if ppr is not None:
                for border in list(ppr.findall(qn('w:pBdr'))): ppr.remove(border)
        styles['Title'].font.underline = False
        styles['Subtitle'].font.italic = False
        styles['Caption'].font.bold = False
        styles['Caption'].font.italic = False
        styles['Normal'].font.size = Pt(10.5)
        styles['Normal'].paragraph_format.line_spacing = 1.25
        styles['Normal'].paragraph_format.space_after = Pt(6)
        for name, size in [('Title', 23), ('Subtitle', 12), ('Heading 1', 16), ('Heading 2', 12), ('Caption', 9)]:
            styles[name].font.size = Pt(size)
        styles['Heading 1'].paragraph_format.space_after = Pt(10)
        styles['Heading 1'].paragraph_format.space_before = Pt(0)
        styles['Heading 2'].paragraph_format.space_before = Pt(8)
        styles['Heading 2'].paragraph_format.space_after = Pt(5)
        styles['Caption'].paragraph_format.space_after = Pt(8)
        for name in ['Heading 1', 'Heading 2']:
            styles[name].paragraph_format.keep_with_next = True
        footer = section.footer.paragraphs[0]
        footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
        footer.add_run('GUI Agent 实验汇报  |  ')
        fld = OxmlElement('w:fldSimple'); fld.set(qn('w:instr'), 'PAGE')
        footer._p.append(fld)
        for run in footer.runs:
            run.font.size = Pt(8)
        props = self.doc.core_properties
        props.title = '桌面 GUI 智能体实验汇报 第一至第二周'
        props.subject = '开发成果 测试方法 真实截图 失败记录与验收边界'
        props.author = ''
        props.created = datetime(2026, 10, 27, tzinfo=timezone.utc)

    def page_start(self, title, first=False):
        if not first:
            self.doc.add_page_break()
        self.page += 1
        self.doc.add_paragraph(title, 'Title' if first else 'Heading 1')

    def p(self, text, small=False, bold=False):
        p = self.doc.add_paragraph()
        r = p.add_run(text); r.bold = bold
        if small:
            r.font.size = Pt(9)
            p.paragraph_format.line_spacing = 1.15
            p.paragraph_format.space_after = Pt(5)
        return p

    def h(self, text):
        self.doc.add_paragraph(text, 'Heading 2')

    def table(self, headers, rows, widths, size=9):
        table = self.doc.add_table(rows=1, cols=len(headers))
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = False
        for c, width in zip(table.columns, widths):
            c.width = Cm(width)
        for i, values in enumerate([headers] + list(rows)):
            cells = table.rows[0].cells if i == 0 else table.add_row().cells
            trpr = table.rows[i]._tr.get_or_add_trPr()
            trpr.append(OxmlElement('w:cantSplit'))
            if i == 0:
                trpr.append(OxmlElement('w:tblHeader'))
            for j, (cell, value) in enumerate(zip(cells, values)):
                cell.width = Cm(widths[j])
                cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
                cell.text = str(value)
                tcpr = cell._tc.get_or_add_tcPr()
                borders = OxmlElement('w:tcBorders')
                for edge in ['top', 'left', 'bottom', 'right']:
                    e = OxmlElement('w:' + edge)
                    for key, val in [('val', 'single'), ('sz', '4'), ('color', 'D9D9D9')]: e.set(qn('w:' + key), val)
                    borders.append(e)
                tcpr.append(borders)
                shade = OxmlElement('w:shd'); shade.set(qn('w:fill'), 'DCE8F3' if i == 0 else ('F5F8FB' if i % 2 == 0 else 'FFFFFF')); tcpr.append(shade)
                mar = OxmlElement('w:tcMar')
                for edge, val in [('top', '75'), ('bottom', '75'), ('left', '95'), ('right', '95')]:
                    e = OxmlElement('w:' + edge); e.set(qn('w:w'), val); e.set(qn('w:type'), 'dxa'); mar.append(e)
                tcpr.append(mar)
                for p in cell.paragraphs:
                    p.paragraph_format.space_after = Pt(1)
                    p.paragraph_format.space_before = Pt(1)
                    p.paragraph_format.line_spacing = 1.1
                    if len(str(value)) < 10 and j > 0: p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    for r in p.runs:
                        r.font.size = Pt(size); r.bold = i == 0
        self.p('', small=True)

    def evidence(self, *paths):
        for path in paths:
            if not (ROOT / path).is_file():
                raise FileNotFoundError(path)
            self.files.add(path.replace('\\', '/'))

    def figure(self, path, caption, width=5.25):
        self.evidence(path)
        parent = (ROOT / path).parent
        for name in ['results.json', 'state.json']:
            if (parent / name).is_file(): self.evidence((parent / name).relative_to(ROOT).as_posix())
        p = self.doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(2)
        p.paragraph_format.keep_with_next = True
        shape = p.add_run().add_picture(str(ROOT / path), width=Inches(width))
        shape._inline.docPr.set('descr', caption)
        number = len(self.figures) + 1
        cp = self.doc.add_paragraph(f'图 {number}  {caption}', 'Caption')
        cp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        self.figures.append({'number': number, 'source': path, 'caption': caption, 'planned_page': self.page})


def build():
    OUT.mkdir(parents=True, exist_ok=True); WORK.mkdir(parents=True, exist_ok=True)
    cuda, perf, mon = read('cuda-verification.json'), read(BENCH + '/summary.json'), read(MON + '/summary.json')
    friday, thursday, project = read(FRI + '/verification.json'), read(THU + '/verification.json'), read(CHECK + '/summary.json')
    assert friday['all_passed'] and friday['full_project_tests'] == 211 and friday['unit_test_count'] == 26
    assert len(friday['desktop_cases']) == 10 and thursday['all_passed'] and project['all_passed']
    r = Report()

    r.page_start('桌面 GUI 智能体实验汇报', first=True)
    r.doc.add_paragraph('第一至第二周开发与验证记录', 'Subtitle')
    r.p('汇报日期：2026 年 10 月 27 日（Australia/Sydney）。项目：基于多模态大模型的桌面 GUI 智能体开发与优化。', small=True)
    r.h('阶段结论')
    r.p('本阶段完成 CUDA 环境验证、屏幕感知与输入控制、任务计划执行和双重结果确认；第二周进一步完成小样本数据处理、LangChain 多模态接口、多步规划、实时闭环和五类受控基础任务测试。当前全项目 211 项回归通过。')
    r.p('真实桌面已验证 OCR 与独立控件状态共同判断的执行链路。规划答案仍使用回放，NVIDIA NIM 的真实推理质量尚未验收，不能将这些结果解释为大模型自主完成任意桌面任务。')
    r.table(['阶段', '已完成内容', '验证与交付'], [
        ['第一周', '截图、OCR、目标定位、输入控制、计划运行', '60 项离线回归；冷热 OCR 基准；两档真实缩放'],
        ['第二周周一', '三源小样本、统一数据格式、清洗和划分', '76 条记录、44 张截图；新增 23 项测试'],
        ['第二周周二', 'LangChain 与 HTTP/回放接口', '新增 35 项；7 组接口验证'],
        ['第二周周三', '子任务、依赖与动作编译', '新增 30 项；11 组规划验证'],
        ['第二周周四', '实时观察、规划、执行与最终目标', '新增 37 项；14 组模拟/HTTP；2 类真实场景'],
        ['第二周周五', '五类基础任务与故障保护', '新增 26 项；10 组模拟与 10 类真实桌面用例'],
    ], [2.6, 7.0, 7.8])
    r.h('汇报口径')
    r.p('本文使用已保存的截图与固定时间戳记录，不重新运行桌面测试。第一周历史结果、第二周新增结果、模拟数据及主动故障注入分别说明；预期拒绝用例通过，不等于业务任务完成。截图已嵌入文档，原始 JSON、日志与图像另收录于配套证据包。', small=True)

    r.page_start('一 实验目标与系统实现')
    r.p('目标是将用户指令、当前窗口截图、可校验的动作计划和执行后状态连接起来。程序先观察，再生成候选并校验，随后对每次操作重新定位和确认，最后独立核对调用方定义的任务目标。')
    r.table(['模块', '实现', '作用'], [
        ['感知', 'gui_perception', '物理像素截图、中英文 OCR、坐标转换和证据保存'],
        ['控制', 'gui_control', '前台/窗口边界保护、鼠标键盘、Unicode 输入、停止请求'],
        ['流程', 'gui_workflow', '整份计划校验、逐步运行、OCR 与独立状态联合判断'],
        ['数据', 'gui_data', '来源登记、预处理、标签隔离、去重与任务分组划分'],
        ['规划', 'gui_agent', 'LangChain、HTTP/回放、任务拆解、上下文复核与反馈'],
        ['基础测试', 'gui_tasks', '本地协作页面、样例文件、收件箱与受控浏览器生命周期'],
    ], [2.5, 4.2, 10.7])
    r.h('实验环境')
    r.table(['项目', '记录值'], [
        ['系统与 Python', 'Windows 桌面；Python ' + cuda['python']],
        ['GPU', cuda['gpu'] + '；可见显存约 ' + str(cuda['vram_gb']) + ' GB'],
        ['PyTorch 与 torchvision', cuda['torch'] + ' / ' + cuda['torchvision']],
        ['CUDA 与 cuDNN', 'PyTorch CUDA 运行时 ' + cuda['cuda_runtime'] + '；cuDNN ' + str(cuda['cudnn'])],
        ['算力与依赖', '计算能力 12.0；编译架构含 sm_120；LangChain Core 1.7.0'],
    ], [4.5, 12.9])
    r.p('环境记录包含 GPU/CPU 矩阵乘法一致性、autograd、cuDNN 卷积和 torchvision CUDA NMS 四项检查，状态 PASS。该记录验证 PyTorch 所带运行时可用，不据此推断独立 CUDA Toolkit 或本地大模型部署已经完成。', small=True)
    r.evidence('cuda-verification.json', 'docs/architecture.md')

    r.page_start('二 屏幕感知与输入控制')
    r.p('截图保存原始像素与 OCR 标注；定位输出文字框及桌面物理坐标。系统支持中英文识别，并保留模型输入图、识别文字、置信度及坐标，用于回溯每次目标选择。')
    r.figure('output/wednesday-validation/1280x720/annotated.png', '第一周固定输入的中英文 OCR 标注，原始实验日期为 2026 年 10 月 20 日', 5.9)
    r.p('该图是 1280×720 图片尺寸测试之一。图片尺寸或重采样实验不能证明 Windows 显示缩放兼容，真实 DPI 测试另见第五节。', small=True)
    r.table(['功能', '验证内容', '边界'], [
        ['文字定位', '精确/包含匹配、置信度过滤、候选与物理坐标', '多候选默认拒绝猜测'],
        ['输入操作', '点击、中英文输入、滚动、拖拽', '旧版保存 13 个检查项；控制目前只验收主屏'],
        ['窗口保护', '前台标题、HWND、区域、覆盖关系及指针位置', '失焦或越界时停止，不把输入返回当作成功'],
        ['停止与部分输入', 'Escape/STOP、事件接收数量和释放清理', '不重发已派发动作；模型等待在返回/超时后处理停止'],
    ], [2.9, 8.0, 6.5])
    r.evidence('output/wednesday-validation/summary.json', 'output/thursday-validation/20261020_023039_804096/summary.json')

    r.page_start('三 双重确认与重名目标规则')
    r.p('每步成功必须同时满足 OCR 回显和独立应用状态。独立状态由协作应用导出，绑定同一窗口，并检查快照新鲜度；它不能由 OCR 文本反向生成。步骤都通过后，还要重新观察并核对调用方提供的最终目标。')
    r.table(['检查层', '条件', '拒绝情形'], [
        ['OCR', '目标文本、存在性、匹配模式与最低置信度', '误识别、缺字或文本不满足预期'],
        ['独立状态', '真实输入内容、焦点、提交值、按钮状态或窗口存在性', '值不一致、状态缺失/过期或绑定其他 HWND'],
        ['动作变化', '操作前条件未成立，操作后两条证据共同成立', '初始已满足时不继续发送同一动作'],
        ['任务级目标', '调用方指定的最终 OCR 与 state 同时成立', '模型宣称 done 或自定义弱目标不等于完成'],
    ], [2.6, 8.5, 6.3])
    r.h('重名处理细则')
    r.p('先规范化大小写和空白等文本，再按匹配方式和置信度阈值过滤。过滤后若多个候选仍匹配，默认抛出 AmbiguousTarget，即使其中一个置信度更高也不自动选择。')
    r.p('只有调用方明确指定 occurrence 时，才可按当前帧的几何顺序选择第几个；排序使用纵向位置，再使用横向位置。序号从 1 开始，仅对当前帧有效，不能作为跨帧稳定的控件 ID。无效序号或无法用几何位置区分的重合检测框仍拒绝执行。')
    r.table(['用例', '预期与实际保护'], [
        ['两个 INPUT FIELD，未指定序号', 'AmbiguousTarget，0 次输入派发'],
        ['两个同名，显式选择第二个', '按当前帧几何顺序解析；执行前仍重拍目标区域'],
        ['两个同名完全重合的检测框', '即使 occurrence=1 也拒绝，序号不能消除几何歧义'],
        ['OCR 成功而控件值错误', '整体不通过；只观察重试，不再次点击或提交'],
    ], [6.3, 11.1])
    r.p('规则实现及具体离线断言见 gui_control/targets.py、tests/test_verification.py；完整说明随证据包保存。', small=True)
    r.evidence('docs/verification-and-target-rules.md')

    r.page_start('四 OCR 冷启动与热启动结果')
    r.p('使用同一张 1280×720 中英文图片。三个新 Python 进程分别初始化 Reader 并完成首次推理，随后各复用 Reader 测量 10 次，共 30 次热样本。推理后同步 CUDA，canvas_size=1600、batch_size=1、workers=0。')
    labels = [('import_ms', '进程内依赖导入'), ('reader_init_ms', 'Reader 初始化及延迟导入'),
              ('first_inference_ms', '首次推理'), ('cold_ready_ms', '进程内启动到首次结果'), ('warm_inference_ms', 'Reader 热推理')]
    stats = perf['statistics']
    r.table(['指标', 'N', '平均 ms', '中位 ms', 'P95 ms'], [
        [label, stats[key]['n'], f"{stats[key]['mean_ms']:.2f}", f"{stats[key]['median_ms']:.2f}", f"{stats[key]['p95_ms']:.2f}"] for key, label in labels
    ], [6.1, 1.0, 3.4, 3.4, 3.5], 9.5)
    r.h('计时口径')
    r.p('冷路径从 worker 函数入口计到首次 OCR 结果，包含进程内导入、图片读取、Reader 初始化和首次推理，不包括解释器启动前时间。模型文件已缓存，操作系统缓存未清理，因此不代表重启机器或首次下载模型的耗时。')
    r.p('热推理只统计同一 Reader 首次推理之后的调用。30 次热样本范围为 157.14–388.68 ms。process_wall_ms 还包含热推理与进程退出，不能替代冷启动值；总计中位数也不能由各阶段中位数相加得到。')
    r.h('结果解释')
    r.p('首次结果的中位耗时约 21.39 秒，热推理中位约 361.27 ms，说明复用 Reader 对该输入有明显意义。此处只描述已有样本，不作 GPU/CPU、跨分辨率或跨硬件性能结论。P95 使用 nearest rank；冷样本只有 3 个，其 P95 等于最大值，不能作为稳定尾延迟估计。')
    r.p('原始记录：' + BENCH + '/summary.json。输入 SHA-256 已保存，三次输入完全相同。此前三个不同图片的单次耗时没有混入本次冷热统计。', small=True)
    r.evidence(BENCH + '/summary.json')

    r.page_start('五 Windows 实际显示缩放验证')
    r.p('第一周修订测试在同一 1920×1080 主屏上真实切换系统缩放并重启窗口，分别记录 96 DPI 和 120 DPI。200% 按用户要求保留待测，没有用修改分辨率或图片重采样代替。', small=True)
    r.figure('output/revision/system/restored-100/20261026_213438_605990/0006_step03_after1/screenshot.png', '100% 缩放，96 DPI，恢复设置后的三步与双重确认均通过', 4.8)
    r.figure('output/revision/system/scale-125/20261026_213059_278656/0007_step03_after1/screenshot.png', '125% 缩放，120 DPI，状态发布修复后的真实正常流程', 4.8)
    r.p('100% 覆盖正常、重名、缺失、不变化、OCR 假阳性五场景；125% 只补测正常流程，共六个去重场景。125% 的四类异常和 200% 均未验收。结束后已恢复 100%。', small=True)
    r.evidence('output/revision/system-summary.json', 'output/revision/system/restored-100/20261026_213438_605990/summary.json', 'output/revision/system/scale-125/20261026_213059_278656/summary.json')

    r.page_start('六 数据采集与预处理')
    r.p('第二周周一获取三种官方来源的小样本，建立统一记录、来源哈希、图片预处理、动作转换、缺陷隔离、去重与任务分组划分。共 76 条记录、44 张真实公开截图，用作接口开发样本；本轮未训练模型。')
    r.table(['来源', '记录', '截图', '训练', '验证', '独立评测'], [
        ['ScreenAgent', 24, 24, 16, 8, 0], ['Multimodal Mind2Web', 20, 20, 19, 1, 0], ['WebArena', 32, 0, 0, 0, 32], ['合计', 76, 44, 35, 9, 32],
    ], [5.9, 2.1, 2.1, 2.3, 2.3, 2.7])
    r.figure('data/week2/processed/20261026T145603_897824Z/preview.png', '已保存的公开样本接触表，用于抽查数据读取与预处理，不是本项目桌面执行结果', 5.9)
    r.p('instruction、observation 与 label 分离；模型输入不带当前/未来动作答案。先按同任务及同图哈希建立连通分量，再去重并划分，避免相关样本跨训练/验证集。WebArena 配置只进入独立评测任务，不编造截图或演示动作。', small=True)
    r.p('同一原始清单再次处理后，六份 JSONL 与图片哈希一致。DOM/CSS 矩形保留为未确认的来源坐标，不直接转换成物理桌面点击点。样本较小，不能用于宣称模型泛化。来源版本与许可声明见 data-sources.json。', small=True)
    r.evidence(MON + '/summary.json', MON + '/verification.json', 'docs/data-sources.json')

    r.page_start('七 模型接口与任务规划')
    r.p('周二使用 LangChain Core 组织输入准备、HTTP/回放调用和响应校验。接口发送任务、图片与先前历史，支持鉴权、超时、响应大小检查及错误分类；本地回环服务验证了实际请求结构，未把固定服务答案当作真实推理。')
    r.table(['验证项', '结果及边界'], [
        ['图片与历史', 'PNG 编码、中文任务、鉴权头及不含标签的真实数据接入通过'],
        ['HTTP 异常', '401、429、非法 JSON、截断响应和中断路径通过；不自动重试'],
        ['NIM', '已选择 NVIDIA NIM；首次真实请求超时并中断，用户反馈服务排队；实际生成质量待测'],
        ['候选约束', '拒绝原始坐标、shell/脚本、未知状态字段和关闭变化检查的动作'],
    ], [3.5, 13.9])
    r.h('周三多步规划')
    r.p('GuiPlanner 解析子任务、依赖与动作序列，校验所有步骤之后才导出候选。最多 8 个子任务，默认 12 个动作，可设 1–32；依赖只能指向此前子任务，按数组顺序串行，不自动并行或重排。')
    r.table(['顺序', '子任务', '动作', 'OCR 与独立状态'], [
        [1, 'focus', 'click_text INPUT FIELD', 'INPUT READY；input_focused=true'],
        [2, 'enter', 'type_text 原始中英文', '完整回显；entry_text 精确一致'],
        [3, 'submit', 'click_text SUBMIT', 'SUBMITTED 回显；submitted 与按钮状态'],
    ], [1.2, 2.0, 5.6, 8.6])
    r.p('上表是固定回放计划。plan_ready 只代表结构合格；模型表达 done 不是验收结果。若末步非法，整份计划在第一步输入前被拒绝。真实 NIM 的自然语言拆解能力与规划成功率未在本轮测量。', small=True)
    r.evidence(TUE + '/verification.json', WED + '/verification.json', 'docs/agent-plan.example.json', 'docs/nim-troubleshooting.md')

    r.page_start('八 实时闭环与最终目标确认')
    r.p('周四接入初始截图/OCR、实际状态、规划后上下文复核、逐步执行和独立最终目标。模型返回后重查停止状态、HWND、窗口区域与导出状态；变化则停止。下面是真实桌面、固定规划回放的结果。', small=True)
    r.figure(NORMAL + '/0010_agent_goal/screenshot.png', '周四正常闭环，完整中英文输入与提交；三次动作、每步和最终目标均通过', 4.85)
    r.figure(FALSE + '/0006_step01_after3/screenshot.png', '周四 OCR 假阳性保护，实际界面为 WRONG STATE，独立状态拒绝成功', 4.85)
    r.p('正常最终 OCR 为 SUBMITTED Hello GUI 123 中文测试，置信度约 0.663；submitted 字段精确一致，submit_enabled=true。故障场景主动注入 INPUT READY，OCR=true、state=false，只点击一次后停止。两类都保存原图、OCR、状态和 feedback。', small=True)
    r.evidence(NORMAL + '/summary.json', NORMAL + '/agent/feedback.json', FALSE + '/summary.json', FALSE + '/agent/feedback.json')

    r.page_start('九 五类基础任务验证')
    r.p('周五使用独立 Edge、本地页面、示例文件、测试收件箱和保持存活的启动器，验证五类任务。全部动作经过原有 OCR 定位与窗口保护，每步和最终目标均用独立状态核对。', small=True)
    r.table(['任务', '派发', '独立证据', '结果'], [
        ['打开浏览器', 1, '本轮唯一标题、HWND/PID，实际窗口存在', '完成'],
        ['搜索', 3, 'draft=GUI Agent；本地目录真实匹配', '完成'],
        ['打开文件', 1, '实际读取 sample.txt 并计算 SHA-256', '完成'],
        ['本地消息', 3, 'inbox.json 恰好 1 条，文本精确一致', '完成'],
        ['关闭应用', 1, '本轮 HWND/PID 对应的实际窗口消失', '完成'],
    ], [3.0, 1.2, 11.2, 2.0])
    r.figure(FULL + '/search-none/observations/0011_agent_goal/screenshot.png', '真实 Edge 的本地搜索：输入 GUI Agent 后返回 FOUND GUI Agent', 5.2)
    r.p('搜索不是公共搜索引擎，文件不是资源管理器或 Office，消息不是外部通信软件。打开/关闭由 Agent 点击测试启动器触发，不是 Windows 开始菜单操作。该结果证明受控集成可运行，不扩展为任意跨窗口桌面代理。', small=True)
    r.evidence(FULL + '/summary.json', FULL + '/search-none/session/feedback.json')

    r.page_start('十 文件与本地消息实验截图')
    r.figure(FULL + '/open_file-none/observations/0006_agent_goal/screenshot.png', '文件打开：真实读取样例内容，独立哈希来自实际字节', 5.05)
    r.figure(FULL + '/send_message-none/observations/0012_agent_goal/screenshot.png', '本地消息：真实输入后只投递一次，收件箱保存完整文本', 5.05)
    r.p('文件状态除 file_open=true 外还核对内容哈希，文件被更改时不能只凭 FILE OPEN 标签成功。消息的操作 ID 相同则不新增记录，重用 ID 却改变文本会拒绝；此幂等边界由单元测试验证，真实正常场景只提交一次。', small=True)
    r.evidence(FULL + '/open_file-none/session/summary.json', FULL + '/open_file-none/fixture/sample.txt', FULL + '/send_message-none/session/summary.json', FULL + '/send_message-none/fixture/inbox.json')

    r.page_start('十一 浏览器打开与关闭复核')
    r.figure(OPEN + '/observations/0006_agent_goal/screenshot.png', '最终打开复核：真实启动器与实际浏览器存在性共同确认', 5.05)
    r.figure(CLOSE + '/observations/0005_agent_goal/screenshot.png', '最终关闭复核：本轮浏览器实际窗口消失，启动器继续存活供观察', 5.05)
    r.p('关闭前核对唯一标题、HWND 和 PID，只发送 WM_CLOSE，不强制杀死个人进程；关闭后按同一 HWND/PID 的窗口是否仍存在判断，单纯标题变化不能算关闭。最终关闭复核启动时由电脑操作工具协助激活专用启动器，任务点击、OCR 与确认由项目完成。', small=True)
    r.evidence(OPEN + '/session/summary.json', OPEN + '/browser.json', CLOSE + '/session/summary.json', CLOSE + '/browser.json')

    r.page_start('十二 测试类型模块与数量')
    r.table(['类型与阶段', '功能模块', '数量', '结果与计数说明'], [
        ['第一周离线', '感知、控制、定位、流程、双通道、发布与分类', 60, '全部通过，第一周冻结结果'],
        ['第二周单元/集成', '数据适配与管线', 23, '三来源、坐标、去重、划分与完整性'],
        ['第二周单元/接口', 'LangChain 与客户端', 35, 'HTTP、输入输出校验、中断与脱敏'],
        ['第二周单元/规划', 'GuiPlanner', 30, '23 契约 + 7 管线/CLI'],
        ['第二周单元/闭环', 'AgentSession 与输入适配', 37, '17 流程 + 9 异常 + 11 输入/诊断'],
        ['第二周单元/任务', 'gui_tasks', 26, '10 状态 + 7 流程 + 4 HTTP + 5 窗口边界'],
        ['全项目回归', '上述用例的最新完整运行', 211, '全部通过；新增项已包含，不重复加总'],
    ], [3.4, 5.1, 1.2, 7.7])
    r.table(['补充测试类型', '范围', '数量', '结果'], [
        ['模型接口集成', '回放与本地 HTTP', '7 组', '符合预期，NIM 待测'],
        ['任务规划集成', '合法/非法计划与数据接入', '11 组', '符合预期'],
        ['闭环模拟/HTTP', '正常、异常、上下文与目标', '14 组', '符合预期'],
        ['基础任务模拟', '五类正常 + 五类故障', '10 组', '符合预期，合成观察'],
        ['真实桌面 周五', '五类正常 + 五类故障', '10 类', '当前各类最新结果符合预期'],
        ['兼容性 历史', '真实 Windows 缩放', '2 档', '100%/125% 正常通过；200% 待测'],
        ['性能 历史', '同图 OCR 冷/热', '3/30 次', '分口径统计，未新增性能基准'],
    ], [3.4, 7.0, 1.6, 5.4])
    r.p('周五固定索引保留 11 份桌面报告、20 次场景尝试，按 case/fault 选各类最新结果。正式十组之前及补充复核共有 7 次初始化保护失败，另有 3 次单项成功；这些不增加“十类用例”的数量。', small=True)
    r.p('模拟、回放、HTTP 与桌面场景存在阶段性重复，不合并成一个模型成功率。正常任务完成率与保护断言通过率分开：周五正式正常任务 5/5；五类注入故障被拒绝也为符合预期。', small=True)
    r.evidence(FRI + '/verification.json', FRI + '/unit_tests.log', CHECK + '/summary.json', CHECK + '/unit_tests.log', THU + '/verification.json')

    r.page_start('十三 失败分类与 OCR 假阳性实验')
    r.table(['实际用例', '错误与分类', '派发', '原因及结果'], [
        ['目标缺失', 'target_unavailable\n定位失败', 0, '观察重试后仍缺失，不输入'],
        ['目标重名', 'AmbiguousTarget\n定位失败', 0, '未给序号，不猜测'],
        ['状态不变化', 'verification_timeout\n确认失败', 1, '两通道不满足，不补点击'],
        ['OCR 假阳性', 'verification_timeout\n确认失败', 1, 'OCR=true、state=false，拒绝成功'],
        ['权限拒绝注入', '运行器确认失败；应用 permission_denied', 1, '明确应用原因，不把所有超时归因权限'],
        ['前台丢失', 'FocusLost\n窗口保护', 0, '初始化停止，不伪造截图'],
    ], [3.0, 5.3, 1.0, 8.1])
    r.figure(FULL + '/open_file-false_ocr/observations/0006_step01_after3/screenshot.png', '真实页面仍为 READY；注入的 FILE OPEN 只存在于 OCR JSON 中', 4.6)
    r.p('本例主动注入 id=999、FILE OPEN、置信度 0.99。真实 file_open=false、file_sha256 为空，OCR 与独立状态不一致，总体失败且只有一次点击。它是故障注入，不是自然发生的 OCR 误识别；权限拒绝也是主动注入，没有修改 Windows 文件 ACL。', small=True)
    r.evidence(FULL + '/open_file-false_ocr/session/feedback.json', FULL + '/open_file-permission_denied/session/summary.json', FULL + '/open_file-permission_denied/application-final.json')

    r.page_start('十四 失败修复与对照记录')
    fail = 'output/revision/system/scale-125/20261026_212909_983255'
    r.p('125% 首次测试中，第一步已通过双重确认，随后原子替换状态文件被 Windows 拒绝，发布回调退出，旧状态超过 2 秒。运行器报告 StateUnavailable，保留已经发生的输入，没有把 OCR 回显当作成功。', small=True)
    r.figure(fail + '/failure-desktop.png', '125% 首次状态发布失败的真实局部截图，原图区域像素未改', 5.25)
    r.p('修复将瞬时发布拒绝留待下个 40 ms 周期重试，不刷新旧快照时间戳；持续失败仍触发过期保护。新增三项发布回归通过，125% 重测三步成功。未确定具体占用者，不推断管理员权限差异。', small=True)
    r.table(['后续问题', '保留的对照与修复结果'], [
        ['周四 SendInput', '退出游戏前 5 次正常流程失败；批量 1/2、逐事件 0/1，后续有效事件记录错误 87。用户退出游戏后同代码正常与假阳性两类通过。'],
        ['原因判断', '支持与当时输入环境有关，但未定位具体组件；未修改防护服务。零事件探针始终不能代替有效输入验收。'],
        ['周五前台保护', '7 次初始化失败均留档；取得前台后正式矩阵与最终打开/关闭复核通过，不从前台失败推断系统拦截。'],
        ['关闭判据', '区分标题身份与实际窗口存在性，新增回归防止标题变化被误认为关闭；只操作本轮窗口。'],
    ], [3.2, 14.2])
    r.evidence(fail + '/summary.json', fail + '/failure-analysis.json', 'docs/input-sending-diagnosis.md',
               'output/week2-thursday/desktop-normal/20261027_180733_632796/summary.json')

    r.page_start('十五 复现入口与待完成事项')
    r.table(['操作', '项目根目录下的命令'], [
        ['完整离线回归', '.\\.venv\\Scripts\\python.exe scripts/check_project.py --with-evidence'],
        ['OCR 冷热基准', '.\\.venv\\Scripts\\python.exe benchmark_ocr.py --cold 3 --warm 10'],
        ['多步规划回放', '.\\.venv\\Scripts\\python.exe agent_cli.py --mode plan --demo'],
        ['闭环模拟', '.\\.venv\\Scripts\\python.exe run_agent.py --demo'],
        ['基础任务模拟', '.\\.venv\\Scripts\\python.exe run_basic_tasks.py --with-protection'],
        ['基础任务真实桌面', '.\\.venv\\Scripts\\python.exe run_basic_tasks.py --desktop --with-protection'],
    ], [4.1, 13.3], 8.7)
    r.p('真实命令会创建测试窗口，运行期间需保持前台并暂停鼠标键盘操作。默认模拟入口不访问真实桌面，也不请求模型服务。回归命令中的旧证据检查只读取已保存结果，不重新执行历史 GUI 场景。', small=True)
    r.h('当前限制与后续实验')
    r.p('NIM 仍需在服务可用时完成真实图片请求、结构化计划质量及端到端任务验证。现有 HTTP 模拟与回放不能替代真实模型评估，也尚未进行 GUI 模型微调。')
    r.p('125% 的异常矩阵、200% 缩放、多屏控制、任意第三方应用的独立状态适配和真实权限边界仍待测。应增加不同任务、重复轮次和稳定负载条件后，再报告成功率及延迟分布。')
    r.p('同步模型调用无法立即取消，返回或超时后才会处理停止状态；当前窗口与已导出字段复核也不能覆盖全部未导出的控件语义变化。下一阶段应先完成真实模型联调及适配，再开展小规模微调和更广泛评测。')
    r.h('主要结论')
    r.p('现有实现已形成可追溯的受控执行原型：实际输入、逐步证据、失败分类与调用方最终目标连接完成。实验报告保留成功和失败现场，并明确区分历史结果、模拟与真实执行；后续工作的重点是验证真实模型及更广应用范围。')

    r.page_start('附录 原始证据索引')
    r.p('下列路径以项目根目录为基准。配套证据包在 evidence/ 下保留相同相对路径，并附带 SHA-256 清单。本文图像均直接嵌入 Word，脱离项目目录仍可查看。', small=True)
    evidence_rows = [
        ['E01 环境', 'cuda-verification.json'],
        ['E02 OCR 基准', BENCH + '/summary.json'],
        ['E03 缩放矩阵', 'output/revision/system-summary.json'],
        ['E04 数据数量与复现', MON + '/verification.json'],
        ['E05 模型接口', TUE + '/verification.json'],
        ['E06 多步规划', WED + '/verification.json'],
        ['E07 周四闭环', THU + '/verification.json'],
        ['E08 周五完整验证', FRI + '/verification.json'],
        ['E09 最新完整回归', CHECK + '/summary.json'],
        ['E10 逐例回归日志', CHECK + '/unit_tests.log'],
        ['E11 具体规则', 'docs/verification-and-target-rules.md'],
        ['E12 周五详细记录', '第二周周五任务验证记录.md'],
    ]
    r.table(['证据', '原始路径'], evidence_rows, [4.3, 13.1], 8.4)
    for _, p in evidence_rows: r.evidence(p)
    r.p('截图来源、配套 results.json/state.json、已引用 summary/feedback 以及生成文档的哈希统一记入证据包清单。包中不包含个人浏览器配置、模型密钥、完整训练数据、模型权重或未经引用的桌面截屏。', small=True)
    r.p('数据来源原始登记：ScreenAgent 官方仓库、Multimodal Mind2Web 官方数据卡与 WebArena 官方配置，固定版本和许可信息见 docs/data-sources.json。报告的测量值以本机留存的原始 JSON 和日志为准；未对外部技术事实重新开展研究。', small=True)
    r.evidence('docs/week2-friday.md', '第二周周四任务验证记录.md', '第二周周五任务使用说明.md')
    r.doc.save(DOCX)
    manifest = {'docx': str(DOCX), 'expected_pages': r.page, 'figure_count': len(r.figures), 'figures': r.figures,
                'evidence': [{'path': p, 'sha256': hashlib.sha256((ROOT / p).read_bytes()).hexdigest(), 'bytes': (ROOT / p).stat().st_size}
                             for p in sorted(r.files)], 'full_project_tests': 211, 'new_task_tests': 26,
                'real_model_validation': 'pending', 'scale_200': 'pending'}
    (WORK / 'report-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'docx': str(DOCX), 'pages': r.page, 'figures': len(r.figures), 'evidence_files': len(r.files)}, ensure_ascii=True))


def bundle():
    manifest = read('tmp/report-two-weeks/report-manifest.json')
    files = {item['path'] for item in manifest['evidence']}
    pending = []

    def add(path):
        path = Path(str(path).replace('\\', '/'))
        resolved = (ROOT / path).resolve()
        if not resolved.is_relative_to(ROOT) or not resolved.is_file():
            return
        relative = resolved.relative_to(ROOT).as_posix()
        if relative not in files:
            files.add(relative)
            if resolved.suffix == '.json': pending.append(relative)

    def observation(path):
        for name in ['screenshot.png', 'annotated.png', 'results.json', 'state.json', 'text.txt']:
            add(Path(str(path).replace('\\', '/')) / name)

    def summary(path):
        add(path)
        p = Path(str(path).replace('\\', '/'))
        for name in ['feedback.json', 'actions.jsonl', 'events.jsonl', 'browser.json', 'application-final.json']:
            add(p.parent / name)
            if p.parent.name in ('session', 'agent'): add(p.parent.parent / name)

    for index in [FRI + '/verification.json', THU + '/verification.json']:
        value = read(index)
        for attempt in value.get('desktop_attempts', []):
            if attempt.get('report'): add(attempt['report'])
            if attempt.get('summary'): summary(attempt['summary'])
            for row in attempt.get('cases', []):
                if row.get('summary'): summary(row['summary'])
    for path in ['output/revision/regression/20261026_213006_614989/summary.json',
                 'data/week2/processed/20261026T145603_897824Z/manifest.json']:
        add(path)
    pending.extend(p for p in files if p.endswith('summary.json') or p.endswith('verification.json'))
    visited = set()
    while pending:
        path = pending.pop()
        if path in visited: continue
        visited.add(path)
        value = read(path)
        if not isinstance(value, dict): continue
        if value.get('workflow_summary'): summary(value['workflow_summary'])
        for location in value.get('observations', []): observation(location)
        for step in value.get('steps', []):
            for location in step.get('observations', []): observation(location)
            location = step.get('verification', {}).get('directory')
            if location: observation(location)
    manifest['evidence'] = [{'path': p, 'sha256': hashlib.sha256((ROOT / p).read_bytes()).hexdigest(), 'bytes': (ROOT / p).stat().st_size}
                            for p in sorted(files)]
    records = []
    target = OUT / (NAME + '-截图与记录.zip')
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(DOCX, DOCX.name)
        for item in manifest['evidence']:
            path = ROOT / item['path']
            if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
                raise ValueError('Evidence changed after report build: ' + item['path'])
            archive.write(path, 'evidence/' + item['path'])
            records.append(item)
        metadata = {'report': DOCX.name, 'report_sha256': hashlib.sha256(DOCX.read_bytes()).hexdigest(),
                    'evidence_count': len(records), 'files': records, 'figures': manifest['figures']}
        archive.writestr('evidence-manifest.json', json.dumps(metadata, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        for item in records:
            assert hashlib.sha256(archive.read('evidence/' + item['path'])).hexdigest() == item['sha256']
    print(json.dumps({'bundle': str(target), 'evidence_count': len(records), 'bytes': target.stat().st_size}, ensure_ascii=True))


if __name__ == '__main__':
    bundle() if '--bundle-only' in sys.argv else build()
