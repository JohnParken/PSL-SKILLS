# Bounded BIFF8 stored-value reading

维护资源说明：文中 `docs/maintenance/et/` 与 `skills/et/scripts/test/` 均以宿主确认的仓库根目录为基准，仅供源码维护，不随运行包分发；运行任务不读取或执行。没有仓库时跳过维护资料，不按 Skill 安装目录拼接。

路径占位符说明：`<skill-root>` 是 ET Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 ET 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

Follow the path and first-operation rules (`<skill-root>/SKILL.md#执行环境约束与首步自检`)
before using this reference; internal resources and task data have different path bases.

The main `"<skill-root>/scripts/read_et.py" input.et` / `"<skill-root>/scripts/read_et.py" input.ett` entrypoint also calls this
reader and preserves its `biff8_readonly_v1` schema and diagnostics. Both routes validate
contents and retain all subset limits. For unified branch-specific `--compatibility`
choices, use the compatibility rules (`<skill-root>/references/format.md#兼容策略和来源`): legacy_excel_clsid
and data are BIFF options; legacy_word_clsid is for MS-DOC, not this low-level reader.
Neither enables writing or formula execution.

Run the entrypoint self-check (`<skill-root>/SKILL.md#执行环境约束与首步自检`), requiring exit 0 and
environment-ok. Confirm the authorized task input is readable. This reader writes no files;
no write probe is required for inspection. Resolve `<skill-root>` from the actual SKILL.md,
replace it before commands, and use task-relative inputs. Python 3.9+, standard library
only; no macro/formula execution, network requests, Office tools or dependency installs.

## Contents

- Evidence and input (`<skill-root>/references/biff8-readonly.md#evidence-and-input`)
- Pure-table Unicode compatibility (`<skill-root>/references/biff8-readonly.md#pure-table-unicode-compatibility`)
- TableStyle source evidence (`<skill-root>/references/biff8-readonly.md#tablestyle-source-evidence`)
- Default data policy (`<skill-root>/references/biff8-readonly.md#default-data-policy`)
- Observed legacy compatibility (`<skill-root>/references/biff8-readonly.md#observed-legacy-compatibility`)
- Real-template compatibility additions (`<skill-root>/references/biff8-readonly.md#real-template-compatibility-additions`)
- DXF selected property interpretation (`<skill-root>/references/biff8-readonly.md#dxf-selected-property-interpretation`)
- Supported records and retained values (`<skill-root>/references/biff8-readonly.md#supported-records-and-retained-values`)
- Inline text and passive metadata (`<skill-root>/references/biff8-readonly.md#inline-text-and-passive-metadata`)
- Dates and formula caches (`<skill-root>/references/biff8-readonly.md#dates-and-formula-caches`)
- Headers and output (`<skill-root>/references/biff8-readonly.md#headers-and-output`)
- Rejection rules and validation limits (`<skill-root>/references/biff8-readonly.md#rejection-rules-and-validation-limits`)
- Migration and continuation validation (`<skill-root>/references/biff8-readonly.md#migration-and-continuation-validation-2026-10-25`)
- Rich SST and formula-group subsets (`<skill-root>/references/biff8-readonly.md#rich-sst-and-formula-group-subsets`)
- Additive number and saved visibility interpretation (`<skill-root>/references/biff8-readonly.md#additive-number-and-saved-visibility-interpretation`)
- ET parser capacity and failure locations (`<skill-root>/references/biff8-readonly.md#et-parser-capacity-and-failure-locations`)

## Evidence and input

Implementation follows the Microsoft MS-XLS online record/structure sections checked
2026-10-22: Record, BOF, BoundSheet8, SST/Unicode strings, Number/RK/MulRk, Blank,
LabelSst, BoolErr, MergeCells, Date1904, XF/Format, Formula/FormulaValue and String.
Exact official source URLs are bibliographic entries in
et-format-evidence.json (`docs/maintenance/et/et-format-evidence.json`), not runtime dependencies. No third-party
parser code or binary samples were incorporated. All tests are synthetic MS-XLS record
fixtures, with manual typed expectations and container fixtures; they are not WPS files.

```python
execute(command="python -I -S -B \"<skill-root>/scripts/read_et.py\" ./input.et")
```

