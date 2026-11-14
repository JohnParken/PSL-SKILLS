# WPS chart presentation and internal workbook values

Version: `20261114.1929.1228`.

- Added bounded `presentation` aligning category/value indices, retaining exact raw strings, simple decimal/percent display, per-row missing fields and saved-value provenance. No normalization or inferred counts.
- Optional internal transitional XLSX package supplementation accepts only explicit same-sheet A1 vectors. Existing chart cache points and original cache status/issues/ranges remain intact; workbook enrichment has separate coverage and point provenance. External links are never followed or exposed by this layer.
- Bounded ZIP/XML, expanded bytes, cell/string counts, references, points and duplicated output text. Resource exceptions still fail; malformed optional workbooks preserve usable chart caches. No formula evaluation, OCR, image rendering or external program dependency.
- Tests use synthetic inputs only. No private file, extract, source reference, content-derived fixture or identifier was added to the repository. Separate authorized local read-only verification completed; source unchanged, no upload or Git operation.

## Validation

macOS, Python 3.14.10, Expat 2.7.4:

- WPS full suite: 727 passed, no skips.
- New presentation tests: 7; new workbook tests: 13, including public binary read entry.
- Isolated read-only runtime package: 5 existing WPS/package tests plus 1 embedded-workbook entry test passed.
- Generic runtime packager: 11 passed.
- Full WPS Pylint errors-only: exit 0.
- Strict skill structure and project-doc checks: pass. git diff --check: clean.

## Limits

Simple number formats only, not Excel formatting/layout fidelity. Original chart partial state remains even if referenced stored values supplement a gap. Supplementation does not refresh data or verify rendered series, dates, styles or original producer. No chart title supplementation, named/formula/multi-area/3D/external ranges, old XLS, macros or full workbook engine. Private real document was not a test of internal workbook fallback; fallback was tested with synthetic inputs. Linux, Windows, WPS/Office rendering and host/model delivery were not tested. No commit or push.
