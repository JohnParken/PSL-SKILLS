# Read-only data analysis and file identification

路径占位符说明：`<skill-root>` 是 XLSX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 XLSX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

不支持读取 WPS 格式（.wps/.et/.ett），请使用 wps skill；本入口明确拒绝，不自动调用或转换。

Follow the path and first-operation rules (`<skill-root>/SKILL.md#执行环境约束与首步自检`)
before using this reference; internal resources and task data have different path bases.

## Contents

- Specialized task directory (`<skill-root>/references/data-analysis.md#specialized-task-directory`)
- Profile a sheet or region (`<skill-root>/references/data-analysis.md#profile-a-sheet-or-region`)
- Filter and summarize (`<skill-root>/references/data-analysis.md#filter-and-summarize`)
- Identify contents, including ET/ETT candidates (`<skill-root>/references/data-analysis.md#identify-contents-including-etett-candidates`)
- Analyze a WPS result without conversion (`<skill-root>/references/data-analysis.md#analyze-a-wps-result-without-conversion`)

Resolve `<skill-root>` from the actual SKILL.md path before running commands. Run the
entrypoint self-check (`<skill-root>/SKILL.md#执行环境约束与首步自检`) first, require exit 0 and
`status: ready`, then confirm input readability. Use Python 3.9+ with `-I -S -B`;
no third-party packages, Office application, network or output-file permission is needed.
Run from the confirmed task directory with task-relative inputs. Commands emit JSON to
stdout and never modify inputs. Redirect stdout only to a new task report if needed.

## Profile a sheet or region

```console
python3 -I -S -B "<skill-root>/scripts/xlsx_analysis.py" profile input.xlsx --sheet Sales
python3 -I -S -B "<skill-root>/scripts/xlsx_analysis.py" profile input.xlsx --sheet Sales --range B3:F100 --no-header
```

By default the first selected row supplies headers and is excluded from statistics.
Column letters remain absolute coordinates. For explicit multi-level rows and path/ID
selectors, use Explicit multi-level headers (`<skill-root>/references/multi-level-headers.md`).
Duplicate/empty header labels do not affect selection. Without a range, use A1 through
the maximum stored row/column. Explicit regions include sparse blank cells.

Reports include column types, missing cells, empty text, blank totals, distinct and
repeated nonblank values, duplicate rows, numeric count/sum/min/max/mean. Duplicate
counts exclude the first occurrence. Whole-row duplicates include blank rows. Whitespace
is text, not blank. Missing cells and empty strings are counted separately. Grouping and
duplicates distinguish text, number, boolean, blank and ISO date text; number 1 and 1.0
are equal, but true and text "1" are distinct. Text comparison is case-sensitive.

Numeric statistics use stored Decimal numbers only. Text "12" and booleans are never
coerced. Date serials remain numeric; number formats are not used to infer dates. Cells
stored as ISO dates have type `date_text`, without calendar conversion or validation.
JSON Decimal values are strings with accompanying type metadata. Sums are exact within
1000-digit arithmetic; exceeding that limit fails. Means use 34 significant digits and
ROUND_HALF_EVEN with `mean_rounded` reporting inexact division. Empty numeric sets return
null statistics, not zero. Hidden/filtered rows are included; merges are not expanded.

## Filter and summarize

Create task-local `query.json`:

```json
{"group_by":["A"],"measure":"C","mixed":"reject",
 "filters":[{"column":"C","op":"ge","value":10}]}
```

```console
python3 -I -S -B "<skill-root>/scripts/xlsx_analysis.py" summarize input.xlsx --sheet Sales --query query.json
```

`group_by` is a list of unique column selectors inside the selected range; omit it for
one aggregate group. Omit `measure` for row counts only. One numeric measure is supported.
`filters` are combined with AND; eq/ne use typed equality, gt/ge/lt/le require JSON
numeric thresholds and select numeric cells only. No string-to-number conversion, expression evaluation, OR expressions or date comparisons
occur. Header path lookup requires explicit header rows; plain label guessing is not used.
Unknown keys and duplicate JSON keys fail. Null filters match missing cells; an empty
string matches empty text. ISO date cells do not match ordinary text filters.

A nonblank, nonnumeric measure fails with its cell address by default. Choose
`"mixed":"ignore"` explicitly to omit it from numeric statistics; reports count omitted
and blank measures per group. Groups appear in first-seen order with typed keys. Blank
and empty-text keys form separate groups. Reports show selected/excluded row counts;
no matches produce an empty group list. Header-only input also yields no groups.

Both analysis commands reject formulas by default, including formulas in header cells.
`--formulas cached` explicitly uses existing caches and reports their number as
`unverified_formula_caches`; missing caches and Excel error cells fail. No recalculation
is performed, and cache freshness is never assumed. These checks cover the selected
rectangle before filtering, so a filter cannot silently hide erroneous inputs.
Limits: 200,000 selected cells including blanks, 10,000 groups, query file 64 KiB,
and the underlying workbook package limits. This is bounded in-memory analysis.
Exit 0 indicates completed analysis; exit 1 returns JSON `status: failed`.

## Identify contents, including ET/ETT candidates

`<skill-root>/scripts/xlsx_identify.py` inspects native OOXML inputs. WPS family suffixes and CFB signatures
return `unsupported` with `recommended_skill: wps`; this does not validate the binary container or extract cells.
Use the separately installed wps skill for those inputs. Never rename input to bypass routing.

## Analyze a WPS result without conversion