The file must pass the CFB container reader (`<skill-root>/references/et-ett-readonly.md`), contain exactly one
root Workbook/Book stream, and start with the 16-byte BOF payload identifying 0x0600
BIFF8 globals. A stream name or .et/.ett suffix does not establish its encoding. Worksheet
BOFs, BoundSheet8 offsets, EOF boundaries and the non-dialog WsBool must agree. Standalone chart,
macro/VBA and dialog sheets, recognized macro storages and encryption are refused.
ET/ETT names may be inspected by content but native_et_ett_parsed and
wps_compatibility_verified always remain false, even after successful BIFF extraction.

## TableStyle source evidence

在 `data` 模式中，已观察到政府真实 `.et` 文件的 `TableStyle(0x088F)` 和连续 `TableStyleElement(0x0890)`。通过 `wps_biff8_tablestyles.py` 对前置 TableStyles 集合、FrtHeader、名称、1–28 个元素/类型/条纹大小、源 DXF 索引及完整连续记录进行结构校验，再保留原始 record bytes 与 `table_style_sources` 证据，标记 `effective_formatting=unknown`。该源字段仅对实际含有效组的 BIFF8 结果出现；无该记录的既有输出字段保持不变。

只有受控记录子集才会被保留；严格、legacy 模式继续拒绝新增组，分组未完成、FrtHeader 非法、元素错位、非法 DXF 或未知后续记录仍拒绝，不能扫描恢复或跳过。两份真实政府文件的 32/43 个受限保存单元格已通过，独立记录观察器分别核对 18/19 项可观测保存值；样本哈希、原件来源与未验收范围见政府 ET 实测（`docs/maintenance/et/biff8-gov-real-et-validation.md`）。该增强不计算或验证字体、边框、条纹格式、页面渲染，也不声称 WPS Office 生产者版本已确认。

## Default data policy

ET BIFF8 file APIs `read_wps.read`, `read_bytes`, `wps_biff8.read_file` and both
reading CLIs use the shared `DEFAULT_FILE_COMPATIBILITY` in
`<skill-root>/scripts/wps_access.py` when compatibility is omitted. The default is
`data`, which selects
the bounded metadata allowlists and supported opaque chart framing before parsing;
no failed parse is retried. Explicit strict/legacy modes and direct byte-parser defaults
retain their previous behavior.
Only the already evidenced exact Excel v3 header GUID selects the legacy spreadsheet
container profile. Unknown GUIDs and private formats still fail.
All file modes share bounded regular-file loading and CFB header/safety prechecks;
data mode can recover a zero header FAT count from explicitly listed DIFAT entries
when the existing CFB allocation and BIFF8 checks pass. The result is partial with
`cfb_zero_fat_count_recovered`. Strict and explicit legacy modes reject a zero FAT count.
This existing FAT-count recovery is independent of the narrowly checked zero-tail
padding rule described in CFB investigation (`<skill-root>/references/et-ett-readonly.md#default-biff8-data-trailing-padding`).
The expert byte-stream parser `parse_stream` retains its existing compatibility and
validation defaults. File entry points enable `extract_stored_values=True` with data
compatibility.

For an otherwise supported static Lbl (`NAME`, `0x0018`), data mode accepts
hidden ordinary names and hidden variants of the supported Print_Area shape.
Validation ignores only the reserved Lbl flag bits and reserved3–7 fields
defined by MS-XLS, plus the seven reserved XLUnicodeStringNoCch option bits.
It retains the original payload and reports `defined_name_passive_header_fields_ignored`.
Name length, encoding, scope, duplicate checks and supported internal reference
forms still apply for interpreted names. File data mode retains unclassified
name records (including macro-marked names) and invalid palettes as opaque
metadata. It also retains correctly framed `SupBook`/`ExternSheet` link metadata in
its original order and indices. `ExternSheet` may use only bounded adjacent
`Continue` records needed to reach its declared XTI byte count; adjacent `ExternName`
and `XCT/CRN` groups remain opaque. A name is interpreted only when its XTI resolves
to a verified internal-workbook reference; valid external-workbook and add-in entries
remain opaque. Malformed or truncated link framing and invalid indices still reject.
No external workbook is opened or downloaded, and no add-in is loaded or invoked.
Formula cells use only saved `Formula` caches; formulas and external content are
never evaluated.

