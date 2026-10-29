# Management reconciliation and arithmetic change allocation

路径占位符说明：`<skill-root>` 是 XLSX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 XLSX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

Follow the path and first-operation rules (`<skill-root>/SKILL.md#执行环境约束与首步自检`)
before using this reference; internal resources and task data have different path bases.

Run the entrypoint self-check (`<skill-root>/SKILL.md#执行环境约束与首步自检`) and require exit 0,
status ready. Confirm the task directory, readable inputs and output write/read/cleanup
probe. Replace `<skill-root>` from the actual SKILL.md location. Python 3.9+, standard
library only; no installation, network or private data discovery.

## Inputs and CLI

Accept two native management_wide_v1 reports, produced by
Configured bank management long-to-wide reports (first release) (`<skill-root>/references/management-wide.md`), including validated
institution hierarchy (`<skill-root>/references/institution-hierarchy.md`) results. Select one explicit period per
side; they may be equal for two-table reconciliation. Select one currency/version/level
scope per run. Other periods and scopes are explicitly excluded, not aggregated.
Copy the fictional configuration (`<skill-root>/assets/fictional_reconciliation_config.json`) to the
task directory as reconcile.json. Provide left.json and right.json for January/February:

```console
(
set -eC  # stop on failure; refuse to overwrite existing report files
python3 -I -S -B "<skill-root>/scripts/xlsx_reconcile.py" left.json right.json --query reconcile.json --xlsx reconciliation.xlsx > reconciliation.json
)
```

`keys` must explicitly retain all native dimensions except period and institution_name.
This includes institution, line, currency, version, report_level and product if retained.
`group_by` is a unique subset (for example institution/product). Duplicate full keys fail;
there is no guessed duplicate reduction or many-to-many expansion. Missing actuals and
any quality flags, including unverified caches, block complete reconciliation. Input
schemas, metric dictionaries, definition versions and static mappings must agree. No
historical institution restatement is attempted. Long IDs retain exact text types.

## Semantics

Compare only additive SUM stock/flow metrics. Ratios, COUNT, MIN/MAX and unknown types
are refused for the combined totals/attribution workflow. Every dimension varying across
selected observations must be declared additive in the metric dictionary. Single-level
scope prevents parent/child double counting. Currency, version and unit incompatibilities
are refused or explicitly scoped out; no conversion is inferred.

Records are added, removed, changed or unchanged_within_tolerance. Absolute Decimal
`tolerance` is required, finite and nonnegative, in each metric's own unit. Deltas within
tolerance remain in arithmetic totals; they are not rounded to zero. Before/after values
of absent records remain null. Zero is used only in the explicit added/removed delta
bridge, not represented as an observed balance. Missing numeric values are not absence.
Checks reconcile sum(after)-sum(before) with sum(record deltas), retaining residuals.

Contributions sum record deltas by selected group, then divide by signed net change.
Shares may be negative or exceed one. Zero net change produces null shares and an
undefined_zero_net diagnostic; negative net change is explicitly marked. These are
arithmetic allocations, not causes or causal explanations. No daily-flow inference,
YTD accumulation, growth-rate model or prediction is performed. Decimal arithmetic is
exact up to 1000 digits; shares use configured 16–100 significant digits (default 34),
ROUND_HALF_EVEN, with rounding flags. No binary float arithmetic is used.

## Output and limits

JSON retains both full source reports and queries, including dictionaries, units, periods,
filters, quality checks and file/sheet/source-row lineage. Each record references left and
right wide row IDs. XLSX contains Records, Checks, Contributions, Metadata and Warnings;
Metadata stores the complete structured result in ordered JSON chunks. It uses shared
readable styles, pale-blue frozen headers and alternating rows. All values are literal;
Excel-unsafe precise numbers become exact text with warnings, using the existing
safe exporter (`<skill-root>/references/result-export.md`) policy. Existing outputs are refused.

Input/snapshot limit is 16 MiB each, query 64 KiB, inherited 5000 rows per report and
20000 metric records; output has at most 20000 matched-union records and 200000 cells
including metadata/warnings. It is bounded in-memory processing. No real bank/customer
files or Excel/WPS visual acceptance were used; generated OOXML/readback is not visual QA.
