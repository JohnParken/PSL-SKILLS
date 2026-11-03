# Validate a standard-library output

路径占位符说明：`<skill-root>` 是 XLSX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 XLSX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

Follow the path and first-operation rules (`<skill-root>/SKILL.md#执行环境约束与首步自检`)
before using this reference; internal resources and task data have different path bases.

## 目录

- 输出验收与结果状态（本节）
- Task intermediate files（`<skill-root>/references/validate.md#task-intermediate-files`）
- Legacy utilities（`<skill-root>/references/validate.md#legacy-utilities`）
- Development verification（`<skill-root>/references/validate.md#development-verification`）
- Real business workbook verification (`<skill-root>/references/validate.md#real-business-workbook-verification`)

Keep these findings separate:

1. **Readable structure:** the ZIP and XML parse, expected sheets/cells exist, values,
   types and style references are appropriate. Parsing is not full OOXML XSD validation.
2. **Change integrity:** only intended cells and required calculation metadata changed.
   Compare untouched ZIP member contents; source file and unrelated data must remain.
3. **Subset calculation:** inspect `status`, `issues`, `calculated_count`, `formula_count`
   and `output_written` from `<skill-root>/scripts/stdlib_xlsx.py`. See Standard-library calculation and file contract (`<skill-root>/references/python-calculation.md`).
4. **Business accuracy:** independently check representative expected totals, ranges,
   rounding and units. A formula can evaluate successfully and still reference wrong data.
5. **Unverified behavior:** report unknown formulas, unrefreshed chart/pivot caches and
   lack of Excel rendering/recalculation verification. Never claim these checks passed.

Reopen the output using `read` to verify stored formulas and caches. Reading old caches
marks them unverified because an XLSX alone does not prove their freshness. Retain the
calculation report as the evidence of this run. No third-party application is launched.

For newly created reports, additionally inspect all `.xml` and `.rels` ZIP members:
require the double-quoted `UTF-8` declaration, the correct default namespace for each
root, no generated `ns0/ns1` prefixes, and correctly bound `r:id` references resolving
to workbook relationships and worksheets. Reopen and verify text, formulas and saved
values. These output conventions do not replace semantic or OOXML schema validation;
do not apply them as rejection criteria to existing files with valid alternate prefixes.

Exit 0 / calculated_subset means this engine completed its supported calculations; it
is not full Excel compatibility. Exit 2 / incomplete produces an output with unsupported
formula caches removed and must be disclosed as `部分公式未计算`. Exit 1 / failed must
not be delivered as a successfully calculated workbook. Static values-only reports must
be identified as such; inputs will not drive automatic updates.

The legacy `<skill-root>/scripts/formula_check.py` is heuristic static inspection. Its clean exit is not
calculation evidence, it does not prove business correctness, and it is not required by
the Python 3.9+ standard-library path. No LibreOffice installation or runtime is required.

## Task intermediate files

Use the executable delivery lifecycle (`<skill-root>/references/delivery-lifecycle.md`): run_xlsx_task.py owns new stdin JSON configs; xlsx_lifecycle.py provides explicit plan/seal/clean for custom helpers. Existing inputs are protected, never automatically adopted. The following rules remain the boundary, rather than permission for extension scanning.

- Before creating auxiliary files, record the exact task-relative paths and purpose of
  files/directories newly created by this task. Use a new independent subdirectory for
  work copies or unpacked data; never reuse an existing directory as disposable storage.
- After successful validation, remove only recorded intermediates no longer needed,
  such as assistant-generated specifications, temporary scripts and inspection outputs.
  Keep final deliverables, required calculation evidence, user-provided inputs and any
  auxiliary outputs the user requested. Never infer ownership from a filename or extension.
- While retrying, retain material still needed. On final failure, retain original inputs,
  existing outputs and recovery backups; keep only necessary diagnostic intermediates
  and report their locations. Do not deliver an old output as a new successful result.
- Clean recorded paths individually with Python standard-library operations within the
  task directory. Do not follow symlinks or use wildcard/recursive deletion; retain and
  report paths whose ownership or type changed, including symlinks in any parent component. Remove directories only after known
  contents are cleaned and the directory is empty. Do not touch the installed Skill.
