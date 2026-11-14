# Bounded chart inspection and pagination consumer migration

Version: `20261114.1946.5595`.

## Runtime

Optional MS-DOC chart inspection still scans at most 64 unique source candidates, including non-chart metroBlobs. Remaining candidates are not parsed; verified text and earlier chart results are retained with partial status, uninspected counts and source bounds explicitly marked noncontiguous. Source-position payload conflicts remain errors, including beyond the scan cap. Byte, ZIP/XML, cache and shared-structure security budgets are unchanged. Seven new synthetic tests cover exact limits, noncharts, duplicates, conflicts, existing results and public entry. An existing local official-bank sample now retains body text instead of being blocked by candidate count. No sample or extracted content was added to Git.

## Consumer migration

The old cross-repository text-budget tests implicitly assumed standard mode after the authorized default changed to maximum. Corrected those legacy consumers to request standard explicitly. Added default-maximum CLI regression validating byte/character budgets, contiguous fragments, chunk/full hashes and full result reconstruction including header text and revisions. No runtime fallback, text-only alias or schema concealment was introduced. The isolated runtime copy test explicitly verifies standard's retained text contract.

Skill commands already distinguish standard and maximum. Reference docs now explicitly route decoders by schema: wps_read_view_v1 versus wps_maximum_page_v1. Full-read JSON remains unchanged. WPS-related loading profiles only were refreshed; other skill snapshots are managed by their respective workers.

## Validation

No private document identifiers or content are recorded. No uploads, commits, pushes, OCR or external data access. Linux, Windows, Office/WPS rendering and real host delivery remain unverified.
