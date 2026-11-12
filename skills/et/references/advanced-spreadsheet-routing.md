# ET高级兼容、命名区域与审计

维护资源说明：文中 `docs/maintenance/et/` 与 `skills/et/scripts/test/` 均以宿主确认的仓库根目录为基准，仅供源码维护，不随运行包分发；运行任务不读取或执行。没有仓库时跳过维护资料，不按 Skill 安装目录拼接。

路径占位符说明：`<skill-root>` 是 ET Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 ET 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

所有公开入口（文件 API、读取 CLI、read/metadata 协议、BIFF/OOXML 分支）统一使用内容优先的容错读取，不再提供 strict/legacy 策略。旧 `--compatibility`、`--salvage-unknown-metadata`、`--chart-policy` 仅为兼容而接受：不改变行为，读取结果附加一次 `compatibility_option_ignored`、`salvage_option_ignored` 或图表忽略诊断。BIFF8 接受有明确编码编号的 CodePage 874、932、936、949、950、1200、1250、1251、1252 作为声明元数据保留，不重新编码或转换保存值；未知 CodePage、缺失 Date1904 或 WsBool 降级为 issue 并继续读取原始保存值。详细边界见非单元格拒绝分类（`<skill-root>/references/et-noncell-compatibility.md`）。

公式仅使用 `Formula` 记录中已有的保存缓存，不重算；缓存新鲜度仍未验证。

Globals 首位空负载 `WriteProtect` 和 Worksheet 首位零保留字段 `Uncalced` 均验证位置/唯一性后保留源字节并返回 partial；已识别记录的缺损和错位降级为 issue，不中止单元格读取。公式缓存新鲜度仍未验证；依据与反向安全样例见 电子表格非单元格拒绝分类（`<skill-root>/references/et-noncell-compatibility.md`）。

默认容错对有证据的非单元格元数据及受支持图表保留原始记录并报告 partial；包括结构校验后的 DXF（`0x088D`）差异格式属性，格式不应用到保存值。边界完整的未知记录及部分名称/调色板会保留为原始证据，标注覆盖不确定。加密、记录帧无法继续、无法安全建立基础分配表、ZIP/XML 安全禁止项与资源耗尽仍停止。图表始终按有界不透明字节保留，旧 `--chart-policy reject` 不再选择拒绝策略。需排查格式时按结果读 BIFF8 边界与高级策略 (`<skill-root>/references/biff8-readonly.md`) 或 OOXML 保存值 (`<skill-root>/references/ooxml-spreadsheet.md`)；编码、容量及失败定位按需读 解析增强边界 (`<skill-root>/references/et-parser-stability.md`)，容器检查另见 CFB 调查 (`<skill-root>/references/et-ett-readonly.md`)。

已在中山市交通运输局两份公开 `.et` 文件上补齐 BIFF8 `TableStyle (0x088F)` 与相邻 `TableStyleElement (0x0890)` 的有界格式证据读取：校验完整样式组及 DXF 索引后原样保留，返回 `partial`，不应用视觉样式；组不可验证时保留原始字节并报告 issue。真实文件核验分别读取 32/43 个单元格，独立 MS-XLS 记录观察器共对照 37 项保存值；详见真实 ET 实测（`docs/maintenance/et/biff8-gov-real-et-validation.md`）。两份的 CodePage 都为 1200，不证明其他 CodePage 的生产兼容性。

## BIFF8 条件格式与分页元数据（默认部分读取）

可读的 `CF` 常量比较和单独填充样式随 `uninterpreted_metadata[].structure` 返回，分别查看 `cell_value_comparison` 和 `differential_fill`。规则保持未执行、格式保持未应用。

默认容错对 `CondFmt (0x01B0)` + 声明的 1–3 个相邻 `CF (0x01B1)` 先验证完整组边界、区域和内部字节分区，再保留源字节；有限的几何/操作符问题或长度完整文本中的语义问题可作为原始证据返回 partial，不求值或应用。组成员缺失/错位、截断、分区歧义或其他结构错误降级为 issue，保留已解析单元格并继续。对已完整分帧、数量受限的分页记录，将越界/倒序/重叠布局记入 `structure.layout_issues` 与 `page_break_layout_not_applied`，继续读取保存值；不修正原值或宣称打印布局有效。实际恢复三份 ETT，详见 `docs/maintenance/et/et-real-validation.md#template-compatibility-iteration-2026-11-03`；字段与限制见 `<skill-root>/references/biff8-readonly.md#inline-text-and-passive-metadata`。

## BIFF8 批注绘图与数据验证（默认部分读取）

