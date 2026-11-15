# WPS nested table grouping evidence, 2026-11-15

Scope: additive child-table indexes over verified nested row fragments, not complete binary DOC tables or layout. WPS version `20261115.2306.2617`. Work performed directly on macOS arm64 with Python 3.14.10 / Expat 2.7.4. No commit, push, private input, saved public fixture or distribution ZIP change.

## Implementation and compatibility

`nested_row_evidence.rows[].cells[].nested_tables[].tables` indexes the existing child row fragments with first-row/count, depth, story-local CP identity, grouping status, boundary evidence and parent-cell source span. A child run is split using the existing stored row identity comparator rather than assuming all adjacent rows are one table. Unknown properties or protection bookmarks force partial groups. Parent links are exposed only after all enclosing cells and rows close. Existing text, legacy flat rows/tables and the old run-level grouping marker remain unchanged. Nested merges, effective style and visual layout remain unknown; the overall read stays partial.

Specification: [MS-DOC 2.4.3](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-doc/5b45f0e7-7760-4fdb-af88-0146de2feb4c). Depth transitions delimit runs; stored table properties and applicable first-paragraph frame differences control adjacent-row grouping. Missing leading parent paragraphs and incomplete structural properties still refuse guessed ownership.

## Synthetic verification

- WPS environment self-check: passed with Python 3.14.10. System Python was below 3.10 and was not used for acceptance.
- Nested suite: 9 tests passed, including 4 new tests covering joined/split rows, unknown properties/bookmarks, distinct parent cells/child runs and unclosed parent rejection.
- Full WPS suite: 755 tests passed, no skips (14.453 seconds).
- Independent readonly package test added for nested ownership; its initial execution passed. Full package suite initially failed only the new reference's missing table of contents and skipped unrelated tl-docx because python-docx was absent. Table of contents corrected; final rerun recorded below.
- Pylint not executed: selected interpreter reports `No module named pylint`; no installation or runtime dependency added. No claim of zero Pylint errors.
- Linux/Windows, WPS client rendering and real positive nested-table ownership remain unverified.

## Public real samples (downloaded in memory only)

Apache POI public fixture directory was queried, then these exact raw files fetched without altering the bytes:

- [innertable.doc](https://raw.githubusercontent.com/apache/poi/trunk/test-data/document/innertable.doc): 11776 bytes; SHA-256 `5b085e66bad882c3c58ec49d7547b42f75141babd4a5e8efcf634b995e44bb3f`. Expected candidate: a nested table fixture by upstream filename, not independently certified producer/layout. Actual: `unsupported`, `Inconsistent FIB version/array/extension layout`, before nested parsing. This is NOT a positive nested-table acceptance. Existing consistency validation was not weakened.
- [simple-table.doc](https://raw.githubusercontent.com/apache/poi/trunk/test-data/document/simple-table.doc): 19456 bytes; SHA-256 `67d7fe8e0555c16516354498a5935f24eeef90beb5efbf526f93a1d6c734da43`. Actual: partial, 240 text characters, zero verified flat rows. This is only a real-input text/degradation smoke test, not evidence of table completeness.

README, docs index and WPS guide updated for version and bounded capability. Description was reviewed and retained: it already limits binary table support and does not claim complete layout. No runtime manifest change was needed because the modified nested helper was already included.

## Final checks

- WPS strict structure: passed after adding the reference table of contents.
- Project docs/link check and `git diff --check`: passed.
- Generic runtime packager: 11 tests passed.
- Final independent readonly **WPS-scoped** package run: 7 tests passed, no skips, including creation/edit/read, chart cache, grouped merges, nested ownership, stored PNG, exact untransformed package inventory and strict installed checks. Scope explicitly selected `wps` and `skill-development-standards`, using the existing package harness.
- The attempted full all-Skill rerun was blocked before any test by concurrently added ET `scripts/et_parse_page.py` not yet classified in its runtime manifest. This unrelated ongoing change was not edited. The WPS-scoped run is not claimed as a full all-Skill pass.
- AST syntax and <=200-character lines checked for all four changed Python files; all passed. This is not a substitute for unavailable Pylint.

After the ET maintainer classified the new module, the full independent package suite was rerun: 18 tests, 17 passed and 1 unrelated tl-docx skip (python-docx 1.2.0 unavailable to Python 3.14.10), exit 0, 7.174 seconds. No failures. WPS source is frozen for integration.

## Follow-up: real nested fixture compatibility

The earlier refusal is retained above as historical evidence, not the final result. Exact FIB fields in the unchanged POI fixture: base nFib `0x0101`, 136 fc/lcb pairs, extension-length word at byte 1242 is zero; WordDocument and cbMac are both 5156 bytes. The known base/array combination is exact, but the extension is explicitly absent. [MS-DOC 2.5.1](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-doc/9aeaa2e7-4a45-468e-ab13-3f6193eb9394) requires two words for that version; [2.5.14](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-doc/fe661052-9c88-4ae1-aec4-44799b2b4777) and [2.5.15](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-doc/acb08ace-f268-4879-a998-de592103a70e) define how a zero extension uses the base version and consumes no extension data.

New maximum-only compatibility accepts exactly known base versions with matching pair counts and explicit cswNew=0, marks `fib_extension_absent_compatibility`, and uses the actual FIB extent. It does not call the file conformant. Strict/standard still refuses; unknown versions, mismatched counts, wrong nonzero extension lengths, truncation, encryption, overlap and budgets stay enforced.

Two further concrete findings were resolved: (1) a bounded 52-byte all-zero header PLC is present despite no header story. Existing maximum mapping recovery now records this independent semantic error, repeats section/SEPX ownership validation without slots, and preserves the main body. (2) both nested row marks have PFInnerTableCell and PFInnerTtp; explicit row marks take precedence only on empty paragraphs after closed cells, preserving the row-boundary checks rather than inventing an extra cell.

Independent expected structure: Apache POI [TestTableRow.java](https://raw.githubusercontent.com/apache/poi/trunk/poi-scratchpad/src/test/java/org/apache/poi/hwpf/usermodel/TestTableRow.java) asserts two inner rows and two cells in the first row. The upstream also expects three outer rows with three cells each; this reader does NOT claim a single complete outer table, because legacy flat rows omit the nested middle row and grouping remains partial.

Actual final unmodified fixture assertions passed: child rows `[1,2]` and `[3,4]`, 2x2 cells, child table CP `[11,21)`, parent cell CP `[9,23)` with direct text E/newline/F, parent row CP `[7,26)`, stored child grouping and no nested-row issues. Overall result remains partial, and standard still refuses the noncanonical FIB. Sample SHA-256 is unchanged from above; fetched in memory only, no private inputs. This establishes one public real binary-DOC nested case, not WPS producer provenance or visual/full-table acceptance.

Three additional synthetic tests cover the missing-extension version matrix and negative safety cases, absent-header-story recovery with SEPX overlap refusal, and dual inner markers with nonempty row rejection.

Follow-up WPS version: `20261115.2340.3260`.
