# Native write and result contract

路径占位符说明：`<skill-root>` 是 XLSX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 XLSX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

先按主入口完成自检与任务路径确认；Python3.9+、标准库、禁止安装及外部软件。只处理原生XLSX/XLSM，WPS专有格式交由wps，不转换。输入受保护，写入新文件；任务输入和临时文件仍在已确认任务目录。本文是操作契约，不是环境或业务验收结果。

## 目录

- 选择（`<skill-root>/references/write-contract.md#按任务选择`）
- 命令（`<skill-root>/references/write-contract.md#commands`）
- 规格（`<skill-root>/references/write-contract.md#input-json`）
- 结果（`<skill-root>/references/write-contract.md#results-and-exit-codes`）

## 按任务选择

- 创建静态值表：本页规格与结果即可；创建说明负责模板/外观，写文件收尾负责runner与归档清理，不读公式语法。
- 创建公式、修复公式或显式calculate：再读公式计算（`<skill-root>/references/formula-calculation.md`）。
- 修改既有值：先读编辑保护（`<skill-root>/references/edit.md`）；edit仍会计算工作簿所有公式，发现公式或计算问题时再读公式计算，不能因用户只改值而忽略incomplete或failed。
- 仅读取、分析或有界视图：按主入口相应路由，不加载本页；旧完整读取仍有自己的缓存/类型解释。
- 引用不递归预加载；宿主只能整文件读取时，以上分支仍只加载所列文件。

## Commands

```console
python3 -I -S -B "<skill-root>/scripts/stdlib_xlsx.py" check
python3 -I -S -B "<skill-root>/scripts/stdlib_xlsx.py" read input.xlsx
python3 -I -S -B "<skill-root>/scripts/stdlib_xlsx.py" read input.csv --encoding utf-8-sig
python3 -I -S -B "<skill-root>/scripts/stdlib_xlsx.py" create report.json report.xlsx
python3 -I -S -B "<skill-root>/scripts/stdlib_xlsx.py" create report.json static.xlsx --values-only
python3 -I -S -B "<skill-root>/scripts/stdlib_xlsx.py" edit original.xlsx patch.json edited.xlsx
python3 -I -S -B "<skill-root>/scripts/stdlib_xlsx.py" calculate original.xlsx calculated.xlsx
```

Output files must not exist; original inputs are never overwritten. Preserve the
`.xlsm` extension when editing a macro-enabled workbook. Creation is XLSX only.
Redirect command stdout to a new task-relative report file if persistent JSON is
needed. Shell redirection is a write operation independent of the CLI: perform the
output-directory probe first. The `> report.json` examples assume a fresh filename
and a POSIX shell with `set -C` (noclobber) enabled; never use `>|` to bypass it.
When capturing stdout through Python instead, create the report with exclusive `x`
mode. Check the command exit status before using the captured report; a failure
report is not a successful analysis result. Copies of bundled examples also need
new destinations: read the internal asset, then write the task copy exclusively.

## Input JSON

A runnable example is bundled at calculation_example.json (`<skill-root>/assets/calculation_example.json`).
Create and edit JSON specifications use UTF-8 encoding; an optional UTF-8 BOM is accepted.

Both create and edit use this shape. Edit accepts only existing sheet names and patches
only listed cells; it does not replace the sheet's remaining data.

```json
{
  "sheets": [
    {
      "name": "Sales",
      "cells": {
        "A1": {"value": "Quantity", "style": 4},
        "B1": {"value": "Unit price", "style": 4},
        "C1": {"value": "Discount factor", "style": 4},
        "D1": {"value": "Amount", "style": 4},
        "A2": {"value": 12},
        "B2": {"value": 19.90},
        "C2": {"value": 0.95},
        "D2": {"formula": "ROUND(A2*B2*C2,2)", "style": 2}
      }
    },
    {
      "name": "Summary",
      "cells": {
        "A1": {"value": "Total"},
        "B1": {"formula": "Sales!D2", "style": 3}
      }
    }
  ]
}
```

Expected numeric results: Sales!D2 and Summary!B1 both 226.86. Styles 2/3 use General;
select a supported style when fixed display decimals are required. Existing-file edits
cannot add number formats; new files may use supported appearance presets.
Each cell has exactly one of `value` and `formula`, plus an optional integer `style`.
Duplicate JSON keys, duplicate case-insensitive cell/sheet names, unknown keys, invalid
addresses and invalid styles are rejected. Formula strings may start with `=`.
`value` accepts null (clear), a number, boolean or string. A string beginning `=` remains
literal text. JSON decimal numbers are loaded as Decimal, not binary floats. Dates must
be deliberately supplied as text or serial numbers; no implicit date conversion occurs.

For new files, template style indices are: 0 default, 1 input, 2 formula, 3 cross-sheet,
4 header, 5 currency input, 6 currency result, 7 percentage input, 8 percentage result,
9 integer input, 10 integer result, 11 year, 12 highlighted assumption. Currency styles
5/6 display whole dollars. For existing files, indices refer to that file's own styles.
Omitting style preserves an existing cell's style. New-workbook appearance presets (`<skill-root>/references/readable-appearance.md`) can append shared readable
styles and formats; existing-file edits cannot create arbitrary custom styles.

## Results and exit codes

| Exit | Status | Meaning |
|---|---|---|
| 0 | `ready` | Runtime self-check succeeded |
| 0 | `calculated_subset` | All formula cells calculated under this engine's subset; may be zero formulas |
| 0 | read result | Inspection succeeded; old formula caches remain unverified |
| 2 | `incomplete` | Unsupported formulas exist; output written with their caches removed |
| 1 | `failed` | Input/runtime/calculation failure, or incomplete values-only request; no new output |

该表描述底层业务结果；参数解析错误也可能退出2，但只有stderr usage、没有incomplete结果JSON，必须停止，不能交付为部分计算。runner另有归档/清理状态与收尾退出码，按写文件收尾（`<skill-root>/references/task-delivery.md`）分别核对；缺失、无效或截断JSON均不算成功。

Reports include formula_count, calculated_count, issues (sheet/cell/code/message),
output_written and excel_equivalence_verified=false. Check both exit and JSON; a file
existing does not mean every formula was calculated. Copy calculated results only when
business expectations are checked. Do not claim a full Excel recalculation.

An unsupported dependency propagates to evaluated dependents. All formula cells are
visited, so a separately stored erroneous formula still fails even if another IF skips
referencing it. Known errors/cycles prevent writing. On incomplete output, formulas
outside the subset remain intact but have no cached result. Values-only mode is available
only for creation and refuses any incomplete formula set.

Recalculation flags request a future spreadsheet application to recalculate; they do not
perform calculation. Formula caches may change on every worksheet. Chart/pivot and
external-link caches are not refreshed: their retained contents may display old data.