对已确认的 note 对象链，在实际 Escher ClientData/ClientTextbox 边界拼接 MsoDrawing，核对 Obj、TxO、文字/格式续段及 Note 的长度和链接后原样保留，继续读取单元格。确认链的批注文字、作者与单元格关联返回在 `sheets[].comments`（`kind='biff8_note'`），绘图属性内部语义和显示仍不解释；边界和未知对象不猜测。完整限制见 `<skill-root>/references/embedded-charts.md#fragmented-note-drawings-data-only`。`DVal/Dv` 另要求声明的相邻组边界和有效内部字节分区；有限的几何/操作符问题或长度完整文本中的语义问题可保留为原始证据并返回 partial。不执行公式、跟随外链、应用验证规则或显示提示；组缺失/错位、截断、分区歧义降级为 issue 并继续读取单元格。两者均保留 partial，资源耗尽仍拒绝。

## BIFF8 静态命名区域与部分内置名称（默认可读）

对 `.et/.ett` 内的 `Name (0x0018)`，仅当 Lbl 头、名称、已验证的内部 XTI、最终 `BoundSheet8` 作用域和静态公式结构均已验证时才作静态解释：普通名称支持一个绝对内部单元格 `PtgRef3d`、单个绝对 `PtgArea3d` 或同一内部表上至多两个区域的 `PtgUnion`；内置 `Print_Titles` 仅支持完整行/列区域，`_FilterDatabase` 仅支持一个静态区域，历史内置 `Print_Area` 的窄子集继续保留。正确分帧的 `SupBook/ExternSheet` 记录保留原顺序和索引；`ExternSheet` 只可使用到达声明 XTI 字节数所需的有界相邻 `Continue`，相邻 `ExternName` 与 `XCT/CRN` 组保持不透明。外部工作簿及 add-in 项目保持不透明，相关名称可作为原始证据返回 partial，覆盖不确定。损坏/截断的链接分帧或无效索引保留原始证据并附 issue，不中止单元格读取；不打开/下载外部文件、不加载 add-in。结果为来源元数据并报告 `partial`，**不计算名称公式、不应用打印/筛选设置**；单元格公式只使用 `Formula` 中已有的保存缓存。已用中山市政府官网固定哈希原件验证此前被 Name 拒绝的 1 份 ET 现在可受限读取：2 张工作表、126 个已识别单元格，并有独立 BIFF 已知记录计数 `66+60` 对照；不等于独立逐格真值或 WPS 软件兼容认证。针对 `Print_Titles` 的另一种真实 BIFF8 编码，本轮已增加严格界定的 `PtgMemFunc(cce=23)` 双区域联合、以及尾随显示用 `PtgParen`，不增加通用公式解码。原始记录验证来自独立 LibreOffice 生成的 `.xls`，**不是 WPS Office 原生 `.et`**；新增九份政府 ET 原件也未观察到该类 Name。详细范围见静态内部名称边界（`<skill-root>/references/biff8-defined-names.md`）和独立生产者记录证据（`docs/maintenance/et/biff8-print-title-real-evidence.json`）。

## BIFF8 默认容错与旧参数忽略

默认读取即为内容优先的容错策略：有界未知记录保留原始负载和位置并标记 `partial`；未知 CodePage 声明、缺少 `Date1904/WsBool` 时保留原始单元格保存值并附加 issue。已知结构化元数据（合并、公式组、条件格式/验证、HLink、Note 等）损坏只使结果 `partial` 并保留证据，不单独把 `cell_value_coverage` 降为不确定；未知记录、值承载记录跳过和未读声明表仍按值覆盖规则保守处理。旧参数 `--compatibility`、`--salvage-unknown-metadata`、`--chart-policy` 仍被接受但不改变行为，结果附加 `compatibility_option_ignored` 或 `salvage_option_ignored`，不会悄悄得到更窄结果。**未知记录可能包含实际数据**，返回 `cell_value_coverage=unverified_due_to_best_effort_salvage`、`omissions_affect_cell_values=true` 时，不得用于自动对账、交易或验证报表总额。未知容器、FILEPASS、宏执行、错误核心记录及资源越界仍拒绝或不执行；详细边界见默认容错与历史 salvage 说明（`<skill-root>/references/et-salvage-best-effort.md`）。

## BIFF8 额外审计（可选，不影响文字读取）

