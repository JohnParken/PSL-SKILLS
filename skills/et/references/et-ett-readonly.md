# ET/ETT investigation and bounded compound-container reading

维护资源说明：文中 `docs/maintenance/et/` 与 `skills/et/scripts/test/` 均以宿主确认的仓库根目录为基准，仅供源码维护，不随运行包分发；运行任务不读取或执行。没有仓库时跳过维护资料，不按 Skill 安装目录拼接。

路径占位符说明：`<skill-root>` 是 ET Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 ET 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

Follow the path and first-operation rules (`<skill-root>/SKILL.md#执行环境约束与首步自检`)
before using this reference; internal resources and task data have different path bases.

The main `"<skill-root>/scripts/read_et.py" input.et` / `"<skill-root>/scripts/read_et.py" input.ett` commands now dispatch to
the separate BIFF8 subset reader with one content-first tolerant policy; there is no
strict or legacy reading mode any more. Old `--compatibility` / `--salvage-unknown-metadata`
options are accepted as deprecated no-ops and recorded as `compatibility_option_ignored`
or `salvage_option_ignored`; see current reading policy (`<skill-root>/references/biff8-readonly.md#default-tolerant-policy`).
This does not change the CFB-only inspector or add WPS private-record semantics. Real-file BIFF8 subset checks now include original ET and ETT downloads;
see real sample validation (`docs/maintenance/et/et-real-validation.md`). Native WPS private-record semantics
and general ET/ETT compatibility remain unverified.

Run the entrypoint self-check (`<skill-root>/SKILL.md#执行环境约束与首步自检`) and require exit 0,
environment-ok. Confirm the input is authorized and readable in the task directory. This
reader writes no files; no write probe is needed for inspection. Resolve `<skill-root>`
from the actual SKILL.md and replace it before running commands with task-relative input.
Python 3.9+, standard library only; no Office launch, network calls or installations at runtime.

## Contents

- Evidence and provenance (`<skill-root>/references/et-ett-readonly.md#evidence-and-provenance`)
- CLI and support levels (`<skill-root>/references/et-ett-readonly.md#cli-and-support-levels`)
- CFB header compatibility (tolerant policy) (`<skill-root>/references/et-ett-readonly.md#cfb-header-compatibility-tolerant-historical-policy-removed`)
- Container subset and safety (`<skill-root>/references/et-ett-readonly.md#container-subset-and-safety`)
- Tolerant CFB padding and header handling (`<skill-root>/references/et-ett-readonly.md#tolerant-cfb-padding-and-header-handling`)
- Tests and remaining work (`<skill-root>/references/et-ett-readonly.md#tests-and-remaining-work`)

## Evidence and provenance

Initial research checked 2026-10-22; real-template follow-up checked 2026-10-23. Sources were read for specifications/support/licensing; no
third-party parser code or test files were copied, installed or bundled. Exact source URLs
are recorded in the evidence manifest (`docs/maintenance/et/et-format-evidence.json`) as bibliographic provenance,
not runtime resources or downloadable dependencies.

| Source | Finding and implementation use |
|---|---|
| Microsoft MS-CFB, published revision 12.0, 2024-04-23 | Documented general-purpose compound container; basis for this independent implementation, not an ET record specification |
| CFB header and directory entry | Sector sizes, header fields, directory offsets and UTF-16 names |
| SheetJS format documentation | Lists ET read support and describes proprietary extensions over BIFF8; this is evidence for a future investigation, not proof that every ET/ETT variant is BIFF8 |
| SheetJS repository/license | CE Apache-2.0; no code incorporated; documentation does not establish all ETT variants or WPS versions |
| olefile API and license | Mature generic OLE stream reader, not native ET semantics; BSD-style terms plus inherited PIL notice; not installed/copied |
| libwps upstream | Microsoft Works import library; its name does not establish Kingsoft WPS ET support |
| WPS format list | Vendor service lists ET/ETT support, but that page is not a binary layout specification |

No real ET/ETT binary sample is bundled in the maintained skill. An earlier public ET template
was tested locally; its source, original SHA-256 and limited comparison results are
recorded in the evidence manifest. No authorized user sample was supplied. The public
SheetJS/test_files repository was disabled when checked, so it was not usable as a
licensed/versioned ET corpus. Distributable tests generate synthetic CFB structures in
memory under this repository's license; creator is the test builder, CFB versions are
3/4, WPS producer/version is not applicable.
Renamed XLS/XLSX or simulated containers are never counted as real ET/ETT acceptance.

## CLI and support levels

```python
execute(command="python3 -I -S -B \"<skill-root>/scripts/read_et.py\" input.et")
execute(command="python3 -I -S -B \"<skill-root>/scripts/wps_cfb.py\" input.et")
```

Content, not extension, selects the container probe. Non-CFB data is rejected by wps_cfb;
read_wps dispatches supported contents and diagnoses unsupported inputs without guessing ET.
The CFB report lists component-array stream paths, lengths and SHA-256 digests, not cells
or full stream contents. Workbook/Book names are only workbook_stream_candidates.

Three separate claims must remain separate: container_readable / BIFF readable / native
ET/ETT accepted. The CFB reader only emits support_level cfb_container_only; biff_parsed,
native_et_ett_parsed and cells_decoded are false. A consistent header alone no longer
passes the compound probe. The unified read_wps entrypoint then checks the root streams
and reads a supported document or BIFF subset, returning ok/partial or a specific refusal.
Known EncryptionInfo/EncryptedPackage streams are refused as encrypted; generic private encryption is not decoded.
A corrupt or outside-subset container returns invalid_or_unsupported, not a fake empty sheet.

