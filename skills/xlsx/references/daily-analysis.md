# Daily snapshots and controlled flow accumulation

路径占位符说明：`<skill-root>` 是 XLSX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 XLSX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

Follow the path and first-operation rules (`<skill-root>/SKILL.md#执行环境约束与首步自检`)
before using this reference; internal resources and task data have different path bases.

Run the SKILL self-check (`<skill-root>/SKILL.md#执行环境约束与首步自检`), require exit 0/status ready,
and confirm the task directory, input readability and XLSX write probe. Resolve
`<skill-root>` from the actual SKILL.md path. Commands use relative task data paths and
Python 3.9+ standard libraries; no application, installation or upload is involved.

A configuration declares entity_keys, source columns (metric/date/value/unit/currency/
version/basis), source metrics, definition_version, start/end and date_format=YYYY-MM-DD.
Averages require stock/daily_snapshot metrics and matching source basis. Accumulation
requires flow/daily_flow; YTD, month-end and endpoint-only snapshots cannot substitute.
Units must match the declared metric. Dates are exact ISO date text (including date_text),
not guessed numeric Excel serials, timestamps or local-language dates. Entity IDs are
nonblank literal text. Currency and definition version are independently retained/checked.

## Reproducible fictional examples

Copy average config (`<skill-root>/assets/fictional_daily_average_config.json`) and
average facts spec (`<skill-root>/assets/fictional_daily_average_facts.json`) into new task files
`./daily.json` and `./daily-facts.json`, respectively, using exclusive creation. Then run:

```console
python3 -I -S -B "<skill-root>/scripts/stdlib_xlsx.py" create ./daily-facts.json ./daily.xlsx
python3 -I -S -B "<skill-root>/scripts/xlsx_daily.py" average daily.xlsx --sheet DailyFacts --query daily.json --xlsx average_report.xlsx
```

Daily-flow config (`<skill-root>/assets/fictional_daily_flow_config.json`) and
flow facts spec (`<skill-root>/assets/fictional_daily_flow_facts.json`) illustrate accumulation.
Copy them exclusively to new task files `./flow.json` and `./flow-facts.json`, respectively:

```console
python3 -I -S -B "<skill-root>/scripts/stdlib_xlsx.py" create ./flow-facts.json ./flow.xlsx
python3 -I -S -B "<skill-root>/scripts/xlsx_daily.py" accumulate flow.xlsx --sheet DailyFacts --query flow.json --xlsx flow_report.xlsx
```

All examples are fictional and contain no real customer or bank records. The ordinary
range/header/path-selection/cache options apply. Without --xlsx, JSON daily_summary_v1
is emitted and may be exported separately with `<skill-root>/scripts/xlsx_result_export.py`. Source files are
never changed. No native pivot cache or charts are written.

## Exact coverage contract

start/end are inclusive Gregorian calendar dates spanning 1..366 days. Weekends and
holidays are included; no business-day calendar is guessed. Every entity/metric/currency/
version/date key must be unique. Duplicate dates fail with both source row numbers, even
when values agree or one is blank. Dates outside the requested interval fail by default;
explicit outside=exclude reports their count. Filters are applied before this date scope.

Reported days count any dated record; observed days count numeric nonmissing values.
Blank values are missing days, while numeric zero is a valid observation. coverage_details
lists expected/reported/observed counts, every missing/blank date, and coverage rounding.
coverage defaults complete: incomplete intervals have null primary results, never zero.
Observed mean/sum is separately available. coverage=observed explicitly uses the observed
value as primary and marks it observed-only; it must not be described as a full-period
mean/total. Groups/entities entirely absent from the source are not invented. An empty
result is flagged; no complete coverage of an unknown entity universe is claimed.

Daily balance mean=sum of numeric daily snapshots / numeric observed days. Under complete
coverage, observed days equal all calendar days in the interval. There is no zero fill,
forward fill, interpolation, endpoint averaging, day-weight estimation or month-end
balance averaging. Source units remain per row; coverage itself is a fraction.

Daily flow accumulation emits explicitly requested periods=['month','ytd'] by default.
Month windows intersect the requested interval and retain actual start/end; partial
month windows are labelled, not claimed as full calendar months. YTD requires the overall
interval to begin January 1; each year uses only that year's daily flows through the
requested end. Missing dates make the complete sum null; observed subtotal is explicit.
Monthly and YTD rows are alternative reporting windows and must not be added together.
Source YTD snapshots are always rejected rather than repeatedly accumulated or differenced.

Exact sums have a 1000-digit bound; exceeding it fails. Means and coverage ratios use
16..100 significant digits (default 34), 16 guard digits, ROUND_HALF_EVEN and explicit
inexact flags. JSON Decimal values are strings; XLSX preserves >15-digit results as text
with warnings. Formula caches require explicit --formulas cached and remain unverified.
No recalculation occurs; missing caches/errors fail before filtering.

The Daily XLSX sheet is accompanied by Columns/Metadata/Warnings/Definitions/Validation/
Lineage. Every window retains original file/sheet/row/metric references. A source day
may legitimately appear in both a month and YTD audit window. Bounds: 20 source metrics,
1000 groups, 20000 lineage records, 200000 output cells plus the full audit/export budget.
Exit 0 means computation completed, not that coverage is complete. Exit 1 gives structured
failure. Actual Excel/WPS rendering and regulator-specific averaging definitions are not
validated. Weighted averages, customer distinct counts and incomplete-calendar repairs
remain unsupported.
