---
name: xlsx
version: "20261105.1736.5039"
description: 用于读取 XLSX/XLSM、创建 XLSX 报告、修改指定单元格及导入导出 CSV/TSV，支持公式子集计算和显式口径的数据统计。保留并提示未计算公式与读取限制；不支持任意行列或工作表结构编辑、宏执行、图表透视缓存刷新及 XLS/XLSB、WPS 专有格式直接处理。
license: MIT
---

# XLSX — Python Standard Library

路径占位符说明：`<skill-root>` 是 XLSX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 XLSX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

Read XLSX/XLSM, create XLSX reports, patch existing cells, or analyze typed data using Python 3.9+ and only its standard library. No package installation, Office/LibreOffice, external applications or network access. Keep the complete Skill directory: bundled modules are in `<skill-root>/scripts/` and templates in `<skill-root>/assets/minimal_xlsx/`.

原生 XLSX/XLSM 按文件格式处理，包括 WPS 软件保存的文件；`.wps` 交由 wps 技能，`.et/.ett` 交由 et 技能（不区分大小写）。本技能拒绝这些输入及伪装成 XLSX/XLSM 的 CFB，不自动调用其他技能或转换。可分析 et 已提取的受支持 JSON，不读取其原件。

用户同时提供多个来源和目标模板、需要先识别指标口径再计算填报时，使用独立的 table-workflow 技能编排；简单创建、已知单元格修改或本技能现有分析不增加该流程。新编排使用稳定CLI；仅迁移旧内存调用时读 `<skill-root>/references/workflow-api.md`，普通 CLI 任务不加载。

维护CLI或接入跨技能编排时，按需读取 `<skill-root>/scripts/docs/cli-contract.md` 的输入输出、接口版本与兼容策略；普通任务不预读维护契约。回归脚本集中在 `<skill-root>/scripts/test/`。

## 执行环境约束与首步自检

**用户文件定位：** 用户上传文件及用户要求读取的交付物，默认以宿主确认的任务工作目录为基准，按 `./文件路径` 查找；文件路径含子目录时使用已确认的相对路径。优先采用宿主提供的附件映射或用户明确指定的路径；映射缺失、文件不可访问或目标不明确时如实说明并按需询问，不猜测其他路径或自行搜索其他目录。此规则不授权扫描或读取未指定的历史归档。

**任务路径基准：** `./` 就是宿主本次确认的任务工作目录，执行期间保持不变。默认示例中的任务文件直接位于 `./`；仅当宿主附件映射或用户明确给出的真实相对路径含子目录时才按该路径读取，不自行添加 `inputs/`、`data/` 等目录，也不为匹配示例搬移文件或切换工作目录。Skill 脚本及内部资源仍按 `<skill-root>` 定位；由程序管理的归档子目录不是工作目录。

1. Run in the harness-confirmed task working directory. Inputs, outputs, JSON configurations, redirected reports and task temporary files use paths relative to that directory and stay within it or its children. Do not use parent paths, absolute data paths or mount changes to bypass the boundary. If currently in Skill source, have the harness confirm a task directory first; do not change to the installation directory to run examples.
2. Determine `<skill-root>` by removing the filename from the actual `file_path` used to read this SKILL.md; resolve a relative path against that read's confirmed base. If inaccessible or unknown, stop rather than guessing from environment variables, working-directory output, other sessions or host masks. Scripts, references, assets and requirements.txt (`<skill-root>/requirements.txt`) are under that directory; confirmed absolute internal resource paths are allowed. Never assume the task directory contains a directory named `scripts`.
3. `<skill-root>` is a documentation placeholder, not a Shell variable. Replace it before use, passing each complete path as one quoted argument. Internal Markdown references use complete paths rooted at `<skill-root>`, not paths resolved from the current document or task directory. ZIP members, XML targets and namespaces are format identifiers, not host paths. Do not put placeholders or host masks in delivery links. The ten supported file-producing CLIs validate their declared input/configuration/output paths before business processing, including direct invocation; absolute paths, parent traversal and symlink components are rejected. Library APIs and other read-only tools still require the caller to enforce the task boundary; this is not an OS sandbox against hostile concurrent writers.
4. Use the harness-confirmed Python 3.9+ interpreter, denoted by `python3` below. Preserve `-I -S -B`; modules load bundled resources from their own source directory. Before the first operation run:

```console
python3 -I -S -B "<skill-root>/scripts/xlsx_cli.py" run check
```

