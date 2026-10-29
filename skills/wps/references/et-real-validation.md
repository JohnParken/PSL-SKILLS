# Original ET/ETT sample validation

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

Follow the path and first-operation rules (`<skill-root>/SKILL.md#执行环境约束与首步自检`)
before using this reference; internal resources and task data have different path bases.

## Contents

- Default data-policy acceptance (`<skill-root>/references/et-real-validation.md#default-data-policy-acceptance`)
- Sources and scope (`<skill-root>/references/et-real-validation.md#sources-and-scope`)
- Historical explicit-legacy results (`<skill-root>/references/et-real-validation.md#historical-explicit-legacy-results`)
- Reproduce offline (`<skill-root>/references/et-real-validation.md#reproduce-offline`)
- Remaining limits (`<skill-root>/references/et-real-validation.md#remaining-limits`)
- Independent source-value observations (`<skill-root>/references/et-real-validation.md#independent-source-value-observations`)

## Default data-policy acceptance

The four pinned originals also pass default file/API reading with partial/3, without
explicit compatibility or chart flags. All 198 independently frozen stored values,
including 33 unverified formula caches, match. Explicit strict still rejects all four.
The original legacy-policy verifier remains available and still expects Schedule to
be refused when its explicit legacy chart policy is reject. To verify default data:

```console
python -I -S -B "<skill-root>/scripts/verify_et_samples.py" ./samples --compatibility data
```

No original is rewritten or bundled; this is the same four-file provenance set and
not a broader producer/version acceptance claim.

## Sources and scope

Original baseline checked 2026-10-23; the four pinned originals were reacquired and
stored-value expectations checked 2026-10-26 using only Python standard-library tools.
The sample manifest (`<skill-root>/references/et-real-samples.json`) records immutable
source URLs, original byte lengths, SHA-256 hashes, producer uncertainty, expected
results and unavailable alternative downloads. Originals retain the source extension;
no generated or renamed XLS/XLSX file is counted as a real ET/ETT sample.

The ET comes from a public WPS integration project's `newfile.et`. The ETT files
come from a public WPS template package resource tree. Their exact producing WPS
versions are unknown. Redistribution rights were not established, so the binary
originals are not bundled. Only provenance, metrics and synthetic regressions are
included in this skill; runtime does not download files or use third-party software.

## Historical explicit-legacy results

The historical comparison below uses explicit `--compatibility legacy_excel_clsid`. Schedule additionally
requires `--chart-policy opaque` for successful worksheet extraction. Explicit strict
mode still rejects these files at metadata records outside its smaller subset.

| Original | Result | Evidence and limits |
|---|---|---|
| newfile.et | Extracted | Three empty sheets, zero cells; basic container/workbook coverage only |
| Telephone List.ett | Extracted after fix | One sheet, 3,976 cells: 10 text and 3,966 blank; no formulas or merges |
| Balance Sheet.ett | Extracted after fix | One sheet, 717 cells: 51 text, 71 numeric (including 21 formula caches), 595 blank; two merges |
| Schedule.ett | Extracted with explicit opaque chart policy | 343 cells (277 blank, 18 text, 48 numeric including 12 caches), one merge; one chart retained as 125 records / 1530 bytes; explicit legacy policy with omitted chart option refuses |

Before the fix, Template (0x0060) or SupBook (0x01AE) blocked the two now-readable
ETT files. The reader now validates a narrow internal-reference/static-print-area
subset and retains known sheet metadata. See BIFF8 details (`<skill-root>/references/biff8-readonly.md#real-template-compatibility-additions`).
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

For current default data-policy acceptance use the command above. The following
commands reproduce the historical explicit-legacy comparison, including its chart
rejection; they are maintenance validation, not the normal user reading workflow.

Separately obtain the exact original files listed in the manifest, under its local
filenames, in a task-relative directory such as `./samples`. Confirm inputs are
readable and run the skill self-check first. From the confirmed task directory:

```console
python3 -I -S -B "<skill-root>/scripts/read_wps.py" --check
python3 -I -S -B "<skill-root>/scripts/verify_et_samples.py" ./samples
python3 -I -S -B "<skill-root>/scripts/verify_et_samples.py" ./samples --chart-policy opaque
python3 -I -S -B "<skill-root>/scripts/read_wps.py" ./samples/Balance_Sheet.ett --compatibility legacy_excel_clsid
```

The verifier checks original hashes before parsing, expected extraction/rejection,
sheet/cell/merge/formula counts, title spot checks and input hashes after parsing.
It also compares frozen typed values (`<skill-root>/references/real-value-expectations.json`): 122 in Balance,
66 in Schedule and 10 in Telephone List (79 text values and 119 numbers including
33 formula caches). The empty ET has no stored-value observations. A wrong value,
type, missing address or lost formula-cache provenance fails independently of counts.
It is read-only and emits JSON to stdout. Exit 0 and `status: passed` mean the fixed
sample expectations matched, including the expected Schedule rejection under the explicit legacy default chart policy; they do
not mean every sample was readable. Missing or changed originals return exit 1. The opaque policy checks all four files
for successful extraction and verifies the retained Schedule chart hash. See
embedded chart scope (`<skill-root>/references/embedded-charts.md`) for its limits.

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
