---
name: xlsx
version: "20261029.2132.0838"
description: 用于读取 XLSX/XLSM、创建 XLSX 报告、修改指定单元格及导入导出 CSV/TSV，支持公式子集计算和显式口径的数据统计。保留并提示未计算公式与读取限制；不支持任意行列或工作表结构编辑、宏执行、图表透视缓存刷新及 XLS/XLSB、WPS 专有格式直接处理。
license: MIT
---

# XLSX — Python Standard Library

路径占位符说明：`<skill-root>` 是 XLSX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 XLSX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

Read XLSX/XLSM, create XLSX reports, patch existing cells, or analyze typed data using Python 3.9+ and only its standard library. No package installation, Office/LibreOffice, external applications or network access. Keep the complete Skill directory: bundled modules are in `<skill-root>/scripts/` and templates in `<skill-root>/assets/minimal_xlsx/`.

原生 XLSX/XLSM 按文件格式处理，包括 WPS 软件保存的文件；`.wps/.et/.ett`（不区分大小写）交由 wps 技能。本技能拒绝这些输入及伪装成 XLSX/XLSM 的 CFB，不自动调用其他技能或转换。可分析 wps 已提取的 JSON，不读取其原件。

## 执行环境约束与首步自检

**任务路径基准：** `./` 就是宿主本次确认的任务工作目录，执行期间保持不变。默认示例中的任务文件直接位于 `./`；仅当宿主附件映射或用户明确给出的真实相对路径含子目录时才按该路径读取，不自行添加 `inputs/`、`data/` 等目录，也不为匹配示例搬移文件或切换工作目录。Skill 脚本及内部资源仍按 `<skill-root>` 定位；由程序管理的归档子目录不是工作目录。

1. Run in the harness-confirmed task working directory. Inputs, outputs, JSON configurations, redirected reports and task temporary files use paths relative to that directory and stay within it or its children. Do not use parent paths, absolute data paths or mount changes to bypass the boundary. If currently in Skill source, have the harness confirm a task directory first; do not change to the installation directory to run examples.
2. Determine `<skill-root>` by removing the filename from the actual `file_path` used to read this SKILL.md; resolve a relative path against that read's confirmed base. If inaccessible or unknown, stop rather than guessing from environment variables, working-directory output, other sessions or host masks. Scripts, references, assets and requirements.txt (`<skill-root>/requirements.txt`) are under that directory; confirmed absolute internal resource paths are allowed. Never assume the task directory contains a directory named `scripts`.
3. `<skill-root>` is a documentation placeholder, not a Shell variable. Replace it before use, passing each complete path as one quoted argument. Internal Markdown references use complete paths rooted at `<skill-root>`, not paths resolved from the current document or task directory. ZIP members, XML targets and namespaces are format identifiers, not host paths. Do not put placeholders or host masks in delivery links. The ten supported file-producing CLIs validate their declared input/configuration/output paths before business processing, including direct invocation; absolute paths, parent traversal and symlink components are rejected. Library APIs and other read-only tools still require the caller to enforce the task boundary; this is not an OS sandbox against hostile concurrent writers.
4. Use the harness-confirmed Python 3.9+ interpreter, denoted by `python3` below. Preserve `-I -S -B`; modules load bundled resources from their own source directory. Before the first operation run:

```console
python3 -I -S -B "<skill-root>/scripts/stdlib_xlsx.py" check
```

5. Require exit 0 and JSON `status: ready`. This checks imports, the template and a limited calculation, not task data, permissions or Excel compatibility. Confirm input readability. For writes, create a unique exclusive probe in each intended task output directory, read it back and remove only that probe; this also applies to configuration copies and redirected reports. Pure reads to stdout need no write probe. Do not use system temporary directories for business-task probes or overwrite existing files.
6. Stop dependent operations if self-check fails, its result is uncertain, or the required marker is absent. Retry only after the environment issue is resolved, without lowering checks. After context compaction re-read this file if constraints are missing; retain confirmed task paths.
7. Before delivery verify the actual file exists and is nonempty; use only a supported harness delivery mechanism, never guessed URLs or unverified log output. Self-check is not result acceptance. File-producing CLIs now append `delivery` archive results; stdout-only reads do not. For owned JSON task configs and cleanup, follow delivery lifecycle (`<skill-root>/references/delivery-lifecycle.md`).

