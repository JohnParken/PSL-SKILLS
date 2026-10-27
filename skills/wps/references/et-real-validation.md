# Original ET/ETT sample validation

Follow the [path and first-operation rules](../SKILL.md#执行环境约束与首步自检)
before using this reference; internal resources and task data have different path bases.

## Contents

- [Sources and scope](#sources-and-scope)
- [Results](#results)
- [Reproduce offline](#reproduce-offline)
- [Remaining limits](#remaining-limits)
- [Independent source-value observations](#independent-source-value-observations)

## Sources and scope

Original baseline checked 2026-10-23; the four pinned originals were reacquired and
stored-value expectations checked 2026-10-26 using only Python standard-library tools.
The [sample manifest](et-real-samples.json) records immutable
source URLs, original byte lengths, SHA-256 hashes, producer uncertainty, expected
results and unavailable alternative downloads. Originals retain the source extension;
no generated or renamed XLS/XLSX file is counted as a real ET/ETT sample.

The ET comes from a public WPS integration project's `newfile.et`. The ETT files
come from a public WPS template package resource tree. Their exact producing WPS
versions are unknown. Redistribution rights were not established, so the binary
originals are not bundled. Only provenance, metrics and synthetic regressions are
included in this skill; runtime does not download files or use third-party software.

## Results

All reads below use explicit `--compatibility legacy_excel_clsid`. Schedule additionally
requires `--chart-policy opaque` for successful worksheet extraction. Default strict
mode still rejects these files at metadata records outside its smaller subset.

| Original | Result | Evidence and limits |
|---|---|---|
| newfile.et | Extracted | Three empty sheets, zero cells; basic container/workbook coverage only |
| Telephone List.ett | Extracted after fix | One sheet, 3,976 cells: 10 text and 3,966 blank; no formulas or merges |
| Balance Sheet.ett | Extracted after fix | One sheet, 717 cells: 51 text, 71 numeric (including 21 formula caches), 595 blank; two merges |
| Schedule.ett | Extracted with explicit opaque chart policy | 343 cells (277 blank, 18 text, 48 numeric including 12 caches), one merge; one chart retained as 125 records / 1530 bytes; default policy still refuses |

Before the fix, Template (0x0060) or SupBook (0x01AE) blocked the two now-readable
ETT files. The reader now validates a narrow internal-reference/static-print-area
subset and retains known sheet metadata. See [BIFF8 details](biff8-readonly.md#real-template-compatibility-additions).
It does not skip arbitrary records to make these files pass.

Cell counts were independently cross-checked by framing the workbook stream and
counting scalar and multi-cell record widths without calling the BIFF parser. All
33 formula-cache byte sequences in Balance Sheet and Schedule were compared with
their source Formula payloads. All 1530 retained Schedule chart bytes were compared
with the original chart substream, including its BOF/EOF. Chart records were excluded
from the independently counted worksheet cells. Template titles were manually spot-checked. This is not
independent formula recalculation or complete cell-by-cell verification by WPS/Excel.
The blank ET provides no evidence for populated ET values, dates or formulas.

## Reproduce offline

Separately obtain the exact original files listed in the manifest, under its local
filenames, in a task-relative directory such as `./samples`. Confirm inputs are
readable and run the skill self-check first. From the confirmed task directory:

```console
python3 -I -S -B "<skill_dir>/scripts/read_wps.py" --check
python3 -I -S -B "<skill_dir>/scripts/verify_et_samples.py" ./samples
python3 -I -S -B "<skill_dir>/scripts/verify_et_samples.py" ./samples --chart-policy opaque
python3 -I -S -B "<skill_dir>/scripts/read_wps.py" ./samples/Balance_Sheet.ett --compatibility legacy_excel_clsid
```

The verifier checks original hashes before parsing, expected extraction/rejection,
sheet/cell/merge/formula counts, title spot checks and input hashes after parsing.
It also compares [frozen typed values](real-value-expectations.json): 122 in Balance,
66 in Schedule and 10 in Telephone List (79 text values and 119 numbers including
33 formula caches). The empty ET has no stored-value observations. A wrong value,
type, missing address or lost formula-cache provenance fails independently of counts.
It is read-only and emits JSON to stdout. Exit 0 and `status: passed` mean the fixed
sample expectations matched, including the expected Schedule rejection under the default chart policy; they do
not mean every sample was readable. Missing or changed originals return exit 1. The opaque policy checks all four files
for successful extraction and verifies the retained Schedule chart hash. See
[embedded chart scope](embedded-charts.md) for its limits.

## Remaining limits

`native_et_ett_parsed` and `wps_compatibility_verified` remain false. Private WPS
streams, effective styling, embedded drawing/chart semantics, numeric-date display and
formula-cache correctness have not been verified. No office application, JavaScript
parser, third-party package or cloud converter was used in this validation run.
The earlier government ET comparison in the older evidence manifest is historical;
its original download was unavailable in this run and was not counted again.

## Independent source-value observations

The 198 expectations were frozen before invoking the target reader. A separate
test-only Python standard-library implementation framed the pinned CFB Workbook
stream and observed plain LabelSst, Number, RK/MulRK and numeric Formula caches;
it imports no WPS parser. The repository maintenance tool is
`tests/wps_source_oracle.py` (relative to the repository root, not inside the
distributed skill); its source digest and original execution digest are recorded
in the expectation file. To reproduce from the confirmed repository root:

```console
python -I -S -B tests/wps_source_oracle.py ./samples ./source-observations.json
```

The output must be new. Compare its samples to the bundled frozen expectations,
then run the offline verifier above. This is source-record value agreement, not
independent recalculation, effective styling, private-stream semantics or rendered
WPS/Excel verification. Original binaries and the source-observation tool are not
runtime dependencies and are not bundled with this skill.