`--compatibility strict` preserves the old narrow record subset. Explicit legacy modes
retain their previous rules and default chart rejection. In data mode only, omitted
`chart_policy` selects opaque; explicit reject always wins. File data mode retains
framed unknown globals/worksheet records under the existing metadata budget.
Recognized core structural, cell and group records, encryption and resource limits
retain their rejection rules. Opaque records yield partial with uncertain coverage.

A successful data read returns ok/0 or partial/3 through both file CLIs. Known unread
metadata is reported with ID, first offset and count, with every original location and
payload retained in `uninterpreted_metadata`. Container deviations and other streams
are visible diagnostics. Legacy/strict low-level APIs keep their historical reader
status strings and CLI success exit 0; the unified entry always normalizes its status.

`cell_value_coverage` reports `unverified_due_to_best_effort_salvage` when opaque
records make coverage uncertain. Without such uncertainty, missing string caches
produce `missing_formula_caches`; otherwise coverage is `complete_within_declared_sheets`.
The first two cases set `omissions_affect_cell_values=true`. All supported literal values and present caches
are retained. No unverified cache is certified or recalculated, no private stream is
applied, and no full-format equivalence is claimed. `reading_assessment.cell_values`
exposes the same distinction. Existing analysis accepts partial and preserves source
issues; it still requires explicit cached-formula opt-in and rejects missing caches.

## Pure-table Unicode compatibility

Data mode accepts explicit CodePage declarations 874, 932, 936, 949, 950, 1200,
1250, 1251 and 1252 only. The additional 874/949/950/1250/1251 entries are
metadata-only compatibility; no bytes are decoded using these code pages. It preserves `code_page_raw` and reports non1200 declarations as
partial; unknown IDs, wrong payload lengths and conflicting declarations still fail.
This is not a byte-encoding fallback. Supported BIFF8 string fields carry their own
Unicode fHighByte flag: compressed bytes are U+0000..U+00FF low bytes, never GBK,
CP1252 or UTF-8 decoding. Numeric cells remain exact Decimal stored values.
Strict and explicit legacy modes retain the previous 1200-only constraint.
The mandatory Date1904 record remains required in every mode; missing it is not
recovered by guessing 1900 or 1904, even if the CodePage declaration is recognized.
The documented expanded code-page values are Microsoft CODEPG identifiers,
not evidence of producer-specific `.et/.ett` encoding or complete WPS support.

For plain XLUnicodeString in inline Label, formula String caches and Format, data
uses only fHighByte and ignores the seven reserved bits as the field requires. Raw
flags and source positions remain in `string_header_flags_raw`,
`cache_string_header_flags_raw` or `format_string_header_flags`; a bounded diagnostic
marks partial. Actual extra rich/phonetic payloads are not consumed as if they belonged
to this plain structure: exact lengths/tails still have to agree. Legacy worksheet
Label remains the existing bounded reader extension, not full modern Label grammar.

This does not mask SST rich/phonetic flags, short-string flags or Continue encoding
bytes. Existing supported Label and cached String character continuations keep their
length, surrogate and EOF checks. Cached String after a shared/array definition keeps
its original cell/group linkage and unverified status. No formula is executed.

No BIFF5/older version, RString, arbitrary format-string grammar, missing Date1904
inference or per-sheet recovery was added. Missing caches, sparse cells, merges,
unknown records and all resource limits retain their existing handling. The current
XLSX analysis consumes these partial results with their original source diagnostics;
this does not change its native responsibilities or formula-cache policy.

These new cases have fixed synthetic tests and old/new default comparisons. The four
allowed original ET/ETT samples still match all 198 independent stored values, but
contain none of these new non1200/reserved-flag cases. They are regression evidence,
not producer acceptance of this extension. A real user file exercising it remains
needed for such acceptance. See validation (`docs/maintenance/et/validation.md`) and sources (`docs/maintenance/et/et-format-evidence.json`).

## Observed legacy compatibility

For the previous explicit legacy policy (data is now the file default):

```python
execute(command="python3 -I -S -B \"<skill-root>/scripts/wps_biff8.py\" input.et --compatibility legacy_excel_clsid")
```

This accepts only the exact observed WorksheetClass header GUID in CFB v3, with a root
Workbook/Book stream and every existing allocation, chain and resource check. It does
not rewrite the header or establish the producer from the GUID. Unknown nonzero GUIDs
and nonzero GUIDs in v4 still fail. See container rules (`<skill-root>/references/et-ett-readonly.md`).

