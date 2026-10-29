# Edit existing cells

路径占位符说明：`<skill-root>` 是 XLSX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 XLSX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

Follow the path and first-operation rules (`<skill-root>/SKILL.md#执行环境约束与首步自检`)
before using this reference; internal resources and task data have different path bases.

Use `read` to inspect the actual sheet names, addresses, formulas, raw values and style
indices. Follow Standard-library calculation and file contract (`<skill-root>/references/python-calculation.md`) to build a JSON patch and
run `python3 -I -S -B "<skill-root>/scripts/stdlib_xlsx.py" edit ./input.xlsx ./patch.json ./output.xlsx`. The output must have a new filename.

Only list intended cells. Existing styles remain when style is omitted. Writing null
clears a cell's value/formula but retains its style. Write a merged region's top-left
anchor only. A patch cannot add/rename sheets or insert/delete/move rows or columns.
Sheets containing shared/array/specially attributed formulas cannot be edited by this API.

Changing input values can invalidate formula caches on other sheets. The tool calculates
all formula cells using the supported subset, clears unresolved caches, and reports issues.
Known errors or cycles block output. Do not suppress a business error with arbitrary zeros.
The original file is never overwritten.

Untouched ZIP members retain their content bytes. Changed worksheet XML is reserialized;
this is not a promise of identical ZIP bytes or complete Excel feature compatibility.
Calculation-chain parts may be removed. Macro, chart, image and pivot parts are retained,
not executed or refreshed. Preserved chart/pivot caches can be stale after an input edit.
Signed/encrypted workbooks and non-worksheet sheet types are outside the workflow.

Do not use the legacy unpack/shift/insert scripts for this mode. The
`<skill-root>/scripts/xlsx_insert_row.py` and `<skill-root>/scripts/xlsx_shift_rows.py` command-line entrypoints are disabled and
exit 1 before writing. A known formula-reference corruption in those legacy scripts
remains unresolved; disabling them does not repair workbooks they may have changed.
Structural row edits are not supported by this API.
Verify output sheet names, unrelated data, changed cell values and recalculated results;
see Validate a standard-library output (`<skill-root>/references/validate.md`).
