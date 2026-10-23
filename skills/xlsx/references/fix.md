# Repair a known formula defect

Follow the [path and first-operation rules](../SKILL.md#执行环境约束与首步自检)
before using this reference; internal resources and task data have different path bases.

Inspect the original formula and its intended business behavior. Use the standard-library
`read` command; if calculation is needed, use `calculate` to a separate output and inspect
its structured issues. A failed calculation produces no new output.

Correct only formulas whose intended meaning is known, using a JSON patch and `edit`.
Do not replace unsupported functions with guessed results. Do not hide division by zero
with an arbitrary default or use IFERROR merely to obtain a clean report. If a default is
part of the agreed business rule, express it explicitly using supported IF conditions.

Recheck inputs, changed formulas, dependent results and unaffected content. See
[python-calculation.md](python-calculation.md) and [validate.md](validate.md). Structural
edits and shared/array formulas require a separate implementation. The legacy row-shift
and insert command-line tools are disabled; a known formula-reference corruption remains
unresolved, and disabling the commands does not repair files they may have changed.
Never use them as a repair shortcut.