- Verify the final deliverables still exist and are nonempty after cleanup. Report any
  cleanup failure without expanding the deletion scope. Cleanup records stay outside
  the workbook. Existing validation can be reused only if the deliverable is unchanged.


## Legacy utilities

The older scripts remain for compatibility and reference. They are not the standard
library workflow: `<skill-root>/scripts/xlsx_reader.py` needs pandas, `<skill-root>/scripts/libreoffice_recalc.py` needs external
software, and older helpers may require Python 3.10+. Do not invoke legacy utilities for
the maintained workflow. The `<skill-root>/scripts/xlsx_insert_row.py` and
`<skill-root>/scripts/xlsx_shift_rows.py` CLI entrypoints are disabled and exit 1 before any writes. A known
formula-reference corruption in these legacy scripts remains unresolved; disabling the
commands does not repair workbooks they may have changed previously. Do not invoke them
for structural edits. `<skill-root>/scripts/formula_check.py` is heuristic static inspection only; exit 0
does not prove calculation. Legacy examples are historical interface documentation,
not supported task instructions. In particular, `<skill-root>/scripts/libreoffice_recalc.py` uses a system
temporary directory and external software; it does not satisfy this workflow's task-directory
and runtime contract. Do not invoke it or treat the main self-check as validating it.


## Development verification

Maintenance only: run from the confirmed repository root, not the business task
directory. `skills/xlsx/scripts` below is repository-relative and is not an
installation path to copy into task commands. Tests use isolated temporary directories;
this does not authorize business workflows to choose a different temporary root.

```console
python3 -I -S -B -m unittest discover -s skills/xlsx/scripts -p 'test_*.py' -v
```

2026-11-02 XML output maintenance: synthetic creation/calculation/save/reopen tests
cover preferred declarations across every XML/relationship member, default namespace
URIs, `r:id`, Chinese text and XML escaping, plus no mutation of the input tree or
process-global namespace registration. Existing preservation, template read-only,
appearance and export regressions remain part of the suite. Description reviewed:
the output convention does not change supported tasks, so description is unchanged.
Validation environment: macOS, Python 3.9.6, standard library only. This is not target
Linux, Windows, real customer workbook, Excel rendering or external importer acceptance.
Python syntax and the 200-character line limit were checked for both changed Python
files. Pylint is unavailable; naming/lint checks were not run. The required strict
Skill structure check was not run because no usable Python 3.10+ was found; the local
Python 3.12 launcher is a broken symlink. Do not count these missing checks as passes.

2026-11-03 calculation reliability maintenance: before editing, all 148 files in the
repository's `xlsx-20261102.1954.3193.zip` matched the current source. Git history showed
no recent formula-engine change. This does not establish which package a remote host
loaded, or identify the cause of a particular user workbook's incorrect result.

Same-input synthetic checks against the pre-change source reproduced three defects:
SUM(10^28, 0.01, -10^28) returned 0 instead of 0.01; inherited Decimal exponent settings
could turn the mean of 1E-100 and 2E-100 into zero; CSV numeric import accepted the
29-digit value 10000000000000000000000000001 after normalize() rounded it. Updated
results are 0.01, 1.5E-100 and explicit precision rejection, respectively. Inputs for
these comparisons were constructed in memory; no user workbook was supplied.

SUM/AVERAGE accumulation now uses exact arithmetic bounded to 1000 digits; formula
and analysis contexts fix rounding, traps and exponent limits. Scalar formula operations
retain 28-digit precision. Group summaries reconcile selected rows, typed value counts
and source/group totals; this detects internal inconsistency, not a wrong business scope.
Tests include mixed-sign permutations, Fraction-based expected totals, injected group
corruption rejection, context restoration, ROUND bounds, overflow/underflow, explicit
per-item versus total rounding, and calculate/save/reopen/summarize/export/reopen.

Validation from the confirmed repository root on macOS:

- Python 3.9.6 and the host-located bundled Python 3.12.14 each ran
  `-I -S -B -m unittest discover -s skills/xlsx/scripts -p 'test_*.py'`:
  331 tests, exit 0, no failures or skips on each interpreter.
