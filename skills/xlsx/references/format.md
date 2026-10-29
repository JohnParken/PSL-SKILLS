# Financial Formatting & Output Standards

路径占位符说明：`<skill-root>` 是 XLSX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 XLSX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

Follow the path and first-operation rules (`<skill-root>/SKILL.md#执行环境约束与首步自检`)
before using this reference; internal resources and task data have different path bases.

## Contents

- 1. Scope of This Reference (`<skill-root>/references/format.md#1-scope-of-this-reference`)
- 2. Financial Format Semantic System (`<skill-root>/references/format.md#2-financial-format-semantic-system`)
- 3. styles.xml Background (Legacy Reference) (`<skill-root>/references/format.md#3-stylesxml-background-legacy-reference`)
- 4. Pre-defined Style Index Complete Reference Table (13 Slots) (`<skill-root>/references/format.md#4-pre-defined-style-index-complete-reference-table-13-slots`)
- 5. Assumption Separation Principle (`<skill-root>/references/format.md#5-assumption-separation-principle`)
- 6. Maintained Formatting Workflow (`<skill-root>/references/format.md#6-maintained-formatting-workflow`)
- 7. Formatting Completeness Checklist (`<skill-root>/references/format.md#7-formatting-completeness-checklist`)
- 8. Maintained Workflow Constraints (`<skill-root>/references/format.md#8-maintained-workflow-constraints`)
- 9. Common Formatting Pitfalls (`<skill-root>/references/format.md#9-common-formatting-pitfalls`)
- 10. Financial Model Structure Conventions (`<skill-root>/references/format.md#10-financial-model-structure-conventions`)
- 11. Assumption Categories (`<skill-root>/references/format.md#11-assumption-categories`)
- 12. Audit Trail Best Practices (`<skill-root>/references/format.md#12-audit-trail-best-practices`)
- 13. Pre-Delivery Checklist (Common Financial Model Checklist) (`<skill-root>/references/format.md#13-pre-delivery-checklist-common-financial-model-checklist`)

> Financial conventions and format-file background. Section 6 describes the maintained
> workflow; XML examples elsewhere are explanatory only. For existing workbooks, use only style indices that
> already exist in that workbook. For new workbooks, use the template's supported style
> indices or the appearance presets (`<skill-root>/references/readable-appearance.md`). Do not unpack, edit XML,
> or repack a workbook for formatting tasks.

---

## 1. Scope of This Reference

Use the financial conventions here as visual guidance. Maintained edits can select an
existing style index in the workbook; they cannot add or change styles. For new files,
select from the template's supported indices or use the supported appearance presets.
This reference does not provide a maintained workflow for applying arbitrary financial
formats or changing an existing workbook's style definitions. The conventions and
checklists below are optional design guidance, not acceptance requirements for every
spreadsheet. Preserve the user's existing conventions. Static reports may contain
verified calculated values without formulas or separate assumption sheets. Apply only
styles supported by the selected workflow; report unsupported formatting requests.

For package structure and cell encoding during implementation or diagnosis, see the
OOXML cheat sheet (`<skill-root>/references/ooxml-cheatsheet.md`). It is background documentation, not permission
to bypass the maintained editor.

---

## 2. Financial Format Semantic System

### 2.1 Font Color = Cell Role (Color = Role)

The primary convention of financial modeling: **font color encodes the cell's role, not decoration**. A reviewer can glance at colors to determine which cells are adjustable parameters and which are model-calculated results. This is an industry-wide convention (followed by investment banks, the Big Four, and corporate finance teams).

| Role | Font Color | AARRGGBB | Use Case |
|------|-----------|----------|----------|
| Hard-coded input / assumption | Blue | `000000FF` | Growth rates, discount rates, tax rates, and other user-modifiable parameters |
| Formula / calculated result | Black | `00000000` | All cells containing a `<f>` element |
| Same-workbook cross-sheet reference | Green | `00008000` | Cells whose formula starts with `SheetName!` |
| External file link | Red | `00FF0000` | Cells whose formula contains `[FileName.xlsx]` (flagged as fragile links) |
| Label / text | Black (default) | theme color | Row labels, category headings |
| Key assumption requiring review | Blue font + yellow fill | Font `000000FF` / Fill `00FFFF00` | Provisional values, parameters pending confirmation |

