# OOXML SpreadsheetML Cheat Sheet

路径占位符说明：`<skill-root>` 是 XLSX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 XLSX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

Follow the path and first-operation rules (`<skill-root>/SKILL.md#执行环境约束与首步自检`)
before using this reference; internal resources and task data have different path bases.

## Contents

- Package Structure (`<skill-root>/references/ooxml-cheatsheet.md#package-structure`)
- Cell Reference Format (`<skill-root>/references/ooxml-cheatsheet.md#cell-reference-format`)
- Cell XML Reference (`<skill-root>/references/ooxml-cheatsheet.md#cell-xml-reference`)
- styles.xml Reference (`<skill-root>/references/ooxml-cheatsheet.md#stylesxml-reference`)
- sharedStrings.xml Reference (`<skill-root>/references/ooxml-cheatsheet.md#sharedstringsxml-reference`)
- workbook.xml / .rels Sync (`<skill-root>/references/ooxml-cheatsheet.md#workbookxml--rels-sync`)
- Column / Row Dimensions (`<skill-root>/references/ooxml-cheatsheet.md#column--row-dimensions`)
- Freeze Panes (`<skill-root>/references/ooxml-cheatsheet.md#freeze-panes`)
- Common Excel Error Types (`<skill-root>/references/ooxml-cheatsheet.md#common-excel-error-types`)

Background reference for package structure and cell encoding during implementation or
diagnosis. For user workbook changes, follow Edit existing cells (`<skill-root>/references/edit.md`); this reference does not
authorize direct XML edits or manual unpacking/repacking.

---

## Package Structure

```
my_file.xlsx  (ZIP archive)
├── [Content_Types].xml          ← declares MIME types for all files
├── _rels/
│   └── .rels                    ← root relationship: points to xl/workbook.xml
└── xl/
    ├── workbook.xml             ← sheet list, calc settings
    ├── styles.xml               ← ALL style definitions
    ├── sharedStrings.xml        ← ALL text strings (referenced by index)
    ├── _rels/
    │   └── workbook.xml.rels    ← maps r:id → worksheet/styles/sharedStrings files
    ├── worksheets/
    │   ├── sheet1.xml           ← Sheet 1 data
    │   ├── sheet2.xml           ← Sheet 2 data
    │   └── ...
    ├── charts/                  ← chart XML (if any)
    ├── pivotTables/             ← pivot table XML (if any)
    └── theme/
        └── theme1.xml           ← color/font theme
```

---

## Cell Reference Format

```
A1  → column A (1), row 1
B5  → column B (2), row 5
AA1 → column 27, row 1
```

Column letter ↔ number conversion:
```python
def col_letter(n):  # 1-based → letter
    r = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        r = chr(65 + rem) + r
    return r

def col_number(s):  # letter → 1-based
    n = 0
    for c in s.upper():
        n = n * 26 + (ord(c) - 64)
    return n
```

---

## Cell XML Reference

### Data Types

| Type | `t` attr | XML Example | Value |
|------|---------|-------------|-------|
| Number | omit | `<c r="B2"><v>1000</v></c>` | 1000 |
| String (shared) | `s` | `<c r="A1" t="s"><v>0</v></c>` | sharedStrings[0] |
| String (inline) | `inlineStr` | `<c r="A1" t="inlineStr"><is><t>Hi</t></is></c>` | "Hi" |
| Boolean | `b` | `<c r="D1" t="b"><v>1</v></c>` | TRUE |
| Error | `e` | `<c r="E1" t="e"><v>#REF!</v></c>` | #REF! |
| Formula | omit | `<c r="B4"><f>SUM(B2:B3)</f><v></v></c>` | computed |

### Formula Types

```xml
<!-- Basic formula (no leading = in XML!) -->
<c r="B4"><f>SUM(B2:B3)</f><v></v></c>

<!-- Cross-sheet -->
<c r="C1"><f>Assumptions!B5</f><v></v></c>
<c r="C1"><f>'Sheet With Spaces'!B5</f><v></v></c>

<!-- Shared formula: D2:D100 all use B*C with relative row offset -->
<c r="D2"><f t="shared" ref="D2:D100" si="0">B2*C2</f><v></v></c>
<c r="D3"><f t="shared" si="0"/><v></v></c>

<!-- Array formula -->
<c r="E1"><f t="array" ref="E1:E5">SORT(A1:A5)</f><v></v></c>
```

---

## styles.xml Reference

### Indirect Reference Chain

```
Cell s="3"
  ↓
cellXfs[3] → fontId="2", fillId="0", borderId="0", numFmtId="165"
  ↓              ↓             ↓            ↓              ↓
fonts[2]      fills[0]    borders[0]    numFmts: id=165
blue color    no fill      no border    "0.0%"
```

