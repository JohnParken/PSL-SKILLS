# 二进制表格的行与单元格证据

维护资源说明：文中 `docs/maintenance/wps/` 与 `skills/wps/scripts/test/` 均以宿主确认的仓库根目录为基准，仅供源码维护，不随运行包分发；运行任务不读取或执行。没有仓库时跳过维护资料，不按 Skill 安装目录拼接。

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

先完成 环境自检 (`<skill-root>/SKILL.md#执行环境约束与首步自检`)。默认读取 MS-DOC 正文或显式选择的附加区域时，出现 U+0007 会附加 `binary_table_structure`；正文及段落的旧有 TAB 输出保持不变，但结果标为 partial。普通 TAB 不触发结构推断。

## 支持范围

`schema_version=ms_doc_table_rows_v1` 的 `rows` 保留存储中的物理行及 `cells`、正文 CP 半开范围、行结束符 CP 与 PAPX 来源。每个 cell 有从零开始的行内 `index`、文字、CP 范围与属性来源。同一单元格的多段文字用换行连接，空单元格保留。CP 按 UTF-16 码元计算。

默认模式只接受已校验 BTE/PAPX FKP 中显式 `sprmPFInTable=1`、`sprmPItap=1` 的一级段落或单元格边界。行终止必须有显式 `sprmPFTtp=1`，未写 PFTtp 的普通标记按规范默认 false。已知 TDefTable 保留初始列数和 twips 边界。未知段落操作在原位置停止解析。

`initial_definition` 描述保存的初始定义。默认模式的 `table_grouping`、`grid_and_merges` 和每行 `merges` 为 `unknown`。最大模式的表级结果见下文。

## 最大化读取的表格与段落证据

在 `--reading-mode maximum` 中，按存储 CP 检查每个候选段落。继续读取与字段及未解释的片段 PRM 无关的行。可越过保持表格结构属性的 `sprmPIstd` 及已知固定长度的编号、文字方向、网格和东亚排版操作，并按顺序解释 `sprmPDtap` 的相对深度。行仍须有一级表格及完整边界的直接证据。保留物理单元格、空单元格和原始文字。

- `contiguous_row_runs` 用零基 `first_row_index`、`row_count` 和半开 `cp_start/cp_end` 索引 `rows`。相邻行段仅依据 CP 连续性。需要表格分组时查看 `tables`。
- 每行 `stored_cell_layout` 保留可核实的 `cell_edges_twips`、行内 `horizontal_merges` 和 `vertical_merge_roles`。在可靠的 `TDefTable` 后按序处理受支持的 `sprmTMerge`、`sprmTSplit` 和 `sprmTVertMerge`。边框、底纹、首选宽度等已知外围属性可与这些操作共存。单元格增删、未核实的几何改写、同一行内定义之后的样式重置或不一致的合并标志会使该行的存储布局为 `null`。
- `unresolved_markers` 最多保留 1000 个位置及原因，使用 `unresolved_marker_count` 和 `unresolved_markers_omitted` 查看统计。字段相交、PAPX 证据不足和不完整行边界可单独定位。
- `binary_paragraph_layout.paragraphs` 用 `mark_cp` 指向原存储段落或单元格结束符。按出现顺序返回识别到的 PAPX 操作，带原始值、位置和可解读值，包括逻辑或物理对齐、保留段落或下段、孤行控制、缩进、段前段后间距。无语义的已识别操作保留 `raw_only`，越界操作数标 `invalid_operand`。普通段落也可获得这些证据。每个故事最多返回 1000 条操作，`modifiers_omitted` 报告超出上限的已观察操作。字段重叠段落及含未解释 PRM 的段落不输出关联证据。直接操作可能被样式或后续操作覆盖，有效布局保持 unknown。

## 表格分组与合并

最大模式在 `binary_table_structure.tables` 返回按故事顺序排列的一级表格或可确认行片段。`table_index` 和 `first_row_index` 从零开始，`row_count` 是已恢复的行数。`source.cp_start/cp_end` 是原故事的半开 CP 范围。按行索引从 `rows` 取原始单元格文字。

