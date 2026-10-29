# Read and analyze with the standard library

路径占位符说明：`<skill-root>` 是 XLSX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 XLSX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

不支持读取 WPS 格式（.wps/.et/.ett），请使用 wps skill；本入口明确拒绝，不自动调用或转换。

Follow the path and first-operation rules (`<skill-root>/SKILL.md#执行环境约束与首步自检`)
before using this reference; internal resources and task data have different path bases.

WPS family files are read by the separate `wps` skill. Native spreadsheet commands
refuse `.wps`, `.et`, `.ett` and CFB data; no automatic conversion is performed.

Use `python3 -I -S -B "<skill-root>/scripts/stdlib_xlsx.py" read ./input.xlsx` as described in
Standard-library calculation and file contract (`<skill-root>/references/python-calculation.md`). The JSON lists sheets and coordinate-keyed
cells containing type, raw value, style index, formula, formula attributes and cache status.
Decimal values serialize as strings in the report to avoid binary-float precision loss.
The read command does not calculate formulas or modify files. Existing formula caches
are unverified; a missing cache is not zero. Dates remain serial numbers or ISO strings,
with the workbook date system reported separately. Display formatting is not rendered.

CSV/TSV reads return text rows, including headers and empty fields. Explicitly choose an
encoding (`--encoding gbk` where appropriate); there is no silent encoding fallback.
Preserve leading zeros and convert only known numeric columns to Decimal. Strings that
begin with `=` are plain data unless deliberately supplied as formula in a write spec.
CSV/TSV input is bounded to 16 MiB, 200,000 rows, 200,000 total fields and 131,072
characters per field. Limit and parse errors return JSON diagnostics with a nonzero exit.

For custom analyses use `csv`, `collections`, `statistics`, `decimal` and Python loops.
Compute sums and means from the actual selected data, not reconstructed approximations.
Make missing-data and rounding policies explicit. Do not mix old formula caches with
freshly computed values without disclosing their status. If fresh results are needed,
run `calculate` into a separate workbook and inspect its report before using them.

The old `<skill-root>/scripts/xlsx_reader.py`/pandas pipeline is legacy and is not used by this workflow.
