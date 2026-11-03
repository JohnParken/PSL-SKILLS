# Formula evaluation contract

路径占位符说明：`<skill-root>` 是 XLSX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 XLSX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

先按主入口完成自检与任务路径确认；Python3.9+、标准库、禁止安装及外部软件。只处理原生XLSX/XLSM，WPS专有格式交由wps，不转换。输入受保护，写入新文件；任务输入和临时文件仍在已确认任务目录。本文是操作契约，不是环境或业务验收结果。

仅在创建/修改公式、显式计算或排查公式issues时加载。共同输入与退出码见写入契约（`<skill-root>/references/write-contract.md`），不重新定义schema。值编辑也可能触发全簿公式计算；结果为incomplete/退出2时不得宣称全部成功，failed/退出1不交付为计算成功。

## Contents

- Formula subset (`<skill-root>/references/formula-calculation.md#formula-subset`)
- Conditional and product aggregation (`<skill-root>/references/formula-calculation.md#conditional-and-product-aggregation`)
- Recalculate before analysis (`<skill-root>/references/formula-calculation.md#recalculate-before-analysis`)

## Formula subset

- Decimal numeric literals, double-quoted strings (`"a""b"` escapes quotes), TRUE/FALSE.
- Parentheses, unary +/-; binary +, -, *, / and =, <>, <, <=, >, >=.
- A1 references, optional `$`, ranges, case-insensitive sheet lookup, quoted sheet names
  (`'Sales Data'!A1`) including doubled apostrophes.
- SUM, COUNT, AVERAGE, MIN, MAX, IF (three arguments), ROUND (two arguments).
- SUMIF, SUMIFS, COUNTIF, COUNTIFS and comma-separated SUMPRODUCT over bounded
  rectangles, under the stricter type/shape rules below.
- IF evaluates only its chosen branch. Dependency cycles in evaluated paths are errors.
- Scalar arithmetic and final AVERAGE division use Decimal precision 28, ROUND_HALF_EVEN.
  SUM and the AVERAGE numerator accumulate exactly within 1000 significant digits;
  exceeding that bound fails instead of silently dropping a small amount during cancellation.
  SUM preserves its exact result; subsequent scalar arithmetic still has the 28-digit limit.
  Arithmetic uses fixed contexts independent of caller rounding, traps and exponent settings.
  ROUND uses ROUND_HALF_UP (ties away from zero), integer digits from -1000 to 1000;
  the quantized result must still fit 28 significant digits.
  This is an explicit Python calculation contract, not a guarantee of Excel floating
  point, locale, type-coercion or date semantics.
- Blank arithmetic values are zero; direct blank-reference formula results become zero.
  Booleans in arithmetic are 1/0. Text-to-number coercion is unsupported.
- Aggregates ignore text, blanks and booleans inside ranges. Scalar text/boolean aggregate
  arguments are unsupported to avoid ambiguous reference-vs-literal coercion. Use ranges
  for mixed data. An empty numeric AVERAGE is an error; empty SUM/MIN/MAX returns zero.
- Text comparisons are case-insensitive; mixed text/numeric and mixed boolean/non-boolean
  comparisons are unsupported. Boolean arithmetic coercion does not apply to comparisons.

Not supported: names, structured/table references, external links, whole-column ranges,
3D ranges, shared/array/data-table formulas, spill functions, exponent notation/operator,
percent/concatenation operators, omitted arguments, Excel error literals and functions
outside the list. Unsupported expressions preserve formulas without fabricating results.
Workbooks using precision-as-displayed calculation report their formulas as unsupported.
Malformed expressions and existing literal error cells block output. Formula error caches
are recomputed rather than used as inputs. ISO-date arithmetic is unsupported; date serial
arithmetic remains numeric and does not imply calendar semantics.

Limits: formula 8192 characters, one range 100,000 cells, package 128 MiB uncompressed,
10,000 ZIP members, 200,000 stored cells, dependency depth 100, bounded syntax nesting.
This is an in-memory tool, not a streaming engine for arbitrarily large workbooks.