- Both interpreters ran `<skill-root>/scripts/stdlib_xlsx.py` with `check`, returning exit 0,
  `status: ready`. Python 3.12.14 also passed the development standard's interpreter
  self-check and checker help, then the repository-relative strict command
  `-I -S -B skills/skill-development-standards/scripts/check_skill.py skills/xlsx --strict`.
- All six changed/new Python files passed syntax, 200-character line and AST statement
  layout checks; `git diff --check` passed. Pylint is unavailable, so these focused
  checks do not establish a Pylint/naming pass.
- CLI regression subprocesses captured stdout, stderr and exit codes; the current
  execution tool returned command output and exit codes to the model. Independent
  channel/truncation behavior of the production cloud harness remains unverified.

Description and routes were reviewed: capability boundaries remain accurate, so the
description is unchanged. No new runtime dependency or published ZIP was introduced.
Linux, Windows, real business files, Excel rendering/recalculation and model-trigger
tests were not run. Main-agent work: baseline comparison, statistics/import fixes,
reconciliation, integration tests, documentation and final review. Subagent execution
record: luna__formula_precision, luna_worker/gpt-6-luna/max, one implementation handoff
and one bounded correction; formula-engine/test files only, 21 focused tests passed.

2026-11-03 formula-table enhancement verification: added bounded SUMIF/SUMIFS,
COUNTIF/COUNTIFS and comma-separated SUMPRODUCT, with explicit type/shape/resource
contracts in `<skill-root>/references/formula-calculation.md`. Synthetic expected
values include Microsoft's published SUMIFS examples (20 and 30), exact-cent
cancellation/products, blank-versus-empty-text matching and argument/budget limits.
These are independently specified examples, not results from executing Microsoft Excel.

The analysis CLI now supports `--formulas calculate`: recompute the whole native
workbook in memory, reject incomplete/failed calculations with their original report,
then profile/summarize current values without writing the input. Calculation refreshes
the library's inspection index, and a present empty string cache is no longer confused
with a missing cache. The runtime self-check verifies six expected formula values,
including conditional/product sums and an empty string, but remains an environment check.

Final validation used the same repository-root unittest discovery command above:
361 tests passed, exit 0, no failures or skips, on both macOS/Python 3.9.6 and the
host-located Python 3.12.14. This includes 22 new function tests and 8 new workflow tests
covering cross-sheet/nested dependencies, stale/missing caches, source byte preservation,
save/reopen, values-only state, refusal of errors outside a selected region, and CLI
success/failure JSON. Both self-checks returned ready/exit 0. Strict Skill checks on
Python 3.12.14 and git diff whitespace checks passed. The five Python files involved
in this enhancement passed syntax/200-character/AST statement-layout checks; Pylint
remains unavailable. No runtime dependency or publication ZIP changed. The description
still accurately says formula subset; routes, API behavior and references were updated.

Linux/Windows, real user workbooks, model-trigger tests, Excel rendering/recalculation,
and production harness channel/truncation behavior remain unverified. Main-agent work:
function-contract decisions and primary-documentation checks, recalculation integration,
empty-cache fix, workflow tests, documentation, diff review and acceptance. Subagent
execution record: luna__conditional_formulas (luna_worker/gpt-6-luna/max), one handoff,
one in-scope correction and six bounded waits; formula-engine/new function tests only.
Worker targeted results: 22 function tests and 21 prior engine tests passed; main-agent
integration and complete-suite results above are separate evidence.

## Real business workbook verification

2026-11-03 follow-up: the user explicitly authorized searching/downloading public
business workbooks for maintenance validation. This does not add network access or
dependencies to the Skill runtime. Five publisher-hosted files were downloaded,
SHA-256 pinned, and retained unchanged outside the distributed Skill. They include
published council accounts/budget monitoring and operational regulatory financial
models; the gas model is a consultation draft, not an audited set of company accounts.

| Published workbook | Original formula cells | Native calculation result |
|---|---:|---|
| Stanton-under-Bardon 2025/26 Q1 budget monitoring | 178 | incomplete: 14 calculated, 164 blocked directly or through shared-formula dependencies |
| Denham cashbook 2019/20 | 16 | incomplete: 2 calculated, 14 blocked directly or through shared-formula dependencies |
| Denham bank reconciliation | 14 | calculated_subset: all 14 calculated |
| Ofgem ED2 PCFM V5 | 20,869 | load rejected: 401,035 stored cells exceed the 200,000-cell limit |
| Ofgem gas transmission RIIO-GT3 PCFM draft | 27,668 | failed: 6,143 individually evaluable cells do not establish workbook success; shared formulas, names and unsupported functions block completion |

