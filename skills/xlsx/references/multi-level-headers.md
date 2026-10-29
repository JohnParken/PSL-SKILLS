# Explicit multi-level headers

路径占位符说明：`<skill-root>` 是 XLSX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 XLSX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

Follow the path and first-operation rules (`<skill-root>/SKILL.md#执行环境约束与首步自检`)
before using this reference; internal resources and task data have different path bases.

Run the entrypoint self-check (`<skill-root>/SKILL.md#执行环境约束与首步自检`) first; require exit 0
and `status: ready`. Confirm the task directory and input readability. Resolve
`<skill-root>` from the actual SKILL.md path, replace it before execution, and use relative
task input paths. Python 3.9+, standard library only; no external application is used.
The commands read workbook contents and emit JSON; they do not write or alter the input.

## Contents

- Declare the header rows (`<skill-root>/references/multi-level-headers.md#declare-the-header-rows`)
- Select columns in data operations (`<skill-root>/references/multi-level-headers.md#select-columns-in-data-operations`)
- Safety boundaries (`<skill-root>/references/multi-level-headers.md#safety-boundaries`)

## Declare the header rows

```console
python3 -I -S -B "<skill-root>/scripts/xlsx_headers.py" report.xlsx --sheet 经营表 --header-rows 3:5
python3 -I -S -B "<skill-root>/scripts/xlsx_analysis.py" profile report.xlsx --sheet 经营表 --header-rows 3:5
```

`--header-rows 3:5` is an inclusive worksheet row range, not a count. It must lie fully
inside the selected `--range`. Data begins at row 6; earlier title and note rows are
ignored. Choose an explicit range when stored cells do not cover the intended table.
`--header-exclude 4` removes an internal note/header row from column paths. Exclusions
must lie inside the declared header rows, and at least one header row must remain.
`--no-header` conflicts with `--header-rows`. Existing single-row behavior is preserved
when no explicit header range is supplied. Candidate suggestions (`<skill-root>/references/header-candidates.md`)
are available separately and are never applied automatically.

Each column returns `id` (absolute coordinate ID such as `col:B`, scoped to this sheet),
`column`, a structured typed `path` array, and one `levels` entry per declared row.
Levels retain original coordinate, source anchor, merge reference, propagated flag,
role and typed value. Vertical merges contribute their anchor once to the path;
ordinary repeated labels at different levels remain separate components. Empty cells
remain explicit blank path components; no ordinary blank is forward-filled.

For a three-row header with B3:E3="2024年", B4:C4="销售", D4:E4="风控",
B5="金额", C5="笔数", D5="金额", E5="笔数", the paths are:

- B: `["2024年", "销售", "金额"]`
- C: `["2024年", "销售", "笔数"]`
- D: `["2024年", "风控", "金额"]`
- E: `["2024年", "风控", "笔数"]`

These are value illustrations; actual output components retain explicit type tags.
A vertically merged A3:A5="部门" produces one path component and three source levels.
This supports differing header depths without inventing missing parent labels.

A single-row horizontal merge spanning the entire selected width is recorded as an
ambiguous `context_headings` candidate and omitted from paths by default. This avoids
attaching a report title to every column. `--include-spanning-headings` explicitly includes
such headings when the user confirms they are column hierarchy. This is a structural
policy, not semantic title recognition. Ordinary unmerged title cells cannot be inferred:
exclude them explicitly. Other parent labels propagate only within actual merged cells.

## Select columns in data operations

All grouping, measure, filter, deduplication and sort positions accept a column letter,
`"col:D"`, `{"id":"col:D"}`, or `{"path":["2024年","风控","金额"]}`.
Path matching requires explicit `--header-rows` and matches the full typed path exactly;
there is no partial/leaf-name matching or string-joined identifier. Zero or multiple
matches fail. Use an absolute ID for duplicate full paths. Blank path components use
null; numeric 2024 differs from text "2024". A typed component from the output may also
be supplied, e.g. `{"type":"number","value":"2024"}` or an ISO `date_text` component.

Example task-local `groups.json`:

```json
{"group_by":[{"path":["部门"]}],
 "measure":{"path":["2024年","风控","金额"]},
 "filters":[{"column":{"path":["2024年","销售","金额"]},"op":"ge","value":10}]}
```

```console
python3 -I -S -B "<skill-root>/scripts/xlsx_analysis.py" summarize report.xlsx --sheet 经营表 --header-rows 3:5 --query groups.json
python3 -I -S -B "<skill-root>/scripts/xlsx_rows.py" report.xlsx --sheet 经营表 --header-rows 3:5 --query rows.json
```

For `rows.json`, use e.g. `{"deduplicate":{"columns":[{"path":["部门"]}]},
"sort":[{"column":{"id":"col:D"},"descending":true}]}`.
See Read-only data analysis and file identification (`<skill-root>/references/data-analysis.md`) and Filter, deduplicate and sort typed rows (`<skill-root>/references/row-processing.md`) for
unchanged numeric, filter, deduplication and sort semantics. Outputs include the header
schema alongside results; row outputs retain original worksheet row numbers.

## Safety boundaries

Reject header merges crossing the declared header boundaries, including merges reaching
into data, or crossing a selected column boundary. Enlarge the range to include complete
header merges; do not silently clip them or use anchors outside the declared header.
Reject malformed/reversed/single-cell merge ranges, overlapping header merges, and
non-anchor header cells containing values/formulas. Malformed merge references anywhere
in the worksheet fail because their affected area cannot be trusted. Overlap checks are
limited to merges touching the selected header. Data-only merges remain unexpanded.

Formulas/errors in selected header sources follow the existing policy: formulas require
`--formulas cached`, missing caches and Excel error cells fail. Cached values remain
unverified, including formula titles/excluded header levels; each anchor is counted once.
Ignored rows before the declared header are not evaluated. Data rows follow the same
cache checks before filtering. Dates, numeric text and precision retain existing types.

Bounds remain 200,000 selected cells and 10,000 merge entries. No font/layout-based
inference, arbitrary blank propagation, split-table discovery, nested table discovery,
reliable automatic header detection or real Excel/WPS rendering is claimed. CLI exit 0
means successful reconstruction/operation; exit 1 returns JSON `status: failed`.
