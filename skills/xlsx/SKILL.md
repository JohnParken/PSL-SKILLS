---
name: xlsx
version: "20261023.1835.5058"
description: 用于读取 XLSX/XLSM、创建表格报告、受控修改单元格及交换 CSV/TSV 数据，支持纯 Python 标准库分析。ET/ETT 仅按文件内容尝试读取已实现的 BIFF8 记录子集，不代表通用 WPS 格式支持；不支持结构编辑。
license: MIT
---

# XLSX — Python Standard Library

Category: productivity. Format reference: ECMA-376 Office Open XML File Formats.

Use Python 3.9+ without installing packages, launching Office/LibreOffice, or using plugins.
The maintained entrypoint is `<skill_dir>/scripts/stdlib_xlsx.py`. Keep the complete
Skill directory structure intact: bundled modules (including optional appearance and
analysis modules) live in `<skill_dir>/scripts/`, and templates in
`<skill_dir>/assets/minimal_xlsx/`. Do not distribute only the entrypoint and its direct imports.

## 执行环境约束与首步自检

1. Run in the harness-confirmed task working directory. Inputs, outputs, JSON
   configurations, redirected reports and task temporary files use paths relative to
   that directory and stay within it or its children. Do not use parent paths,
   absolute data paths or mount changes to bypass this boundary. If the current
   directory is the Skill source, have the harness confirm a separate task directory
   first; do not change to the Skill directory just to run an example.
2. Determine `<skill_dir>` by removing the filename from the `file_path` actually
   used to read this SKILL.md. For a relative `file_path`, resolve it against the
   confirmed base directory used for that read. If the real accessible location
   cannot be established, stop and report that it cannot be located; do not guess
   from environment variables, working-directory output, another session or host masks.
3. Locate scripts, references, assets and the dependency manifest under
   `<skill_dir>/scripts/`, `<skill_dir>/references/`, `<skill_dir>/assets/` and
   `<skill_dir>/requirements.txt`. These internal paths may be confirmed absolute
   paths; the task-data relative-path rule does not apply to them. Never assume the
   task directory contains `scripts/` or a fixed Skill installation directory.
4. `<skill_dir>` is a documentation placeholder, not a Shell variable or executable
   literal. Replace it with the confirmed directory before reading resources or
   executing commands. Pass each complete path as one correctly quoted argument,
   including paths containing spaces. Host path masks are not real directory values
   and must not enter commands or delivery links.
5. Use the harness-confirmed Python 3.9+ interpreter; `python3` below denotes that
   available entrypoint. Read [requirements.txt](requirements.txt): the maintained
   workflow requires only the standard library, with no installation. Preserve
   `-I -S -B`; scripts load bundled modules from their own source directory.

Markdown links remain relative to their containing Markdown file: `references/python-calculation.md`
in this entrypoint resolves under `<skill_dir>/references/`; a sibling link in a reference
resolves in that reference's directory, and `../SKILL.md` resolves to this entrypoint.
Do not resolve internal links against the task directory. ZIP member names, XML relationship
targets and namespace identifiers are format identifiers, not host resource paths; do not
prefix them with `<skill_dir>`. Repository maintenance commands have their own explicitly
stated base directory below.

These are execution instructions for the assistant and harness, not a claim that the CLI
rejects every out-of-bound filesystem path. This section takes precedence over later examples.

Before the first operation, run:

```console
python3 -I -S -B "<skill_dir>/scripts/stdlib_xlsx.py" check
```

Require exit 0 and JSON `status: ready`. This verifies imports, the template and a
limited calculation; it does not verify task data, directory permissions, or Excel
compatibility. Check input readability and, for writes, output-directory writability
with a uniquely named probe created exclusively in each intended output directory,
read back and removed. This includes copied configurations and redirected JSON reports,
even when the underlying command only reads a workbook. Do not use a default system
temporary directory for a business-task probe. Never overwrite an existing probe. Stop dependent
operations if a check fails or its result cannot be confirmed. Report the failed condition;
retry only after the underlying environment issue is resolved, without weakening checks.
Re-read this entrypoint if its constraints are lost after context compaction; retain the
confirmed task directory and filenames. Before delivery, verify the actual output exists
and is nonempty, and use only a harness-supported delivery mechanism for that file. Never
construct delivery links from placeholders, host masks or unverified log output.

## Route the task

`read`, `create`, `edit` and `calculate` below are subcommands of
`<skill_dir>/scripts/stdlib_xlsx.py`, not standalone executables. See the complete
invocations in [python-calculation.md](references/python-calculation.md). In the table,
script locations and subcommands are separate fields of an invocation; quote only the
script path, not the path together with its arguments.