Official source pages (full download URLs, retrieval times and file hashes are in
the evidence manifest):

- `https://www.stantonunderbardonparishcouncil.gov.uk/budget-monitoring`
- `https://denhamparishcouncil.gov.uk/parish-council/finance/`
- `https://www.ofgem.gov.uk/guidance/ed2-price-control-financial-model`
- `https://www.ofgem.gov.uk/cy/consultation/gas-interconnector-import-capability-proposed-modifications-national-gas-transmissions-licence`

Independent verification uses a separate XML reader, rational arithmetic (`Fraction`)
and a narrow AST interpreter, never formula caches as inputs. All 30 calculated
formulas in the three council files matched their independently recomputed exact
values. Five original SUMPRODUCT formulas in the gas model (`Transform_TO!AU120:AY120`)
also matched independent weighted sums from the original inputs, traversing their
cross-sheet dependencies. These five are isolated formula checks, not a successful
native workbook run. Nine newly written SUMIF/SUMIFS/COUNTIF/COUNTIFS/ROUND checks over
the unchanged bank transactions passed; they are explicitly labelled derived tests,
not formulas authored by the publisher. Original SUMIFS/COUNTIFS in the gas model
remain blocked by dependency/type/support restrictions and are not claimed validated.

Seven bank totals were also checked to GBP cents: opening 3,906.41, income 5,609.68,
payments 5,495.73, closing 4,020.36, uncleared cheques 102.56, adjusted cash 4,122.92,
and bank statements 4,122.92. Grouping all 28 payment rows into 16 payees reconciled
exactly after whole-workbook recalculation, with zero unverified caches used. Some
stored amounts already contain binary-floating-point tails: these were preserved,
not silently normalized. Twelve of the 35 checked original caches differed in exact
digits; none differed at GBP cents, including conversion from GBP millions in the gas
model. Old-cache agreement alone is not proof of correct or fresh Excel calculation.
The Denham bank download filename says March 2020, but its contents describe 2018/19;
the content dates, rather than the filename, define this validation's business period.

The actual analysis CLI was exercised on every original file: one success and four
explicit failures, including when a tiny region was selected. The full calculation
failure cannot be bypassed by region selection or replaced with cached results. The
gas model also exposes a diagnostic limitation: valid named references such as RIIO3
are classified as out-of-bounds cell errors in 15 formula results/dependencies. This
is a parser/support gap, not evidence that the publisher's formula is incorrect.
No resource limits were raised and no formulas or source files were rewritten.

Evidence is repository maintenance material, not installed Skill content. From the
confirmed repository root, rerun the standard-library harness read-only to stdout:

```console
python3 -I -S -B tests/xlsx_real_business_20261103/verify_real_business.py
```

Repository-relative `tests/xlsx_real_business_20261103/` retains `sources.json`, the
five unchanged inputs, ownership records, the harness, and `results.json` /
`results_py312.json`. Both macOS/Python 3.9.6 and 3.12.14 completed the assertions with
exit 0; this means the documented successes and refusal contracts passed, not all
five workbooks calculated. CLI stdout/stderr/exit codes were captured, with status,
length and stdout digest retained. Source hashes matched before and after all checks.
The stdout-only rerun produced complete JSON, exit 0 and empty stderr; its business
results matched both saved runs. Syntax, 200-character lines, AST statement-layout,
strict Skill structure and git whitespace checks passed. Pylint remains unavailable.
An initial harness assertion confused the selected-region formula count with the
whole-workbook count; it was corrected to check both scopes separately, then both
interpreter runs passed. The engine and its calculation expectations were unchanged.
No new runtime dependency or publication ZIP was introduced;
description, routes and environment self-check remain accurate and unchanged.
Linux/Windows, Excel execution/rendering, original-model SUMIFS/COUNTIFS success and
production cloud harness channel/truncation behavior remain unverified.
Main-agent work: source selection, validation design/implementation, independent
business checks, evidence interpretation and final review. Subagent execution record:
none in this real-file validation follow-up.