- `grouping_status`、`start_boundary` 和 `end_boundary` 报告分组及其依据。分界取故事端点、已证实的表外段落或足够的直接行属性/段落框架差异。相邻行比较保存的表格样式编号、分组编号、方向、表格定位属性及适用的首单元格段落框架。存在未确认的行、书签分界、未知表格属性变化或无法比较的段落框架时返回 `partial` 行片段。
- `column_edges_twips` 是由严格递增、外端点一致的行边界组成的存储坐标网格。每表上限 256 列。网格缺失时返回 `null`，仍可保留已确认的合并。列跨距和页面显示取决于其他布局属性。
- `horizontal_merges` 每项报告表内零基 `row_index`、`first_cell`、`cell_count` 及边界。以明确的 TC80 标志或合并操作为依据。物理单元格及其各自文字均保留，读取合并内容时以起始单元格为主。
- `vertical_merges` 每项报告起始 `row_index`、`row_count`、边界及 `cells`。`cells` 按连续物理行列出每行的 `first_cell` 和 `cell_count`。延续单元格须没有文字，且两侧存储边界完全一致。遇到范围不明的中间行或表尾时，已配对前缀带 `status=partial`。
- `merge_status` 取 `stored`、`partial` 或 `unknown`。`unresolved_merge_count` 统计问题，`merge_issues` 每表保留前 100 项，`merge_issues_omitted` 报告其余项。顶层 `table_grouping` 和 `grid_and_merges` 概括当前故事。`stored` 表示声明范围内的存储结构证据。嵌套表格、样式级联、修订视图和客户端版式仍待核对，因此文档继续返回 `partial`。

开发依据为 MS-DOC 的 Overview of Tables、Direct Paragraph Formatting、Table Properties、UpxTapx 和 VerticalMergeFlag。实现与证据见仓库维护记录。

## 缺口和失败

- 有观察行时 `status=rows_partial`；无可靠行时为 `unresolved`。`unresolved_markers` 给出未确认 U+0007 的 CP，不能视为完整遗漏清单。
- 属性缺失或不支持时保留正文，并附加 `binary_table_structure_incomplete`；已知元数据损坏时另有 `binary_table_metadata_invalid`，不把失败元数据生成的行交付。
- 输出包含的行仍可能只是文件中可确认的子集。最大模式恢复行时，摘要的 `binary_tables.state=partially_extracted`。完整结构及遗漏以行记录和诊断为准。
- BTE/FKP/属性指针、长度、排序和空间归属经过边界检查；格式化页不得覆盖正文、FIB、已解码节结构或已解码字符属性页。预算超限明确失败，不伪装成完整提取。
- 每个故事最多 100000 个标记、4096 个 FKP 页、100000 个单元格、1 MiB 属性读取量；每行最多 63 个单元格。仍受统一输入、正文与 JSON 输出预算限制。

## 依据与验收边界

依据微软 MS-DOC 的表格概述、PapxFkp、PapxInFkp、直接段落格式及 TDefTableOperand 实现有限读取；定位与使用范围见格式依据 (`docs/maintenance/wps/format-evidence.json`)。这些是开发材料，运行时不联网。

`initial_definition.initial_cell_properties` 逐项保留完整 TC80 的原始 TCGRF、初始横向/纵向合并位及原始首选宽度，另计不完整记录；依据 MS-DOC TC80 与 TCGRF（https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-doc/9dd62a79-8c0b-4b11-99ee-05742ae7cf6d 、https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-doc/11bf5b1c-943f-421d-bbf3-39088cd1b8dd）。这是表格定义的初始状态；后续修饰符未应用，不能当最终合并范围或渲染宽度，结果仍为 partial。

每行 `uninterpreted_table_modifier_ids` 汇总已定界但未应用的表格修饰符编号及次数，最多 64 类；超出的记录数由 `uninterpreted_table_modifier_ids_omitted` 标注。编号不表示操作数的语义已解析或实际视觉样式已恢复。