Both data and this explicit mode retain an allowlist of MS-XLS 2.3.2 metadata record
IDs as scope/offset/name/raw hex, within the existing metadata budgets. These include
interface markers, workbook options, protection flags, style/theme extensions, printing
and layout settings. They are not executed, semantically validated, rendered or applied;
raw XF/format values are not effective inherited display formats. The result includes
known_metadata_retained_uninterpreted and diagnostic status. Unknown records, external
reference data, macros, encryption and unsupported continuations remain refused.
Shared/array formula support has its own authoritative subset rules (`<skill-root>/references/formula-groups.md`);
this compatibility mode does not relax them.
The allowlist is explicit in LEGACY_GLOBAL_METADATA / LEGACY_SHEET_METADATA and the
bounded additions described under inline text and passive metadata (`<skill-root>/references/biff8-readonly.md#inline-text-and-passive-metadata`); it is not
an option to skip arbitrary record IDs. Other container streams, including ETExtData
and ETSharedLinks, remain undecoded and are listed by path, size and digest. Do not
assume these private extensions have no effect on a full WPS rendering or data model.

One public government ET template was read without changing its bytes: three sheets,
22 text cells, 54 explicit blanks and 18 merges. Sheet names, all nonblank values and
merge sets matched an isolated LibreOffice XLSX conversion. Its Date1904=0 marker also
matched; it contains no numeric date cells or formulas, so those capabilities still lack
real-sample validation. Source/hash and limitations are in
the evidence manifest (`docs/maintenance/et/et-format-evidence.json`). The file is not redistributed and its
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

The default BIFF8 `data` reader has a separate **non-macro static Name** subset:
one fully bounded ordinary Unicode name with one internal absolute `PtgArea3d`,
or a two-area same-sheet `PtgUnion` with strict framing; selected built-in
`Print_Titles` (full-row/full-column areas) and `_FilterDatabase` (one
single-sheet region) have their own equally narrow checks. All are validated
against a self-linked ExternSheet and final BoundSheet8 scope. It retains original
bytes and marks partial; it never evaluates defined-name expressions or changes
saved cells, print settings or filtered rows. The static classifier rejects complex expressions, macros, external XTI, other
built-in names and relative areas. In file data mode, a rejected name payload can
be retained as opaque evidence after BIFF record framing is verified. Strict and
explicit `legacy_excel_clsid` keep their historical Print_Area-only subset.
See defined-name acceptance (`<skill-root>/references/biff8-defined-names.md`)
and pinned real evidence (`docs/maintenance/et/biff8-defined-names-evidence.json`).
The explicit opaque chart policy (`<skill-root>/references/embedded-charts.md`) adds bounded framing/raw retention
for a supported embedded-chart subset without decoding or rendering charts. Record bytes are retained for diagnosis;
metadata retention is not full semantic or layout validation. Strict mode is unchanged.
See real sample validation (`docs/maintenance/et/et-real-validation.md`) for original-file hashes, results,
spot checks, explicit legacy chart rejection, the data/opaque policy and reproducible offline verification.

## Supported records and retained values

Read Number, RK, MulRk, BoolErr, Blank, MulBlank, LabelSst and plain Label cells. Shared and inline strings support
plain compressed-Unicode or UTF-16 text, including bounded SST, Label and formula-cache String Continue records. Encoding may change at a character-data continuation; fixed headers may not split. Rich SST text and its Continue segments are supported with raw FormatRun metadata; phonetic ExtRst bytes are now retained separately without decoding, with a partial diagnostic; unrelated Continue uses are refused. Text such as long leading-zero identifiers stays text. Number/RK values use
an exact Decimal representation of the stored IEEE754 value (which may differ from the
intended decimal); raw numeric bytes are retained. No numeric text coercion occurs.

Preserve sheet order/names/hidden state, source record offsets, raw XF indices/bytes,
custom format strings and merged-range coordinates. Merges are validated for overlap and
bounds but never expanded; non-anchor data is retained, not silently erased. Built-in
format IDs remain IDs; fonts/styles/layout are not rendered or reconstructed.

