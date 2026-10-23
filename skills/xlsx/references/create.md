# Create a spreadsheet

Follow the [path and first-operation rules](../SKILL.md#执行环境约束与首步自检)
before using this reference; internal resources and task data have different path bases.

Read [python-calculation.md](python-calculation.md) for the JSON schema and CLI.
Plan sheet names, input values, calculations and formatting first. Use `create` with
an explicit JSON cell specification. The tool builds from the bundled XML template.
The source assets use `content_types.xml` and `_rels/root.rels` to satisfy filename
rules. Creation maps them in memory to the OOXML ZIP members `[Content_Types].xml`
and `_rels/.rels`; it never renames or writes files in the installed skill directory.
Do not manually rename template files before execution. Existing XLSX/XLSM inputs
continue to use the standard ZIP member names.

Choose the output behavior:
- Editable model: use supported formulas; the engine writes formulas and calculated caches.
- Static report: calculate business results in Python and use `value`, or use
  `create --values-only` to evaluate supported formulas and replace them with values.

Do not invent data or formula caches. Label assumptions and units. Preserve required
column/sheet names. For financial styling consult [format.md](format.md); select template
style indices deliberately. For new-workbook pale-blue headers, row/category banding,
header merges, explicit widths and freezing, use [readable-appearance.md](readable-appearance.md).
Charts, auto-fit and arbitrary layout editing remain unsupported.

Check the calculation report, reopen the output with `read`, verify expected sheet names,
input values and independently known results. Explain static outputs and unresolved
formulas. See [validate.md](validate.md). No Office runtime or third-party packages are needed.
