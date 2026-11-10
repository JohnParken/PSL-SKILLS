# Profile and summarize a declared region

路径占位符说明：`<skill-root>` 是 XLSX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 XLSX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

不直接读取 WPS 专有格式：.wps 使用 wps skill，.et/.ett 使用 et skill；本入口明确拒绝，不自动调用或转换。

Follow the path and first-operation rules (`<skill-root>/SKILL.md#执行环境约束与首步自检`)
before using this reference; internal resources and task data have different path bases.

## Contents

- Profile a sheet or region (`<skill-root>/references/data-analysis.md#profile-a-sheet-or-region`)
- Filter and summarize (`<skill-root>/references/data-analysis.md#filter-and-summarize`)
- Reliable amount summaries (`<skill-root>/references/data-analysis.md#reliable-amount-summaries`)

Resolve `<skill-root>` from the actual SKILL.md path before running commands. Run the
entrypoint self-check (`<skill-root>/SKILL.md#执行环境约束与首步自检`) first, require exit 0 and
`status: ready`, then confirm input readability. Use Python 3.9+ with `-I -S -B`;
no third-party packages, Office application, network or output-file permission is needed.
Run from the confirmed task directory with task-relative inputs. Commands emit JSON to
stdout and never modify inputs. Redirect stdout only to a new task report if needed.

## Profile a sheet or region

```python
execute(command="python3 -I -S -B \"<skill-root>/scripts/xlsx_analysis.py\" profile input.xlsx --sheet Sales")
execute(command="python3 -I -S -B \"<skill-root>/scripts/xlsx_analysis.py\" profile input.xlsx --sheet Sales --range B3:F100 --no-header")
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
Arithmetic contexts fix precision, rounding, exponent bounds and traps; prior Decimal
settings in the calling process cannot change these results. This also applies to the
shared exact/rounded helpers used by advanced statistics and management calculations.

## Filter and summarize

Create task-local `query.json`:

```json
{"group_by":["A"],"measure":"C","mixed":"reject",
 "filters":[{"column":"C","op":"ge","value":10}]}
```

```python
execute(command="python3 -I -S -B \"<skill-root>/scripts/xlsx_analysis.py\" summarize input.xlsx --sheet Sales --query query.json")
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

`summarize` additionally returns `reconciliation`: selected/group row counts, and,
when a measure exists, numeric/blank/ignored counts plus `source_sum` and `group_sum`.
All `checks` must be true and `status` must be `passed`; otherwise the command fails.
Totals are recomputed from the selected source population and compared with grouped
output, without tolerance or early rounding. No numeric samples retain null totals.
This checks internal consistency, not whether the user intended the selected scope.

Both analysis commands reject formulas by default, including formulas in header cells.
`--formulas cached` explicitly uses existing caches and reports their number as
`unverified_formula_caches`; missing caches and Excel error cells fail. No recalculation
is performed in cached mode, and cache freshness is never assumed. These checks cover the selected
rectangle before filtering, so a filter cannot silently hide erroneous inputs.

For native XLSX/XLSM, explicitly use `--formulas calculate` to recompute the entire
workbook in memory and then analyze current values. The source is never saved or
overwritten, and no temporary workbook is created. Unsupported formulas, errors and
cycles anywhere in the workbook stop analysis; `status: failed` includes the original
`calculation` report in CLI JSON. There is no fallback to caches or partial totals.
Success includes `formula_calculation` (with `excel_equivalence_verified: false`) and
`recalculated_formula_cells` for the selected rectangle including headers; only this
mode reports those fresh cells with `unverified_formula_caches: 0`. Empty-string
formula results remain empty text, not missing caches or numeric zero. Other analysis
CLI entrypoints retain their existing reject/cached options; this new CLI option is
exposed by profile/summarize here. WPS extraction JSON cannot be recalculated.

```python
execute(command="python3 -I -S -B \"<skill-root>/scripts/xlsx_analysis.py\" summarize ./input.xlsx --sheet Sales --range A1:C100 --query ./query.json --formulas calculate")
```

Read `<skill-root>/references/formula-calculation.md` before using this mode. It does
not refresh chart/pivot/external-link caches or establish business/Excel equivalence.
Limits: 200,000 selected cells including blanks, 10,000 groups, query file 64 KiB,
and the underlying workbook package limits. This is bounded in-memory analysis.
Exit 0 indicates completed analysis; exit 1 returns JSON `status: failed`.

## Reliable amount summaries

1. Confirm sheet/range, header rows, currency/unit, date scope and filters. Exclude
   subtotal/grand-total rows explicitly when aggregating detail; do not add both.
   Hidden/filtered Excel rows remain included unless query filters exclude them.
2. Preserve numeric types. Text amounts, identifiers, blanks and zero have different
   meanings; do not silently coerce or replace missing values. Use `mixed: reject`
   unless omission was explicitly chosen; report omitted/blank counts.
3. Use full-region `summarize` or the appropriate specialized script, not a sum of
   preview pages. For formulas, default rejection remains; `--formulas cached` is
   only for explicitly accepted unverified stored results. Use `--formulas calculate`
   for supported-subset recalculation before analysis; incomplete results are refused.
4. Keep full Decimal precision through aggregation. Confirm decimal places, rounding
   mode and per-item versus final-total rounding before formatting monetary results.
   Built-in summaries do not automatically round amounts to cents or convert units.
5. Require reconciliation to pass and independently spot-check source rows, one
   group total and the overall total against user-provided expectations or a separate
   calculation. For custom compound calculations, retain component values and check
   counts, additive totals and numerator/denominator definitions before exporting.
   Summing percentages or averaging group averages is not a weighted aggregate.
6. Reopen exported results and compare types and exact values. The result exporter
   may preserve numbers exceeding Excel's numeric precision as text with a warning;
   retain that warning, and do not treat displayed decimal places as stored precision.

## Identify contents, including ET/ETT candidates

仅需格式识别时直达 `<skill-root>/references/read-analyze.md#identify-contents-including-etett-candidates`，不预读区域统计。

## Analyze a WPS result without conversion

消费已有 ET 原始 JSON 时必须加读 `<skill-root>/references/et-result-analysis.md`，保留来源 partial、缓存、覆盖与文件交接边界；不把投影或分析 JSON 当原工作簿。

## Specialized task directory

专项直接从 `<skill-root>/SKILL.md#按需加载与任务路由` 选择，并读取 `<skill-root>/references/analysis-contract.md` 与命中专项；不经本页或递归加载所有分支。以上三个章节保留旧锚点，仅作条件转向。