Known metadata records are explicitly retained as offset/type/raw payload hex: global
CodePage (strict/legacy 1200; data also 874/932/936/949/950/1250/1251/1252), Window1, Font, Style; worksheet Dimensions, Row, Index,
DBCell and Window2. Their internal semantics are not validated or applied. Other records
fail with ID and offset. This is a strict data-record subset, not full MS-XLS grammar or
workbook validity certification; many genuine XLS/ET files will be outside this subset.

## Inline text and passive metadata

Both strict and explicit compatibility modes read worksheet `Label` (`0x0204`) as
plain `XLUnicodeString` text after the six-byte row/column/XF prefix. This is a
bounded legacy inline-cell layout, not support for older BIFF versions. The
compressed form contains Unicode low bytes, not GBK or a CodePage-selected byte
encoding. Empty text remains text; numeric-looking text is not converted. Character
Continue segments use the existing encoding and UTF-16 boundary checks. Fixed string
headers, trailing bytes, duplicate addresses, XF references and all text/cell budgets
remain checked. Data ignores plain Unicode header reserved bits with diagnostics; actual rich/phonetic Label tail layouts and `RString` are not supported. Source
record offsets/IDs and existing typed-cell fields are retained.

Data or explicit `--compatibility legacy_excel_clsid` retains these
records in `uninterpreted_metadata`, with scope, record offset, ID and raw payload:

| Record | Structural checks before raw retention |
|---|---|
| Global DXF (`0x088D`) | Validate the matching FrtHeader, scope flags, XFProps count, property types and byte framing; retain raw payload plus structural property metadata. Differential formatting is not applied to cells. |
| Global Palette (`0x0092`) | Exactly 56 colors and 226 payload bytes; colors are not applied to styles. |
| Standalone worksheet Pane (`0x0041`) | Ten bytes, a valid active-pane byte and a BIFF8 column index. The reserved byte is retained and ignored. A standalone record does not establish a saved view. |
| HorizontalPageBreaks (`0x001B`) | At most 1026 six-byte entries, exact length. Data retains invalid layout with explicit diagnostics; legacy still requires valid, sorted nonoverlapping column intervals. |
| VerticalPageBreaks (`0x001A`) | At most 255 six-byte entries, exact length. Data retains invalid layout with explicit diagnostics; legacy still requires valid columns and sorted nonoverlapping row intervals. |
| CondFmt (`0x01B0`) + CF (`0x01B1`), data only | Declared 1–3 adjacent CF rules; bounded CondFmt range list contained by its bounding rectangle; group boundaries and inner byte partitions checked. Bounded geometry/operator or in-bounds text semantic issues may be retained as raw evidence with partial status; contents are never applied or executed. Structural ambiguity, truncation or misgrouping rejects. |
| DVal (`0x01B2`) + Dv (`0x01BE`), data only | Declared adjacent rule count, bounded four Unicode strings, two formula-length partitions and 1–432 ranges per rule. A bounded geometry/operator or in-bounds text semantic issue may be retained as raw evidence with partial status; rule flags and formula semantics are never enforced, executed or followed. Structural ambiguity, truncation or misgrouping rejects. |

For page breaks, data records `structure.layout_checks_passed`, bounded
`layout_issues` counts/first indices and `layout_applied=false`; invalid bounds,
order or overlap also produce `page_break_layout_not_applied`. This is partial
stored-value recovery, not correction or validation of the print layout. Counts,
exact byte framing and resource limits still fail closed. The invalid
`(row=0, colStart=65535, colEnd=255)` entry in Start-up Expenses ETT is retained.
Its subsequent note drawing and validation records now pass the bounded opaque
subsets below; the returned saved cells still carry partial status.

Conditional-format metadata retains every group's raw bytes, ranges and
rule-length/partition evidence. `cell_value_comparison` lists recognized operators
and exact literal operands. `differential_fill` exposes saved fill pattern and palette
indices when DXFN contains only the fill structure. `inner_payload_validated=false`
marks the limits of that interpretation. Bounded geometry/operator or
in-bounds text semantic faults can be retained with raw evidence and partial status,
while structural ambiguity still rejects. CF12/CondFmt12/CFEx, orphan CF and arbitrary
unknown records remain outside this addition. See the real template results in
`docs/maintenance/et/et-real-validation.md#template-compatibility-iteration-2026-11-03`.
Maintenance references: MS-XLS CondFmt
(`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/03ae6098-bdc2-475b-ba2c-b8aef7882174`),
CF (`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/d6dcadf2-7e07-4f7d-a60a-0f643780225d`)
and DXFN (`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/a1141f1d-f607-45ef-b8dd-4a1f2b27b4f9`).