Self-check reporting: `check` keeps its single JSON stdout and original `ready`/`failed`/`unsupported` statuses and exit codes; it adds `check_kind: environment` and `artifact_validation: not_run` on handled outcomes. It does not create or accept a workbook. Creation/calculation reports keep their existing contract, and `ready` must never be treated as `output_written`.

The harness must return this invocation's stdout, stderr and actual exit code together with an invocation identifier, including nonzero exits and empty channels. A library return or captured subprocess result is not a model-visible tool result until the harness returns it. Missing execution, timeout or signal termination must be explicit, with no invented normal exit code; truncation must identify the channel and known length. Missing or truncated evidence is unconfirmed, not success. Preserve JSON stdout without added prose; result acceptance still follows validation (`<skill-root>/references/validate.md`).

## 工具调用稳定性

简单JSON（短文本、少量字段、浅层结构）可直接写；复杂JSON（长正文、多层对象或数组、表格批量数据、大量引号或反斜杠）必须用Python构造dict/list，再用json.dumps(..., ensure_ascii=False, allow_nan=False)序列化，不手工拼接JSON。Python语法与业务字段仍需核对，序列化成功不等于业务验收通过。 使用已验证的Python调用模板（`<skill-root>/references/complex-json.md`），默认源码及JSON均走stdin，不落盘额外文件。

执行前核对真实工具schema、非空命令及完整JSON；语法错误改正后才重试，同一call_id重复错误不触发重发。临时配置只用任务根`./`，不写`/tmp`。退出3先读取完整结果，区分业务与清理，不重算掩盖残留。`/large_tool_results/`下的结果文件必须使用`read_file`工具读取，不当作execute可见路径。故障处理与宿主要求见工具调用与结果读取（`<skill-root>/references/tool-execution.md`）。

## Route the task

Read only the selected reference. `read`, `create`, `edit` and `calculate` are subcommands of `<skill-root>/scripts/stdlib_xlsx.py`, not standalone executables. Quote the script path separately from arguments.

| Task | Action | Read when needed |
|---|---|---|
| Read XLSX/XLSM or CSV/TSV | `"<skill-root>/scripts/stdlib_xlsx.py" read` | Read and inspect (`<skill-root>/references/read-analyze.md`) |
| Profile, filter or group a region; identify format | `"<skill-root>/scripts/xlsx_analysis.py" profile` / `summarize`; `<skill-root>/scripts/xlsx_identify.py` | Analysis and identification (`<skill-root>/references/data-analysis.md`) |
| Analyze WPS extraction JSON | `"<skill-root>/scripts/xlsx_analysis.py" profile` / `summarize` | WPS result contract (`<skill-root>/references/data-analysis.md#analyze-a-wps-result-without-conversion`) |
| Import/export CSV/TSV | `"<skill-root>/scripts/xlsx_tabular.py" import` / `export` | Tabular interchange (`<skill-root>/references/tabular-interchange.md`) |
| Create a report | stdin task specification → `<skill-root>/scripts/run_xlsx_task.py`; existing `"<skill-root>/scripts/stdlib_xlsx.py" create` remains supported | Create (`<skill-root>/references/create.md`) |
| Correct existing cells | JSON patch → `"<skill-root>/scripts/stdlib_xlsx.py" edit` | Edit and preservation (`<skill-root>/references/edit.md`) |
| Compute supported formulas | `"<skill-root>/scripts/stdlib_xlsx.py" calculate` to a new file | Calculation contract (`<skill-root>/references/python-calculation.md`) |
| Fix a known formula defect | Diagnose intent, then `edit` | Fix (`<skill-root>/references/fix.md`) |
| Validate an output | Inspect stored data, calculation report and preserved content | Validation (`<skill-root>/references/validate.md`) |

For a first read after self-check:

```console
python3 -I -S -B "<skill-root>/scripts/stdlib_xlsx.py" read ./input.xlsx
```

Before any writing or calculation, read the JSON/CLI contract (`<skill-root>/references/python-calculation.md`). New-table appearance is optional and creation-only: appearance presets (`<skill-root>/references/readable-appearance.md`); existing-file edits use existing style indices. Use financial formatting conventions (`<skill-root>/references/format.md`) only when requested. Banking reports, matching, grouping and multi-level headers route through the specialized analysis directory (`<skill-root>/references/data-analysis.md#specialized-task-directory`).

## Interpret results and keep boundaries