`local_width_operands` 最多保留每行 128 个已定界的 `0xD635` 首选单元格宽度及 `0xD634` 默认内边距操作数，含源流偏移和原始十六进制；格式与取值符合对应子集时给出单元格范围、单位、宽度及边掩码，否则标记 `invalid_or_unsupported`，不因非关键格式语义无效丢弃正文。超出上限由 `local_width_operands_omitted` 计数。局部操作数未经样式级联、后续覆盖及实际布局计算，不代表最终宽度；原 partial 不变。依据 MS-DOC TableCellWidthOperand（https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-doc/e0d4c0db-623d-4a6f-94d2-48e05f34f564）、CSSAOperand（https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-doc/fd43c8b3-8d6e-484a-8bc5-efebf377f422）。

当前验证为独立字面量构造的合成 PAPX/FKP 样例，包括中文、压缩文本、代理对、空单元格、多段文字、初始列边界、属性缺失/未知、嵌套深度、PRM、损坏和预算。另有两份工行官网原件的有限核验：价格表恢复9行（单元格数为10、10、10、10、10、4、5、5、5），大连采购表恢复2行、每行5个单元格；独立struct实现的BTE/FKP/PRL与FIB/CLX步行结果一致（仅共享CFB分配层），正文及原件哈希不变。价格表的5处HugePapx已按有界PrcData读取；不证明WPS生产者、完整表格、合并或客户端视觉效果。macOS/Python3.13验证，Linux/Windows未测。

## 固定段落属性与保守停止

MS-DOC 2.6.2（开发来源URL：`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-doc/484822ee-a9d9-4af4-8423-29fda67a6a58`）及Sprm长度定义（开发来源URL：`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-doc/099eb99c-a927-4caf-a80c-66254ea83d6a`）为开发依据。仅明确白名单中的固定长度段落排版属性可越过，不应用其版式语义；长度不够仍报告元数据缺口，不扫描操作数字节寻找表格标志。

| SPRM | 固定操作数 | 本轮处理 |
|---|---|---|
| 0x2431 WidowControl | 1字节 | 保留原始操作数及来源，忽略渲染 |
| 0x8418 DxaAbs | 2字节 | 同上，不据此推断视觉位置 |
| 0x6412 DyaLine | 4字节LSPD | 同上，不推断行高 |
| 0x2461 Jc | 1字节 | 同上，不渲染对齐 |
| 0x4457 DxcLeft1 | 2字节 | 同上，不解释字符单位缩进 |
| 0x6646 HugePapx | 4字节Data流引用 | 有界读取PrcData；不跳过引用语义，不继续解释原数组后缀 |

同一规范已明确的保留/间距/缩进/框架位置等固定属性也作白名单处理，清单以 `<skill-root>/scripts/wps_binary_tables.py` 的LAYOUT_PROPERTIES为准。`layout_properties_not_rendered` 和 `unresolved_properties` 各最多100条，分别附总计数及truncated计数。没有按此诊断绘制样式的能力；rows仍始终为partial范围。

## HugePapx与外部PrcData

开发依据另见 MS-DOC 2.9.210（`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-doc/473fd992-c824-4655-8880-3186bd432f80`），运行时不联网。PrcData的2字节有符号长度必须处于10至0x3FA2，且全部属性字节位于Data流内。HugePapx若非数组首项按规范忽略；位于内联GrpPrlAndIstd时要求istd=0且只有此PRL。处理外部数组后不再处理原数组后缀。支持HugePapx/PTableProps的有界交接链，每链最多32个不同引用、每次表格检查最多4096次引用，累计仍受1MiB属性预算约束。循环、缺Data流、指针/长度越界、部分重叠数组和破损PRL不生成猜测行，保留正文及metadata_invalid原因；预算超限明确失败。

`external_property_references` 最多 100 条，附总计数和截断计数。行和单元格保留 FKP 来源及外部属性来源。可重复引用同一完整数组，交叉覆盖不同数组时拒绝合并证据。最大模式可在这些已验证的一级行上恢复存储分组和合并，完整样式和修订视图仍待核对。