| Task | Action | Reference |
|---|---|---|
| Read a strict BIFF8 record subset, raw dates/caches and merges | `<skill_dir>/scripts/xlsx_biff8.py`; explicit observed-legacy profile, broader ET/ETT unverified | [biff8-readonly.md](references/biff8-readonly.md) |
| Inspect bounded compound-container structure for ET/ETT investigation | `<skill_dir>/scripts/xlsx_cfb.py`; no spreadsheet cell decoding | [et-ett-readonly.md](references/et-ett-readonly.md) |
| Summarize explicit deposit maturity buckets and cumulative windows | `<skill_dir>/scripts/xlsx_maturity.py` | [maturity-distribution.md](references/maturity-distribution.md) |
| Aggregate observed customers, product coverage and two-period sets | `<skill_dir>/scripts/xlsx_customers.py` | [customer-structure.md](references/customer-structure.md) |
| Validate management policy, explicit plan deviation and comparable peers | `<skill_dir>/scripts/xlsx_management_policy.py` | [management-policy.md](references/management-policy.md) |
| Analyze deposit snapshot changes, daily/end differences and explicit targets | `<skill_dir>/scripts/xlsx_deposits.py` | [deposit-analysis.md](references/deposit-analysis.md) |
| Calculate explicit deposit category shares and Top N concentration | `<skill_dir>/scripts/xlsx_deposit_structure.py` | [deposit-analysis.md](references/deposit-analysis.md) |
| Reconcile two comparable management reports and allocate arithmetic changes | `<skill_dir>/scripts/xlsx_reconcile.py`; optional `--xlsx` | [management-reconciliation.md](references/management-reconciliation.md) |
| Build a static management dashboard from a native wide report | `<skill_dir>/scripts/xlsx_dashboard.py`; optional `--xlsx` | [management-dashboard.md](references/management-dashboard.md) |
| Roll leaf institutions up a validated static hierarchy | `<skill_dir>/scripts/xlsx_management.py` with hierarchy config | [institution-hierarchy.md](references/institution-hierarchy.md) |
| Compute explicit daily means or month/YTD daily-flow sums | `<skill_dir>/scripts/xlsx_daily.py`, subcommand `average` / `accumulate` | [daily-analysis.md](references/daily-analysis.md) |
| Build configured management wide reports | `<skill_dir>/scripts/xlsx_management.py`; optional `--xlsx` | [management-wide.md](references/management-wide.md) |
| Export known analysis JSON to readable XLSX | `<skill_dir>/scripts/xlsx_result_export.py` | [result-export.md](references/result-export.md) |
| Rank observations and calculate group shares | `<skill_dir>/scripts/xlsx_rank_share.py` | [rank-share.md](references/rank-share.md) |
| Compute median, quantiles and variance/stddev | `<skill_dir>/scripts/xlsx_advanced.py`, subcommand `describe` | [advanced-statistics.md](references/advanced-statistics.md) |
| Compare explicit monthly periods | `<skill_dir>/scripts/xlsx_advanced.py`, subcommand `compare` | [period-comparison.md](references/period-comparison.md) |
| Match tables by typed composite keys | `<skill_dir>/scripts/xlsx_match.py` | [table-matching.md](references/table-matching.md) |
| Deduplicate and stably sort typed rows | `<skill_dir>/scripts/xlsx_rows.py` | [row-processing.md](references/row-processing.md) |
| Suggest possible header ranges for review | `<skill_dir>/scripts/xlsx_header_candidates.py` | [header-candidates.md](references/header-candidates.md) |
| Reconstruct or select multi-level headers | `<skill_dir>/scripts/xlsx_headers.py`; `--header-rows` in data operations | [multi-level-headers.md](references/multi-level-headers.md) |
| Profile a region, filter or group data | `<skill_dir>/scripts/xlsx_analysis.py`, subcommand `profile` / `summarize` | [data-analysis.md](references/data-analysis.md) |
| Diagnose file contents or ET/ETT candidates | `<skill_dir>/scripts/xlsx_identify.py` | [data-analysis.md](references/data-analysis.md) |
| Import CSV/TSV or export a worksheet/range | `<skill_dir>/scripts/xlsx_tabular.py`, subcommand `import` / `export` | [tabular-interchange.md](references/tabular-interchange.md) |
| Inspect XLSX/XLSM or CSV/TSV; ET/ETT BIFF8 subset read | `read` | [read-analyze.md](references/read-analyze.md) |
| Style a newly created table for readability | `<skill_dir>/scripts/stdlib_xlsx.py`: `create --appearance`; `<skill_dir>/scripts/xlsx_tabular.py`: `import --appearance` | [readable-appearance.md](references/readable-appearance.md) |
| Create a report | JSON specification → `create` | [create.md](references/create.md) |
| Fill or correct existing cells | JSON patch → `edit` | [edit.md](references/edit.md) |
| Compute supported formulas in an existing file | `calculate` to a new file | [python-calculation.md](references/python-calculation.md) |
| Repair a known formula defect | Diagnose intent, then `edit` | [fix.md](references/fix.md) |
| Validate output | Inspect calculation report and preserved content | [validate.md](references/validate.md) |

