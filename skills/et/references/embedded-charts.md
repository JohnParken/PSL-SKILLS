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

```python
execute(command="python3 -I -S -B \"<skill-root>/scripts/read_et.py\" ./Schedule.ett")
execute(command="python3 -I -S -B \"<skill-root>/scripts/wps_biff8.py\" ./Schedule.ett")
```

The tolerant reader always retains supported chart bytes as bounded opaque evidence;
there is no chart policy switch any more. `--chart-policy` and `--compatibility` are
accepted as deprecated no-ops and recorded as issues. `--chart-policy reject` no longer
discards charts, and strict/legacy modes no longer exist. Retention applies to BIFF8
reads, never to writing, conversion or recalculation, and no chart bytes are decoded,
rendered or executed. Nothing is retried with a different policy after a failure.

## What is retained and checked

The reader recognizes bounded OfficeArt frames, Obj records and embedded BIFF8 chart
substreams. Failed optional drawing/member/ID/Begin-End validation produces issues
and retains bounded opaque bytes; it does not discard already read worksheet values.
The outer BIFF record framing, encryption and container safety limits still apply.

`uninterpreted_metadata` retains accepted raw payloads with offsets and IDs within its
budget. `embedded_charts` describes framed chart spans, hashes and validation issues;
`charts_decoded=false` means no chart semantics, rendering or caches were evaluated.

Chart SERIESDATA may legally contain Number, BoolErr, Blank and Label after SIIndex
(MS-XLS 2.1.7.20.1). Those records stay inside the chart, never becoming worksheet
values. Worksheet-only records (including RK/MulRK/MulBlank), or value records outside
a recognized SERIESDATA sequence, are bounded recovery points even with unbalanced
Begin/End. A malformed chart EOF is consumed as the chart terminator so later worksheet
cells remain readable. Ambiguous/missing chart or worksheet boundaries mark
`omissions_affect_cell_values=true`; a missing EOF after chart series data may still
leave worksheet-looking values opaque because their ownership cannot be established.
This is recovery with explicit uncertainty, not a guarantee to reconstruct damaged files.

Macro/link records remain opaque and are never executed or followed. Standalone chart
sheets, arbitrary drawing semantics and full chart decoding remain outside the subset.
No office application, third-party package or network access is needed at runtime.

## Limits and evidence

At most 128 chart reports are kept. Each chart scan is bounded by 4096 records;
Begin/End validation is bounded at depth 32. Drawing validation uses 4096 records and
depth 16; malformed/oversized optional structures are opaque with issues. Raw payloads
share the BIFF metadata limits (8 MiB / 65536 entries), and omitted evidence is reported.
Input, stream, record and JSON hard limits remain unchanged. Unknown object semantics
remain unverified; retention does not certify their effect on cell values.

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
Other author trailing bytes, missing links and out-of-grid coordinates fail the specialized note-chain check; the reader then retains bounded opaque records with issues.

All source payloads remain in `uninterpreted_metadata`. `note_drawings` reports the
joined drawing SHA-256, fragment/object/record counts and `semantics_validated=false`.
Unknown OfficeArt atoms or recursively framed containers in this verified chain are
listed in `unknown_officeart_records`; their meaning and effect on cell values are
unknown, so coverage is unverified and status remains partial. The linked records,
marker positions, bounds and budgets must still pass their existing checks.
`note_drawing_retained_opaque` records that note drawings stay as bounded raw evidence;
decoded comment text and author are returned separately in `sheets[].comments` and are
never applied to cell values. Runs, shape properties and layout are still not interpreted
or rendered.
No formula, macro, link, property or external content is executed or followed.

Each group has at most 128 notes, 4096 Escher records and depth 16, sharing the
bounded raw metadata budget. General mixed chart/note drawings,
arbitrary fragment positions, continued drawing bytes and other object types are
retained as bounded opaque evidence with issues; they are never executed. The
tolerant policy applies to every public entry.

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