Data-validation metadata checks DVal's exact 18-byte header and declared adjacent Dv
count, four string byte boundaries, both DVParsedFormula partitions and exact SqRefU
bounds. It retains source bytes and `formula_semantics_validated=false`; validation
flags, prompt/error display, allowed-value decisions and formula contents are not
applied. Semantic issues in a bounded payload may remain as raw evidence with partial
status only for bounded geometry/operator or in-bounds text cases; orphan/interrupted
Dv groups, malformed lengths and ambiguous partitions still fail, including in salvage.
Sources: MS-XLS DVal (`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/f88c3d08-b7fe-4730-9068-39a5dcd41db5`),
Dv (`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/862bbc7c-009a-4fd6-a93e-3e32e591c2f8`),
DVParsedFormula (`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/f30fbda8-d382-4d5a-901f-4691743f83e2`).

Data with opaque drawing policy also retains a narrow, linked note-object chain:
MsoDrawing fragments split exactly at actual Escher client markers, fixed note Obj,
TxO/text/run continuations and linked Note records. Data mode also retains unknown
OfficeArt atoms and recursively framed containers in this chain with uncertain cell
coverage; linked record and split validation still applies. See the precise scope and
omissions in `<skill-root>/references/embedded-charts.md#fragmented-note-drawings-data-only`.

Zero-entry page-break lists are allowed. These additions share the existing
metadata byte/count limits and compatibility diagnostics; they do not apply print
layout, hide/filter cells, render colors or claim complete metadata validation.
The standard `Window2` group can also be read in strict mode. It exposes saved freeze counts and the active pane's cell in `sheets[].views`; see `<skill-root>/references/saved-visibility.md#保存的工作表视图`. Strict mode still refuses the standalone additions above. Unknown BIFF records
are not skipped. CFB allocation checks remain in force; the separately documented
file-data FAT-count and zero-tail compatibility rules do not bypass them. Explicit
strict minor `0x003E` (data/legacy also accept observed `0x003B` with diagnostics),
explicit Date1904, policy-specific CodePage validation and explicit chart rejection
are unchanged. Missing date-system information is never silently replaced by 1900.

Source references are recorded in the format evidence (`docs/maintenance/et/et-format-evidence.json`).
The original Palette/Pane additions have synthetic coverage; the newer conditional-format, note and validation groups have the limited real ETT evidence linked above. Unlinked BIFF Note,
HLink and RString, arbitrary XML extensions and private WPS records remain outside
this addition. Actual verification results are in validation (`docs/maintenance/et/validation.md`).

## Dates and formula caches

Require explicit Date1904, preserving the 1900/1904 system. A numeric date serial stays
numeric with raw XF/number-format ID and any explicit custom format text. The separate
`numeric_interpretation` explains supported formats and reports the parent StyleXF when a
linked CellXF agrees with its stored format ID. See `<skill-root>/references/numeric-interpretation.md`
for the supported subset. Calculations continue to use the saved numeric value.
Serial 60 in the 1900 system is not invented as a real calendar date; fractional serials
and the original bytes remain available for later, separately verified interpretation.

Formula records retain token bytes, ancillary bytes, raw flags and cache bytes. A bounded
standalone expression can also return `formula.formula_text`. It supports numeric and
Boolean constants, same-sheet cell and range references, basic arithmetic and a small
function list including SUM and AVERAGE. Shared, array, external and unsupported tokens
remain opaque. The reader never evaluates expressions. Shared/array formula rules (`<skill-root>/references/formula-groups.md`)
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

```python
execute(command="python3 -I -S -B \"<skill-root>/scripts/wps_biff8.py\" input.xls --sheet Data --range A1:F20 --header-rows 1:3")
```

Explicit header reconstruction reuses the explicit merge-only header rules in `<skill-root>/scripts/wps_biff8_headers.py`,
including merge-only propagation, stable column IDs, clipping/cross-boundary rejection and
non-anchor-data checks. Formula headers default to rejection; --header-formulas cached
is an explicit opt-in with a warning, and missing/error caches still fail. Dates remain
numeric serials in paths. No implicit spreadsheet-to-OOXML reconstruction is performed.