**Decision tree**:
```
Does the cell contain a <f> element?
  +-- Yes -> Does the formula start with [FileName]?
  |           +-- Yes -> Red (external link)
  |           +-- No  -> Does the formula contain SheetName!?
  |                       +-- Yes -> Green (cross-sheet reference)
  |                       +-- No  -> Black (same-sheet formula)
  +-- No  -> Is the value a user-adjustable parameter?
              +-- Yes -> Blue (input/assumption)
              +-- No  -> Black default (label)
```

When this color convention is requested, distinguish inputs from formulas using supported
styles. Existing user conventions and appearance presets may use different colors.

### 2.2 Number Format Matrix

| Data Type | formatCode | numFmtId | Display Example | Applicable Scenario |
|-----------|-----------|----------|-----------------|---------------------|
| Standard currency (whole dollars) | `$#,##0;($#,##0);"-"` | 164 | $1,234 / ($1,234) / - | P&L, balance sheet amount rows |
| Standard currency (with cents) | `$#,##0.00;($#,##0.00);"-"` | 169 | $1,234.56 / ($1,234.56) / - | Unit prices, detailed costs |
| Thousands (K) | `#,##0,"K"` | 171 | 1,234K | Simplified display for management reports |
| Millions (M) | `#,##0,,"M"` | 172 | 1M | Macro-level summary rows |
| Percentage (1 decimal) | `0.0%` | 165 | 12.5% | Growth rates, gross margins |
| Percentage (2 decimals) | `0.00%` | 170 | 12.50% | IRR, precise interest rates |
| Multiple / valuation multiplier | `0.0x` | 166 | 8.5x | EV/EBITDA, P/E |
| Integer (thousands separator) | `#,##0` | 167 | 12,345 | Employee count, unit quantities |
| Year | `0` | 1 (built-in, no declaration needed) | 2024 | Column header years, prevents 2,024 |
| Date | `m/d/yyyy` | 14 (built-in, no declaration needed) | 3/21/2026 | Timelines |
| General text | General | 0 (built-in, no declaration needed) | — | Label rows, cells with no format requirement |

Custom format IDs are workbook-local, not universal meanings. The IDs above describe
the template or illustrative extensions; an existing workbook may assign them differently.
For new files, appearance `number_formats` can store these format codes and allocate IDs.
Existing-file edits can only reuse styles already present. Format 166 is declared in the
template but has no selectable cell style among indices 0–12.

**Built-in format IDs do not need to be declared in `<numFmts>`** (IDs 0–163 are built into Excel/LibreOffice; simply reference the numFmtId in `<xf>`):

| numFmtId | formatCode | Description |
|----------|-----------|-------------|
| 0 | General | General format |
| 1 | `0` | Integer, no thousands separator (use this ID for years) |
| 3 | `#,##0` | Thousands-separated integer (no decimals) |
| 9 | `0%` | Percentage integer |
| 10 | `0.00%` | Percentage with two decimals |
| 14 | `m/d/yyyy` | Short date |

### 2.3 Negative Number Display Standards

Financial reports have two mainstream conventions for negative numbers — choose one and **maintain consistency** throughout the entire workbook:

**Parenthetical style (investment banking standard, recommended for external deliverables)**

```
Positive: $1,234    Negative: ($1,234)    Zero: -
formatCode: $#,##0;($#,##0);"-"
```

**Red minus sign style (suitable for internal operational analysis reports)**

```
Positive: $1,234    Negative: -$1,234 (red)
formatCode: $#,##0;[Red]-$#,##0;"-"
```

Rule: Once a style is determined, maintain it across the entire workbook. Do not mix two negative number display styles within the same workbook.

