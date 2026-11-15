# ET / WPS / XLSX / table-workflow final integration, 2026-11-15

## Scope and first integration baseline

Performed directly on macOS 27.2.1 arm64, Python 3.14.10 / Expat 2.7.4. No commit, push, dependency installation, persistent distribution ZIP rebuild or private sample access. Existing unrelated untracked material was left untouched.

- et: `20261115.2326.5498`
- wps: `20261115.2306.2617`
- xlsx: `20261115.2323.4212`
- table-workflow: `20261115.2323.4212`

ET manifest includes both `wps_biff5.py` and `et_parse_page.py` plus the BIFF5 reference. WPS table-reference contents list is valid. Current README/docs version declarations agree. ET consumer reference prose and executable example were corrected to include the distinct BIFF5 schema; the new parse-page command follows the repository execute-example contract. No historical snapshot was changed.

## Verified results at first integration

|Scope|Total|Passed|Skipped|Failed/error|
|---|---:|---:|---:|---:|
|ET complete suite|728|728|0|0|
|WPS complete suite|755|755|0|0|
|XLSX complete suite after review fixes|418|418|0|0|
|table-workflow repository suite after review fixes|191|191|0|0|
|Repository integration final rerun|390|389|1|0|
|All-Skill readonly independent packages|18|17|1|0|
|DOCX suite|493|493|0|0|
|OFD suite|199|199|0|0|
|PPTX Skill suite|126|126|0|0|
|PPTX repository suite|3|3|0|0|
|Artifact lifecycle suite|21|21|0|0|
|Skill standards suite|44|44|0|0|

Rows overlap: workflow and independent-package tests are also included in the 390 repository tests. Do not sum the rows as unique tests. The single repository/package skip is TL-DOCX read/write requiring missing python-docx 1.2.0.

The full-repository aggregate also attempted TL-DOCX unit discovery: 4 actual tests passed and 26 modules failed to import because python-docx is unavailable. These are dependency/import errors, not 26 executed business assertions. Consequently the full-repository aggregate is NOT all green. `-I -B` inspection also confirmed the module is absent, independently of the aggregate runner's `-S`. No dependencies were installed.

The initial aggregate's repository result had one failing documentation example check (console fence in the new pagination reference). It was fixed; its 3 focused checks passed and the complete 390-test repository suite was rerun successfully. Initial results remain in `integration-20261115-results/summary.json`; final overrides are `post-review.json` and `repository-final.json`. Generated verbose temporary logs were removed after summaries and failure reasons were recorded.

All 12 Skill strict checks, project documentation/link/version checks, and git diff whitespace checks passed. AST syntax and <=200-character Python lines were checked for 24 changed/new Python files; Skill files also satisfy the <=1000-line limit. Pylint is unavailable and was NOT executed; these checks do not establish zero Pylint errors.

## Independent review and static evidence

Astra review identified and verified fixes for malformed `extraction_schemas` capability declarations and BIFF5 out-of-grid region queries. The provider must advertise list[str] including the distinct schema; string/dict/null/mixed lists reject. BIFF5 queries outside IV16384 reject instead of appearing blank. Partial, unknown coverage, missing/error caches, explicit cached opt-in, precision and original inputs remain protected.

79 human-authored selection cases were retained after reviewing affected ET/WPS and XLSX/workflow boundaries; only current entry fingerprints were updated. The 58 planned-loading profiles were regenerated and freshness checked. Neither artifact is a model-selection, DeepAgents, host usage or actual token measurement.

Historical count clarification: earlier prose calling the prior 379-test repository run “379 passed including one skipped” should be read as 379 total, 378 passed and one skipped. The original historical record is not rewritten.

## Limits and linked evidence at first integration

- BIFF5/7 is a bounded saved-value core, not full format support. The public Excel95 fixture yielded 195 stored cells; 25 explicit mapped saved-value/type comparisons passed. Same-coordinate whole-sheet comparison had 37 differences among 101 nonempty cells; no whole-sheet equality claim. See [BIFF5/SST evidence](biff5-sst-20261115.md).
- New SST recovery and edge combinations have synthetic evidence, not real noncanonical SST acceptance.
- OOXML parse-time paging retains full validation but only page cell payloads; inherited row/column styles fall back to full parsing. The reported 64.4% tracemalloc peak reduction is a single synthetic 50,000-cell observation, not RSS or a general promise. See [paging evidence](parse-page-20261115.md).
- WPS nested grouping has synthetic ownership/grouping tests; the attempted public nested candidate was rejected before nested parsing. No positive real nested-layout acceptance. See [WPS evidence](../wps/nested-grouping-20261115.md).
- Cross-provider plan/apply and readonly installation tests are synthetic. See [handoff evidence](../table-workflow/biff5-handoff-20261115.md).
- Linux/Windows, real WPS client rendering, other legacy producers, production host behavior and Pylint remain unverified.

## Latest WPS follow-up: real nested fixture compatibility

This section supersedes only the earlier WPS version, WPS test count and lack of a positive public nested case. Current WPS version is `20261115.2340.3260`; ET/XLSX/table-workflow remain at the versions listed above. The earlier aggregate and earlier public-fixture refusal remain historical evidence.

The unchanged public POI innertable.doc is now read through a tightly bounded maximum-only noncanonical compatibility path: exact known base/array counts with explicitly absent FIB extension, independently checked header/SEPX recovery, and empty closed-cell nested-row terminator precedence. Standard still rejects the noncanonical FIB. Overall status stays partial. This does NOT claim complete specification conformance, complete outer-table reconstruction, WPS producer provenance or visual layout.

Implementation and independent Astra review both confirmed the public original in memory: child cells [[1,2],[3,4]], child CP [11,21), parent cell CP [9,23), parent row CP [7,26). The independent review additionally passed 74 targeted/legacy tests. Source/hash and the upstream independent expectation are recorded in [WPS evidence](../wps/nested-grouping-20261115.md).

Final reruns after WPS freeze:

- WPS complete suite: **758 total, 758 passed, no skips/errors** (14.397 s).
- Repository integration: **390 total, 389 passed, one dependency skip, no failures** (108.947 s).
- All-Skill readonly independent packages: **18 total, 17 passed, one dependency skip, no failures** (7.158 s).
- All 12 strict checks, docs/link/version checks and diff checks passed. AST/line-length checks passed for all 28 changed/new Python files; relevant Skill files remain within 1000 lines.
- Current selection fingerprint and planned-loading snapshot were refreshed after manual boundary review; snapshot freshness passed. Historical snapshots were not modified. WPS manifest needed no additions because all changed runtime modules were already included.

Results are saved separately as `integration-20261115-results/wps-followup.json`; no earlier result file was overwritten. Repository/package skipped item remains TL-DOCX requiring python-docx. The earlier full-repository TL-DOCX import errors remain unresolved, and Pylint remains unavailable. Linux/Windows, real WPS producer/client validation and production host behavior remain unverified. No private sample was read, no dependency installed, no distribution ZIP rebuilt, and no commit or push performed.