Read [python-calculation.md](references/python-calculation.md) before any calculation
or writing task. It defines the JSON contract, commands, formula subset and exit codes.
Use [format.md](references/format.md) for optional financial conventions and supported formatting routes.
In maintained tasks, use existing style indices for edits and the template's supported
style indices or [new-workbook appearance presets](references/readable-appearance.md)
for creation; the reference does not authorize direct XML style edits.

## ET/ETT subset reading

The main `read` command now routes `.et`/`.ett` (case-insensitive) to the bundled
CFB/BIFF8 reader. The extension selects a candidate reader; actual CFB structure,
Workbook/Book stream, BOF and every supported record must validate before cells are returned.
Use the default strict mode first. For the exact documented legacy header variant,
explicitly opt in after reviewing [biff8-readonly.md](references/biff8-readonly.md):

```console
python3 -I -S -B "<skill_dir>/scripts/stdlib_xlsx.py" read ./input.et
python3 -I -S -B "<skill_dir>/scripts/stdlib_xlsx.py" read ./input.ett --compatibility legacy_excel_clsid
```

This returns the separate `biff8_readonly_v1` schema with typed cells and diagnostics,
not the OOXML inspection schema. Exit 0 means the documented record subset was extracted;
check issues and unverified formula caches. `native_et_ett_parsed` and
`wps_compatibility_verified` remain false. Unknown/private records and invalid containers
fail without partial cell output. ETT dispatch has synthetic coverage only; no real ETT
sample has been accepted. This route adds no private-record decoding, formula calculation,
ET/ETT editing, or integration with the OOXML analysis APIs. Runtime remains standard-library
only; no converter, third-party parser or application is invoked.

## Calculation policy

- For known business rules, calculate in Python using `Decimal` with an explicit
  rounding policy. Write those results as `value` when a static report is intended.
- For editable reports, use supported `formula` expressions. The engine evaluates
  them and writes both formula and cache. Never accept caller-supplied formula caches.
- `create --values-only` evaluates all formulas first, then writes static values. It
  refuses partial results; explain that these values will not update when inputs change.
- Existing formulas are recalculated only within the documented subset. Unsupported
  formulas and their dependents keep their formulas and lose stale caches. Exit 2
  means incomplete calculation, not a fully verified workbook.
- A known calculation error or circular dependency blocks output. Do not hide errors
  with default zeros or change business rules just to make calculation pass.
- This is Decimal subset calculation, not Excel-equivalent recalculation. Report the
  engine and limits. For unresolved formulas say `部分公式未计算` with affected cells.
- The opened workbook's old caches are unverified; missing caches are not zero.

## Editing and preservation

Never reconstruct an existing workbook from extracted values. `edit` patches the
original package and preserves unmodified ZIP member contents byte-for-byte. Modified
worksheet XML is serialized again; full-file byte identity or arbitrary feature
compatibility is not promised. Formula caches across sheets may change when an input
changes. Calculation-chain metadata may be removed and recalculation requested.

Only cell values, formulas and existing style indices are editable in existing files.
New workbooks may opt into readable appearance presets; these are never applied to edits.
Do not insert/delete/move rows, columns or sheets. Editing a sheet containing shared,
array or other specially attributed formulas is refused. Macro parts can be retained
in XLSM files, but macros are never run. Charts and pivots are retained, not refreshed.
Dates are exposed as stored serials or ISO strings, without display-format conversion.
Signed/encrypted files, chart sheets and strict OOXML are unsupported. The OOXML workflow
rejects legacy XLS/XLSB; the separate BIFF8 reader supports only its documented read-only
record subset and does not enable generic XLS editing or native ET/ETT compatibility.

## Legacy utilities

The older scripts remain for compatibility and reference. They are not the standard
library workflow: `<skill_dir>/scripts/xlsx_reader.py` needs pandas, `<skill_dir>/scripts/libreoffice_recalc.py` needs external
software, and older helpers may require Python 3.10+. Do not invoke legacy utilities for
the maintained workflow. The `<skill_dir>/scripts/xlsx_insert_row.py` and
`<skill_dir>/scripts/xlsx_shift_rows.py` CLI entrypoints are disabled and exit 1 before any writes. A known
formula-reference corruption in these legacy scripts remains unresolved; disabling the
commands does not repair workbooks they may have changed previously. Do not invoke them
for structural edits. `<skill_dir>/scripts/formula_check.py` is heuristic static inspection only; exit 0
does not prove calculation. Legacy examples are historical interface documentation,
not supported task instructions. In particular, `libreoffice_recalc.py` uses a system
temporary directory and external software; it does not satisfy this workflow's task-directory
and runtime contract. Do not invoke it or treat the main self-check as validating it.

## Task intermediate files

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

## Development verification

Maintenance only: run from the confirmed repository root, not the business task
directory. `skills/xlsx/scripts` below is repository-relative and is not an
installation path to copy into task commands. Tests use isolated temporary directories;
this does not authorize business workflows to choose a different temporary root.

```console
python3 -I -S -B -m unittest discover -s skills/xlsx/scripts -p 'test_*.py' -v
```
