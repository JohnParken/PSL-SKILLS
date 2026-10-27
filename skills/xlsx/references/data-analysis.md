# Read-only data analysis and file identification

不支持读取 WPS 格式（.wps/.et/.ett），请使用 wps skill；本入口明确拒绝，不自动调用或转换。

Follow the [path and first-operation rules](../SKILL.md#执行环境约束与首步自检)
before using this reference; internal resources and task data have different path bases.

## Contents

- [Specialized task directory](#specialized-task-directory)
- [Profile a sheet or region](#profile-a-sheet-or-region)
- [Filter and summarize](#filter-and-summarize)
- [Identify contents, including ET/ETT candidates](#identify-contents-including-etett-candidates)
- [Analyze a WPS result without conversion](#analyze-a-wps-result-without-conversion)

Resolve `<skill_dir>` from the actual SKILL.md path before running commands. Run the
[entrypoint self-check](../SKILL.md#执行环境约束与首步自检) first, require exit 0 and
`status: ready`, then confirm input readability. Use Python 3.9+ with `-I -S -B`;
no third-party packages, Office application, network or output-file permission is needed.
Run from the confirmed task directory with task-relative inputs. Commands emit JSON to
stdout and never modify inputs. Redirect stdout only to a new task report if needed.

## Profile a sheet or region

```console
python3 -I -S -B "<skill_dir>/scripts/xlsx_analysis.py" profile input.xlsx --sheet Sales
python3 -I -S -B "<skill_dir>/scripts/xlsx_analysis.py" profile input.xlsx --sheet Sales --range B3:F100 --no-header
```

By default the first selected row supplies headers and is excluded from statistics.
Column letters remain absolute coordinates. For explicit multi-level rows and path/ID
selectors, use [multi-level-headers.md](multi-level-headers.md).
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
python3 -I -S -B "<skill_dir>/scripts/xlsx_analysis.py" summarize input.xlsx --sheet Sales --query query.json
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

`xlsx_identify.py` inspects native OOXML inputs. WPS family suffixes and CFB signatures
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
python3 -I -S -B "<skill_dir>/scripts/xlsx_analysis.py" profile result.json --sheet Data
python3 -I -S -B "<skill_dir>/scripts/xlsx_analysis.py" summarize result.json --sheet Data --query query.json
```

Extraction diagnostics are included as `source_issues` and `source_status`. Formula cells
still require explicit `--formulas cached`; missing/error caches fail. This is analysis of
previously extracted JSON, not reopening WPS originals or validating caches. Other native loader
APIs still require XLSX/XLSM. Dates, private streams and opaque charts retain the source limits.

## Specialized task directory

Read the selected reference before invoking its script. Paths use the confirmed `<skill_dir>`; inputs and outputs remain in the task directory.

| Task | Action | Reference |
|---|---|---|
| Summarize explicit deposit maturity buckets and cumulative windows | `<skill_dir>/scripts/xlsx_maturity.py` | [maturity-distribution.md](maturity-distribution.md) |
| Aggregate observed customers, product coverage and two-period sets | `<skill_dir>/scripts/xlsx_customers.py` | [customer-structure.md](customer-structure.md) |
| Validate management policy, explicit plan deviation and comparable peers | `<skill_dir>/scripts/xlsx_management_policy.py` | [management-policy.md](management-policy.md) |
| Analyze deposit snapshot changes, daily/end differences and explicit targets | `<skill_dir>/scripts/xlsx_deposits.py` | [deposit-analysis.md](deposit-analysis.md) |
| Calculate explicit deposit category shares and Top N concentration | `<skill_dir>/scripts/xlsx_deposit_structure.py` | [deposit-analysis.md](deposit-analysis.md) |
| Reconcile two comparable management reports and allocate arithmetic changes | `<skill_dir>/scripts/xlsx_reconcile.py`; optional `--xlsx` | [management-reconciliation.md](management-reconciliation.md) |
| Build a static management dashboard from a native wide report | `<skill_dir>/scripts/xlsx_dashboard.py`; optional `--xlsx` | [management-dashboard.md](management-dashboard.md) |
| Roll leaf institutions up a validated static hierarchy | `<skill_dir>/scripts/xlsx_management.py` with hierarchy config | [institution-hierarchy.md](institution-hierarchy.md) |
| Compute explicit daily means or month/YTD daily-flow sums | `<skill_dir>/scripts/xlsx_daily.py`, subcommand `average` / `accumulate` | [daily-analysis.md](daily-analysis.md) |
| Build configured management wide reports | `<skill_dir>/scripts/xlsx_management.py`; optional `--xlsx` | [management-wide.md](management-wide.md) |
| Export known analysis JSON to readable XLSX | `<skill_dir>/scripts/xlsx_result_export.py` | [result-export.md](result-export.md) |
| Rank observations and calculate group shares | `<skill_dir>/scripts/xlsx_rank_share.py` | [rank-share.md](rank-share.md) |
| Compute median, quantiles and variance/stddev | `<skill_dir>/scripts/xlsx_advanced.py`, subcommand `describe` | [advanced-statistics.md](advanced-statistics.md) |
| Compare explicit monthly periods | `<skill_dir>/scripts/xlsx_advanced.py`, subcommand `compare` | [period-comparison.md](period-comparison.md) |
| Match tables by typed composite keys | `<skill_dir>/scripts/xlsx_match.py` | [table-matching.md](table-matching.md) |
| Deduplicate and stably sort typed rows | `<skill_dir>/scripts/xlsx_rows.py` | [row-processing.md](row-processing.md) |
| Suggest possible header ranges for review | `<skill_dir>/scripts/xlsx_header_candidates.py` | [header-candidates.md](header-candidates.md) |
| Reconstruct or select multi-level headers | `<skill_dir>/scripts/xlsx_headers.py`; `--header-rows` in data operations | [multi-level-headers.md](multi-level-headers.md) |
