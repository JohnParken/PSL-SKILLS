# Analyze an existing ET extraction result

仅在消费已有 ET/ETT 原始提取 JSON 时读取本页；与 `<skill-root>/references/data-analysis.md` 的区域、类型、查询、公式策略和结果核对规则并读。普通原生 XLSX 分析不加载本页。先完成主入口自检与文件定位；使用已确认 Python 3.9+、`-I -S -B`，仅标准库、离线，输入不修改。

## Input and source diagnostics

For these stdout-only commands, use an existing, user-provided ET extraction JSON as a protected input; do not create a temporary JSON solely for this flow. The documented successful cleanup protocol requires a verified final file and an archive result, neither of which this flow produces. Do not label a successful analysis as failed to bypass that requirement, and do not delete or claim an existing JSON. If no suitable input or supported non-file handoff is available, stop and report the missing handoff.

Pass the existing original spreadsheet JSON result from the et skill to the same region-based
analysis command. Accepted typed-cell schemas are `biff5_readonly_v1`, `biff8_readonly_v1` and
`ooxml_spreadsheet_readonly_v1`; successful/partial extraction retains its actual
input schema and diagnostics. Failed extraction and Word-text results are rejected.
This validates schema/budgets and restores explicitly tagged numeric values as Decimal;
text remains text, date serials remain numbers, and no XLSX file is created.

```python
execute(command="\"<skill-root>/scripts/.venv/bin/python\" -I -S -B \"<skill-root>/scripts/xlsx_analysis.py\" profile result.json --sheet Data")
execute(command="\"<skill-root>/scripts/.venv/bin/python\" -I -S -B \"<skill-root>/scripts/xlsx_analysis.py\" summarize result.json --sheet Data --query query.json")
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

BIFF5/7 JSON retains its distinct schema and the legacy 16384-row/256-column grid. Unknown date systems remain rejected; old formatting/date interpretations are not invented. All existing numeric precision, source coverage, partial and saved-cache gates remain unchanged. Public extract capabilities advertise accepted schemas in `extraction_schemas`; this is JSON consumption, not direct XLS/ET binary parsing.
