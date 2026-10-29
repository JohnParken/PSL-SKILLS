# Readable appearance for newly created workbooks

路径占位符说明：`<skill-root>` 是 XLSX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 XLSX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

Follow the path and first-operation rules (`<skill-root>/SKILL.md#执行环境约束与首步自检`)
before using this reference; internal resources and task data have different path bases.

Run the entrypoint self-check (`<skill-root>/SKILL.md#执行环境约束与首步自检`), requiring exit 0 and
`status: ready`. Confirm the task directory, input readability and output-directory write
probe. Resolve `<skill-root>` from the actual SKILL.md path and replace it before executing
commands with task-relative data paths. Python 3.9+, standard library only. No renderer,
Office automation, installation, network or upload is used. Existing outputs are refused.

Create task-local `appearance.json`:

```json
{
  "preset":"readable",
  "sheets":{"Sales":{
    "header_rows":"1:2",
    "merges":["A1:A2","B1:C1"],
    "alternate_rows":true,
    "category_columns":["A"],
    "freeze_headers":true,
    "borders":true,
    "wrap_headers":true,
    "wrap_text":false,
    "header_alignment":"center",
    "body_alignment":"general",
    "number_formats":{"B":"#,##0.00"},
    "column_widths":{"A":18,"B":22,"C":14}
  }}
}
```

```console
python3 -I -S -B "<skill-root>/scripts/stdlib_xlsx.py" create sales.json sales.xlsx --appearance appearance.json
python3 -I -S -B "<skill-root>/scripts/xlsx_tabular.py" import sales.csv imported.xlsx --sheet Sales --numeric-columns B --header-rows 2 --appearance appearance.json
```

The appearance file is separate from the unchanged sheet/cell JSON schema. Sheet names
must match exactly; only listed sheets receive the preset. Without --appearance, existing
creation behavior remains. `Workbook.create(spec, appearance=config)` is the Python API.
Editing/loading existing files never applies presets; the helper rejects loaded workbooks.
CSV export cannot retain colors and has no appearance option.

Default styling is a pale-blue header (DCEAF7), bold dark-blue text (17365D), dark body
text (1F2937), and alternating white/very pale blue rows. Calibri 11 is requested; actual
font availability/substitution depends on the viewing application. Category text remains
visible and row order is unchanged; color is supplementary rather than the only label.

## Scope and precedence

`range` optionally restricts styling to a rectangle such as A1:C20; default is A1 through
the last stored cell. The rectangle includes sparse blanks and is capped at 200,000 cells.
For empty sheets without range, styling is a reported no-op. An explicit blank rectangle
can be styled. The workbook-wide stored-cell limit still applies after filling blanks.
`header_rows` is an inclusive absolute row or row range, defaulting to the first selected
row. null means no header. Leading title/note rows before it remain unstyled. For CSV
import, omitted header_rows inherits the importer's numeric --header-rows count (zero
means no header); an explicit appearance setting takes precedence for styling only.

Priority is **explicit cell style > header > category > alternating rows**. Any cell
entry with an explicit `style` retains its full original style, including style 0; preset
fill, font, border, alignment and number format are all skipped for that cell. To combine
a numeric display format with preset banding, use number_formats instead of a cell style.
Header rows always use header styling unless explicitly overridden. Data category colors
replace row banding; alternate_rows=false disables ordinary banding but not category color.

Category columns accept letters, col: IDs or unique full header paths. Composite keys
use exact stored types: number 1, true, text "1", blank and empty text are distinct.
The first occurrence of a key receives the next of four pale palette colors; every later
occurrence, including noncontiguous rows, uses the same color. Colors cycle after four
keys, so distinct categories can share a color. Category keys must be literal inputs;
formula category cells are rejected because styling precedes formula calculation.

## Merges, layout and formats

Only explicitly listed merges wholly inside header rows and selected columns are allowed.
No merges are inferred. Overlaps, crossed data boundaries, non-anchor values/formulas and
malformed ranges are rejected by the existing header checks. A merged header can require
an explicit styling range when its extent is larger than stored nonblank cells. Generated
blank merged members receive header styles without overwriting anchor text or formulas.

freeze_headers defaults true and freezes all rows through the last header row, including
preceding titles. null headers disable freezing. Borders are thin, light bottom separators,
not heavy boxes. Headers center/wrap by default; body alignment General lets the application
align text/numbers normally. Alignment options are general, left, center, right. Boolean
wrap_text controls data wrapping. Headers have fixed 30-point height; header_height can
set 12..80 points. No auto-fit, automatic row-height fitting or print layout is done.
Widths are explicit column-letter mappings in 3..80 character units. Number formats are
explicit column-letter mappings with 1..256-character XML-safe codes; they change display
only, never values or types. Codes are stored as supplied, without Excel format validation.

Styles, fills, fonts and formats are shared/reused rather than created per cell. At most
4096 cell styles are allowed. The appearance query is limited to 64 KiB. The result report
lists styled/skipped cells, category count, frozen rows and added shared styles. Formula
calculation and file-writing status still must be checked independently. XML style refs,
merges, values and numbering are tested; no real Excel/WPS rendering or visual QA has been
performed. Inspect the generated file in the target application before asserting layout.
