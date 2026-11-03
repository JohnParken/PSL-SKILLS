# ET高级兼容、命名区域与审计

路径占位符说明：`<skill-root>` 是 ET Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 ET 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

BIFF8 `data` 模式额外接受有明确编码编号的 CodePage 874、949、950、1250、1251，作为声明元数据保留，不重新编码或转换任何保存值；`strict/legacy` 仍只接受 1200，缺少必需的 Date1904 仍拒绝。详细边界见非单元格拒绝分类（`<skill-root>/references/et-noncell-compatibility.md`）。

仅 BIFF8 data 模式新增精准非单元格规则：Globals 首位空负载 `WriteProtect` 和 Worksheet 首位零保留字段 `Uncalced`，均验证位置/唯一性后保留源字节并返回 partial；缺损、错位、其他未知记录保持拒绝。公式缓存新鲜度仍未验证；依据与反向安全样例见 电子表格非单元格拒绝分类（`<skill-root>/references/et-noncell-compatibility.md`）。

默认 data 对有证据的非单元格元数据及受支持图表保留原始记录并报告 partial；包括结构校验后的 DXF（`0x088D`）差异格式属性，格式不应用到保存值。未知记录、加密、结构与资源错误仍停止，不在失败后轮换策略。只有任务明确要求更窄子集才加 `--compatibility strict`；单独拒绝图表可加 `--chart-policy reject`。需排查格式时按结果读 BIFF8 边界与高级策略 (`<skill-root>/references/biff8-readonly.md`) 或 OOXML 保存值 (`<skill-root>/references/ooxml-spreadsheet.md`)；编码、容量及失败定位按需读 解析增强边界 (`<skill-root>/references/et-parser-stability.md`)，容器检查另见 CFB 调查 (`<skill-root>/references/et-ett-readonly.md`)。

已在中山市交通运输局两份公开 `.et` 文件上补齐 BIFF8 `TableStyle (0x088F)` 与相邻 `TableStyleElement (0x0890)` 的严格格式证据读取：仅 `data` 模式校验完整样式组及 DXF 索引后原样保留，返回 `partial`，不应用视觉样式。真实文件核验分别读取 32/43 个单元格，独立 MS-XLS 记录观察器共对照 37 项保存值；详见真实 ET 实测（`<skill-root>/references/biff8-gov-real-et-validation.md`）。两份的 CodePage 都为 1200，不证明其他 CodePage 的生产兼容性。

## BIFF8 静态命名区域与部分内置名称（默认 data 可读）

对 `.et/.ett` 内的 `Name (0x0018)`，仅当 Lbl 头、名称、内部 `SupBook/ExternSheet`、最终 `BoundSheet8` 作用域和静态公式结构均已验证时才受限保留：普通名称支持单个绝对 `PtgArea3d` 或同一内部表上至多两个区域的 `PtgUnion`；内置 `Print_Titles` 仅支持完整行/列区域，`_FilterDatabase` 仅支持一个静态区域，历史内置 `Print_Area` 的窄子集继续保留。结果为来源元数据并报告 `partial`，**不计算名称公式、不应用打印/筛选设置**。其他内置宏/函数名称、外链和无法确认语义的 Name 仍拒绝，即使开启临时 salvage 也不能绕过。已用中山市政府官网固定哈希原件验证此前被 Name 拒绝的 1 份 ET 现在可受限读取：2 张工作表、126 个已识别单元格，并有独立 BIFF 已知记录计数 `66+60` 对照；不等于独立逐格真值或 WPS 软件兼容认证。针对 `Print_Titles` 的另一种真实 BIFF8 编码，本轮已增加严格界定的 `PtgMemFunc(cce=23)` 双区域联合、以及尾随显示用 `PtgParen`，不增加通用公式解码。原始记录验证来自独立 LibreOffice 生成的 `.xls`，**不是 WPS Office 原生 `.et`**；新增九份政府 ET 原件也未观察到该类 Name。详细范围见静态内部名称边界（`<skill-root>/references/biff8-defined-names.md`）和独立生产者记录证据（`<skill-root>/references/biff8-print-title-real-evidence.json`）。

## BIFF8 临时最大兼容（仅显式低层入口）

如果用户**明确要求尽可能读取不标准或超出当前子集的 ET/ETT**，可在完成自检后选用独立 BIFF8 读取入口 `python -I -S -B "<skill-root>/scripts/wps_biff8.py" ./input.et --compatibility data --salvage-unknown-metadata`。它对有界未知记录只保留原始负载和位置、标记 `partial`，可在缺少 `Date1904/WsBool` 时保留原始单元格保存值，但**未知记录可能包含实际数据**，返回 `cell_value_coverage=unverified_due_to_best_effort_salvage`、`omissions_affect_cell_values=true`，不得用于自动对账、交易或验证报表总额。未知容器、FILEPASS、宏、错误核心记录及资源越界仍拒绝，不把 `--salvage-unknown-metadata` 加到统一 `read_et.py` 或 .wps/MS-DOC/OOXML 路由；详细边界见临时 BIFF8 部分读取（`<skill-root>/references/et-salvage-best-effort.md`）。

## BIFF8 额外审计（可选，不影响文字读取）

需要核对 ET/ETT 中富字符串 FormatRun 位置、公式保存缓存风险、原始 XF 格式证据时，可以在完成上述自检后，使用**独立入口** `python -I -S -B "<skill-root>/scripts/wps_biff8_audit.py" ./input.et`。它只接受经当前读取器确认的 `biff8_readonly_v1`，默认正常 `read_et.py` 输出与 Word/MS-DOC 文字读取器均不变；新增可选的 Font 原始记录引用、CalcMode/CalcSaveRecalc/RecalcId 保存值和 XF 父样式直接来源观察，**不计算字体效果、样式级联或公式缓存的最新性**，绝不执行公式。详情、范围、退出码和上限见 BIFF8 保存证据审计（`<skill-root>/references/biff8-stored-audit.md`）。本次为合成及格式证据验证，未证明新审计结果在 WPS Office 原生富文本/公式文档上的完整语义兼容。