For currency totals, prefer the exact analysis aggregation when the task is a data
summary, rather than a long chain of scalar additions. Establish whether rounding is
per item or after aggregation: ROUND(0.005,2)+ROUND(0.005,2) is 0.02, whereas
ROUND(SUM(0.005,0.005),2) is 0.01. Do not substitute one business policy for the other.
An exact sum does not prove the selected range, currency or unit is correct.

## Conditional and product aggregation

- `SUMIF(range, criterion[, sum_range])`, `SUMIFS(sum_range, range, criterion, ...)`,
  `COUNTIF(range, criterion)`, `COUNTIFS(range, criterion, ...)` support rectangular
  ranges and single-cell references, including cross-sheet references. Every range
  must have identical row and column dimensions. SUMIF's implicit range resizing is
  deliberately unsupported. Up to 127 condition pairs; conditions combine with AND.
- Criteria may be scalar numbers, numeric comparison strings (`">=100"`), or text
  equality/inequality (`"East"`, `"<>West"`), including scalar cell references. Text
  matching is case-insensitive; `*` and `?` are wildcards and `~` escapes a wildcard.
  Empty text criteria (`""` or `"="`) match missing/empty-string cells; `"<>"` matches
  nonempty cells. A nonempty text pattern such as `"*"` does not match a genuinely
  missing cell; an empty-string formula result remains text. Criteria are limited to 255 characters.
- Numeric criteria require numeric range values. Text, blank and boolean values in
  those ranges are unsupported, rather than implicitly converted. Nonempty text
  criteria accept only text/blank data; numeric/boolean data are unsupported. Boolean
  criteria and references to blank criterion cells are unsupported. Text ordering and
  locale-specific dates are unsupported; use explicit numeric serials for date thresholds.
- Conditional sums add matching numbers and ignore matching blank/text amounts; boolean
  amounts anywhere in the sum range are rejected. A SUMIF with no sum_range uses its first range. No match
  returns zero. Referenced errors/unsupported conditions are not hidden by other criteria.
- `SUMPRODUCT(range, ...)` supports 1–255 identically shaped ranges, with nonnumeric
  array entries contributing zero. Products and sums are exact within 1000 digits.
  Array expressions such as `SUMPRODUCT(A1:A3*B1:B3)` remain unsupported; use commas.
  Errors in referenced ranges propagate even where another factor is zero.
- Each range retains the 100,000-cell limit; combined range arguments to one of these
  new functions may reference at most 200,000 cells, counting repeated arguments.
  Text matching also enforces a 10,000,000-unit work budget per conditional call;
  long text/pattern combinations may be refused before matching.
  Type, shape or resource failures never silently truncate or reuse old caches.

These are deliberately bounded implementations, not complete Excel compatibility.
Function semantics were checked against Microsoft's references below; they are sources,
not runtime dependencies. The restrictions above remain authoritative for this Skill.

- SUMIF: `https://support.microsoft.com/en-us/excel/functions/sumif-function`
- SUMIFS: `https://support.microsoft.com/en-us/excel/functions/sumifs-function`
- COUNTIFS: `https://support.microsoft.com/en-us/excel/functions/countifs-function`
- COUNTIF: `https://support.microsoft.com/en-us/excel/get-started/use-the-countif-function-in-microsoft-excel?view=Schedule`
- SUMPRODUCT: `https://support.microsoft.com/en-us/excel/functions/sumproduct-function`

IFERROR, lookup functions,
shared/array formulas, structured table references and dynamic arrays remain unsupported.

## Recalculate before analysis

For a native formula table, the analysis CLI's `--formulas calculate` computes the
whole workbook in memory before profiling/grouping. It requires `calculated_subset`,
retains the calculation report, and never writes the input or an intermediate workbook.
An unsupported formula or error anywhere in the workbook blocks analysis, including
outside the selected rectangle. This is stricter than partial formula-file delivery.
The default formula policy still rejects formula data; `cached` explicitly accepts
unverified old values. See `<skill-root>/references/data-analysis.md` for commands.

