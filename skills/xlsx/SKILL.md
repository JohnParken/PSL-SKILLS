---
name: xlsx
version: "20261027.2320.5486"
description: 用于读取 XLSX/XLSM、创建表格报告、受控修改单元格及交换 CSV/TSV，使用纯 Python 标准库。支持公式子集计算、合并表头重建和显式配置的数据统计分析；.wps/.et/.ett 读取使用 wps 技能，不支持任意结构编辑或办公软件排版还原。
license: MIT
---

# XLSX — Python Standard Library

Read XLSX/XLSM, create XLSX reports, patch existing cells, or analyze typed data using Python 3.9+ and only its standard library. No package installation, Office/LibreOffice, external applications or network access. Keep the complete Skill directory: bundled modules are in `<skill_dir>/scripts/` and templates in `<skill_dir>/assets/minimal_xlsx/`.

原生 XLSX/XLSM 按文件格式处理，包括 WPS 软件保存的文件；`.wps/.et/.ett`（不区分大小写）交由 wps 技能。本技能拒绝这些输入及伪装成 XLSX/XLSM 的 CFB，不自动调用其他技能或转换。可分析 wps 已提取的 JSON，不读取其原件。

## 执行环境约束与首步自检

1. Run in the harness-confirmed task working directory. Inputs, outputs, JSON configurations, redirected reports and task temporary files use paths relative to that directory and stay within it or its children. Do not use parent paths, absolute data paths or mount changes to bypass the boundary. If currently in Skill source, have the harness confirm a task directory first; do not change to the installation directory to run examples.
2. Determine `<skill_dir>` by removing the filename from the actual `file_path` used to read this SKILL.md; resolve a relative path against that read's confirmed base. If inaccessible or unknown, stop rather than guessing from environment variables, working-directory output, other sessions or host masks. Scripts, references, assets and [requirements.txt](requirements.txt) are under that directory; confirmed absolute internal resource paths are allowed. Never assume task-directory `scripts/` exists.
3. `<skill_dir>` is a documentation placeholder, not a Shell variable. Replace it before use, passing each complete path as one quoted argument. Markdown links resolve relative to their containing file, not the task directory. ZIP members, XML targets and namespaces are format identifiers, not host paths. Do not put placeholders or host masks in delivery links. These are assistant/harness rules, not a claim that every CLI enforces all path boundaries.
4. Use the harness-confirmed Python 3.9+ interpreter, denoted by `python3` below. Preserve `-I -S -B`; modules load bundled resources from their own source directory. Before the first operation run:

```console
python3 -I -S -B "<skill_dir>/scripts/stdlib_xlsx.py" check
```

5. Require exit 0 and JSON `status: ready`. This checks imports, the template and a limited calculation, not task data, permissions or Excel compatibility. Confirm input readability. For writes, create a unique exclusive probe in each intended task output directory, read it back and remove only that probe; this also applies to configuration copies and redirected reports. Pure reads to stdout need no write probe. Do not use system temporary directories for business-task probes or overwrite existing files.
6. Stop dependent operations if self-check fails, its result is uncertain, or the required marker is absent. Retry only after the environment issue is resolved, without lowering checks. After context compaction re-read this file if constraints are missing; retain confirmed task paths.
7. Before delivery verify the actual file exists and is nonempty; use only a supported harness delivery mechanism, never guessed URLs or unverified log output. Self-check is not result acceptance. For tasks that create intermediates, follow [cleanup and preservation](references/validate.md#task-intermediate-files).

## Route the task

Read only the selected reference. `read`, `create`, `edit` and `calculate` are subcommands of `<skill_dir>/scripts/stdlib_xlsx.py`, not standalone executables. Quote the script path separately from arguments.

| Task | Action | Read when needed |
|---|---|---|
| Read XLSX/XLSM or CSV/TSV | `stdlib_xlsx.py read` | [Read and inspect](references/read-analyze.md) |
| Profile, filter or group a region; identify format | `xlsx_analysis.py profile` / `summarize`; `xlsx_identify.py` | [Analysis and identification](references/data-analysis.md) |
| Analyze WPS extraction JSON | `xlsx_analysis.py profile` / `summarize` | [WPS result contract](references/data-analysis.md#analyze-a-wps-result-without-conversion) |
| Import/export CSV/TSV | `xlsx_tabular.py import` / `export` | [Tabular interchange](references/tabular-interchange.md) |
| Create a report | JSON specification → `stdlib_xlsx.py create` | [Create](references/create.md) |
| Correct existing cells | JSON patch → `stdlib_xlsx.py edit` | [Edit and preservation](references/edit.md) |
| Compute supported formulas | `stdlib_xlsx.py calculate` to a new file | [Calculation contract](references/python-calculation.md) |
| Fix a known formula defect | Diagnose intent, then `edit` | [Fix](references/fix.md) |
| Validate an output | Inspect stored data, calculation report and preserved content | [Validation](references/validate.md) |

For a first read after self-check:

```console
python3 -I -S -B "<skill_dir>/scripts/stdlib_xlsx.py" read ./input.xlsx
```

Before any writing or calculation, read [the JSON/CLI contract](references/python-calculation.md). New-table appearance is optional and creation-only: [appearance presets](references/readable-appearance.md); existing-file edits use existing style indices. Use [financial formatting conventions](references/format.md) only when requested. Banking reports, matching, grouping and multi-level headers route through the [specialized analysis directory](references/data-analysis.md#specialized-task-directory).

## Interpret results and keep boundaries

- Inspect structure, cell types, missing values and user-selected ranges before analysis. Numeric strings with type tags are not text identifiers; use `Decimal` for known business rules and explicit rounding. Old formula caches are unverified; missing caches are never zero.
- WPS JSON analysis accepts `biff8_readonly_v1` and `ooxml_spreadsheet_readonly_v1`, preserves exact values, source status/issues and optional source coverage/assessment. The source assessment describes extraction scope, not completeness of the selected analysis or cache freshness. See [the source-result contract](references/data-analysis.md#analyze-a-wps-result-without-conversion).
- Calculation is a Decimal formula subset, not Excel-equivalent recalculation. Editable reports use supported formulas; never accept caller-supplied caches. `create --values-only` evaluates everything first and refuses partial output; disclose that static values will not update.
- Existing-file calculations remove unresolved stale caches and report incomplete/exit 2 as `部分公式未计算`. Known errors or cycles block output. Do not hide errors with zeros or change business rules to force success; see [result codes](references/python-calculation.md#results-and-exit-codes).
- Never rebuild an existing workbook from extracted values. Edit only values, formulas and existing style indices; no row/column/sheet insertion, deletion or movement. Shared/array/specially attributed formula sheets are refused for edits. Untouched ZIP member content is preserved; changed XML is reserialized, formula caches and calculation-chain metadata may change. No full-file identity or arbitrary compatibility guarantee.
- XLSM macros are retained, never executed; chart/pivot caches are retained, never refreshed. Dates remain stored serials or ISO text. Signed/encrypted files, chart sheets, strict OOXML and native XLS/XLSB are unsupported. The wps skill's documented read-only record subset does not enable generic binary spreadsheet editing.
- Do not run legacy utilities or install their dependencies as fallbacks. Legacy limitations and disabled row-edit commands are documented under [maintenance/legacy boundaries](references/validate.md#legacy-utilities). Development tests and intermediate-file cleanup are also conditional sections there, not extra steps for a simple read.