需要核对 ET/ETT 中富字符串 FormatRun 位置、公式保存缓存风险、原始 XF 格式证据时，可以在完成上述自检后，使用**独立入口** `execute(command="python -I -S -B \"<skill-root>/scripts/wps_biff8_audit.py\" ./input.et")`。它只接受经当前读取器确认的 `biff8_readonly_v1`，默认正常 `read_et.py` 输出与 Word/MS-DOC 文字读取器均不变；新增可选的 Font 原始记录引用、CalcMode/CalcSaveRecalc/RecalcId 保存值和 XF 父样式直接来源观察，**不计算字体效果、样式级联或公式缓存的最新性**，绝不执行公式。详情、范围、退出码和上限见 BIFF8 保存证据审计（`<skill-root>/references/biff8-stored-audit.md`）。本次为合成及格式证据验证，未证明新审计结果在 WPS Office 原生富文本/公式文档上的完整语义兼容。


## 默认读取诊断与容器恢复

- CFB 文件头声明 FAT 数量为 0 时，仅在 DIFAT 列出了 FAT 扇区且全部结构与 BIFF8 校验通过时恢复 FAT 数量并返回 partial（退出 3），保留 `cfb_zero_fat_count_recovered` 诊断；索引缺失导致分配无法建立时仍失败。不足一扇区的尾缀（全零或非零）在完整扇区前缀通过分配链及各流声明长度校验后忽略并附诊断（`cfb_zero_trailing_padding_ignored` 或尾缀保留诊断）。
- 默认可读取仅带隐藏位或非零保留位的已支持静态 `NAME`，保留原记录与 `defined_name_passive_header_fields_ignored` 诊断，继续提取保存值及公式缓存并返回 partial。其他无法解释的名称（包括带宏标志的名称）仅作为原始元数据保留，名称公式和宏均不执行。
- 默认保留外部工作簿和加载项的原始 SupBook/XTI 索引，仅将经验证的内部 XTI 用于名称解析。公式使用 Workbook 流中已有的保存缓存，外部缓存只保留为证据。
- `0x088D` 按 MS-XLS 标准 DXF 结构读取并保留属性；有界 DXF 内未知/无效格式属性保留原始记录并报告 partial，不阻断可靠单元格读取。边界不可分帧时保留原始字节并附 issue，资源超限仍按预算截断或拒绝。
- 默认另可保留有界 `CondFmt/CF` 条件格式组与异常分页布局。可读出部分常量比较和填充索引，均不应用到保存值。普通静态名称可指向一个内部绝对单元格。图表、条件格式和数据验证仅在完整组边界及必要字节分区通过校验后，才保留有界语义异常并返回 partial；组边界错误降级为 issue。
- 默认对边界完整的未知绘图原子、嵌套边界均通过校验的未知 OfficeArt 容器及简单 FtCmo/FtEnd 对象保留原始记录、类型与位置，返回 partial 并标明单元格覆盖不确定；不执行对象。已确认的批注分片链也可保留这类未知记录，并在 note_drawings 中列出；无法定界的对象保留原始字节并附 issue。
- 默认可保留受限批注绘图分片链与 `DVal/Dv` 数据验证组，返回 partial；BIFF Note 的批注文字/作者与 HLink/HLinkTooltip 元数据分别返回在 `sheets[].comments` / `sheets[].hyperlinks`，不并入单元格值、不解析目标、不执行或跟随。完整原始记录、对象链接与遗漏诊断保留；除上述有界对象外，未知关联、错误分片边界、截断及资源越界按 issue 或预算处理。其他绘图类型和完整规则语义尚未支持。
- 容器头 CLSID、minor version、保留字节、未引用分配、不可达目录项、大小写重名及非法流名等非值元数据差异降级为 issue；FAT/MiniFAT 链循环、交叉引用、越界和声明长度不符仍拒绝。

## 显示与保存视图

完整读取在工作表的 `visibility` 中附带可解析的保存行高 `height_pt` 与列宽 `width_chars`。已确定为 `General` 的 BIFF8 二进制数值可在 `numeric_interpretation.display_text` 中提供简短显示，统计继续使用保存值。

数字解释可核对 BIFF8 单元格 XF 与父样式，并识别常见科学计数、分数、带颜色的金额和带字面量单位的格式。遇到样式冲突或未支持的格式时，按 `numeric_interpretation` 的状态和原因说明，保留保存值。详见 `<skill-root>/references/numeric-interpretation.md`。

查看文字强调信息时，完整读取可提供富文本分段及保存的字体属性。可支持的 BIFF8 简单公式另返回可读表达式，单元格值仍取保存缓存。

查询冻结窗格或当前单元格时，查看完整结果的 `sheets[].views`。每个保存视图返回冻结行数 `freeze_row`、冻结列数 `freeze_col`、活动窗格 `active_pane` 和当前单元格 `selected_cell`。表头仍由任务显式指定。字段和缺失含义见 `<skill-root>/references/saved-visibility.md#保存的工作表视图`。