JSON schema biff8_readonly_v1 contains typed sheet cells, merges, date/format metadata,
source file/stream digest, unparsed metadata and diagnostics. biff_parsed true means this
limited record subset passed; it is separate from the container-only report and native
ET/ETT acceptance. Unsupported inputs exit 1 with no partial cell report; failures now preserve known unsupported/encrypted/resource_limit classes and recorded locations (other ValueError failures retain invalid_or_unsupported); subset extraction
exits 0, but read_biff8_subset_with_diagnostics requires review. Missing formula caches are
explicit diagnostics, not evidence of complete calculated data. No file is written.
Python APIs: parse_stream(bytes), read_file(path), reconstruct_headers(result,...).
Native OOXML editing APIs do not accept this separate schema. If the xlsx skill is
available, its typed-JSON region/profile adapter can analyze a saved report; it is
optional and is not a dependency of this package. Its current WPS JSON adapter still limits merges to 256 per sheet, so large-merge reports supported by this reader are not automatically accepted downstream.

## Rejection rules and validation limits

Reject unknown/private records outside the default file-data metadata allowlist,
FilePass, unsupported BOF versions, offset aliases/gaps, truncated/oversized payloads,
nonfinite numbers, duplicate cells, bad SST/XF references, malformed strings/merges,
orphan String records and trailing unbound BIFF data. Default file data mode retains
only correctly framed unknown global/worksheet metadata within its existing budget;
no record is silently skipped. Present but opaque formulas remain explicitly unverified.

Limits: stream 8 MiB, records 100000, cells 200000, sheets 128, XFs 4096, SST strings
200000, merges 65536 per workbook, uninterpreted metadata 1 MiB/4096 records, diagnostics 1000, JSON output
16 MiB. Payloads over 8224 bytes remain outside the subset. String-specific Continue
handling is limited to SST character/run/opaque phonetic tails and plain Label/cached
String character data. Separate bounded adjacent continuation is supported only for
ExternSheet through its declared XTI byte count and the linked note drawings described
in `<skill-root>/references/embedded-charts.md#fragmented-note-drawings-data-only`;
other Continue uses remain outside the subset. Header regions are capped at 200000
cells and BIFF8's 65536 rows/256 columns. Inherited CFB chain,
allocation and memory budgets apply before record decoding. At the CFB layer, only the
default BIFF8 file-data route accepts an all-zero trailing remainder shorter than one
sector after full-sector allocation and declared-stream checks succeed; it reports a
warning/partial result. Nonzero tails, truncated referenced sectors and trailing
unbound BIFF data still reject. Padding is not treated as stream content; see CFB
investigation (`<skill-root>/references/et-ett-readonly.md#default-biff8-data-trailing-padding`).

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
manifests moved from xlsx to wps. `<skill-root>/scripts/wps_biff8_headers.py` retains only the pure address and
merge-header algorithm from xlsx_headers/stdlib_xlsx; it has no native OOXML dependency.
The source is recorded in that module; future header-policy changes must regression-check
both standalone algorithms. No adjacent skill is imported at runtime.

Plain SST and cached String continuation behavior follows MS-XLS
XLUnicodeRichExtendedString: fixed fields stay in one record, character continuations
start with fHighByte, and UTF-16 units do not split across records. Phonetic tails are retained raw with a partial diagnostic; rich SST character/run continuation is described below. Tests use independently specified A + Chinese + astral character,
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
Fixed headers stay in one record. Per-string and remaining aggregate budgets are
checked before collecting run data. The result retains source run bytes and supported
text segments once per SST index. `rich_string_fonts` maps saved font indices to
limited properties. Rendering remains partial. Phonetic extensions are retained as opaque ExtRst bytes, and rich/phonetic cached String records remain unsupported. See `<skill-root>/references/biff-rich-continuation.md`.

For shared/array groups, use formula-group rules and evidence (`<skill-root>/references/formula-groups.md`)
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

Raw values and cache provenance remain unchanged. See `<skill-root>/references/numeric-interpretation.md` for the bounded CellXF format and inheritance rules. See saved visibility (`<skill-root>/references/saved-visibility.md`) for Row/ColInfo flags and raw filter metadata. Default extraction includes all cells. Row selection keeps unknown states; filter conditions are not decoded or executed. The unified entrypoint routes OOXML packages to the OOXML spreadsheet reader (`<skill-root>/references/ooxml-spreadsheet.md`).