Save the JSON result from the wps skill, then pass that `.json` to the same region-based
analysis command. Accepted typed-cell schemas are `biff8_readonly_v1` and
`ooxml_spreadsheet_readonly_v1`; successful/partial extraction retains its actual
input schema and diagnostics. Failed extraction and Word-text results are rejected.
This validates schema/budgets and restores explicitly tagged numeric values as Decimal;
text remains text, date serials remain numbers, and no XLSX file is created.

```console
python3 -I -S -B "<skill-root>/scripts/xlsx_analysis.py" profile result.json --sheet Data
python3 -I -S -B "<skill-root>/scripts/xlsx_analysis.py" summarize result.json --sheet Data --query query.json
```

Extraction diagnostics are included as `source_issues` and `source_status`. When supplied,
the original `cell_value_coverage`, `omissions_affect_cell_values` and `reading_assessment`
are preserved as `source_cell_value_coverage`, `source_omissions_affect_cell_values` and
`source_reading_assessment`. No replacement assessment schema is generated. These optional
source assertions pass identity/type and consistency checks within the existing 16 MiB JSON
and depth-64 limits; malformed or conflicting assertions fail. Older valid result JSON may
omit them, in which case no completeness claim is inferred or added.

Coverage describes the original extraction's declared scope, not completeness of the selected
analysis region or freshness of formula caches. Missing caches outside the selected region
remain disclosed in the source coverage; missing/error caches inside it fail when used.
Formula cells still require explicit `--formulas cached`. Numeric strings retain exact
`Decimal` values while text identifiers remain text. This analyzes previously extracted JSON,
not WPS originals or independently validated caches. Other native loader APIs still require
XLSX/XLSM. Dates, private streams and opaque charts retain source limits.

## Specialized task directory

Read the selected reference before invoking its script. Paths use the confirmed `<skill-root>`; inputs and outputs remain in the task directory.

| Task | Action | Reference |
|---|---|---|
| Summarize explicit deposit maturity buckets and cumulative windows | `<skill-root>/scripts/xlsx_maturity.py` | Explicit deposit maturity distribution (`<skill-root>/references/maturity-distribution.md`) |
| Aggregate observed customers, product coverage and two-period sets | `<skill-root>/scripts/xlsx_customers.py` | Observed customer aggregation and product structure (`<skill-root>/references/customer-structure.md`) |
| Validate management policy, explicit plan deviation and comparable peers | `<skill-root>/scripts/xlsx_management_policy.py` | Explicit management statistics policy (`<skill-root>/references/management-policy.md`) |
| Analyze deposit snapshot changes, daily/end differences and explicit targets | `<skill-root>/scripts/xlsx_deposits.py` | Deposit observations, daily means and concentration (`<skill-root>/references/deposit-analysis.md`) |
| Calculate explicit deposit category shares and Top N concentration | `<skill-root>/scripts/xlsx_deposit_structure.py` | Deposit observations, daily means and concentration (`<skill-root>/references/deposit-analysis.md`) |
| Reconcile two comparable management reports and allocate arithmetic changes | `<skill-root>/scripts/xlsx_reconcile.py`; optional `--xlsx` | Management reconciliation and arithmetic change allocation (`<skill-root>/references/management-reconciliation.md`) |
| Build a static management dashboard from a native wide report | `<skill-root>/scripts/xlsx_dashboard.py`; optional `--xlsx` | Static management dashboard (`<skill-root>/references/management-dashboard.md`) |
| Roll leaf institutions up a validated static hierarchy | `<skill-root>/scripts/xlsx_management.py` with hierarchy config | Static institution hierarchy rollup (`<skill-root>/references/institution-hierarchy.md`) |
| Compute explicit daily means or month/YTD daily-flow sums | `<skill-root>/scripts/xlsx_daily.py`, subcommand `average` / `accumulate` | Daily snapshots and controlled flow accumulation (`<skill-root>/references/daily-analysis.md`) |
| Build configured management wide reports | `<skill-root>/scripts/xlsx_management.py`; optional `--xlsx` | Configured bank management long-to-wide reports (first release) (`<skill-root>/references/management-wide.md`) |
| Export known analysis JSON to readable XLSX | `<skill-root>/scripts/xlsx_result_export.py` | Export analysis results to a readable XLSX (`<skill-root>/references/result-export.md`) |
| Rank observations and calculate group shares | `<skill-root>/scripts/xlsx_rank_share.py` | Controlled ranking and within-group shares (`<skill-root>/references/rank-share.md`) |
| Compute median, quantiles and variance/stddev | `<skill-root>/scripts/xlsx_advanced.py`, subcommand `describe` | Descriptive statistics with explicit Decimal semantics (`<skill-root>/references/advanced-statistics.md`) |
| Compare explicit monthly periods | `<skill-root>/scripts/xlsx_advanced.py`, subcommand `compare` | Explicit monthly period comparisons (`<skill-root>/references/period-comparison.md`) |
| Match tables by typed composite keys | `<skill-root>/scripts/xlsx_match.py` | Match two tables by typed composite keys (`<skill-root>/references/table-matching.md`) |
| Deduplicate and stably sort typed rows | `<skill-root>/scripts/xlsx_rows.py` | Filter, deduplicate and sort typed rows (`<skill-root>/references/row-processing.md`) |
| Suggest possible header ranges for review | `<skill-root>/scripts/xlsx_header_candidates.py` | Suggest header ranges for review (`<skill-root>/references/header-candidates.md`) |
| Reconstruct or select multi-level headers | `<skill-root>/scripts/xlsx_headers.py`; `--header-rows` in data operations | Explicit multi-level headers (`<skill-root>/references/multi-level-headers.md`) |