### Maintained Style Selection

Existing-file edits may reference an existing `cellXfs` index; they cannot append styles.
New-file appearance presets can create shared styles and custom number formats through
Readable appearance for newly created workbooks (`<skill-root>/references/readable-appearance.md`). IDs in the diagram above are illustrative
and do not describe the bundled template. Do not add styles by editing XML manually.

### Color Format

`AARRGGBB` — Alpha + Red + Green + Blue. The template uses `00` prefixes and
appearance presets use `FF`; neither is a reason to infer invisible cell text. See
3.3 AARRGGBB Color Format Explanation (`<skill-root>/references/format.md#33-aarrggbb-color-format-explanation`) for the color caveat.

```
000000FF → Blue
00000000 → Black
00008000 → Green (dark)
00FF0000 → Red
00FFFF00 → Yellow (for fills)
00FFFFFF → White
```

### Built-in numFmtIds (no declaration needed)

| ID | Format | Display |
|----|--------|---------|
| 0 | General | as-is |
| 1 | 0 | 2024 (use for years!) |
| 2 | 0.00 | 1000.00 |
| 3 | #,##0 | 1,000 |
| 4 | #,##0.00 | 1,000.00 |
| 9 | 0% | 15% |
| 10 | 0.00% | 15.25% |
| 14 | m/d/yyyy | 3/21/2026 |

---

## sharedStrings.xml Reference

```xml
<sst count="3" uniqueCount="3">
  <si><t>Revenue</t></si>      <!-- index 0 -->
  <si><t>Cost</t></si>         <!-- index 1 -->
  <si><t>Margin</t></si>       <!-- index 2 -->
</sst>
```

Text with leading/trailing spaces:
```xml
<si><t xml:space="preserve">  indented  </t></si>
```

Special characters:
```xml
<si><t>R&amp;D Expenses</t></si>   <!-- & must be &amp; -->
```

---

## workbook.xml / .rels Sync

Every `<sheet>` in workbook.xml needs a matching `<Relationship>` in workbook.xml.rels:

```xml
<!-- workbook.xml -->
<!-- NOTE: rId numbering depends on what rIds are already in workbook.xml.rels.
     The minimal template reserves rId1=sheet1, rId2=styles, rId3=sharedStrings.
     When ADDING sheets to the template, start from rId4 to avoid conflicts.
     The rId3 here is just a generic illustration — use the next available rId. -->
<sheet name="Summary" sheetId="3" r:id="rId3"/>

<!-- workbook.xml.rels -->
<Relationship Id="rId3"
  Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
  Target="worksheets/sheet3.xml"/>
```

And a matching `<Override>` in `[Content_Types].xml`:
```xml
<Override PartName="/xl/worksheets/sheet3.xml"
  ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
```

---

## Column / Row Dimensions

```xml
<!-- Before <sheetData> -->
<cols>
  <col min="1" max="1" width="28" customWidth="1"/>   <!-- A: 28 chars -->
  <col min="2" max="6" width="14" customWidth="1"/>   <!-- B-F: 14 chars -->
</cols>

<!-- Row height on individual rows -->
<row r="1" ht="20" customHeight="1">
  ...
</row>
```

---

## Freeze Panes

Inside `<sheetView>`:
```xml
<!-- Freeze row 1 (header row stays visible) -->
<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>

<!-- Freeze column A -->
<pane xSplit="1" topLeftCell="B1" activePane="topRight" state="frozen"/>

<!-- Freeze both row 1 and column A -->
<pane xSplit="1" ySplit="1" topLeftCell="B2" activePane="bottomRight" state="frozen"/>
```

---

## Common Excel Error Types

Report observed errors and follow Standard-library calculation and file contract (`<skill-root>/references/python-calculation.md`).
Do not erase source errors merely to make a checklist pass. Missing caches do not prove
error-free calculation; unsupported formulas remain unresolved.

| Error | Meaning | Detect in XML |
|-------|---------|---------------|
| `#REF!` | Invalid cell reference | `<c t="e"><v>#REF!</v></c>` |
| `#DIV/0!` | Divide by zero | `<c t="e"><v>#DIV/0!</v></c>` |
| `#VALUE!` | Wrong data type | `<c t="e"><v>#VALUE!</v></c>` |
| `#NAME?` | Unknown function/name | `<c t="e"><v>#NAME?</v></c>` |
| `#NULL!` | Empty intersection | `<c t="e"><v>#NULL!</v></c>` |
| `#NUM!` | Number out of range | `<c t="e"><v>#NUM!</v></c>` |
| `#N/A` | Value not found | `<c t="e"><v>#N/A</v></c>` |
