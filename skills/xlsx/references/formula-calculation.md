# Formula evaluation contract

路径占位符说明：`<skill-root>` 是 XLSX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 XLSX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

先按主入口完成自检与任务路径确认；Python3.9+、标准库、禁止安装及外部软件。只处理原生XLSX/XLSM，WPS专有格式交由wps，不转换。输入受保护，写入新文件；任务输入和临时文件仍在已确认任务目录。本文是操作契约，不是环境或业务验收结果。

仅在创建/修改公式、显式计算或排查公式issues时加载。共同输入与退出码见写入契约（`<skill-root>/references/write-contract.md`），不重新定义schema。值编辑也可能触发全簿公式计算；结果为incomplete/退出2时不得宣称全部成功，failed/退出1不交付为计算成功。

## Formula subset

- Decimal numeric literals, double-quoted strings (`"a""b"` escapes quotes), TRUE/FALSE.
- Parentheses, unary +/-; binary +, -, *, / and =, <>, <, <=, >, >=.
- A1 references, optional `$`, ranges, case-insensitive sheet lookup, quoted sheet names
  (`'Sales Data'!A1`) including doubled apostrophes.
- SUM, COUNT, AVERAGE, MIN, MAX, IF (three arguments), ROUND (two arguments).
- IF evaluates only its chosen branch. Dependency cycles in evaluated paths are errors.
- Decimal precision 28; ROUND uses ROUND_HALF_UP (ties away from zero), integer digits.
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

