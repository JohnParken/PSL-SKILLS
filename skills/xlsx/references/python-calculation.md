# Standard-library calculation and file contract

路径占位符说明：`<skill-root>` 是 XLSX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 XLSX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

不支持读取 WPS 格式（.wps/.et/.ett），请使用 wps skill；本入口明确拒绝，不自动调用或转换。

Follow the path and first-operation rules (`<skill-root>/SKILL.md#执行环境约束与首步自检`)
before using this reference; internal resources and task data have different path bases.

WPS family files are read by the separate `wps` skill. Native spreadsheet commands
refuse `.wps`, `.et`, `.ett` and CFB data; no automatic conversion is performed.

All commands run from the task directory. Replace `<skill-root>` with the confirmed
skill directory. Python 3.9+; no pip packages, external applications or network access.

本页保留旧章节锚点、库API及维护边界；普通创建/编辑使用写入契约（`<skill-root>/references/write-contract.md`），实际公式任务追加公式计算（`<skill-root>/references/formula-calculation.md`），不默认加载本页。

## Contents

- Commands (`<skill-root>/references/python-calculation.md#commands`)
- Input JSON (`<skill-root>/references/python-calculation.md#input-json`)
- Formula subset (`<skill-root>/references/python-calculation.md#formula-subset`)
- Results and exit codes (`<skill-root>/references/python-calculation.md#results-and-exit-codes`)
- Known business computations without Excel formulas (`<skill-root>/references/python-calculation.md#known-business-computations-without-excel-formulas`)

## Commands

当前权威契约已移至 `<skill-root>/references/write-contract.md#commands`；按任务只读该文件，不递归加载其余章节。原CLI/API保持兼容。

## Input JSON

当前权威契约已移至 `<skill-root>/references/write-contract.md#input-json`；按任务只读该文件，不递归加载其余章节。原CLI/API保持兼容。

## Formula subset

当前权威契约已移至 `<skill-root>/references/formula-calculation.md#formula-subset`；按任务只读该文件，不递归加载其余章节。原CLI/API保持兼容。

## Results and exit codes

当前权威契约已移至 `<skill-root>/references/write-contract.md#results-and-exit-codes`；按任务只读该文件，不递归加载其余章节。原CLI/API保持兼容。

## Known business computations without Excel formulas

Use Decimal from strings for currency; explicitly choose rounding timing and policy.
Use `csv`, `collections`, `statistics` and ordinary Python loops for analysis. Write
computed static results through `value`, and explain their non-updating nature.
Do not wrap an arbitrary formula string in Python eval/exec.

The Python API is `Workbook.create(spec, appearance=None)`, `Workbook.load(path)`, `patch(spec)`,
`calculate(values_only=False)`, `save(path)`, `inspect()`. Import `stdlib_xlsx` from the confirmed `<skill-root>/scripts/` directory: in a
task-local helper run with `-I -S -B`, add only that resolved directory to `sys.path`
before importing. Do not import from a guessed task-local directory named `scripts` or set PYTHONPATH.
API data paths use the same task-directory base as CLI data paths. `calculate` returns a report; do not call save if status is failed. It rewrites
formula caches but does not refresh the in-memory inspection index; reopen the saved
file to inspect results. Caller-supplied caches are never accepted through the JSON API.

JSON input deeper than 128 object/array levels returns a structured nesting failure before decoding, independently of CPython recursion behavior. Brackets inside quoted/escaped strings do not count toward this budget. Bounded query
readers also enforce byte limits on actual reads, not only filesystem size metadata.

## Interpreter and test boundaries

The maintained XLSX workflow supports Python 3.9+ using only its standard library.
Its XML entrypoint rejects DTD/entity declarations; it currently has no Expat-version
minimum gate. This differs from the standalone WPS OOXML reader (Expat >=2.6) and
DOCX workflow (Python >=3.10 and Expat >=2.6). Use each skill's own self-check rather
than inferring availability from Python's version alone.

The WPS suite skips OOXML behavior tests on older Expat because that branch is
intentionally unavailable there. A skip is not a successful OOXML extraction test;
binary CFB/BIFF/MS-DOC tests can still run. The XLSX JSON-depth regression is unrelated
to Expat: Python 3.12 can decode nesting that Python 3.9 rejects through RecursionError,
so explicit structural depth checks are required for stable failures on both.