### 2.4 Zero Value Display Standards

In financial models, "0" and "no data" have different semantics and should be visually distinct:

| Scenario | Recommended Display | formatCode Third Segment |
|----------|-------------------|--------------------------|
| Sparse matrix (most rows have zero-value periods) | Dash `-` | `"-"` |
| Quantity counts (zero itself is meaningful) | `0` | `0` or omit |
| Placeholder row (explicitly empty) | Leave blank | Do not write to cell |

Four-segment format syntax: `positive format;negative format;zero value format;text format`

Zero as dash: `$#,##0;($#,##0);"-"`
Zero preserved as 0: `#,##0;(#,##0);0`

---

## 3. styles.xml Background (Legacy Reference)

### 3.1 Style Index Concept

In OOXML, a cell style index points to a `cellXfs` entry, which in turn references
number formats, fonts, fills and borders. For maintained tasks, inspect style indices
with the skill's `read` command. Do not unpack the workbook or edit its XML to inspect or
change formatting.

### 3.2 Maintained Style Limits

The maintained editor cannot append custom formats, fonts, fills or cell styles. For an
existing workbook, read its style indices and reuse a suitable existing index. For a new
workbook, use the template's supported indices or an appearance preset. If no supported
style matches the requested display, the current workflow cannot apply that custom style.

### 3.3 AARRGGBB Color Format Explanation

OOXML's `rgb` attribute uses **8-digit hexadecimal AARRGGBB** format (not HTML's 6-digit RRGGBB):

```
AA  RR  GG  BB
|   |   |   |
Alpha Red Green Blue
```

The bundled template uses `00` prefixes; the appearance preset uses `FF` prefixes.
Do not infer cell invisibility from that prefix or rewrite it for that reason. Cell-style
alpha is not a reliable transparency control. Preserve the supported template or preset
colors; assess actual appearance in the target application rather than changing alpha
bytes to simulate transparency.

| Color | AARRGGBB | Corresponding Role |
|-------|----------|-------------------|
| Blue (input) | `000000FF` | Hard-coded assumptions |
| Black (formula) | `00000000` | Calculated results |
| Green (cross-sheet reference) | `00008000` | Same-workbook cross-sheet |
| Red (external link) | `00FF0000` | References to other files |
| Yellow (review-required fill) | `00FFFF00` | Key assumption highlight |
| Light gray (projection period fill) | `00D3D3D3` | Distinguishing historical vs. forecast periods |
| White | `00FFFFFF` | Pure white fill |

Use the existing supported style rather than constructing a color from an HTML string.

### 3.4 numFmtId Background

OOXML reserves built-in number format IDs below 164; higher IDs are custom formats.
Existing-file edits do not assign new IDs or append formats. For new files, the
appearance implementation allocates and reuses custom format IDs automatically.

---

## 4. Pre-defined Style Index Complete Reference Table (13 Slots)

The following are the 13 style slots (cellXfs index 0–12) pre-defined in the minimal_xlsx template. They can be selected in new-workbook JSON specifications. Existing workbooks use their own style indices; read those indices before editing.

