# Read and analyze with the standard library

不支持读取 WPS 格式；`.wps` 使用 wps skill，`.et/.ett` 使用 et skill；本入口明确拒绝，不自动调用或转换。

Follow the path and first-operation rules (`<skill-root>/SKILL.md#执行环境约束与首步自检`)
before using this reference; internal resources and task data have different path bases.

Route `.wps` to the `wps` skill and `.et`/`.ett` to the `et` skill. Native spreadsheet commands
refuse `.wps`, `.et`, `.ett` and CFB data; no automatic conversion is performed.

Use `execute(command="\"<skill-root>/scripts/.venv/bin/python\" -I -S -B \"<skill-root>/scripts/stdlib_xlsx.py\" read ./input.xlsx")` as described in
the main entry. Ordinary XLSX/XLSM reads are self-contained in the main entry; load this reference only for CSV/TSV, identification or additional type details. Read-only inspection does not require the write/formula contract. The JSON lists sheets and coordinate-keyed
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

## Identify contents, including ET/ETT candidates

`<skill-root>/scripts/xlsx_identify.py` inspects native OOXML inputs. WPS family suffixes and CFB signatures
return `unsupported`: `.wps` uses `recommended_skill: wps`, `.et/.ett` uses `et`, and a CFB signature
with another suffix uses `wps_or_et` pending content identification. These values replace the older generic
`wps` marker; consumers must handle all three. The inspection does not validate the binary container or
extract cells. Locate the selected Skill independently; never rename input to bypass routing.
