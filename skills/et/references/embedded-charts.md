# Worksheet extraction with opaque charts and note drawings

维护资源说明：文中 `docs/maintenance/et/` 与 `skills/et/scripts/test/` 均以宿主确认的仓库根目录为基准，仅供源码维护，不随运行包分发；运行任务不读取或执行。没有仓库时跳过维护资料，不按 Skill 安装目录拼接。

路径占位符说明：`<skill-root>` 是 ET Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 ET 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

Follow the path and first-operation rules (`<skill-root>/SKILL.md#执行环境约束与首步自检`)
before using this reference; internal resources and task data have different path bases.

## Contents

- Policy (`<skill-root>/references/embedded-charts.md#explicit-policy`)
- Chart retention (`<skill-root>/references/embedded-charts.md#what-is-retained-and-checked`)
- Limits and evidence (`<skill-root>/references/embedded-charts.md#limits-and-evidence`)
- Note fragments (`<skill-root>/references/embedded-charts.md#fragmented-note-drawings-data-only`)

Run the skill self-check first. Confirm the task-relative input is readable.
This route reads only and needs no write probe unless saving stdout to a report.
Use Python 3.9+ with its standard library and replace `<skill-root>` with the
directory of the actual SKILL.md read by the harness.

## Explicit policy

```console
python3 -I -S -B "<skill-root>/scripts/read_et.py" ./Schedule.ett --compatibility legacy_excel_clsid --chart-policy opaque
python3 -I -S -B "<skill-root>/scripts/wps_biff8.py" ./Schedule.ett --compatibility legacy_excel_clsid --chart-policy opaque
```

`data` is now the file-entry default and selects opaque chart retention. Explicit
strict/legacy modes still default to reject; `--chart-policy reject` always overrides
data. Opaque requires data or legacy_excel_clsid and is available for BIFF8 reads; it is not a write,
conversion or recalculation option. Python `read_file` and `parse_stream` accept
`chart_policy="opaque"` with `compatibility="legacy_excel_clsid"`.

## What is retained and checked

The reader recognizes a bounded subset of OfficeArt drawing frames, a chart-type
Obj with FtCmo/FtEnd, and one embedded BIFF8 chart substream per object. It checks
exact frame lengths, explicit record-type allowlists, BOF type/version, balanced
Begin/End and EOF, unique nonzero object IDs and the existing workbook/resource
limits. A complete supported drawing/Obj/chart sequence must be present.

All accepted drawing, Obj and chart record payloads are retained in
`uninterpreted_metadata`, with offsets and IDs. `embedded_charts` reports the sheet,
object ID, offset, record count and SHA-256 of the full framed chart bytes.
`embedded_chart_retained_opaque` issues and `charts_decoded: false` disclose the
scope. Worksheet extraction resumes after the chart EOF; chart records are never
reported as worksheet cells, and worksheet cells after the chart are retained.

This is framing validation and raw preservation, not full MS-XLS/MS-ODRAW grammar,
chart interpretation, chart-cache correctness, rendering or effective layout
validation. Chart formula tokens and drawing properties are not evaluated or
followed. Macros and unknown records remain refused. No office application,
third-party package, external executable or network request is used.

## Limits and evidence

At most 128 embedded charts are accepted. Each chart has at most 4096 records;
Begin/End nesting is capped at 32. Each drawing frame has at most 4096 OfficeArt
records and nesting at most 16. All opaque payloads share the existing 1 MiB /
4096-record metadata budget; stream, record and JSON limits still apply.

Unknown records/types, truncated drawings, unsupported drawing splits, missing Obj/EOF, nested
macro/workbook substreams, external supporting-link records and unsupported chart
cache record types fail without a partial cell report. Standalone chart sheets,
arbitrary drawing objects, unattached Continue records and full chart decoding remain outside
the subset. An allowed opaque payload may contain uninterpreted properties or tokens;
acceptance never certifies their semantics.

The original Schedule.ett now yields 343 worksheet cells, one merge and 12
unverified formula caches. Its 125 chart records (1530 bytes) are retained exactly;
the chart bytes and source worksheet record counts were independently cross-checked.
See real-file validation (`docs/maintenance/et/et-real-validation.md`) and sample hashes (`docs/maintenance/et/et-real-samples.json`).
`native_et_ett_parsed` and `wps_compatibility_verified` remain false.

## Fragmented note drawings (data only)

The default data/opaque route also retains one complete DgContainer split across
MsoDrawing records, with note objects interleaved. Each fragment must end exactly
at a real zero-length ClientData or ClientTextbox leaf in an SpContainer. A bounded
recursive Escher walk checks the joined parent/child lengths and every physical
split. Marker-looking bytes inside an opaque property cannot substitute for a leaf.
Only the immediately required Obj, TxO and Continue records may intervene; no scan
past cells or unknown records is attempted.

Each ClientData requires an exact 52-byte note Obj (FtCmo/FtNts/FtEnd), without macro
or link-formula subrecords. Each ClientTextbox requires a fixed 18-byte TxO without
a linked formula, exact declared Unicode text units and formatting-run byte count.
The group must then have one adjacent Note per unique object ID and cell location.
Author lengths are bounded; an omitted unused terminal byte, observed in the real
template, is recorded as `unused_terminal_byte_present=false` rather than invented.
Other author trailing bytes, missing links and out-of-grid coordinates fail.

All source payloads remain in `uninterpreted_metadata`. `note_drawings` reports the
joined drawing SHA-256, fragment/object/record counts and `semantics_validated=false`.
`note_drawing_retained_opaque` explicitly marks omission of comment text, authors
and display from the cell values. Text is checked for framing/Unicode validity but
not exposed as decoded comments; runs, shape properties and layout are not interpreted.
No formula, macro, link, property or external content is executed or followed.

Each group has at most 128 notes, 4096 Escher records and depth 16, sharing the
existing 1 MiB/4096-record raw metadata budget. General mixed chart/note drawings,
arbitrary fragment positions, continued drawing bytes and other object types remain
unsupported. Explicit strict/legacy behavior and `--chart-policy reject` are unchanged.

In Start-up Expenses, the first 194-byte fragment at offset 15324 declares a
1056-byte DgContainer. Its 16 fragments join exactly; eight note chains account for
56 BIFF records. The independent source comparison verifies all those payloads
plus two data-validation records and 162 saved values (24 numeric formula caches).
This does not certify comment contents, shape semantics or rendering.

Maintenance sources: MS-XLS MsoDrawing
(`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/1b0b0ea3-5e1d-41ce-8e79-e27f1041331e`),
OfficeArtClientTextbox (`https://learn.microsoft.com/en-gb/openspecs/office_file_formats/ms-xls/c240a79a-7bd2-4879-a03c-9720855f7022`),
TxO (`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/638c08e6-2942-4783-b71b-144ccf758fc7`),
Obj (`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/dd34df60-8250-40a9-83a3-911476a31ea7`),
FtNts (`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/b0991167-6b90-4e6f-910b-8c153a7e0f87`),
NoteSh (`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/3a610bb3-9d35-435f-92ef-cdbc42974404`).