| Index | Semantic Role | Font Color | Fill | numFmtId | Format Display | Typical Use |
|-------|--------------|------------|------|----------|---------------|-------------|
| **0** | Default style | Theme black | None | 0 | General | Cells requiring no special formatting |
| **1** | Input / assumption (general) | Blue `000000FF` | None | 0 | General | Text-type assumptions, flags |
| **2** | Formula / calculated result (general) | Black `00000000` | None | 0 | General | Text concatenation formulas, non-numeric calculations |
| **3** | Cross-sheet reference (general) | Green `00008000` | None | 0 | General | Values pulled from cross-sheet (general format) |
| **4** | Header (bold) | Bold black | None | 0 | General | Row/column headings |
| **5** | Currency input | Blue `000000FF` | None | 164 | $1,234 / ($1,234) / - | Amount inputs in the assumptions area |
| **6** | Currency formula | Black `00000000` | None | 164 | $1,234 / ($1,234) / - | Amount calculations in the model area (revenue, EBITDA) |
| **7** | Percentage input | Blue `000000FF` | None | 165 | 12.5% | Rate inputs in the assumptions area (growth rate, gross margin assumptions) |
| **8** | Percentage formula | Black `00000000` | None | 165 | 12.5% | Rate calculations in the model area (actual gross margin) |
| **9** | Integer (comma) input | Blue `000000FF` | None | 167 | 12,345 | Quantity inputs in the assumptions area (employee count) |
| **10** | Integer (comma) formula | Black `00000000` | None | 167 | 12,345 | Quantity calculations in the model area |
| **11** | Year input | Blue `000000FF` | None | 1 | 2024 | Column header years (no thousands separator) |
| **12** | Key assumption highlight | Blue `000000FF` | Yellow `00FFFF00` | 0 | General | Key parameters pending review or confirmation |

**Selection guide for new workbooks**:
- Determine "input" vs. "formula" -> Choose odd-numbered (input/blue) or even-numbered (formula/black) paired slots
- Determine data type -> Choose the corresponding currency (5/6) / percentage (7/8) / integer (9/10) / year (11) slot
- Cross-sheet references have a green General style (3); the template has no green numeric-format combination
- Parameter pending review -> index 12

For existing workbooks, these template indices apply only if the workbook actually has
the same styles. Read the workbook's own style index before editing.

---

## 5. Assumption Separation Principle

### 5.1 Structural Design

Assumption separation principle: **Input assumptions are centralized in a dedicated area (sheet or block); the model calculation area contains only formulas, no hard-coded values**.

Recommended structure:
```
Workbook sheet layout
  sheet 1 "Assumptions"  -> All blue-font cells (style 1/5/7/9/11/12)
  sheet 2 "Model"        -> All black or green-font cells (style 2/3/4/6/8/10)
```

Same-sheet zoning approach for simple models:
```
Rows 1-5:   [Assumptions block - blue font]
Row 6:      [Empty row separator]
Rows 7+:    [Model block - black/green font formulas referencing assumptions area]
```

### 5.2 Applying the Principle

Keep assumptions in a clearly labeled block or sheet and make calculated areas derive
from those inputs. For new workbooks, choose supported input, formula and header styles
from the template. In existing workbooks, preserve the current style scheme and use only
its existing indices. The maintained workflow cannot create a new green number-format
combination for a cross-sheet reference.

## 6. Maintained Formatting Workflow

For an existing workbook, follow Edit existing cells (`<skill-root>/references/edit.md`) and the JSON contract in
Standard-library calculation and file contract (`<skill-root>/references/python-calculation.md`):

```console
python3 -I -S -B "<skill-root>/scripts/stdlib_xlsx.py" read input.xlsx
python3 -I -S -B "<skill-root>/scripts/stdlib_xlsx.py" edit input.xlsx patch.json output.xlsx
```

Inspect the target cell's value or formula and the workbook's existing style indices.
The `read` output exposes cell style indices, not a style-definition or appearance catalog.
Each patch cell requires exactly one of `value` or `formula`; retain that content when
changing its `style`. A style-only object is not supported. Do not substitute a cached
value for a formula. Choose an existing style whose suitability is established; a numeric
index alone does not describe its appearance. If no suitable style is known, explain the
limit instead of guessing or adding styles. Editing may recalculate formulas and clear
unsupported caches; inspect the calculation report even for a formatting request.

Preserve types when preparing the patch: CLI `read` serializes numeric values and style
indices as strings. Use the reported `type` to write a numeric `value` as an exact JSON
number (not a quoted string), and write `style` as a JSON integer. Preserve booleans as
booleans and literal text as text. For example, a numeric read value `"12.50"` needs
`{"value": 12.50, "style": 5}` only if style 5 is suitable in that workbook.
Keep formula text in `formula`, not its cached `value`. The scalar patch interface cannot
preserve ISO-date cell type `d`, literal error type `e`, or rich-text runs when rewriting
a cell; do not promise formatting-only preservation for those cells. Explain this limit
instead of rewriting them through a plain string. Verify types and formulas after editing.

