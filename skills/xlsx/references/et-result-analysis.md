# Analyze an existing ET extraction result

路径占位符说明：`<skill-root>` 是 XLSX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 XLSX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

仅在消费已有 ET/ETT 原始提取 JSON 时读取本页；与 `<skill-root>/references/data-analysis.md` 的区域、类型、查询、公式策略和结果核对规则并读。普通原生 XLSX 分析不加载本页。先完成主入口自检与文件定位；使用已确认 Python 3.9+、`-I -S -B`，仅标准库、离线，输入不修改。

## Input and source diagnostics

For these stdout-only commands, use an existing, user-provided ET extraction JSON as a protected input; do not create a temporary JSON solely for this flow. The documented successful cleanup protocol requires a verified final file and an archive result, neither of which this flow produces. Do not label a successful analysis as failed to bypass that requirement, and do not delete or claim an existing JSON. If no suitable input or supported non-file handoff is available, stop and report the missing handoff.

Pass the existing original spreadsheet JSON result from the et skill to the same region-based
analysis command. Accepted typed-cell schemas are `biff8_readonly_v1` and
`ooxml_spreadsheet_readonly_v1`; successful/partial extraction retains its actual
input schema and diagnostics. Failed extraction and Word-text results are rejected.
This validates schema/budgets and restores explicitly tagged numeric values as Decimal;
text remains text, date serials remain numbers, and no XLSX file is created.

```python
execute(command="python3 -I -S -B \"<skill-root>/scripts/xlsx_analysis.py\" profile result.json --sheet Data")
execute(command="python3 -I -S -B \"<skill-root>/scripts/xlsx_analysis.py\" summarize result.json --sheet Data --query query.json")
```

Extraction diagnostics are included as `source_issues` and `source_status`. When supplied,
the original `cell_value_coverage`, `omissions_affect_cell_values` and `reading_assessment`
are preserved as `source_cell_value_coverage`, `source_omissions_affect_cell_values` and
`source_reading_assessment`. No replacement assessment schema is generated. These optional
source assertions pass identity/type and consistency checks within the existing 16 MiB JSON
and depth-64 limits; malformed or conflicting assertions fail. Older valid result JSON may
omit them, in which case no completeness claim is inferred or added. Unverified saved-value coverage
is preserved with its issues and partial source status; it must never be treated as complete. The analysis
CLI retains its existing exit 0 for a successful analysis, including a partial source: inspect `source_status`
and coverage separately. ET extraction itself returns exit 3 for partial; the codes describe different stages.

Coverage describes the original extraction's declared scope, not completeness of the selected
analysis region or freshness of formula caches. Missing caches outside the selected region
remain disclosed in the source coverage; missing/error caches inside it fail when used.
Formula cells still require explicit `--formulas cached`. Numeric strings retain exact
`Decimal` values while text identifiers remain text. This analyzes previously extracted JSON,
not WPS originals or independently validated caches. Other native loader APIs still require
XLSX/XLSM. Dates, private streams and opaque charts retain source limits.
