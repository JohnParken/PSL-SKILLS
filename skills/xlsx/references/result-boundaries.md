# 按任务解释结果与保持内容

路径占位符说明：`<skill-root>` 是 XLSX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 XLSX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## Interpret results and keep boundaries

- 新建 XLSX 的 XML 部件统一使用双引号声明 `<?xml version="1.0" encoding="UTF-8"?>`，主元素使用对应的默认命名空间，工作表关系属性使用 `r:id`，不生成自动编号的 `ns0/ns1` 前缀。此为生成输出约定，不将其他合法前缀、单引号或编码名大小写作为输入非法依据。已有文件编辑继续保护原命名空间和未修改部件，不为格式统一重写整个包。
- Inspect structure, cell types, missing values and user-selected ranges before analysis. Numeric strings with type tags are not text identifiers; use `Decimal` for known business rules and explicit rounding. Old formula caches are unverified; missing caches are never zero.
- WPS JSON analysis accepts `biff8_readonly_v1` and `ooxml_spreadsheet_readonly_v1`, preserves exact values, source status/issues and optional source coverage/assessment. The source assessment describes extraction scope, not completeness of the selected analysis or cache freshness. See the source-result contract (`<skill-root>/references/data-analysis.md#analyze-a-wps-result-without-conversion`).
- Calculation is a Decimal formula subset, not Excel-equivalent recalculation. Editable reports use supported formulas; never accept caller-supplied caches. `create --values-only` evaluates everything first and refuses partial output; disclose that static values will not update.
- Existing-file calculations remove unresolved stale caches and report incomplete/exit 2 as `部分公式未计算`. Known errors or cycles block output. Do not hide errors with zeros or change business rules to force success; see result codes (`<skill-root>/references/python-calculation.md#results-and-exit-codes`).
- Never rebuild an existing workbook from extracted values. Edit only values, formulas and existing style indices; no row/column/sheet insertion, deletion or movement. Shared/array/specially attributed formula sheets are refused for edits. Untouched ZIP member content is preserved; changed XML is reserialized, formula caches and calculation-chain metadata may change. No full-file identity or arbitrary compatibility guarantee.
- XLSM macros are retained, never executed; chart/pivot caches are retained, never refreshed. Dates remain stored serials or ISO text. Signed/encrypted files, chart sheets, strict OOXML and native XLS/XLSB are unsupported. The et skill's documented read-only record subset does not enable generic binary spreadsheet editing.
- Do not run legacy utilities or install their dependencies as fallbacks. Legacy limitations and disabled row-edit commands are documented under maintenance/legacy boundaries (`<skill-root>/references/validate.md#legacy-utilities`). Development tests and intermediate-file cleanup are also conditional sections there, not extra steps for a simple read.

