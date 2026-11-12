# OFD isolated publication validation — 2026-11-12

Repository maintenance only; not part of the runtime package.

## Scope

Base: 57b24ad550ae9d88fb544265697044b12603ee52. Skill version: 20261112.0721.3845.
The commit candidate contains only OFD source, tests and documentation, plus the OFD rows/sections and OFD-only selection/loading evidence in shared files.
ET and other uncommitted changes were not copied into the validation worktree. Original source file hashes were checked before and after staging.

The staged candidate was applied to a detached temporary worktree at the base commit. This verifies the OFD changes without depending on the concurrent ET work. Existing dated records retain their original validation limits.

## Checks on the isolated candidate

Platform: macOS arm64. Functional checks: Python 3.14.10, Expat 2.7.4.
Commands were executed from the validation repository root using the confirmed Python executable and -I -S -B.

- OFD strict skill check: passed.
- Project documentation check: passed after correcting the staged README section boundary; original working README was untouched.
- OFD environment self-check: passed.
- OFD unittest discovery: 199 passed.
- test_runtime_packager.py: 11 passed.
- test_skill_runtime_packages.py: 12 passed; synthetic fixtures, including read-only independent packages.
- test_skill_selection_current.py: 6 passed.
- test_skill_loading_benchmark.py: 25 passed.
- benchmark_skill_loading.py --check-snapshot tests/runtime_loading_profiles.json: fresh.
- Staged diff whitespace check: passed.

Pylint: 4.1.3 / Python 3.12.12, using the separate maintenance virtual environment.
Scanned all 21 OFD Python files in script and test groups, with --max-line-length=200 --persistent=n --output-format=json --reports=n and explicit script/test import paths.
No rules were disabled. Results: Error 0, Fatal 0, Warning 28, Convention 401, Refactor 159. Both groups exited 28 due to the remaining W/C/R diagnostics. This is not a claim of all-rule cleanliness.

## Limits

No Linux/Windows rerun, external real OFD or visual/OCR validation, real DeepAgents host-return verification, deployment, or replacement of historical runtime ZIPs was performed.
Static selection/loading evidence does not measure model routing or host token usage.