5. Require exit 0 and JSON `status: ready`. This checks imports, the template and expected results for rounding, conditional/product sums, counting and empty-string formulas, not task data, permissions or Excel compatibility. Confirm input readability. For writes, create a unique exclusive probe in each intended task output directory, read it back and remove only that probe; this also applies to configuration copies and redirected reports. Pure reads to stdout need no write probe. Do not use system temporary directories for business-task probes or overwrite existing files.
6. Stop dependent operations if self-check fails, its result is uncertain, or the required marker is absent. Retry only after the environment issue is resolved, without lowering checks. After context compaction re-read this file if constraints are missing; retain confirmed task paths.
7. Before delivery verify the actual file exists and is nonempty; use only a supported harness delivery mechanism, never guessed URLs or unverified log output. Self-check is not result acceptance. File-producing CLIs now append `delivery` archive results; stdout-only reads do not. For owned JSON task configs and cleanup, follow delivery lifecycle (`<skill-root>/references/delivery-lifecycle.md`).

自检的JSON与宿主结果缺失、截断或失败回传需要排查时，再读自检与回传（`<skill-root>/references/environment-results.md`）。自检不代表产物验收，非零退出或缺少ready时停止依赖操作；缺失/截断结果不得当作成功。

## 工具调用稳定性

只读stdout不加载写入JSON模板。构造复杂写入规格时才读 `<skill-root>/references/complex-json.md`：用Python dict/list和标准JSON序列化，不手拼、不落盘额外脚本；业务字段与实际工具schema仍须核对。

执行前核对真实工具schema、非空命令及完整JSON；语法错误改正后才重试，同一call_id重复错误不触发重发。临时配置只用任务根`./`，不写`/tmp`。退出3先读取完整结果，区分业务与清理，不重算掩盖残留。`/large_tool_results/`下的结果文件必须使用`read_file`工具读取，不当作execute可见路径。故障处理与宿主要求见工具调用与结果读取（`<skill-root>/references/tool-execution.md`）。 read_file不可用或读取失败时停止依赖，不用execute/Python/Shell绕过；不得虚构参数名。

## 按需加载与任务切换

同一任务只复用已确认仍在上下文、且真实路径/版本或指纹未变的参考；不要因链接主动递归预读或重复读取。环境、任务或版本变化、上下文缺失时重新核对必要约束。宿主是否预加载、缓存、截断或计费由宿主决定，本说明不能强制去重；复用参考不等于复用其他任务的素材或授权。脚本按已知命令执行，不为调用而先读实现；只有维护或明确排障需要时读取源码。

## Route the task

Read only the selected reference. `read`, `create`, `edit` and `calculate` are subcommands of `<skill-root>/scripts/stdlib_xlsx.py`, not standalone executables. Quote the script path separately from arguments.

| Task | Action | Read when needed |
|---|---|---|
| Read XLSX/XLSM or CSV/TSV | `"<skill-root>/scripts/xlsx_cli.py" run read` | Read and inspect (`<skill-root>/references/read-analyze.md`) |
| Profile, filter or group a region; identify format | `"<skill-root>/scripts/xlsx_analysis.py" profile` / `summarize`; `<skill-root>/scripts/xlsx_identify.py` | Analysis and identification (`<skill-root>/references/data-analysis.md`) |
| Analyze a formula table using freshly computed values | `"<skill-root>/scripts/xlsx_analysis.py" profile` / `summarize` with `--formulas calculate` | Analysis (`<skill-root>/references/data-analysis.md`) and formula subset (`<skill-root>/references/formula-calculation.md`) |
| Analyze WPS extraction JSON | `"<skill-root>/scripts/xlsx_analysis.py" profile` / `summarize` | WPS result contract (`<skill-root>/references/data-analysis.md#analyze-a-wps-result-without-conversion`) |
| Import/export CSV/TSV | `<skill-root>/scripts/run_xlsx_task.py` wrapping `xlsx_tabular.py import` / `export` | Tabular interchange (`<skill-root>/references/tabular-interchange.md`) |
| Create a report | stdin task specification → `<skill-root>/scripts/run_xlsx_task.py`; existing `"<skill-root>/scripts/xlsx_cli.py" run create` remains supported | Create (`<skill-root>/references/create.md`) |
| Correct existing cells | `<skill-root>/scripts/run_xlsx_task.py` wrapping `stdlib_xlsx.py edit` | Edit and preservation (`<skill-root>/references/edit.md`) |
| Compute supported formulas | `<skill-root>/scripts/run_xlsx_task.py` wrapping `stdlib_xlsx.py calculate` to a new file | 写入与结果契约（`<skill-root>/references/write-contract.md`）及公式计算（`<skill-root>/references/formula-calculation.md`） |
| Fix a known formula defect | Diagnose intent, then `edit` | Fix (`<skill-root>/references/fix.md`) |
| Validate an output | Inspect stored data, calculation report and preserved content | Validation (`<skill-root>/references/validate.md`) |

