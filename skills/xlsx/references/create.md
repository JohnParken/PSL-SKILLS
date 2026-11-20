# Create a spreadsheet

Follow the path and first-operation rules (`<skill-root>/SKILL.md#执行环境约束与首步自检`)
before using this reference; internal resources and task data have different path bases.

小型静态值表先用主入口的完整快速路径（`<skill-root>/SKILL.md#简单静态创建仅本入口`），无需预读本页。超出该范围时再使用下述完整创建契约。

Read Standard-library calculation and file contract (`<skill-root>/references/write-contract.md`) for the JSON schema and CLI.
仅生成静态值时无需公式参考；包含公式才读公式计算（`<skill-root>/references/formula-calculation.md`）。
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
column/sheet names. For financial styling consult Financial Formatting & Output Standards (`<skill-root>/references/format.md`); select template
style indices deliberately. For new-workbook pale-blue headers, row/category banding,
header merges, explicit widths and freezing, use Readable appearance for newly created workbooks (`<skill-root>/references/readable-appearance.md`).
Charts, auto-fit and arbitrary layout editing remain unsupported.

New reports use `<?xml version="1.0" encoding="UTF-8"?>` in every XML/relationship
part, with double quotes and uppercase encoding. SpreadsheetML, package relationships
and content types use their respective default namespaces; workbook sheet references
use `r:id` bound to the Office document relationships namespace. Generated parts do
not use automatic `ns0/ns1` prefixes. This is an output convention, not a claim that
other correctly bound prefixes or single-quoted XML declarations are invalid.
Do not strip prefixes with text replacement or rewrite existing input packages to
enforce this convention. The standard-library writer remains the supported generator.

Check the calculation report, reopen the output with `read`, verify expected sheet names,
input values and independently known results. Explain static outputs and unresolved
formulas. See Validate a standard-library output (`<skill-root>/references/validate.md`). No Office runtime or third-party packages are needed.