## ET parser capacity and failure locations

See current capacity and stability rules (`<skill-root>/references/et-parser-stability.md`) for bounded phonetic SST retention, expanded shared-string/merge counts, sweep-based merge validation and precise record failure locations. These capacities apply to BIFF8 only; they do not change OOXML limits. The WPS-only header adapter accepts the increased merge-list budget while preserving region/anchor rules; the neighboring XLSX implementation is unchanged.

## DXF selected property interpretation

`0x088D` is the standard MS-XLS DXF global record, not a CFB format or private WPS record. Default data and explicit legacy_excel_clsid preserve its validated framing, raw payload and partial diagnostics; explicit strict still refuses it. A worksheet-scoped DXF is refused. Existing `uninterpreted_metadata[].structure.properties` fields remain unchanged; additive `property_interpretations` refers to each property's original index and ID, never an effective cell style.

The reader checks selected property values without making formatting a prerequisite for reading stored cells: boolean flags (0D/0E/14–17/1C–21/2B/2C) accept only 0/1; horizontal alignment 0F accepts 0–7 or FF, vertical 10 accepts 0–4, reading order 13 accepts 0–2; indentation 12 is unsigned 16-bit 0–15, relative indentation 2A is signed 16-bit -15..15 or 255; charset 22 is one byte, family 23 is 0–5, text size 24 is unsigned 32-bit 20..8191 twips. Font name 18 validates a two-byte UTF-16 length prefix, exact bytes, at most 32 UTF-16 units and strict decoding. justifyDistributed 15=1 requires an explicit horizontal alignment 0F=7. Duplicate properties are retained independently; their precedence is not inferred.

`validation_scope=framing_and_selected_property_values`, `effective_formatting=unknown` and `uninterpreted_property_count` distinguish selected values from unimplemented legal complex properties. Colors, borders, gradients, number formats and other unimplemented property blobs remain raw, with partial status; their semantic validity, application, theme resolution and rendering are not certified. No style flag changes stored cell values, formula caches or merge ranges. In data and legacy_excel_clsid readers, `formatting_policy=preserve` isolates unsupported XFProp types, invalid scalar/font payload values or lengths, scope flags and formatting dependency conflicts inside an already bounded DXF record. Raw record/property bytes remain unchanged; `formatting_status=uninterpreted_due_to_issues` and `formatting_issues` identify property index/ID/offset and cause. Invalid properties are not interpreted or applied. The top-level `dxf_formatting_issues_retained` issue includes record offset, metadata_index and count; partial remains explicit. Reliable sheets/cells/caches/merges remain available; these formatting omissions do not alter stored cell values or certify their visual appearance.

Outer BIFF and DXF/XFProp framing must still be readable and bounded: truncated headers, cb smaller than its header or beyond the record, count/size mismatch, trailing bytes, wrong inner record type, encryption and resource overflow still reject. Unknown BIFF data records are not covered by this exception. The direct `validate_dxf` helper retains its strict default for explicit validation; readers deliberately use preserve. BIFF compatibility=strict still refuses the DXF record itself; changing formatting interpretation does not silently retry or widen strict. No arbitrary record scan or value guessing.

Failure JSON adds `diagnostic_context.layer` (container, routing, BIFF record, MS-DOC, OOXML, input/output/options or unknown) without replacing status/location/message/exit codes. For a known DXF failure it adds record_name=DXF, normalized record_id=0x088D and the data/strict scope; BIFF location retains the actual compatibility_mode. This distinguishes a valid container with unsupported records from a container rejection. It is diagnostic, not authorization to retry with weaker settings or treat damaged metadata as valid. CFB policy failures identify unsupported minor/CLSID profiles separately from allocation conflicts and bounded reader limits; unknown cause remains explicitly a validation-or-subset rejection.

Primary maintenance references (runtime does not access the network):
- DXF: `https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/ea2a96e1-5ec3-43d0-8b97-9fa0b7e9fc27`
- XFProp: `https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/ff15a3cd-f2fb-4fc6-aa52-55f93c5ba121`
- LPWideString: `https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/1f77dc80-3fec-4b4b-a55d-3a41292d1dc4`
- Compound File Header: `https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-cfb/05060311-bfce-4b12-874d-71fd4ce63aea`
