# 表格布局、样式与示例

本页仅供对应任务按需加载。定位 `<skill-root>` 与任务目录、执行限制以主入口 `<skill-root>/SKILL.md` 为准；本页提供完整原始字段契约。

## 表格配置与示例

在顶层 `table_format` 或单表 `format` 使用以下便捷字段；同层显式字段优先于预设默认值：

| 字段 | 规则 |
|---|---|
| `preset` | `grid`、`three_line`、`zebra`；省略保持原有框线表。显式选择预设重设六边的样式与粗细；同层全局边框字段再覆盖预设，逐边字段优先。 |
| `column_sizing` | `equal`（默认）或 `content`；显式 `widths_mm` 始终优先。内容估算按中西文字符权重平方根分配，保留每列留白及至少 8mm 内容宽；无法满足则报错。不是字体测量或阅读器 AutoFit。 |
| `band_fill` | 六位 RGB 或 `none`；每隔一个数据行设置底色，表头不计入数据行。单元格条纹覆盖表格统一底色；显式 `none` 可关闭。 |

`three_line` 默认顶底线 1.5pt、无竖线和内部横线；已声明表头且存在数据行时，在最后表头行之后加 0.75pt 分隔线。显式单元格共享边优先，原有冲突检测继续生效。`zebra` 默认无框线，第二个及后续偶数数据行底色为 `F2F4F7`。需要表头样式必须显式声明 `header` 或 `header_rows`，不猜测第一行。


参照 GB/T 9704—2012 第 5.2.2 条的一般字体字号原则使用仿宋 16pt；将边框、留白、加粗、对齐、行距与跨页控制标为项目约定，不要声称这些具体参数均由国标规定。保留当前页面边距；不要将本预设宣称为整份文档的国标符合性检查。国标第 8 章的横排表格页码和单双页表头朝向需要另外的分节与分页能力，当前不提供。

为 `styles.table_body` 和 `styles.table_header` 分别设置表格文字样式；不要让它们继承 `styles.body` 或相互继承。默认均为仿宋 16pt、西文 Times New Roman、黑色、首行缩进 0、段前段后 0、1.15 倍行距；正文不加粗且左对齐，表头加粗且居中。

表格文字与普通正文共用上述格式字段；以下列出颜色及倍数行距的表格默认值：

| 字段 | 类型及范围 | 用法 |
|---|---|---|
| `color` | 六位十六进制 RGB，不带 `#` | 默认 `"000000"`。 |
| `line_spacing_multiple` | 数值，0.5–10 | 默认 1.15；使用倍数行距。 |

在同一对象中仅提供 `line_spacing_pt` 或 `line_spacing_multiple` 之一；不要同时传入。后续层显式指定一种行距时替换前一层的行距模式，例如从固定 24pt 改为 1.5 倍。普通段落、标题和表格文字样式均接受颜色及倍数行距。

按“内置表格文字默认值 → `styles.table_body` / `styles.table_header` → 当前表格的 `body_format` / `header_format`”逐字段覆盖。正文和表头分别计算，不要将 `body_format` 当作整张表格的通用文字覆盖；需要统一字体时分别设置两者。单元格默认用字符串，或按 列表与表格细节（`<skill-root>/references/generation-details.md`） 使用严格的 text/image/borders 对象；不接受任意 run、段落或合并布局。

将公共文字格式保存在 `TableBody` / `TableHeader`，各单元格仅引用样式；仅写出单表显式 `body_format` / `header_format` 的直接属性。显式覆盖即使与全局样式相同也保留，`false` 及固定/倍数行距切换仍有效。后续在 Word 中修改公共样式可影响未局部覆盖的属性；不要仅检查单元格直接属性来判断最终格式。

在顶层 `table_format` 和表格块的 `format` 中使用同一组布局字段；按“内置布局默认值 → 顶层 `table_format` → 单表 `format`”覆盖：

| 字段 | 类型及范围 | 默认值 |
|---|---|---|
| `align` | `left`、`center`、`right`；整张表的位置 | `left` |
| `vertical_align` | `top`、`center`、`bottom`；单元格内容位置 | `center` |
| `border_style` | `single` 或 `none`；作用于内外框 | `single` |
| `border_width_pt` | 数值，0.25–12；单位 pt | 0.5 |
| `border_color` | 六位十六进制 RGB，不带 `#` | `"000000"` |
| `cell_fill` | 六位十六进制 RGB 或 `"none"`；所有单元格底色 | `"none"` |
| `cell_margin_top_mm`、`cell_margin_bottom_mm` | 数值，0–50；单位 mm | 各 1 |
| `cell_margin_left_mm`、`cell_margin_right_mm` | 数值，0–50；单位 mm | 各 1.5 |
| `repeat_header` | 布尔值；对声明的所有前置表头行生效 | `true` |
| `allow_row_split` | 布尔值；是否允许单行跨页拆分 | `false` |

- 使用 `body_format.align` / `header_format.align` 控制文字水平对齐，使用 `format.align` 控制整张表的位置；不要混淆两者。
- 省略 `widths_mm` 时均分页面可用宽度；指定列宽时保留原有规则。列宽包含单元格留白，换算到文档单位后左右留白之和须小于每列宽度。需要窄列时显式减小留白，不要依赖阅读器自动挤压表格。
- 保持自动行高；不要传入 `row_height` 等未声明字段，不要以固定行高压缩内容。重复表头和避免拆行是分页请求，不保证超高行保持完整，也不验证实际页数。
- `header: true` 将第一行作为表头，`header_rows` 可明确指定连续前置表头数量；关闭 `repeat_header` 仅关闭重复，不取消表头格式。未声明表头时，不自动识别第一行。
- 使用 `border_style: "none"` 关闭边框，使用 `cell_fill: "none"` 清除全局底色；不要使用 `null` 表示关闭。所有提供的字段均须有效，即使之后被覆盖或当前不生效；拒绝未知字段、错误类型和非有限数值。

使用 `format.borders` 逐边设置表格边框，使用单元格对象的 `borders` 覆盖其 top/left/bottom/right；按 列表与表格细节（`<skill-root>/references/generation-details.md`） 核对继承、共享边冲突、层级续号、`header_rows` 和单元格图片规则。

保留上文原有中英文示例的内容。需要覆盖表格样式时，使用下面的完整中英混排示例；其中的 14pt 是显式覆盖，默认仍为 16pt：

```json
{
  "styles": {
    "table_body": {"size_pt": 14, "line_spacing_multiple": 1.2},
    "table_header": {"size_pt": 14, "bold": true}
  },
  "table_format": {"border_width_pt": 0.75, "cell_fill": "F2F2F2"},
  "blocks": [
    {"type": "heading", "level": 1, "text": "项目清单 / Project list"},
    {"type": "table", "header": true, "widths_mm": [70, 70],
      "format": {"cell_fill": "none", "cell_margin_top_mm": 0, "repeat_header": false},
      "body_format": {"line_spacing_pt": 22},
      "header_format": {"font_east_asia": "黑体", "bold": false},
      "rows": [["项目 / Item", "状态 / Status"], ["材料复核", "已完成 / Done"]]}
  ]
}
```

核对该例：表格文字 14pt；正文使用固定 22pt 行距，表头仍为 1.15 倍行距；表头改为黑体、不加粗，继续居中；边框为 0.75pt、无底色、顶部留白为 0；表头不跨页重复。普通标题和正文仍沿用原有公文预设，不要把单表覆盖保存为下一份文档的默认值。

