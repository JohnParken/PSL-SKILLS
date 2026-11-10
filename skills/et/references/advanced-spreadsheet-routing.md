# ET高级兼容、命名区域与审计

维护资源说明：文中 `docs/maintenance/et/` 与 `skills/et/scripts/test/` 均以宿主确认的仓库根目录为基准，仅供源码维护，不随运行包分发；运行任务不读取或执行。没有仓库时跳过维护资料，不按 Skill 安装目录拼接。

路径占位符说明：`<skill-root>` 是 ET Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 ET 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

BIFF8 `data` 模式额外接受有明确编码编号的 CodePage 874、949、950、1250、1251，作为声明元数据保留，不重新编码或转换任何保存值；`strict/legacy` 仍只接受 1200，缺少必需的 Date1904 仍拒绝。详细边界见非单元格拒绝分类（`<skill-root>/references/et-noncell-compatibility.md`）。

这里的默认 `data` 指 ET BIFF8 文件读取入口；显式 strict/legacy 与直接字节解析入口的默认行为保持原状。公式仅使用 `Formula` 记录中已有的保存缓存，不重算。

仅 BIFF8 data 模式新增精准非单元格规则：Globals 首位空负载 `WriteProtect` 和 Worksheet 首位零保留字段 `Uncalced`，均验证位置/唯一性后保留源字节并返回 partial；已识别记录的缺损和错位仍拒绝。公式缓存新鲜度仍未验证；依据与反向安全样例见 电子表格非单元格拒绝分类（`<skill-root>/references/et-noncell-compatibility.md`）。

默认 data 对有证据的非单元格元数据及受支持图表保留原始记录并报告 partial；包括结构校验后的 DXF（`0x088D`）差异格式属性，格式不应用到保存值。边界完整的未知记录及部分名称/调色板会保留为原始证据，标注覆盖不确定。加密、核心结构与资源错误仍停止。只有任务明确要求更窄子集才加 `--compatibility strict`；单独拒绝图表可加 `--chart-policy reject`。需排查格式时按结果读 BIFF8 边界与高级策略 (`<skill-root>/references/biff8-readonly.md`) 或 OOXML 保存值 (`<skill-root>/references/ooxml-spreadsheet.md`)；编码、容量及失败定位按需读 解析增强边界 (`<skill-root>/references/et-parser-stability.md`)，容器检查另见 CFB 调查 (`<skill-root>/references/et-ett-readonly.md`)。

已在中山市交通运输局两份公开 `.et` 文件上补齐 BIFF8 `TableStyle (0x088F)` 与相邻 `TableStyleElement (0x0890)` 的严格格式证据读取：仅 `data` 模式校验完整样式组及 DXF 索引后原样保留，返回 `partial`，不应用视觉样式。真实文件核验分别读取 32/43 个单元格，独立 MS-XLS 记录观察器共对照 37 项保存值；详见真实 ET 实测（`docs/maintenance/et/biff8-gov-real-et-validation.md`）。两份的 CodePage 都为 1200，不证明其他 CodePage 的生产兼容性。

## BIFF8 条件格式与分页元数据（默认 data 部分读取）

可读的 `CF` 常量比较和单独填充样式随 `uninterpreted_metadata[].structure` 返回，分别查看 `cell_value_comparison` 和 `differential_fill`。规则保持未执行、格式保持未应用。

默认 data 对 `CondFmt (0x01B0)` + 声明的 1–3 个相邻 `CF (0x01B1)` 先验证完整组边界、区域和内部字节分区，再保留源字节；有限的几何/操作符问题或长度完整文本中的语义问题可作为原始证据返回 partial，不求值或应用。组成员缺失/错位、截断、分区歧义或其他结构错误仍拒绝，不扫描跳过。对已完整分帧、数量受限的分页记录，将越界/倒序/重叠布局记入 `structure.layout_issues` 与 `page_break_layout_not_applied`，继续读取保存值；不修正原值或宣称打印布局有效。显式 strict/legacy 保留原边界。实际恢复三份 ETT，详见 `docs/maintenance/et/et-real-validation.md#template-compatibility-iteration-2026-11-03`；字段与限制见 `<skill-root>/references/biff8-readonly.md#inline-text-and-passive-metadata`。

## BIFF8 批注绘图与数据验证（默认 data 部分读取）