For a first read after self-check:

```console
python3 -I -S -B "<skill-root>/scripts/xlsx_cli.py" run read ./input.xlsx
```

概况用于选表，分页用于抽查；全量统计优先直接profile/summarize指定区域，不先逐页搬运整表。大表读取先按需用概况、选表/区域及分页（`<skill-root>/references/bounded-reading.md`）；旧默认完整读取不变，新视图不是完整数据或旧分析schema。

写入前读取共同规格/结果（`<skill-root>/references/write-contract.md`）及对应任务说明；仅创建/修改公式、显式计算或排查公式issues时加载公式子集（`<skill-root>/references/formula-calculation.md`）。值编辑也会触发全簿公式计算，仍检查所有issues与退出码。仅读取/统计不加载写入或公式参考，引用不递归加载；宿主不支持章节读取时只读所选专项文件。 New-table appearance is optional and creation-only: appearance presets (`<skill-root>/references/readable-appearance.md`); existing-file edits use existing style indices. Use financial formatting conventions (`<skill-root>/references/format.md`) only when requested. Banking reports, matching, grouping and multi-level headers route through the specialized analysis directory (`<skill-root>/references/data-analysis.md#specialized-task-directory`).

## 核心结果与安全边界

- 缓存可能过时或缺失，不当作零或已重算；数字字符串与文本编号分开解释。分析前确认工作表、区域、类型及用户口径，不猜业务精度。
- 公式仅为支持子集，不等同Excel；incomplete/退出2必须报告“部分公式未计算”，failed/退出1不能交付为计算成功。不用零值掩盖错误。
- 金额/复杂汇总先确认统计区域、明细与合计行、单位、筛选条件、缺失值及舍入时点；不要将页面预览或分页抽样当全量数据。用确定性脚本执行，按数据分析说明核对笔数与金额勾稽；成功退出不证明业务口径正确。缓存参与的结果继续标记未验证。
- 编辑仅限指定单元格值、公式及现有样式，不重建既有工作簿、不进行任意行列或工作表结构编辑；未改ZIP部件保持内容字节，输入和非目标内容受保护。
- 不支持签名/加密、图表工作表、Strict OOXML及XLS/XLSB；共享/数组/特殊属性公式工作表不用于编辑。不执行宏或刷新图表/透视缓存，不自动运行legacy工具、安装依赖或回退转换。不承诺完整OOXML、字体、视觉排版或公式等价性。
- 写入/计算前按路由读取任务契约；涉及WPS提取JSON、日期、宏保留、XML生成约定或编辑兼容边界时，读结果解释（`<skill-root>/references/result-boundaries.md`）中对应条目，不默认加载其余部分。

## 文件交付与中间文件收尾

所有受支持的写文件CLI在主产物写入后自动归档，新增 `delivery` 字段，原status、output、退出码和已有输出保护不变。先按原状态验收业务结果；部分计算仍为incomplete/退出2，归档完成不代表公式完整。只读stdout不创建归档或清理清单。

所有文件交付优先使用 `<skill-root>/scripts/run_xlsx_task.py`，写入前按任务路由读对应说明，并读取写文件收尾（`<skill-root>/references/task-delivery.md`）。以返回的实际output交付，归档失败不重算、不回滚主文件，业务、归档和清理状态分别报告。只读stdout不加载写入收尾说明。用户话术按实际delivery结果映射，不展示内部归档路径、ID或日志，不虚构下载能力。

## 最终清理原则

任务结束时必须清理本轮全部中间文件，包括脚本、配置、回读结果、日志、暂存、锁、回执和清单；仅将当前与历史交付物作为任务生成的留存文件。用户原件与其他任务文件仍受保护，不属于可删除中间文件。创建前完整登记，普通内部暂存由创建函数收尾；若删除失败、归属不明或强杀留下未确认残留，报告收尾未完成，不声称只剩交付物，也不扫描删除未知文件。生成或编辑失败同样立即清理本轮临时文件和清单，不因可能重试而保留；需要重试时从原始输入重新创建独占任务。删除受阻、锁仍被占用或归属无法确认时报告cleanup_incomplete，处理阻碍后继续清理，不能宣称已完成。
