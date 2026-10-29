# Explicit management statistics policy

路径占位符说明：`<skill-root>` 是 XLSX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 XLSX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

Follow the path and first-operation rules (`<skill-root>/SKILL.md#执行环境约束与首步自检`)
before using this reference; internal resources and task data have different path bases.

Run the entrypoint self-check (`<skill-root>/SKILL.md#执行环境约束与首步自检`), requiring exit 0 and
status ready; confirm task inputs and output write/read/cleanup probe. Resolve
`<skill-root>` from the actual SKILL.md, replace it before commands, and use task-relative
data paths. Python 3.9+, standard library only. No bank policy or regulator standard is
inferred from these examples.

## Input and CLI

Use native management_wide_v1 JSON (or the explicitly qualified daily adapter below) from Configured bank management long-to-wide reports (first release) (`<skill-root>/references/management-wide.md`) and the
explicit fictional policy (`<skill-root>/assets/fictional_management_policy_config.json`), copied to
the task directory as policy.json. Supply a source wide.json containing the selected
January–March institution observations. The sample plan values are explicitly non-linear.

```console
(
set -eC  # stop on failure; refuse to overwrite existing report files
python3 -I -S -B "<skill-root>/scripts/xlsx_management_policy.py" wide.json --query policy.json --xlsx progress.xlsx > progress.json
)
```

`scope` declares reporting level, currency, source version, definition version, unit,
kind, time_basis and month granularity. `institutions` explicitly maps unique IDs to that
level and user-defined peer groups; no size-based group is inferred. `context` must fix
all other retained dimensions, such as line/product/channel. Ancestor or other institutions
outside the declared universe are excluded and counted. A selected institution/period with
conflicting currency/version/level blocks calculation; it is not silently pooled. Conflicting
metric units/kind/basis or root definition versions also fail.

## Actuals and explicit plans

Current support is monthly point-in-time stock, complete natural-month daily mean, single-month flow, YTD snapshot and
validated ratio actuals, with target_type balance, daily_average, period_flow, ytd or ratio respectively.
They are never summed across periods or interchanged. Ratio actuals require upstream
aggregate-numerator-and-denominator-first validation evidence. No average of ratios is
calculated. Native daily_summary_v1 average inputs require coverage complete, exactly one entity key
explicitly declared by daily_entity_role: institution, and one entire natural month. Use
empty context because no line/product dimensions are inferred. Declared expected/observed/reported day counts must agree with the natural month and
with the presence of a complete mean; contradictions fail. Incomplete coverage stays
flagged and cannot rank. Entity scope must truly be institutional; no account-to-institution
aggregation is inferred. Daily source rows retain their own IDs and lineage. Do not relabel
a daily mean as a point-in-time balance to pass validation.

`plan_definition` repeats unit/kind/basis/currency/version/definition_version plus matching
target_type, explicit plan_version and direction at_least/at_most. Each `plans` entry
specifies institution, period, applicability and a value. Supply planned-to-date values for
every comparison; there is no linear interpolation, annual-target division, YTD subtraction
or implicit carry-forward. Missing plan stays unknown. not_applicable requires null value,
not zero. Finite zero/negative plans retain nonpositive_plan with null comparison outputs.

For positive comparable plans, favorable_deviation = actual-plan for at_least, plan-actual
for at_most. Gap = max(-deviation,0); behind_plan means deviation < 0. Attainment is
actual/positive plan for at_least, positive plan/positive actual for at_most. A zero actual
is observed_zero, never missing. at_most zero/negative actual has no invented infinite
attainment or rank, although arithmetic deviation remains available. Missing record,
missing value, missing plan, not applicable, nonpositive plan and unverified/quality actual
are distinct statuses. Any upstream row diagnostic or unverified cache blocks clean
comparison, conservatively including unrelated-metric row diagnostics.

## Comparable peers and continuity

Ranks compare attainment within the same explicit peer group and period under the fixed
scope/context and single plan direction. They never compare raw institution sizes, mix
levels/currencies/versions, or classify group membership automatically. Only comparable
nonnegative actuals with defined attainment rank. Choose competition, dense or ordinal;
ordinal ties retain configured institution order. Ratios use configured rounded attainment
values, with rounding flags, so near-equal attainments can tie at that precision.

`periods` is an explicit ordered sequence of 1–36 consecutive YYYY-MM months. Trailing
behind count is reported only when every institution-period in the window is comparable.
Missing values/plans, not applicable or quality issues produce unknown_incomplete_or_
inapplicable_window and a null count. Missing months cannot be treated as consecutive
observations or filled with zero. Counts do not imply future performance or causes.

## Output and boundary

XLSX contains Progress, Continuity, Dictionary, Calculations, Lineage, Quality, Metadata
and Warnings. Progress includes source report row IDs, status text and shared readable
styles. Metadata retains full policy and source JSON, including original file/sheet/row
lineage and input filters. Long identifiers remain text, formulas are not created, exact
numbers unsafe for Excel precision become text with warnings, and existing outputs fail.
Source reports are unchanged. Actual and plan units, periods and versions remain explicit.

Limits: native source/snapshot 16 MiB, query 64 KiB, up to 1000 institutions, 36 periods,
10000 institution-period combinations/plans and 200000 XLSX cells including audits. Decimal
exact arithmetic up to 1000 digits; divided values use 16–100 significant digits (default
34), ROUND_HALF_EVEN. No bank internal policy, regulatory reporting conformity, personnel
evaluation decision or causal inference is claimed. No real Excel/WPS rendering acceptance
has been performed. Signed/weighted plan rules and multi-month daily-report concatenation remain explicit
future work; unsupported bases fail rather than being guessed.