For a new workbook, choose supported template indices in the creation specification or
use `create --appearance` / tabular `import --appearance` as documented in
Readable appearance for newly created workbooks (`<skill-root>/references/readable-appearance.md`). Appearance presets apply only to new
files. Never recreate an existing workbook from extracted values to apply a preset.

The maintained skill does not support unpacking, editing style XML, appending custom
styles to existing files, or repacking workbooks. Use a new output path and verify the
result following Validate a standard-library output (`<skill-root>/references/validate.md`).

## 7. Formatting Completeness Checklist

Apply only the checks relevant to the requested financial-model convention and supported
styles. IDs below refer to the bundled template, not arbitrary existing workbooks.
XML-specific checks describe structure, not a manual editing workflow.

### Color Role Consistency
- [ ] All numeric cells containing `<f>` elements: fontId corresponds to black (formula) or green (cross-sheet reference)
- [ ] All hard-coded numeric values that are user-adjustable parameters: fontId corresponds to blue (input)
- [ ] Cross-sheet references (formula contains `SheetName!`): fontId corresponds to green
- [ ] External file references (formula contains `[FileName.xlsx]`): fontId corresponds to red
- [ ] No cell simultaneously contains a `<f>` element and uses blue font (color role contradiction)

### Number Format Correctness
- [ ] Year columns: numFmtId="1" (`0` format), displays as 2024 not 2,024
- [ ] Currency rows: numFmtId="164" or variant, negative numbers display as ($1,234) not -$1,234
- [ ] Percentage rows: values stored as decimals (0.08 = 8%), format numFmtId="165", displays as 8.0%
- [ ] Zero values: displayed as `-` in sparse matrices rather than `0` (formatCode third segment contains `"-"`)
- [ ] Multiple rows (EV/EBITDA, etc.): numFmtId="166" (`0.0x` format)
- [ ] Negative number display style is consistent throughout the entire workbook (parenthetical or red minus sign)

### Legacy XML Reference Checks

The count and reference checks below describe OOXML structure only. They do not imply
that the maintained skill supports direct style XML editing.

- [ ] `<numFmts count>` matches the number of `<numFmt>` elements
- [ ] `<fonts count>`, `<fills count>` and `<cellXfs count>` match their child elements
- [ ] Style references remain within their corresponding collection bounds

### Assumption Separation Verification
- [ ] No black-font numeric cells in the assumptions area/sheet (black numeric = formula, should not be in assumptions)
- [ ] No blue-font non-year numeric cells in the model area/sheet (blue numeric = hard-coded, should be in assumptions)
- [ ] Input parameters in the model area reference the assumptions area via formulas, not by directly copying values

### Formula and Format Linkage
- [ ] Formula cells use a suitable existing or template style; default style 0 is valid
- [ ] SUM summary rows: style uses black font + corresponding number format (e.g., s="6" for currency summaries)
- [ ] Percentage formulas: values stored as decimals, format is `0.0%`; do not multiply values by 100 before applying percentage format

### Visual Hierarchy
- [ ] Header rows (years/metric names): style=4 (bold black)
- [ ] Summary rows (Total/EBITDA/Net Income): bold with a suitable existing style
- [ ] Unit description rows (e.g., "$ thousands"): use style=0 or style=2 (blue not needed)

---

## 8. Maintained Workflow Constraints

- Existing workbook style definitions are not changed by the maintained editor.
- Existing-file edits may select only style indices present in that workbook.
- New workbooks may use the template's supported style indices and appearance presets.
- This reference does not authorize direct XML changes, manual packaging, or use of
  third-party libraries to bypass these limits.

## 9. Common Formatting Pitfalls

- A year displayed with a thousands separator is using an unsuitable number format.
- Percentage values should be stored as decimals (0.08 for 8%) when using a percentage
  format.
