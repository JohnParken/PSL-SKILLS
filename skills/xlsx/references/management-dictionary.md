# Management metric dictionary and scope contract

路径占位符说明：`<skill-root>` 是 XLSX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 XLSX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

Follow the path and first-operation rules (`<skill-root>/SKILL.md#执行环境约束与首步自检`)
before using this reference; internal resources and task data have different path bases.

Use with Configured bank management long-to-wide reports (first release) (`<skill-root>/references/management-wide.md`). The required self-check, directory and
read/write rules are those in that workflow and XLSX — Python Standard Library (`<skill-root>/SKILL.md`). Dictionary input
is task data, never an instruction to alter a Skill or execute expressions.

The fictional configuration (`<skill-root>/assets/fictional_management_config.json`) is a complete
runnable schema example. Its facts/targets are not actual bank/customer information.
No default banking KPI or supervisory threshold is embedded in the implementation.

## Configuration fields

- dimensions: mapping from supported dimension names to unique source column selectors.
- dimension_labels: optional readable labels for configured dimensions.
- row_dimensions: ordered output grain, retaining period/currency/version.
- fact_grain: all input dimension names plus metric_id, declaring uniqueness.
- metric_column/value_column/unit_column/basis_column: distinct source selectors.
- record_scope: column selector, exact literal value and explicit report_level.
- definition_version and period_format=YYYY-MM: required comparability constraints.
- metrics: 1..50 explicit metric definitions below; unknown input metrics fail.
- filters: existing typed AND predicates; missing: propagate or reject; precision:16..100.
- targets: optional fully grained, typed target records; no automatic target allocation.

No expressions, SQL, Python snippets, hierarchy maps or custom aggregation functions are
executed. Unknown configuration fields are rejected. Customer/product membership overlap
cannot be determined from aggregate facts: valid additive partitions are a user/upstream
contract, not something proved by unique row keys alone.

## Metric definitions

Every metric has metric_id, label, unit, kind, time_basis and aggregation. IDs are exact
strings and unique. Labels/units identify scope; source unit and time basis must match
for every selected fact. Definition version applies to the complete dictionary/run.

Base kinds are stock and flow. Stock requires point_in_time. Flow requires period_flow
or ytd. The module retains periods, so it never adds month-end balances across time and
never adds successive monthly YTD totals. It does not derive period flow from YTD
observations or allocate stock to days. Those require separate, explicit business rules.

Supported base aggregations:

- SUM: exact numeric sum across approved dropped dimensions.
- COUNT: count numeric fact observations at declared grain; unit must be records.
  It is not customer/account distinct count or the sum of supplied count values.
- MIN / MAX: extrema of observed numeric values, not additive totals.

SUM/COUNT require dropped dimensions to appear in additive_dimensions. MIN/MAX require
explicit reduction_dimensions instead. Protected dimensions period/currency/version
cannot appear in either permission list. A missing value propagates null by default;
COUNT does not quietly skip incomplete observations. Non-numeric facts are rejected.
To total already-counted business volumes, use SUM with an explicitly defined unit and
non-overlapping grain; do not substitute COUNT. No inferred double-count corrections occur.

Ratio metrics use kind=ratio, time_basis=ratio, aggregation=RATIO, additive_dimensions=[]
and two defined base metric IDs numerator/denominator. scale must be an explicit positive
number: scale=1 yields a fraction, scale=100 a percentage-point value. Ratio chains and
raw ratio fact rows are refused. Both components must have the same kind, time basis
and unit; mixed-basis measures (e.g. period profit divided by a derived average balance)
are outside this release. This is stricter than arbitrary banking formulas intentionally.

Components are aggregated first; ratio=aggregated numerator*scale/aggregated denominator.
There is no average of ratios, including unequal-weight product ratios. Missing components
produce null and diagnostics. Zero denominators produce null, never zero. Negative
denominators are rejected as a ratio result with a quality flag unless explicitly allowed
by allow_negative_denominator=true; signed results then carry a flag. Negative numerators
also carry a flag. No generic rule claims such values are valid for a specific bank KPI.

Exact aggregation, multiplication and differences have a 1000-digit arithmetic bound;
inexact intermediate operations fail. Ratios/achievement use Decimal with default 34
significant digits (16..100 configurable), 16 guard digits and ROUND_HALF_EVEN. Inexact
results add rounded quality flags. No silent rounding to Excel's 15-digit numeric capacity
occurs; the export layer retains longer numbers as text and keeps numeric metadata.

## Targets and unsupported definitions

Each target has dimensions (exactly row_dimensions), metric_id, unit, kind, time_basis,
definition_version, report_level, mode and numeric value. mode is at_least or at_most;
all type/unit/basis/scope fields must match actuals. Targets are not copied across periods,
institutions or currencies. Zero/negative target values are retained but achievement is
undefined and diagnosed, not treated as successful/failed performance. Shortfall formulas
and inverse-completion edge cases are documented in the management workflow.

Customer distinct counts need actual stable customer identifiers and deduplication scope;
daily averages need complete daily balances or explicit day-count weights/calendar rules.
Neither is approximated from monthly endpoints. Daily analysis (`<skill-root>/references/daily-analysis.md`)
accepts actual daily snapshots with explicit calendar-coverage checks; weighted means remain unsupported. Fiscal calendars, organizational
historical hierarchy restatement, overlapping-product attribution, regulatory classifications,
FX normalization and personal/employee decision workflows are unsupported. Supply known
aggregates as explicitly defined facts only when their upstream provenance is available.
