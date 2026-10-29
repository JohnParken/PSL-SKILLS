# Export analysis results to a readable XLSX

路径占位符说明：`<skill-root>` 是 XLSX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 XLSX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

Follow the path and first-operation rules (`<skill-root>/SKILL.md#执行环境约束与首步自检`)
before using this reference; internal resources and task data have different path bases.

Run the entrypoint self-check (`<skill-root>/SKILL.md#执行环境约束与首步自检`), requiring exit 0 and
`status: ready`. Confirm task directory, readable result input and output write probe.
Resolve `<skill-root>` from the actual SKILL.md path; replace it in commands and use
relative task data paths. Python 3.9+, standard library only; no upload or application use.

```console
(
set -eC  # stop on failure; refuse to overwrite existing report files
python3 -I -S -B "<skill-root>/scripts/xlsx_rows.py" source.xlsx --sheet Sales --query rows.json > selected.json
python3 -I -S -B "<skill-root>/scripts/xlsx_result_export.py" selected.json readable.xlsx
)
```

The accepted native statuses are selected, summarized, described and management_wide
with schema_version=management_wide_v1, plus daily_summary/daily_summary_v1.
Daily summaries use Daily instead of Wide and retain the same audit sheets. Ranking/share
results reuse selected typed-table schema. Compared/profiled/matched results and arbitrary
objects are currently rejected. Required source sheet/range, table/group/metric shapes,
typed cells and row counts are checked. The tool does not authenticate a JSON report or
recompute its business calculations. Keep the original analysis run and source for audit.

- selected: unique column IDs, optional equally sized typed headers, output_rows, and rows
  containing positive source_row plus an equally sized typed cells array.
- summarized: group_by, measure and groups with ordered typed keys, observation counts,
  and known numeric summary fields. Group membership row IDs are not available natively.
- described: known column statistics with value/rounded objects, requested quantiles,
  and sample/population variance status/ddof records.

Ordinary reports have Results, Columns, Metadata and Warnings sheets. Management reports
use Wide and additionally include Definitions, Validation and Lineage; see
Configured bank management long-to-wide reports (first release) (`<skill-root>/references/management-wide.md`). Results is a data-only
report, not a reconstruction preserving source-file layout. Joined path labels are for
human display; original column IDs, complete structured paths/merge levels and complete
source JSON remain in Columns/Metadata. Generated columns have explicit calc: IDs.

Metadata stores the source JSON in ordered source_json chunks, each at most 30,000
characters. Concatenate its C-column values in numeric Part order to recover the source
JSON. This retains supplied policies, query/filter configuration, rounding metadata,
cache warnings and source references. Current analysis commands include requested_query;
older JSON lacking it produces a warning rather than a guessed query. Aggregated output
has source sheet/range/count but may lack contributing row IDs; this limitation is explicit.
No aggregate lineage is invented. Selected results retain their actual source row numbers.

Only typed numeric cells and schema-defined statistic fields are interpreted as numbers.
Numeric strings elsewhere, long identifiers and leading zeros remain text. Up to 15
significant decimal digits within the supported numeric exponent range (-307..307) are
stored as numbers; longer/out-of-range numeric values are stored as exact text and flagged.
Trailing insignificant zeros do not count toward that threshold. Excel may use binary
floating point or different display formatting even for <=15-digit numbers. Use source
JSON as the computational record; precise numeric text in Results will not behave as a
numeric cell in ordinary spreadsheet formulas. No automatic rounding-to-15-digits occurs.

Boolean cells remain boolean; date_text becomes literal ISO text without calendar/date
format inference. All cells are created using value, never formula. Text starting =, +,
- or @ stays literal inline text; it is not reinterpreted as an executable expression.
Embedded metadata/query text is also literal. Invalid XML text/overlong cell text fails,
with no silent truncation. Native caches remain unverified and are not recalculated.

Readable appearance is on by default: pale-blue headers, dark text, alternating rows and
frozen headers. --plain disables it. See Readable appearance for newly created workbooks (`<skill-root>/references/readable-appearance.md`).
The input JSON is bounded to 16 MiB; all sheets together are limited to 200,000 cells,
including lineage/warnings and sparse blanks. Existing outputs are never overwritten.
Exit 0/status exported means a new file was written; exit 1/status failed means rejected
schema/data/I/O. Reopen with the `read` subcommand of `<skill-root>/scripts/stdlib_xlsx.py` to check
values and representative paths. Use the full invocation in
Commands (`<skill-root>/references/python-calculation.md#commands`).
This XML/read-back verification is not real Excel/WPS visual/application acceptance.