- Inspect structure, cell types, missing values and user-selected ranges before analysis. Numeric strings with type tags are not text identifiers; use `Decimal` for known business rules and explicit rounding. Old formula caches are unverified; missing caches are never zero.
- WPS JSON analysis accepts `biff8_readonly_v1` and `ooxml_spreadsheet_readonly_v1`, preserves exact values, source status/issues and optional source coverage/assessment. The source assessment describes extraction scope, not completeness of the selected analysis or cache freshness. See the source-result contract (`<skill-root>/references/data-analysis.md#analyze-a-wps-result-without-conversion`).
- Calculation is a Decimal formula subset, not Excel-equivalent recalculation. Editable reports use supported formulas; never accept caller-supplied caches. `create --values-only` evaluates everything first and refuses partial output; disclose that static values will not update.
- Existing-file calculations remove unresolved stale caches and report incomplete/exit 2 as `部分公式未计算`. Known errors or cycles block output. Do not hide errors with zeros or change business rules to force success; see result codes (`<skill-root>/references/python-calculation.md#results-and-exit-codes`).
- Never rebuild an existing workbook from extracted values. Edit only values, formulas and existing style indices; no row/column/sheet insertion, deletion or movement. Shared/array/specially attributed formula sheets are refused for edits. Untouched ZIP member content is preserved; changed XML is reserialized, formula caches and calculation-chain metadata may change. No full-file identity or arbitrary compatibility guarantee.
- XLSM macros are retained, never executed; chart/pivot caches are retained, never refreshed. Dates remain stored serials or ISO text. Signed/encrypted files, chart sheets, strict OOXML and native XLS/XLSB are unsupported. The wps skill's documented read-only record subset does not enable generic binary spreadsheet editing.
- Do not run legacy utilities or install their dependencies as fallbacks. Legacy limitations and disabled row-edit commands are documented under maintenance/legacy boundaries (`<skill-root>/references/validate.md#legacy-utilities`). Development tests and intermediate-file cleanup are also conditional sections there, not extra steps for a simple read.

## 文件交付与中间文件收尾

所有受支持的写文件CLI在主产物写入后自动归档，新增 `delivery` 字段，原status、output、退出码和已有输出保护不变。先按原状态验收业务结果；部分计算仍为incomplete/退出2，归档完成不代表公式完整。只读stdout不创建归档或清理清单。

推荐对需要临时JSON配置的任务使用 `<skill-root>/scripts/run_xlsx_task.py`：stdin提供原CLI参数和具名配置，创建前登记本轮专属JSON；子进程业务成功且归档步骤返回后自动seal/clean；归档失败或跳过也清理，核对后删除本次清单，临时JSON均平铺于任务根。业务失败、部分计算、子进程超时或退出异常也清理配置及清单，保留原退出码；不自动覆盖已有输出重跑。无临时配置时不建立无用清单。它不运行用户Python脚本，不认领旧CLI传入的既存JSON；其他自定义辅助文件仍须创建前通过 `"<skill-root>/scripts/xlsx_lifecycle.py" plan` 显式登记。完整命令、入口覆盖、恢复及限制见交付生命周期 (`<skill-root>/references/delivery-lifecycle.md`)。

用户归档提示根据完整 `delivery` 结果按归档用户话术 (`<skill-root>/references/delivery-lifecycle.md#归档用户话术`)映射，保留原 `delivery.archive_message` 机器文案而不逐字转贴，不展示内部路径、目录、ID或原始配置日志；正常主文件链接保留。归档失败保留主产物，不为此重跑计算；归档、业务计算与清理状态分别报告。

## 最终清理原则

任务结束时必须清理本轮全部中间文件，包括脚本、配置、回读结果、日志、暂存、锁、回执和清单；仅将当前与历史交付物作为任务生成的留存文件。用户原件与其他任务文件仍受保护，不属于可删除中间文件。创建前完整登记，普通内部暂存由创建函数收尾；若删除失败、归属不明或强杀留下未确认残留，报告收尾未完成，不声称只剩交付物，也不扫描删除未知文件。生成或编辑失败同样立即清理本轮临时文件和清单，不因可能重试而保留；需要重试时从原始输入重新创建独占任务。删除受阻、锁仍被占用或归属无法确认时报告cleanup_incomplete，处理阻碍后继续清理，不能宣称已完成。

若`read_file`不可用或读取失败，报告具体缺口并停止依赖该结果的操作；不得改用execute、Python或Shell读取`/large_tool_results/`，也不得猜测替代路径。工具参数遵循实际schema，不虚构参数名。