## CFB header compatibility (tolerant, historical policy removed)

All public entries now accept compound files with a zero header CLSID, the observed Excel
WorksheetClass GUID, any other nonzero GUID, any minor version, nonzero reserved header
bytes, nonzero v4 header padding and any trailing remainder. Each difference is recorded
as a `issues[]` diagnostic (`nonstandard_header_clsid`, `cfb_nonstandard_minor_version`,
`cfb_nonzero_reserved_header`, `cfb_nonzero_header_padding`, tail diagnostics) and the
result becomes `partial`; the sector, FAT and stream validation below is unchanged.
Historical explicit `--compatibility legacy_excel_clsid` / `strict` policies are no longer
selected: those arguments are ignored with `compatibility_option_ignored`. A root
Workbook/Book stream must still be locatable after regular allocation validation; unknown
GUIDs no longer block reading because the CLSID is metadata. Invalid foundational
allocation tables still reject; stream-chain defects stop the chain and retain validated prefixes with issues. All length, chain, cross-link, orphan-tolerance, name-tolerance, encryption
and quota checks are retained. The BIFF entrypoint applies the same tolerant policy,
documented in the BIFF reference (`<skill-root>/references/biff8-readonly.md#observed-legacy-compatibility`).

The standalone CFB CLI exits 0 after container diagnosis, including encrypted_unsupported;
invalid/unsupported structure or I/O returns 1. The general inspector retains exit 0 for
completed diagnoses; inspect JSON status and support flags, not exit alone.
Python API: CompoundFile.load(path).report() and read_stream([component,...]) -> bytes.
read_stream refuses recognized encrypted payloads; it never extracts to a filesystem path.
Raw stream bytes are not a spreadsheet model and cannot be fed directly to region/profile.

## Container subset and safety

Support version 3 (512-byte sectors) and version 4 (4096-byte sectors), header and extended
DIFAT, FAT, directory graphs, normal streams and 64-byte mini streams/MiniFAT. Check bounds,
chain termination/length, cycles and cross-linked allocation. Invalid foundational
FAT/DIFAT tables remain fatal. Stream-level defects stop that chain before reading
an invalid sector; validated prefixes may be retained with issues and uncertain coverage. Noncanonical but readable producer quirks are
retained as issues and do not abort the read: duplicate case-insensitive names, illegal
stream names (that stream is skipped), unreachable nonempty directory entries, unreferenced
allocated sectors/mini sectors, nonstandard header fields and padded tails. Never follow
OLE links, URLs or external objects, execute macros, decrypt or decompress nested streams.
Directory graph checks do not constitute full red-black ordering/conformance validation.
The CFB entrypoint interprets no spreadsheet types, dates, merges, formulas or styles.
A separate BIFF8 record reader (`<skill-root>/references/biff8-readonly.md`) provides a bounded record
subset and explicit header adapter, without changing CFB-only or ET compatibility claims.

## Tolerant CFB padding and header handling

The ET/ETT file-data route accepts a final CFB remainder shorter than one sector regardless
of content: after the complete sectors pass FAT/MiniFAT chain, directory graph and declared
stream-length validation, the unaddressable tail is retained as an issue and the readable
prefix is used. The read remains read-only and reports partial. Truncated referenced sectors,
stream-chain defects or declared-length mismatch stop the chain and retain validated prefixes
with issues; invalid foundational allocation tables still reject. This does not weaken any
bounds or permit scanning for a later record. A zero declared FAT count is recovered when
DIFAT entries describe a valid allocation (`cfb_zero_fat_count_recovered`). Direct
byte-parser entrypoints share the same tolerant policy; the old strict/legacy options
no longer narrow it.

Limits: input 32 MiB (+ one limit-detection byte), individual user stream 32 MiB, aggregate
user streams 32 MiB, directory 4096 entries, graph depth 32, 512 FAT and 16 DIFAT sectors.
Header-declared sizes/counts are bounded before materialization; over-limit stream
declarations are clamped to the remaining budget with an issue rather than aborting when a
readable prefix exists. CFB has no compression
in this reader; existing OOXML ZIP member/uncompressed limits remain separate. No changes
were made to permit unbounded compression or to trust extension-based dispatch.

## Tests and remaining work

Synthetic tests cover v3/v4, Chinese names/raw bytes, normal/mini/fragmented chains,
extended DIFAT, nested storages, empty streams, cycles, cross-links, overlong declarations,
malformed pointers/names, orphan allocations, encrypted stream rejection and CLI no-write.
These verify container behavior only, not WPS or Excel interoperability.

Next obtain permitted, versioned WPS-generated ET/ETT samples plus corresponding XLSX and
producer/platform metadata. Establish stream signatures and private record rules, then
extend the documented BIFF8 subset only after verifying ET private records and comparing
real sheet/type/date/merge data. The separate BIFF8 tests are still synthetic. Unknown records must not be silently skipped to claim full data
recovery. Formula caches would remain unverified with raw representation, never computed.
For files outside the subset, the skill stops with a diagnostic. Independently of this
standard-library runtime, a user may use a trusted local WPS copy to Save As a new XLSX
and verify it; renaming is
not conversion. The earlier ET comparison and current original-template checks are limited evidence;
full native ET/ETT acceptance has not been performed. Real formula-cache bytes were retained
and cross-checked, but their calculation correctness and real numeric-date interpretation remain unverified.
