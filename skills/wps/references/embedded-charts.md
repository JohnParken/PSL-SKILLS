# Worksheet extraction with opaque embedded charts

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

Follow the path and first-operation rules (`<skill-root>/SKILL.md#执行环境约束与首步自检`)
before using this reference; internal resources and task data have different path bases.

Run the skill self-check first. Confirm the task-relative input is readable.
This route reads only and needs no write probe unless saving stdout to a report.
Use Python 3.9+ with its standard library and replace `<skill-root>` with the
directory of the actual SKILL.md read by the harness.

## Explicit policy

```console
python3 -I -S -B "<skill-root>/scripts/read_wps.py" ./Schedule.ett --compatibility legacy_excel_clsid --chart-policy opaque
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

Unknown records/types, truncated or fragmented drawings, missing Obj/EOF, nested
macro/workbook substreams, external supporting-link records and unsupported chart
cache record types fail without a partial cell report. Standalone chart sheets,
arbitrary drawing objects, Continue records and full chart decoding remain outside
the subset. An allowed opaque payload may contain uninterpreted properties or tokens;
acceptance never certifies their semantics.

The original Schedule.ett now yields 343 worksheet cells, one merge and 12
unverified formula caches. Its 125 chart records (1530 bytes) are retained exactly;
the chart bytes and source worksheet record counts were independently cross-checked.
See real-file validation (`<skill-root>/references/et-real-validation.md`) and sample hashes (`<skill-root>/references/et-real-samples.json`).
`native_et_ett_parsed` and `wps_compatibility_verified` remain false.
