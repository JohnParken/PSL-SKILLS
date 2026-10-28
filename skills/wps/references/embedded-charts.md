# Worksheet extraction with opaque embedded charts

Follow the [path and first-operation rules](../SKILL.md#执行环境约束与首步自检)
before using this reference; internal resources and task data have different path bases.

Run the skill self-check first. Confirm the task-relative input is readable.
This route reads only and needs no write probe unless saving stdout to a report.
Use Python 3.9+ with its standard library and replace `<skill_dir>` with the
directory of the actual SKILL.md read by the harness.

## Explicit policy

```console
python3 -I -S -B "<skill_dir>/scripts/read_wps.py" ./Schedule.ett --compatibility legacy_excel_clsid --chart-policy opaque
python3 -I -S -B "<skill_dir>/scripts/wps_biff8.py" ./Schedule.ett --compatibility legacy_excel_clsid --chart-policy opaque
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
See [real-file validation](et-real-validation.md) and [sample hashes](et-real-samples.json).
`native_et_ett_parsed` and `wps_compatibility_verified` remain false.