对已确认的 note 对象链，默认 data/opaque 在实际 Escher ClientData/ClientTextbox 边界拼接 MsoDrawing，核对 Obj、TxO、文字/格式续段及 Note 的长度和链接后原样保留，继续读取单元格。批注文字、作者和显示未作为已解码内容返回，绘图属性内部语义未验证；边界和未知对象不猜测。完整限制见 `<skill-root>/references/embedded-charts.md#fragmented-note-drawings-data-only`。`DVal/Dv` 另要求声明的相邻组边界和有效内部字节分区；有限的几何/操作符问题或长度完整文本中的语义问题可保留为原始证据并返回 partial。不执行公式、跟随外链、应用验证规则或显示提示；组缺失/错位、截断、分区歧义仍拒绝。两者均保留 partial，严格/legacy 不扩大，资源和截断错误仍拒绝。

## BIFF8 静态命名区域与部分内置名称（默认 data 可读）

对 `.et/.ett` 内的 `Name (0x0018)`，仅当 Lbl 头、名称、已验证的内部 XTI、最终 `BoundSheet8` 作用域和静态公式结构均已验证时才作静态解释：普通名称支持一个绝对内部单元格 `PtgRef3d`、单个绝对 `PtgArea3d` 或同一内部表上至多两个区域的 `PtgUnion`；内置 `Print_Titles` 仅支持完整行/列区域，`_FilterDatabase` 仅支持一个静态区域，历史内置 `Print_Area` 的窄子集继续保留。正确分帧的 `SupBook/ExternSheet` 记录保留原顺序和索引；`ExternSheet` 只可使用到达声明 XTI 字节数所需的有界相邻 `Continue`，相邻 `ExternName` 与 `XCT/CRN` 组保持不透明。外部工作簿及 add-in 项目保持不透明，相关名称可作为原始证据返回 partial，覆盖不确定。损坏/截断的链接分帧或无效索引仍拒绝；不打开/下载外部文件、不加载 add-in。结果为来源元数据并报告 `partial`，**不计算名称公式、不应用打印/筛选设置**；单元格公式只使用 `Formula` 中已有的保存缓存。已用中山市政府官网固定哈希原件验证此前被 Name 拒绝的 1 份 ET 现在可受限读取：2 张工作表、126 个已识别单元格，并有独立 BIFF 已知记录计数 `66+60` 对照；不等于独立逐格真值或 WPS 软件兼容认证。针对 `Print_Titles` 的另一种真实 BIFF8 编码，本轮已增加严格界定的 `PtgMemFunc(cce=23)` 双区域联合、以及尾随显示用 `PtgParen`，不增加通用公式解码。原始记录验证来自独立 LibreOffice 生成的 `.xls`，**不是 WPS Office 原生 `.et`**；新增九份政府 ET 原件也未观察到该类 Name。详细范围见静态内部名称边界（`<skill-root>/references/biff8-defined-names.md`）和独立生产者记录证据（`docs/maintenance/et/biff8-print-title-real-evidence.json`）。

## BIFF8 临时最大兼容（仅显式低层入口）

如果用户**明确要求尽可能读取不标准或超出当前子集的 ET/ETT**，可在完成自检后选用独立 BIFF8 读取入口 `execute(command="python -I -S -B \"<skill-root>/scripts/wps_biff8.py\" ./input.et --compatibility data --salvage-unknown-metadata")`。默认读取已对有界未知记录保留原始负载和位置、标记 `partial`；显式选项另外允许未知 CodePage 声明及缺少 `Date1904/WsBool` 时保留原始单元格保存值，但**未知记录可能包含实际数据**，返回 `cell_value_coverage=unverified_due_to_best_effort_salvage`、`omissions_affect_cell_values=true`，不得用于自动对账、交易或验证报表总额。未知容器、FILEPASS、宏、错误核心记录及资源越界仍拒绝，不把 `--salvage-unknown-metadata` 加到统一 `read_et.py` 或 .wps/MS-DOC/OOXML 路由；详细边界见临时 BIFF8 部分读取（`<skill-root>/references/et-salvage-best-effort.md`）。

## BIFF8 额外审计（可选，不影响文字读取）

需要核对 ET/ETT 中富字符串 FormatRun 位置、公式保存缓存风险、原始 XF 格式证据时，可以在完成上述自检后，使用**独立入口** `execute(command="python -I -S -B \"<skill-root>/scripts/wps_biff8_audit.py\" ./input.et")`。它只接受经当前读取器确认的 `biff8_readonly_v1`，默认正常 `read_et.py` 输出与 Word/MS-DOC 文字读取器均不变；新增可选的 Font 原始记录引用、CalcMode/CalcSaveRecalc/RecalcId 保存值和 XF 父样式直接来源观察，**不计算字体效果、样式级联或公式缓存的最新性**，绝不执行公式。详情、范围、退出码和上限见 BIFF8 保存证据审计（`<skill-root>/references/biff8-stored-audit.md`）。本次为合成及格式证据验证，未证明新审计结果在 WPS Office 原生富文本/公式文档上的完整语义兼容。

