# 二进制表格的行与单元格证据

先完成 [环境自检](../SKILL.md#执行环境约束与首步自检)。默认读取 MS-DOC 正文或显式选择的附加区域时，出现 U+0007 会附加 `binary_table_structure`；正文及段落的旧有 TAB 输出保持不变，但结果标为 partial。普通 TAB 不触发结构推断。

## 支持范围

`schema_version=ms_doc_table_rows_v1` 的 `rows` 是独立观察到的行，不是完整表格对象。每行保留 `cells`、正文 CP 半开范围、行结束符 CP 与 PAPX 来源。每个 cell 有从零开始的行内 `index`、文字、CP 范围与属性来源；同一单元格的多段文字用换行连接，空单元格保留。CP 按 UTF-16 码元计算，不按 Python 字符索引计算。

只接受已校验 BTE/PAPX FKP 中显式 `sprmPFInTable=1`、`sprmPItap=1`、`sprmPFTtp=0/1` 的一级段落/单元格/行界。缺少属性、嵌套层级、非零片段 PRM、字段内容或未知段落操作不猜测。未知段落操作的剩余字节不会被扫描为表格标志。已知 TDefTable 保留初始列数和 twips 边界；其他表格操作只按有界操作数跳过并计数，不应用其含义。

`initial_definition` 只描述保存的初始定义，不能据此推断最终列网格。`table_grouping`、`grid_and_merges` 和每行 `merges` 均为 unknown；不把相邻行自动拼成同一表格，不推断跨行/跨列合并、布局、样式、修订后的结构或显示顺序。未读取全部属性时也不声称源文件结构合法。

## 缺口和失败

- 有观察行时 `status=rows_partial`；无可靠行时为 `unresolved`。`unresolved_markers` 给出未确认 U+0007 的 CP，不能视为完整遗漏清单。
- 属性缺失或不支持时保留正文，并附加 `binary_table_structure_incomplete`；已知元数据损坏时另有 `binary_table_metadata_invalid`，不把失败元数据生成的行交付。
- 输出包含的行仍可能只是文件中可确认的子集。摘要中的 `binary_tables=not_decoded` 表示完整结构未解码，并不表示 `rows` 必为空。
- BTE/FKP/属性指针、长度、排序和空间归属经过边界检查；格式化页不得覆盖正文、FIB、已解码节结构或已解码字符属性页。预算超限明确失败，不伪装成完整提取。
- 每个故事最多 100000 个标记、4096 个 FKP 页、100000 个单元格、1 MiB 属性读取量；每行最多 63 个单元格。仍受统一输入、正文与 JSON 输出预算限制。

## 依据与验收边界

依据微软 MS-DOC 的表格概述、PapxFkp、PapxInFkp、直接段落格式及 TDefTableOperand 实现有限读取；定位与使用范围见[格式依据](format-evidence.json)。这些是开发材料，运行时不联网。

当前验证为独立字面量构造的合成 PAPX/FKP 样例，包括中文、压缩文本、代理对、空单元格、多段文字、初始列边界、属性缺失/未知、嵌套深度、PRM、损坏和预算。尚无真实 WPS 二进制表格与独立内容/结构预期的验收，不能据此称完整表格兼容。
