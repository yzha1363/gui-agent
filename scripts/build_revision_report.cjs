// Native OOXML authoring; actual measurements and test counts are read from JSON.
const fs = require('node:fs');
const path = require('node:path');
const {p,h,sub,body,caption,table,figure,packageDoc} = require('./report_ooxml.cjs');
const root=process.cwd();
const load=f=>JSON.parse(fs.readFileSync(path.join(root,f),'utf8'));
// This is a historical week-one report: later weeks must not change its evidence scope.
const regDir='output/revision/regression/20261026_213006_614989';
const perfDir='output/revision/benchmark/20261026_203843_946808';
const reg=load(regDir+'/summary.json'), perf=load(perfDir+'/summary.json'), cuda=load('cuda-verification.json');
const system=load('output/revision/system-summary.json');
if(!reg.success || reg.tests!==60 || system.tests!==6 || system.passed!==6) throw Error('Review report inventory after test changes');
const currentCase=(scenario,scale=100)=>system.cases.find(c=>c.scenario===scenario&&c.scale_percent===scale);
const shot=(scenario,scale=100)=>currentCase(scenario,scale).last_observation.replaceAll('\\','/')+'/screenshot.png';
const failureDir='output/revision/system/scale-125/20261026_212909_983255';
const small=t=>p(t,{size:19,line:285,after:75});
const code=t=>p(t,{size:18,line:275,after:75,font:'等线'});
const pages=[];
pages.push(
 p('桌面 GUI 智能体基础模块实验报告',{size:36,bold:true,style:'Title',font:'微软雅黑',after:160})+
 p('第一周开发成果与桌面补测修订',{size:25,after:90})+
 small('修订日期  2026年10月26日    历史桌面实验日期  2026年10月20日')+
 h('一  实验概况')+
 body('本阶段建立了基于 CUDA 的屏幕截图、中文与英文 OCR、文字定位、鼠标键盘控制和显式 JSON 流程执行模块。本次针对评审意见增加 OCR 与真实控件状态的双重确认、重名候选处理规则、失败分类及同图冷热启动基准，并重新整理测试章节。')+
 body(`本次 ${reg.tests} 项离线单元及回归用例全部通过；OCR 完成 3 次新进程冷启动与 30 次热推理测量。新版系统测试的 6 组场景最终均符合预期：100% 缩放下五种场景，以及 125% 下正常流程。实际 DPI 分别为 96 和 120，分辨率均为 1920×1080。200% 按要求保留待测，系统已恢复 100%。`)+
 sub('实验环境')+
 table(['项目','配置与验证范围'],[
 ['操作系统与屏幕','Windows 64 位；历史主屏 1920×1080，单屏控制'],
 ['GPU','NVIDIA GeForce RTX 5070；显存约 '+cuda.vram_gb+' GB'],
 ['Python 与框架',cuda.python+'；PyTorch '+cuda.torch+'；torchvision '+cuda.torchvision],
 ['CUDA 与 cuDNN','CUDA 运行时 12.8；cuDNN 9.10.2；计算能力 12.0'],
 ['感知与控制','MSS 10.2.0；EasyOCR 1.7.2；PyAutoGUI 0.9.54；pynput 1.8.2'],
 ['验证方法','unittest 与 Mock 输入后端；独立 Tk 测试窗口；原始 JSON 和截图留档']
 ],[2050,7350])+
 body('历史 CUDA 检查覆盖矩阵乘法与 CPU 对照、反向传播、cuDNN 卷积和 torchvision CUDA NMS。本项目当前仍由人工 JSON 给出任务步骤，尚未接入多模态大模型自主规划、公开数据集训练或 LoRA 微调。')+
 sub('本次结论的边界')+
 body('离线测试覆盖双通道判定与异常分支；桌面补测验证两档缩放下的正常流程及 100% 下的异常保护。125% 首次状态发布故障已修复，失败证据保留。200%、其他应用、多屏控制及管理员权限边界仍未验收，不推断任意桌面任务的成功率。')
);
pages.push(
 h('二  系统结构与双重结果确认')+
 body('系统分为感知、控制和流程编排三层。感知层保存截图与物理坐标；控制层检查窗口、边界和停止请求；流程层验证计划、执行一次动作，再重新观察两个独立通道。')+
 table(['步骤','OCR 回显条件','独立控件状态条件'],[
 ['激活输入框','INPUT READY','输入框确实获得焦点，状态变量为 INPUT READY'],
 ['输入文字','Hello GUI 123 中文测试','entry.get() 与预期逐字符相等'],
 ['提交结果','SUBMITTED Hello GUI 123 中文测试','提交变量精确相等，提交按钮为可用状态']
 ],[1400,3300,4700])+
 sub('判定规则')+
 body('成功条件为 OCR.met 与 state.met 同时为真。OCR 文本允许 NFKC、空白和大小写归一化；控件内容采用逐字符精确比较，布尔与整数不互相替代。独立状态缺失或不符时，即使 OCR 把错误文本识别成正确文本，也不能将步骤标记为 verified。')+
 body('演示程序在 Tk 主线程读取控件内容、焦点和提交状态，以原子替换方式导出状态文件。运行器检查导出时间不超过 2 秒、HWND 与已锁定窗口相同。截图前后状态或窗口位置改变时，丢弃不稳定观察，重新取样。状态证据从不由 OCR 结果生成。')+
 code('expect: { text: "INPUT READY", state: { input_focused: true, status: "INPUT READY" } }')+
 body('每次观察保留 screenshot.png、annotated.png、results.json、text.txt 与 state.json。步骤摘要分别记录 verification.ocr、verification.state.checks、期望值、实际值及各自判定。dispatch_started 表示可能部分发送，action_sent 表示调用返回，verified 才表示两通道都满足。')+
 sub('错误识别的验证')+
 body('离线 DC01 用例注入 OCR=DONE、实际状态=WRONG，预期整体失败。运行器测试确认仅点击一次，随后超时并归为 verification_failure。DC02 则将真实状态设为正确、OCR 设为错误，也不得成功。模拟用于检验判定逻辑，不代表 EasyOCR 在该截图上实际发生误识别。')+
 sub('适配范围')+
 body('当前状态接口是受控演示应用的测试适配器。接入第三方软件仍需 UI Automation、原生控件或应用状态接口，不能自动读取任意软件；没有适配器时拒绝执行。窗口标题不足以证明输入或提交正确。新版受控 GUI 已在 100% 与 125% 下逐步完成 OCR 和真实状态的双重确认。')
);
pages.push(
 h('三  重名目标处理规则')+
 body('重名候选不按最高置信度自动选择。处理顺序如下，序号与点击坐标都只对应当前这一帧观察。')+
 small('1  限定窗口。精确匹配前台标题并锁定首次 HWND，只识别该客户区。其他窗口同名控件不进入候选；同标题但 HWND 改变也终止。')+
 small('2  统一文字。先执行 NFKC、移除空白和 casefold。默认完整文本匹配，仅 contains=true 时允许包含匹配。归一化后相同的全角、大小写或空格变体按重名处理。')+
 small('3  过滤与校验。置信度必须有限且达到阈值；匹配框必须有正面积、有限坐标并完整位于截图内。匹配框非法时拒绝使用；不通过提高分数猜测用户意图。')+
 small('4  排序。候选按物理框 (top, left, bottom, right, 字符串形式的 id) 升序。先严格比较上边界，再比较左边界，不做视觉行聚类；同一行的细小高度差也可能改变 occurrence。')+
 small('5  缺失或歧义。无候选时仅重新观察，最多 lookup_attempts 次，仍无结果为 target_unavailable。多个候选且未指定 occurrence 时立即 AmbiguousTarget，不点击、不靠重试碰运气消歧。')+
 small('6  明确选择。occurrence 从 1 开始，必须为正整数，不能为布尔值、0、负数或越界。完全相同的框无法区分重复检测，即使给序号也拒绝；部分重叠框保留为多个候选，不自动合并。')+
 small('7  点击前复核。保存 ID、文字、置信度、物理框和中心。窗口、遮挡或目标补丁改变时重新截图及排序；补丁灰度平均差阈值为 12。动态列表的序号不保证同一语义控件，应增加应用专用范围或语义约束。')+
 small('8  派发后不重放。一旦开始点击或输入，不因确认超时自动重复执行，防止重名、状态延迟或部分输入引发重复操作。')+
 table(['示例','预期行为'],[
 ['两个 INPUT FIELD，无 occurrence','AmbiguousTarget；0 次输入'],
 ['两个 INPUT FIELD，occurrence=2','选择本帧排序后的第二个有效框'],
 ['两个候选，分数 0.95 和 0.51','都满足阈值时仍为歧义，不自动选 0.95'],
 ['两个同名且完全相同的框','拒绝选择；序号无法消除几何歧义']
 ],[4450,4950])+
 small('实现与详细说明：gui_control/targets.py；docs/verification-and-target-rules.md。原有定位 5 项加新增重名边界 5 项离线用例均通过。')
);
const statKeys=[['import_ms','进程内依赖导入'],['reader_init_ms','Reader 初始化及 EasyOCR 导入'],['first_inference_ms','首次推理'],['cold_ready_ms','进程内启动到首次结果'],['warm_inference_ms','Reader 热推理']];
pages.push(
 h('四  OCR 冷启动与热启动测量')+
 body('使用同一张 1280×720 中文与英文图片，三个新 Python 进程分别创建 Reader。每个进程完成首次推理后，复用同一 Reader 再测 10 次，共 30 次热样本。canvas_size=1600、batch_size=1、workers=0；每次推理结束同步 CUDA，计入 GPU 工作完成。')+
 table(['指标','N','平均 ms','中位 ms','P95 ms'],statKeys.map(([key,label])=>{const s=perf.statistics[key];return[label,s.n,s.mean_ms.toFixed(2),s.median_ms.toFixed(2),s.p95_ms.toFixed(2)]}),[3650,550,1750,1700,1750])+
 small('冷路径包含进程内依赖导入、图片读取、Reader 初始化和首次推理；从 worker 函数入口计时，不包含解释器启动前的时间。模型已下载、OS 缓存未清理，不是重启机器或首次下载模型的耗时。Reader 初始化阶段还包括 EasyOCR 的延迟导入。')+
 small('热推理范围 157.14–388.68 ms。P95 使用 nearest rank；3 个冷样本的 P95 等于最大值，仅作描述。后台负载未隔离，无 CPU 对照，不作硬件横向结论。process_wall_ms 含热推理与进程退出，不得当作冷启动。')+
 figure('output/wednesday-validation/1280x720/annotated.png',4.9,'历史固定输入图片及 OCR 标注')+
 caption('图1  固定 OCR 输入的历史标注图  2026年10月20日')+
 small('基准原始逐进程与逐次数据：'+perfDir+'/。输入 SHA-256 已记录，可核对三次输入完全一致。此前不同尺寸图片的 15133.90、257.62、237.27 ms 保留为历史单次值，不再用来比较分辨率性能。')
);
pages.push(
 h('五  Windows 实际显示缩放验证')+
 body('此前的图片尺寸检查不能证明操作系统缩放行为。本次通过 Windows 显示设置真实切换 100% 与 125%，保持主屏分辨率 1920×1080。每档重启演示窗口，由 GetDpiForWindow 记录实际 DPI，再运行完整正常流程。')+
 table(['系统缩放','目标 DPI','分辨率约束','需要确认的指标','状态'],[
 ['100%','96 实测','同一主屏\n1920×1080','三步 OCR 与独立状态均通过，含恢复后回归','通过'],
 ['125%','120 实测','同上','首次发布状态失败，修复后三步均通过','通过'],
 ['200%','192','同上','同上，检查布局截断与坐标虚拟化','待测']
 ],[1200,1000,1850,4200,1150])+
 sub('执行方法与记录')+
 small('1  记录原缩放、屏幕分辨率和显示器。通过 Windows 显示设置修改真实缩放，保持分辨率与主屏一致，每档重启测试窗口。')+
 small('2  用启用 DPI 感知的目标窗口调用 GetDpiForWindow，分别确认 96、120、192；记录 HWND、DPI、实际物理截图尺寸和窗口边界。返回 0 或与档位不符时，该轮不能计为通过。')+
 small('3  执行 normal 三步流程：定位 INPUT FIELD、输入中英文、提交。OCR 与控件状态必须逐步同时满足；保存目标框、点击中心、原图和 state.json。')+
 small('4  本次在 100% 下补测重名、缺失、界面不变化和 OCR 假阳性；125% 只验收正常三步流程，未扩展四类异常测试。动态缩放中的旧坐标不得继续使用。')+
 small('5  恢复原缩放。若系统要求注销或其他应用无法保持稳定，记录中断条件，不将未完成档位填为通过。')+
 code('.\\.venv\\Scripts\\python.exe scripts/test_display_scaling.py --scale 125')+
 body('缩放启动器不自动修改系统设置。本次运行 friday_demo.py 的 --expected-scale 125 与 100 参数，实际窗口 DPI 不符时在发送动作前拒绝执行。两档均保存物理像素截图、目标坐标、OCR 结果及独立 state.json。')+
 sub('200% 与系统恢复')+
 body('当前 Windows 预设列表只有 100%、125%、150%、175%，没有 200%。200% 需进入自定义缩放，可能涉及注销；本次按要求不应用自定义缩放、不注销，保留待测。补测结束后已将显示设置恢复 100%，恢复后的新窗口实测 96 DPI，正常三步回归通过。')
);
const groups=[['CoordinateTests','截图坐标'],['ControllerTests','输入控制'],['TargetTests','文字定位'],['WorkflowTests','流程编排'],['DoubleCheckTests','双通道判定'],['DoubleCheckRunnerTests','双通道及失败流程'],['StateFileTests','状态读取'],['StatePublisherTests','状态发布恢复'],['DuplicateEdgeTests','重名边界'],['FailureCategoryTests','异常分类']];
pages.push(
 h('六  测试类型 模块与数量')+
 body('本次离线测试明细由运行工具自动记录，含全部 case ID、测试类、状态和耗时。下表按测试类型、功能模块和数量列出结果。')+
 table(['测试类型','功能模块','数量','实际结果'],[
 ...groups.map(([key,label])=>['单元及离线回归',label,String(reg.groups[key].total),reg.groups[key].passed+' 通过']),
 ['本次离线合计','以上十个模块','60','60 通过；0 失败'],
 ['历史系统功能','OCR 尺寸与空白图','4 张图','2026-10-20 留档通过'],
 ['历史系统功能','点击 输入 滚动 拖拽','13 检查项','2026-10-20 留档通过'],
 ['历史系统功能','正常与异常闭环','8 场景','旧版符合预期'],
 ['本次性能','OCR 冷启动','3 次','固定输入，新进程'],
 ['本次性能','OCR 热推理','30 次','Reader 复用'],
 ['本次系统回归','100% 新版五场景','5 场景','均符合预期'],
 ['本次兼容性','100% 与 125% 正常流程','2 档','均完成双重确认'],
 ['兼容性待测','200% 真实缩放','1 档','按要求保留待测']
 ],[2250,3250,1350,2550])+
 small('计数口径：系统按“缩放×场景”去重为 6 组，100% 正常流程同时属于五场景与两档兼容性，不能重复加总。125% 首次失败及重复运行另列 run_attempts，不隐藏失败。离线回归与单元测试也不重复计算。')+
 code('本次明细  '+regDir+'/summary.json')+
 small('本次系统原始日志与去重断言见 output/revision/system-summary.json。单元测试使用 Mock，不生成桌面截图；新版真实截图见第九至十一节。历史系统结果单列，不替代本次验收。')
);
pages.push(
 h('七  具体用例与实际结果')+
 table(['Case','前置条件与输入','预期与本次实际结果'],[
 ['DC01','OCR=DONE；独立状态=WRONG','超时失败；verification_failure；仅点击 1 次'],
 ['DC02','OCR=WRONG；独立状态=DONE','整体 false，不将控件单独正确视为成功'],
 ['DC03','OCR=DONE；state=None','不能成功；运行器超时，禁止单通道降级'],
 ['ST01','导出时间为 10 秒前；上限 2 秒','StateUnavailable，拒绝旧状态'],
 ['ST02','导出 HWND=456；锁定 HWND=123','StateUnavailable，拒绝其他窗口状态'],
 ['TG01','两个 START；未指定 occurrence','AmbiguousTarget；0 次点击'],
 ['TG02','两个 START；occurrence=2','按本帧几何顺序选择第二个，点击预期坐标'],
 ['TG03','两个同名、完全同框；occurrence=1','仍 AmbiguousTarget，序号不能消除重复检测'],
 ['TG04','同名分数 0.95 与 0.51；阈值 0.5','仍为歧义；不自动选最高分'],
 ['FL01','Mock click 抛 RuntimeError','execution_failure；派发已开始，调用 1 次不重试'],
 ['FL02','Mock click 抛 PermissionError','permission_denied；调用 1 次；属于模拟权限用例'],
 ['FL03','检查时抛 FocusLost','window_guard_failure；0 次点击'],
 ['FL04','Escape 请求中止','aborted；停止后续输入']
 ],[1000,3600,4800])+
 small('上述用例均满足断言。DC01 对应 test_forged_ocr_times_out_without_repeating_input；FL01、FL02、FL03 对应 DoubleCheckRunnerTests 中各自独立方法。完整 ID 和逐条状态见本次 regression/summary.json。')+
 sub('模拟与真实实验的区别')+
 body('Mock 用例用于稳定复现难触发的错误分支，不能证明真实管理员权限边界已经验证。false_ocr 已完成真实桌面故障注入：控件保持 WRONG STATE，向 OCR JSON 加入高置信度 INPUT READY 并标记 fault_injection。最终 OCR=true、独立状态=false、整体=false，仅点击一次后超时；它证明防误判逻辑，不能写成 EasyOCR 自然发生的误识别。')
);
pages.push(
 h('八  失败分类与历史场景分析')+
 table(['类别','典型错误','处理规则'],[
 ['定位失败','AmbiguousTarget\ntarget_unavailable\nTargetError','派发前停止；缺失可重观察，重名不猜测'],
 ['执行失败','RuntimeError 或未知输入异常','可能部分发送；保留派发状态，不重放'],
 ['权限不足','PermissionError 或 WinError 5','仅在明确访问被拒绝时归类；停止'],
 ['窗口保护','FocusLost','前台或 HWND 改变；停止后续输入'],
 ['结果确认失败','verification_timeout\ncondition_already_satisfied','两通道未同时成立或操作前已成立；不重发'],
 ['状态不可用','StateUnavailable','文件缺失、过期或不属于目标窗口；停止'],
 ['主动中止','StopRequested\nKeyboardInterrupt\nFailSafeException','Esc、STOP 或保险触发；终止后续动作']
 ],[1600,3150,4650])+
 sub('历史八个场景的结果分布')+
 small('2026年10月20日，正常 GUI 与 CLI 各 1 个 completed，共 2 个；重名与缺失属于定位失败，共 2 个；不变化超时属于确认失败，1 个；Esc 与 STOP 属于主动中止，共 2 个；失焦属于窗口保护，1 个。异常场景符合预期属于测试通过，不是任务执行成功。')+
 small('历史原始日志未改写。新版系统测试包含重名与缺失两类定位失败、no_change 与 false_ocr 两类确认失败；均符合防护预期。另有 125% 首次状态发布异常，窗口控制成功但状态文件过期，归 state_unavailable；下节保留实际失败与修复证据。PermissionError 注入只验证分类，不等于管理员权限边界验收。')+
 sub('本次启动拦截')+
 small('新版启动尝试在前台检查中发现目标窗口未获焦点，记录 FocusLost / window_guard_failure，未发出任务动作。该运行未完成 OCR 与双通道流程，不纳入五场景验收。')+
 code('output/revision/system/normal-100/20261026_203915_460657/summary.json')+
 small('该目录中的 normal-100 是原计划标签，不代表实际验证了 100% Windows 缩放。')
);
pages.push(
 h('九  真实失败用例与修复')+
 body('125% 首次测试中，输入框激活已通过双重确认；随后 Windows 拒绝替换状态文件，Tk 发布回调退出，旧状态超过 2 秒。运行器记录 StateUnavailable，未把已输入的文字判为成功。原始失败 summary.json 未覆盖。')+
 figure(failureDir+'/failure-desktop.png',5.2,'125% 状态发布失败后的真实输入框画面')+
 caption('图2  125% 失败现场局部截图 输入已发生 状态证据不可用')+
 table(['阶段','实际结果'],[
 ['根异常','PermissionError WinError 5，发生于状态文件原子替换；未证明具体占用者或管理员权限差异'],
 ['流程判定','state_unavailable；保留派发状态，不宣称任务成功'],
 ['修复','临时拒绝只跳过当前发布，下个 40 ms 周期重试；不修改旧快照的时间戳'],
 ['重测','125% 三步通过；又记录 1 次发布拒绝但自动恢复；新增 3 项发布回归测试通过']
 ],[1500,7900])+
 small('证据：'+failureDir+'/summary.json、failure-analysis.json、failure-desktop.png。图2仅裁去窗口外区域，保留区像素未改；裁剪范围已记录。重测见系统汇总的 125% normal，持续发布失败仍会触发过期保护。')
);
pages.push(
 h('十  新版正常流程的真实截图')+
 small('以下为 2026年10月26日新版受控窗口原始截图，分别对应实际 96 DPI 与 120 DPI；物理屏幕均为 1920×1080。')+
 figure(shot('normal',100),5.05,'100% 恢复后正常输入与提交的真实截图')+
 caption('图3  100% 实测 96 DPI 三步 OCR 与独立状态均通过')+
 small('对应恢复后的 normal 回归：3 次任务动作，真实 entry_text 与 submitted 均精确相等。')+
 figure(shot('normal',125),5.05,'125% 缩放下正常输入与提交的真实截图')+
 caption('图4  125% 实测 120 DPI 修复后三步均通过')+
 small('125% 截图中的文字渲染与 100% 不同。窗口位置仍按物理客户区计算，逐步保存 OCR 与控件值；没有用图片重采样替代系统缩放。')
);
pages.push(
 h('十一  新版异常场景的真实截图')+
 figure(shot('duplicate'),5.1,'本次重复 INPUT FIELD 的真实窗口')+
 caption('图5  重名目标 同时出现两个 INPUT FIELD')+
 small('本次 duplicate 场景：未指定 occurrence，AmbiguousTarget，任务动作 0 次。missing 也未发出动作，no_change 只点击一次后超时，原始截图均留档。')+
 figure(shot('false_ocr'),5.1,'实际 WRONG STATE 与注入 OCR 成功文本不一致的窗口')+
 caption('图6  OCR 假阳性注入 实际界面为 WRONG STATE')+
 small('results.json 注入 INPUT READY，verification.ocr.met=true；真实 input_focused=false、status=WRONG STATE，state.met=false，整体失败。仅点击一次，未误报完成。两图均为 2026年10月26日实测。')
);
pages.push(
 h('十二  复现步骤与证据索引')+
 small('1  使用项目虚拟环境执行 scripts/test_revision.py，生成新的逐用例离线明细；随后运行 scripts/check_project.py --with-evidence，检查计划、依赖与历史证据。')+
 code('.\\.venv\\Scripts\\python.exe scripts/test_revision.py')+
 code('.\\.venv\\Scripts\\python.exe scripts/check_project.py --with-evidence')+
 small('2  执行 benchmark_ocr.py，默认同图 3 个新进程、每个 10 次热推理。更换输入或参数时另存新基准，不混入本次统计。')+
 code('.\\.venv\\Scripts\\python.exe benchmark_ocr.py --cold 3 --warm 10')+
 small('3  启动 friday_demo.py，选择场景并点击 BEGIN RUN，保持目标窗口在前台。新版 CLI 必须提供独立状态文件；无状态证据则拒绝执行。')+
 code('.\\.venv\\Scripts\\python.exe run_workflow.py --plan plans/friday_demo.json --device cuda --state-file output/demo-state.json')+
 small('4  本次已完成 100% 五场景及 125% 正常流程。后续在适合的测试会话补测 200% 与其他档位的异常场景；不得用改变分辨率或重采样图片代替实际 DPI。')+
 sub('主要证据索引')+
 code('CUDA 环境  cuda-verification.json')+
 code('本次离线  '+regDir+'/summary.json')+
 code('本次性能  '+perfDir+'/summary.json')+
 code('本次系统  output/revision/system-summary.json  含每组断言及全部执行尝试')+
 code('历史 OCR  output/wednesday-validation/summary.json')+
 code('历史控制  output/thursday-validation/20261020_023039_804096/summary.json')+
 code('历史八场景  output/friday-test-summary.json 及其索引的原始目录')+
 small('代码规则与完整测试说明：docs/verification-and-target-rules.md；docs/testing.md。原第一周 Word 保留，修订版另存。性能受后台负载影响，缺少 CPU 对照；新版第三方应用适配、多屏控制、真实权限边界、模型规划与微调仍属后续工作。')+
 sub('参考资料')+
 small('[1] Microsoft Learn  GetDpiForWindow\nhttps://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getdpiforwindow')+
 small('[2] PyTorch  CUDA semantics  Asynchronous execution\nhttps://docs.pytorch.org/docs/stable/notes/cuda.html')+
 small('资料核对日期：2026年10月26日。测量结论以本项目原始 JSON、日志和截图为准。')
);
const output=path.join(root,'output/reports/桌面GUI智能体基础模块实验报告-修订版.docx');
packageDoc(pages.join('<w:p><w:r><w:br w:type="page"/></w:r></w:p>'),output);
pages.forEach((xml,i)=>packageDoc(xml,path.join(root,`tmp/report-revision/qa/section-${String(i+1).padStart(2,'0')}.docx`)));
fs.mkdirSync('tmp/report-revision',{recursive:true});
fs.writeFileSync('tmp/report-revision/structure.json',JSON.stringify({output,pages:pages.length,figures:6,regression:regDir,benchmark:perfDir,system:'output/revision/system-summary.json'},null,2));
console.log(output);
