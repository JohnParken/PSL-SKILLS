# Original ET/ETT sample validation

维护路径说明：本页以宿主确认的 PSL-SKILLS 仓库根目录为基准；`skills/et/` 是源码目录，`docs/maintenance/et/` 是维护证据目录。先确认仓库位置；不能从独立运行包推断仓库。原记录的测试数量、平台、版本与结论保留历史含义，不代表当前重跑。测试仅在源码树执行，不随精简运行包分发。

Follow the path and first-operation rules (`skills/et/SKILL.md#执行环境约束与首步自检`)
before using this reference; internal resources and task data have different path bases.

## Contents

- [Edge 新增八份政府公开 ET 原件审计（2026-11-12）](edge-real-samples-20261112.md)：独立核对 599 个非空值和 83 个缓存，发现 cUse=0 共享公式兼容性缺口；该缺口后续由 [ShrFmla cUse=0 兼容修复](shared-formula-zero-use-20261112.md) 处理，原始审计数字保留。以下四份固定集记录保留历史范围。
- Default tolerance acceptance (`docs/maintenance/et/et-real-validation.md#default-tolerance-acceptance`)
- Sources and scope (`docs/maintenance/et/et-real-validation.md#sources-and-scope`)
- Historical explicit-legacy results (superseded; retained for provenance) (`docs/maintenance/et/et-real-validation.md#historical-explicit-legacy-results`)
- Reproduce offline (`docs/maintenance/et/et-real-validation.md#reproduce-offline`)
- Remaining limits (`docs/maintenance/et/et-real-validation.md#remaining-limits`)
- Independent source-value observations (`docs/maintenance/et/et-real-validation.md#independent-source-value-observations`)
- Template compatibility iteration (`docs/maintenance/et/et-real-validation.md#template-compatibility-iteration-2026-11-03`)

## Default tolerance acceptance

Current Skill version `20261111.1759.5400` reads the four pinned originals through the
unified content-first tolerant policy with partial/3, without any compatibility or chart
flags (those flags are deprecated no-ops). All 198 independently frozen stored values,
including 33 unverified formula caches, match. The tolerant reader keeps Schedule.ett's
retained chart bytes and returns partial with 343 cells, 12 formula caches and 1 chart;
no rejection is expected any more, and no strict mode remains.

To verify the tolerant reading path:

```console
python -I -S -B "skills/et/scripts/test/verify_et_samples.py" ./samples
```

No original is rewritten or bundled; this is the same four-file provenance set and
not a broader producer/version acceptance claim. Cloud Linux and real WPS-native OOXML
`.et` samples remain unverified.

## Sources and scope

Original baseline checked 2026-10-23; the four pinned originals were reacquired and
stored-value expectations checked 2026-10-26 using only Python standard-library tools.
The sample manifest (`docs/maintenance/et/et-real-samples.json`) records immutable
source URLs, original byte lengths, SHA-256 hashes, producer uncertainty, expected
results and unavailable alternative downloads. Originals retain the source extension;
no generated or renamed XLS/XLSX file is counted as a real ET/ETT sample.

The ET comes from a public WPS integration project's `newfile.et`. The ETT files
come from a public WPS template package resource tree. Their exact producing WPS
versions are unknown. Redistribution rights were not established, so the binary
originals are not bundled. Only provenance, metrics and synthetic regressions are
included in this skill; runtime does not download files or use third-party software.

## Historical explicit-legacy results

The historical comparison below used explicit `--compatibility legacy_excel_clsid`.
Schedule additionally required `--chart-policy opaque` for successful worksheet
extraction, and explicit strict mode rejected these files at metadata records outside
its smaller subset. The tolerant refactor supersedes this policy: both flags are now
ignored no-ops and records are covered by default. The table is retained only to
preserve the historical evidence and its provenance.

| Original | Result | Evidence and limits |
|---|---|---|
| newfile.et | Extracted | Three empty sheets, zero cells; basic container/workbook coverage only |
| Telephone List.ett | Extracted after fix | One sheet, 3,976 cells: 10 text and 3,966 blank; no formulas or merges |
| Balance Sheet.ett | Extracted after fix | One sheet, 717 cells: 51 text, 71 numeric (including 21 formula caches), 595 blank; two merges |
| Schedule.ett | Extracted (historical explicit policy) | 343 cells (277 blank, 18 text, 48 numeric including 12 caches), one merge; one chart retained as 125 records / 1530 bytes. Current tolerant default returns the same partial result without any flag |

Before the fix, Template (0x0060) or SupBook (0x01AE) blocked the two now-readable
ETT files. The reader now validates a narrow internal-reference/static-print-area
subset and retains known sheet metadata. See BIFF8 details (`skills/et/references/biff8-readonly.md#real-template-compatibility-additions`).
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

For current tolerant acceptance use the command above. The following commands are the
historical explicit-legacy reproduction; the compatibility and chart flags are now
deprecated no-ops and the historical chart rejection is no longer expected. They are
maintenance validation, not the normal user reading workflow.

Separately obtain the exact original files listed in the manifest, under its local
filenames, in a task-relative directory such as `./samples`. Confirm inputs are
readable and run the skill self-check first. From the confirmed task directory:

```console
python3 -I -S -B "skills/et/scripts/read_et.py" --check
python3 -I -S -B "skills/et/scripts/test/verify_et_samples.py" ./samples
python3 -I -S -B "skills/et/scripts/test/verify_et_samples.py" ./samples --chart-policy opaque
python3 -I -S -B "skills/et/scripts/read_et.py" ./samples/Balance_Sheet.ett --compatibility legacy_excel_clsid
```

The historical verifier checks original hashes before parsing, expected extraction/rejection,
sheet/cell/merge/formula counts, title spot checks and input hashes after parsing.
It also compares frozen typed values (`docs/maintenance/et/real-value-expectations.json`): 122 in Balance,
66 in Schedule and 10 in Telephone List (79 text values and 119 numbers including
33 formula caches). The empty ET has no stored-value observations. A wrong value,
type, missing address or lost formula-cache provenance fails independently of counts.
It is read-only and emits JSON to stdout. Exit 0 and `status: passed` mean the frozen
sample expectations matched. Under the current tolerant policy the four files are
expected to be partial rather than refused, so the old explicit-legacy rejection check
no longer describes normal behavior; the historical verifier still records the earlier
expected rejection for provenance. Missing or changed originals return exit 1. The
tolerant default checks all four files for successful extraction and verifies the
retained Schedule chart hash. See
embedded chart scope (`skills/et/references/embedded-charts.md`) for its limits.

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

## Unified-reader regression

The verifier now checks both the legacy BIFF entry and the unified WPS byte reader against the frozen typed values. The unified result must preserve `partial`, `biff8_subset` and a noncertifying reading assessment. Expected legacy default-chart rejection is also checked through both entries. Reacquired pinned originals on 2026-10-27 passed both legacy reject and opaque verifier runs; the opaque run checked all 198 stored values including 33 unverified formula caches. This extends entry-point acceptance, not producer-version or rendered-display coverage.

## Template compatibility iteration 2026-11-03

An in-memory survey of all 12 `pt_BR` ETT originals in the same pinned template
repository found four readable under data mode and eight rejected. The iteration
adds absolute internal cell names and opaque CondFmt/CF groups, plus diagnosed
retention of layout-invalid but correctly framed page-break metadata. The follow-up
adds precisely bounded fragmented note drawings and opaque DVal/Dv groups.

| Pinned original | Before | Current data result | Independent evidence |
|---|---|---|---|
| Amortization.ett | Name `0x18`, offset 4825; after the Name fix, CondFmt `0x1b0`, offset 14093 | partial; 260 cells; 8 static cell names; 2 conditional groups | 260 known cell records; 35 typed saved values including 1 numeric formula cache matched |
| Home Analysis.ett | CondFmt `0x1b0`, offset 10394 | partial; 309 cells; 1 conditional group | 309 known cell records; 35 typed saved values including 1 numeric formula cache matched |
| Start-up Expenses.ett | Invalid page-break interval `0x1b`, offset 8288; then drawing `0xec`, offset 15324 | partial; 520 cells; 8 opaque notes; 1 data-validation rule | 520 known cells; 162 typed saved values including 24 numeric formula caches; 58 opaque record payloads matched |

The 232 new value observations come from the pre-existing independent standard-library
source oracle, called before the target reader and pinned by original and observation
SHA-256. They cover plain SST text and numeric source/cache values, not every blank,
error or text-formula cache. Source record counts are a separate check. All four
original baseline ET/ETT files were rechecked: 198 frozen values including 33 numeric
formula caches match. No cache was recalculated or certified current.

The first drawing fragment is 194 bytes; its Escher DgContainer declares 1056 bytes
including the header. Joining only the 16 MsoDrawing payloads yields exactly that
length and valid nested frame ends. Obj/TxO/Continue bytes are outside those Escher
lengths, as specified by MS-XLS. The old complete-per-record assumption caused the
rejection. The new path preserves all 56 note-group BIFF payloads, then the two
DVal/Dv payloads; each is independently compared with its pinned source offset.
The joined drawing digest and source evidence are recorded below. Neither the
eight comment bodies nor drawing/validation semantics are certified or rendered.

The explicit maintenance command is repository-relative, outside the distributed Skill:

```console
python -I -S -B tests/et_template_compatibility_probe.py --download --include-baseline
```

Run it from the confirmed repository root. It uses HTTPS with default certificate
validation, rejects redirects, pins source hashes, and limits each download to 4 MiB.
Original bytes exist only in memory; no binaries or cell contents are saved, published
or bundled. Redistribution rights and producing WPS versions remain unestablished.
The skill itself remains offline. The current seven-file probe must return `passed`
and match all observations; each returned workbook remains `partial`. The other
five failures in the initial 12-template survey have not been reassessed here.

Machine evidence, scope and remaining corpus failures are in
`docs/maintenance/et/et-template-compatibility-evidence.json`.
The follow-up ran on macOS using the requested exact Python 3.12.11 with Expat 2.6.3;
the earlier name/page/conditional iteration also ran on 3.13.12. Linux container
and Windows validation were not performed. The previously recorded
Linux CI results elsewhere in these references do not cover this iteration.
