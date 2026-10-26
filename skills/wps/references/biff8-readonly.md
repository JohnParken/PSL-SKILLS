# Strict BIFF8 read-only record subset

Follow the [path and first-operation rules](../SKILL.md#执行环境约束与首步自检)
before using this reference; internal resources and task data have different path bases.

The main `read_wps.py input.et` / `read_wps.py input.ett` entrypoint also calls this
reader and preserves its `biff8_readonly_v1` schema and diagnostics. Both routes validate
contents and retain all subset limits. For unified branch-specific `--compatibility`
choices, use the [compatibility rules](format.md#兼容策略和来源): legacy_excel_clsid
is the BIFF option; legacy_word_clsid is for MS-DOC, not this low-level reader.
Neither enables writing or formula execution.

Run the [entrypoint self-check](../SKILL.md#执行环境约束与首步自检), requiring exit 0 and
environment-ok. Confirm the authorized task input is readable. This reader writes no files;
no write probe is required for inspection. Resolve `<skill_dir>` from the actual SKILL.md,
replace it before commands, and use task-relative inputs. Python 3.9+, standard library
only; no macro/formula execution, network requests, Office tools or dependency installs.

## Contents

- [Evidence and input](#evidence-and-input)
- [Observed legacy compatibility](#observed-legacy-compatibility)
- [Real-template compatibility additions](#real-template-compatibility-additions)
- [Supported records and retained values](#supported-records-and-retained-values)
- [Dates and formula caches](#dates-and-formula-caches)
- [Headers and output](#headers-and-output)
- [Rejection rules and validation limits](#rejection-rules-and-validation-limits)
- [Migration and continuation validation](#migration-and-continuation-validation-2026-10-25)
- [Rich SST and formula-group subsets](#rich-sst-and-formula-group-subsets)
- [Additive number and saved visibility interpretation](#additive-number-and-saved-visibility-interpretation)

## Evidence and input

Implementation follows the Microsoft MS-XLS online record/structure sections checked
2026-10-22: Record, BOF, BoundSheet8, SST/Unicode strings, Number/RK/MulRk, Blank,
LabelSst, BoolErr, MergeCells, Date1904, XF/Format, Formula/FormulaValue and String.
Exact official source URLs are bibliographic entries in
[et-format-evidence.json](et-format-evidence.json), not runtime dependencies. No third-party
parser code or binary samples were incorporated. All tests are synthetic MS-XLS record
fixtures, with manual typed expectations and container fixtures; they are not WPS files.

```console
python3 -I -S -B "<skill_dir>/scripts/wps_biff8.py" input.xls
```

The file must pass the [CFB container reader](et-ett-readonly.md), contain exactly one
root Workbook/Book stream, and start with the 16-byte BOF payload identifying 0x0600
BIFF8 globals. A stream name or .et/.ett suffix does not establish its encoding. Worksheet
BOFs, BoundSheet8 offsets, EOF boundaries and the non-dialog WsBool must agree. Standalone chart,
macro/VBA and dialog sheets, recognized macro storages and encryption are refused.
ET/ETT names may be inspected by content but native_et_ett_parsed and
wps_compatibility_verified always remain false, even after successful BIFF extraction.

## Observed legacy compatibility

Default behavior remains strict. For a known legacy Excel header variant, opt in:

```console
python3 -I -S -B "<skill_dir>/scripts/wps_biff8.py" input.et --compatibility legacy_excel_clsid
```

This accepts only the exact observed WorksheetClass header GUID in CFB v3, with a root
Workbook/Book stream and every existing allocation, chain and resource check. It does
not rewrite the header or establish the producer from the GUID. Unknown nonzero GUIDs
and nonzero GUIDs in v4 still fail. See [container rules](et-ett-readonly.md).

The same explicit mode additionally retains an allowlist of MS-XLS 2.3.2 metadata record
IDs as scope/offset/name/raw hex, within the existing metadata budgets. These include
interface markers, workbook options, protection flags, style/theme extensions, printing
and layout settings. They are not executed, semantically validated, rendered or applied;
raw XF/format values are not effective inherited display formats. The result includes
known_metadata_retained_uninterpreted and diagnostic status. Unknown records, external
reference data, macros, encryption and unsupported continuations remain refused.
Shared/array formula support has its own [authoritative subset rules](formula-groups.md);
this compatibility mode does not relax them.
The allowlist is explicit in LEGACY_GLOBAL_METADATA / LEGACY_SHEET_METADATA; it is not
an option to skip arbitrary record IDs. Other container streams, including ETExtData
and ETSharedLinks, remain undecoded and are listed by path, size and digest. Do not
assume these private extensions have no effect on a full WPS rendering or data model.

One public government ET template was read without changing its bytes: three sheets,
22 text cells, 54 explicit blanks and 18 merges. Sheet names, all nonblank values and
merge sets matched an isolated LibreOffice XLSX conversion. Its Date1904=0 marker also
matched; it contains no numeric date cells or formulas, so those capabilities still lack
real-sample validation. Source/hash and limitations are in
[the evidence manifest](et-format-evidence.json). The file is not redistributed and its
WPS version is unknown. This is observed BIFF8 cell-subset interoperability, not general
native ET/ETT acceptance; native_et_ett_parsed and wps_compatibility_verified stay false.

## Real-template compatibility additions

The explicit `legacy_excel_clsid` mode also accepts these narrowly checked records
found in original public ETT templates, even when their CFB header CLSID is already zero:

- Empty, nonduplicate `Template` marker, retained as raw metadata.
- One self-referencing `SupBook` (exact four-byte layout, cch 0x0401).
- Bounded `ExternSheet` entries referring only to that self link and actual worksheet indices.
- Sheet-local built-in `Print_Area` names with the exact supported static absolute
  `PtgArea3d` layout. Macro flags, other names, relative areas, invalid references and
  duplicate print areas fail; no name formula is executed or applied as page layout.
- Raw preservation of known `Protect`, `Password`, `ScenarioProtect`, `ObjProtect`,
  `Scl` and `SheetExt` sheet metadata under the existing metadata budgets.

These additions do not allow external workbook/add-in/DDE links, arbitrary names,
private records or embedded chart substreams by default.
The explicit [opaque chart policy](embedded-charts.md) adds bounded framing/raw retention
for a supported embedded-chart subset without decoding or rendering charts. Record bytes are retained for diagnosis;
metadata retention is not full semantic or layout validation. Strict mode is unchanged.
See [real sample validation](et-real-validation.md) for original-file hashes, results,
spot checks, default chart rejection, the explicit opaque policy and reproducible offline verification.

## Supported records and retained values

Read Number, RK, MulRk, BoolErr, Blank, MulBlank and LabelSst cells. Shared strings support
plain compressed-Unicode or UTF-16 text, including bounded SST and formula-cache String Continue records. Encoding may change at a character-data continuation; fixed headers may not split. Rich SST text and its Continue segments are supported with raw FormatRun metadata; phonetic strings and unrelated Continue uses are refused. Text such as long leading-zero identifiers stays text. Number/RK values use
an exact Decimal representation of the stored IEEE754 value (which may differ from the
intended decimal); raw numeric bytes are retained. No numeric text coercion occurs.

Preserve sheet order/names/hidden state, source record offsets, raw XF indices/bytes,
custom format strings and merged-range coordinates. Merges are validated for overlap and
bounds but never expanded; non-anchor data is retained, not silently erased. Built-in
format IDs remain IDs; fonts/styles/layout are not rendered or reconstructed.

Known metadata records are explicitly retained as offset/type/raw payload hex: global
CodePage (Unicode 1200 only), Window1, Font, Style; worksheet Dimensions, Row, Index,
DBCell and Window2. Their internal semantics are not validated or applied. Other records
fail with ID and offset. This is a strict data-record subset, not full MS-XLS grammar or
workbook validity certification; many genuine XLS/ET files will be outside this subset.

## Dates and formula caches

Require explicit Date1904, preserving the 1900/1904 system. A numeric date serial stays
numeric with raw XF/number-format ID and any explicit custom format text. No date-format
heuristic, locale resolution, effective-style inheritance or ISO conversion is performed.
Serial 60 in the 1900 system is not invented as a real calendar date; fractional serials
and the original bytes remain available for later, separately verified interpretation.

Formula records retain opaque token bytes, ancillary bytes, raw flags and cache bytes.
Tokens are not decompiled, semantically validated or executed; unknown token semantics do
not trigger any formula evaluation. [Shared/array formula rules](formula-groups.md)
define the supported bounded group subset and its partial status; Table data-table
formulas remain refused. Supported caches are numeric, boolean, text (String record
with the group ordering described in that reference), error
and explicit empty string. Every present cache is unverified and may be stale, regardless
of the source recalculation flag. Error caches remain errors, never zero. Missing expected
String cache produces null, cache_status missing, an issue and a diagnostic result status;
it is distinct from a legitimate empty-string cache. Other truncated records fail.

## Headers and output

This example assumes an existing `Data` sheet and explicitly chosen header rows 1–3
within A1:F20. Select the actual sheet/range for the task; error cells cannot form
header paths, and formula headers require the explicit cache policy described below.

```console
python3 -I -S -B "<skill_dir>/scripts/wps_biff8.py" input.xls --sheet Data --range A1:F20 --header-rows 1:3
```

Explicit header reconstruction reuses the explicit merge-only header rules in `wps_biff8_headers.py`,
including merge-only propagation, stable column IDs, clipping/cross-boundary rejection and
non-anchor-data checks. Formula headers default to rejection; --header-formulas cached
is an explicit opt-in with a warning, and missing/error caches still fail. Dates remain
numeric serials in paths. No implicit spreadsheet-to-OOXML reconstruction is performed.

JSON schema biff8_readonly_v1 contains typed sheet cells, merges, date/format metadata,
source file/stream digest, unparsed metadata and diagnostics. biff_parsed true means this
limited record subset passed; it is separate from the container-only report and native
ET/ETT acceptance. Unsupported inputs exit 1 with no partial cell report; subset extraction
exits 0, but read_biff8_subset_with_diagnostics requires review. Missing formula caches are
explicit diagnostics, not evidence of complete calculated data. No file is written.
Python APIs: parse_stream(bytes), read_file(path), reconstruct_headers(result,...).
Native OOXML editing APIs do not accept this separate schema. If the xlsx skill is
available, its typed-JSON region/profile adapter can analyze a saved report; it is
optional and is not a dependency of this package.

## Rejection rules and validation limits

Reject unknown/private records, FilePass, unsupported BOF versions, offset aliases/gaps,
truncated/oversized payloads, nonfinite numbers, duplicate cells, bad SST/XF references,
malformed strings/merges, orphan String records and trailing unbound data. No data-bearing
record is silently skipped. Present but opaque formulas remain explicitly unverified.

Limits: stream 8 MiB, records 100000, cells 200000, sheets 128, XFs 4096, SST strings
10000, merges 256, uninterpreted metadata 1 MiB/4096 records, diagnostics 1000, JSON output
16 MiB. Payloads over 8224 bytes and Continue uses outside SST characters/runs and plain cached String character data are outside the subset. Header
regions are capped at 200000 cells and BIFF8's 65536 rows/256 columns. Inherited CFB chain,
allocation and memory budgets apply before record decoding. Trailing producer padding is
not guessed or discarded, so some noncanonical streams are deliberately refused.

The earlier public ET comparison above and newer template checks do not establish WPS
private-extension semantics, general ET/ETT support, numeric-date rendering, formula-cache
correctness or real Excel acceptance. ETT subset extraction now has real-file evidence,
as described in the linked validation report. Next samples must include
permitted provenance/license or user authorization, WPS version/platform, untouched ET/ETT
and corresponding XLSX, and expectations for typed cells/merges/date system/formula caches.
Comparisons must distinguish stored IEEE754 from rounded decimal display and unverified
cache values. Do not rename XLS/XLSX fixtures and present them as genuine WPS validation.

## Migration and continuation validation (2026-10-25)

The BIFF/CFB implementation, chart framing, original synthetic tests and source/sample
manifests moved from xlsx to wps. `wps_biff8_headers.py` retains only the pure address and
merge-header algorithm from xlsx_headers/stdlib_xlsx; it has no native OOXML dependency.
The source is recorded in that module; future header-policy changes must regression-check
both standalone algorithms. No adjacent skill is imported at runtime.

Plain SST and cached String continuation behavior follows MS-XLS
XLUnicodeRichExtendedString: fixed fields stay in one record, character continuations
start with fHighByte, and UTF-16 units do not split across records. Phonetic tails remain unsupported; rich SST character/run continuation is described below. Tests use independently specified A + Chinese + astral character,
5000 Chinese characters, empty strings and malformed split/flag/orphan cases. These are
synthetic record samples, not evidence of real-producer coverage.

The unified entrypoint retains this schema but normalizes status to ok/partial, preserves
legacy status as reader_status, and exits 3 for partial. The low-level wps_biff8 CLI keeps
its prior status and exit conventions. To analyze a saved typed JSON report, use the xlsx
skill's region-based profile/summarize commands with the `.json` result. This consumes JSON
and preserves source diagnostics; it does not reopen the WPS file or construct an XLSX.

Cell output text has an additional 8 Mi UTF-16-unit aggregate budget, charged for
every repeated SST cell occurrence and formula String cache before storage. Repeated
custom-format text shares this budget. This prevents small shared-string tables from
expanding into unbounded JSON text; the 16 MiB final serialized JSON limit still applies.

## Rich SST and formula-group subsets

Rich SST character segments and raw FormatRun segments can cross Continue records.
The character phase consumes continuation encoding bytes; the run phase does not.
Fixed headers stay in one record; per-string and remaining aggregate budgets are
checked before collecting run data. Formatting is retained once by SST index and
reported partial, never applied. Phonetic extensions and rich cached String records
remain unsupported. See [continuation rules and evidence](biff-rich-continuation.md).

For shared/array groups, use [formula-group rules and evidence](formula-groups.md)
as the single detailed contract for supported records, member caches, ordering,
partial diagnostics and rejection cases. Rich strings and groups have synthetic
regression evidence, not independent real-file semantic acceptance.

The four unchanged real ET/ETT originals in the sample manifest contain no rich SST
strings, so this new capability has no real-producer acceptance evidence. All four still
extract with diagnostics under explicit legacy compatibility plus opaque chart policy:
newfile.et 0 cells; Balance_Sheet.ett 717; Schedule.ett 343; Telephone_List.ett 3976.
Schedule.ett remains expected-unsupported without the opaque policy (record 0xEB at 2788).
The other three extract with metadata diagnostics. These are partial subset results,
not four complete-format successes; hashes and existing text/metric expectations match.

## Additive number and saved visibility interpretation

Raw values and cache provenance remain unchanged. See [number interpretation](numeric-interpretation.md) for the bounded explicit CellXF format subset and unknown inheritance/complex formats; this is not effective-style or Excel display rendering. See [saved visibility](saved-visibility.md) for Row/ColInfo flags and raw filter metadata. Default extraction includes all cells. Row selection keeps unknown states; filter conditions are not decoded or executed. These BIFF helpers do not parse OOXML; the unified entrypoint separately routes OOXML packages to the [OOXML spreadsheet reader](ooxml-spreadsheet.md).
