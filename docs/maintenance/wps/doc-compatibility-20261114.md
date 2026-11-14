# WPS DOC compatibility boundaries

Version: `20261114.1941.2640`.

## Implementation

The application release is not the MS-DOC nFib layout. The reader already routes actual CFB/MS-DOC, RTF and OOXML Word content independently of a `.doc` suffix. Five supported binary layouts and both Table streams now have a combined default-maximum regression matrix, plus real CLI `.doc` content-routing checks. These are synthetic contracts, not WPS product certification.

Fixed one independently reproducible optional-object failure: in maximum only, a structurally bounded PlcfSpa reference pointing at ordinary text no longer discards the readable document. The invalid drawing association is omitted and reported as `binary_shape_anchor_unresolved`, with its recorded location and partial status. Valid anchors still work. Shared range reservations remain even when all semantic anchors are excluded; aliasing, out-of-range CPs, ordering, encryption and resource checks remain strict. Standard retains its prior strict behavior. No producer-specific magic or unknown-version bypass was added.

## Evidence and limitations

Existing locally retained official bank inputs were read in memory. Their saved metadata can claim an application version; metadata is not authenticated producer evidence and website origin does not establish a software release. Public samples whose metadata claims WPS 11.8 include both binary DOC and OOXML under `.doc`; supported text was returned. No source files, extracted content or reversible fixtures were copied into this record or Git.

Another locally retained document reached the existing 64 metroBlob candidate budget: 66 distinct candidates, not 66 verified charts. The first 64 were non-chart containers. The cap includes examination work, not only successfully recognized charts. It remains enforced; this check is not labeled a successful complete read. No limit was raised or bypassed.

Separate authorized private-document verification stays local. No private document name, hash, metadata stream, content, source reference or content-derived fixture is stored in the repository. Real samples do not establish all WPS 11/12.1 builds or full rendering compatibility.

## Validation

macOS, Python 3.14.10, Expat 2.7.4:

- WPS full suite: 736 passed, no skips.
- New DOC content/layout matrix: 3 tests, including ten binary layout/Table combinations and three CLI content families.
- New bounded semantic drawing recovery: 6 tests, including header, public entry, good/bad mixed anchors and retained shared-range protection.
- Read-only isolated WPS package: 6 passed, including `.doc` recovery.
- Generic packaging safety: 11 passed.
- Full WPS Pylint errors-only: exit 0.
- Strict skill structure, project documents and diff whitespace: pass.

No OCR, external upload, formula execution, commit or push. Linux, Windows, actual WPS product save/reopen and visual fidelity remain unverified. WPS 12.1.0.26899-specific behavior is not certified.