- Formula and input colors have different meanings; select styles whose semantics match
  the cell contents.
- Editing an existing style definition can affect every cell that refers to it. The
  maintained workflow uses existing style indices and avoids direct style XML edits.

## 10. Financial Model Structure Conventions

### 10.1 Header Rows

- Bold font (corresponds to style index 4 in this skill's template)
- Year columns: use number format `0` (numFmtId="1", no thousands separator) to prevent 2024 from displaying as 2,024
- A unit description row may be added below headers: gray or italic text, e.g., "$ thousands" or "% of Revenue"

### 10.2 Row Type Standards

| Row Type | Style Recommendation | Example |
|----------|---------------------|---------|
| Category heading row | Bold, optionally with fill color | "Revenue" |
| Line item row | Normal style | "Product A", "Product B" |
| Subtotal row | Bold + top border | "Total Revenue" |
| Operating metric row | Normal style | "Gross Margin %" |
| Separator row | Empty row | (empty) |

### 10.3 Multi-Year Model Column Layout

```
Col A: Label column          (width 28, left-aligned text, s="4" for headers or s="0" for labels)
Col B: FY2022 Actual         (width 12, year header s="11", data cells styled by semantic role)
Col C: FY2023 Actual
Col D: FY2024E               (forecast period; distinguish visually only with a supported style)
Col E: FY2025E
Col F: FY2026E
```

### 10.4 Cross-Sheet Reference Patterns

Financial models often use a distinct color for formulas that reference another sheet.
The standard template provides a green General style but no green currency or percentage
combination; the maintained workflow cannot add one.

---

## 11. Assumption Categories

In the assumptions area (Assumptions sheet or assumptions block), organize assumptions in the following standard order for ease of review and maintenance:

1. **Revenue assumptions**: Growth rates, pricing, sales volume
2. **Cost assumptions**: Gross margin, fixed/variable cost ratios
3. **Working capital**: DSO (Days Sales Outstanding), DPO (Days Payable Outstanding), inventory days
4. **Capital expenditures (CapEx)**: As a percentage of revenue or absolute amounts
5. **Financing assumptions**: Interest rates, debt repayment schedules
6. **Tax and other**: Effective tax rate, depreciation & amortization (D&A)

---

## 12. Audit Trail Best Practices

- For new workbooks, use template style 12 (blue font + yellow fill) to highlight assumptions requiring review; in existing workbooks, use a suitable existing style.
- In sensitivity analysis rows or a separate Sensitivity tab, show the impact of +/-1% changes in key assumptions on results
- **Do not hide rows containing assumptions**: Assumption rows must be visible to reviewers; do not use the `hidden="1"` attribute
- Note a "Last Updated" date at the top of the assumptions area or in a dedicated cell, recording the last modification time of the model

---

## 13. Pre-Delivery Checklist (Common Financial Model Checklist)

For the requested financial-model convention, check the applicable items below. Do not
change unrelated cells, add sheets to existing files, or replace static values with
formulas merely to satisfy this optional checklist:

- [ ] Editable models retain formulas; static reports identify their non-updating values. A static formula scan cannot establish this intent.
- [ ] Year columns display as 2024 not 2,024 (numFmtId="1", format `0`)
- [ ] Negative numbers display as (1,234) not -1,234 (use parenthetical style for externally delivered financial reports)
- [ ] Zero values display as `-` in sparse rows rather than `0` (formatCode third segment is `"-"`)
- [ ] Growth rates and percentages are stored as decimals (0.08 = 8%), format is `0.0%`
- [ ] Cross-sheet references follow a consistent visual convention supported by the workbook's existing styles
- [ ] Assumptions block and model block are clearly separated (different sheets or separated by empty rows within the same sheet)
- [ ] Editable models use `SUM()` for summary rows; static reports use verified Python totals.
- [ ] Balance verification: summary rows = sum of their respective line items (a check row can be added at the end of the model to verify)
