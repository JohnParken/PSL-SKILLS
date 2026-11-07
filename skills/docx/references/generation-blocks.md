# 生成器结构化内容块与扩展字段

路径占位符说明：`<skill-root>` 是本 DOCX Skill 根目录的文档占位符，不是 Shell 变量。取得宿主本次实际读取主入口 `SKILL.md` 的真实 `file_path`；若为相对路径，先按本次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名，所得目录才是根目录，不能使用参考文档所在目录。读取资源或执行命令前，将 `<skill-root>` 整体替换为该真实目录，保留资源后缀，将完整路径作为一个参数正确引用；嵌入 execute 字符串时按其语法转义。无法确认主入口真实路径时停止依赖操作并报告，禁止猜测路径。示例的工作目录为宿主已确认的任务目录 `./`，任务输入和受控临时文件沿用该目录内相对路径规则；交付输出只传裸文件名，由脚本固定写入任务根目录 `./`，不写入 Skill 安装目录。

本页仅供对应任务按需加载。定位 `<skill-root>` 与任务目录、执行限制以主入口 `<skill-root>/SKILL.md` 为准；本页提供完整原始字段契约。

## 顶层字段

- 使用可选 `profile` 选择固定的内置排版配置（`official-basic`、`business-report`、`academic`、`presentation-material`）；不接受路径。
- 使用可选 `theme` 选择命名主题；使用可选布尔值 `deduplicate_images` 复用相同图片字节，默认 `false`。
- 每次生成或预检独立缓存规范化路径的图片字节及尺寸头；同一路径读取一次，不跨调用保存缓存。缓存与包内去重独立，`false` 仍生成独立媒体，各引用的尺寸、替代文字和绘图 ID 独立。累计缓存和待写媒体仍受 128 MiB 预算约束，单图仍最多 32 MiB，最终包预算还包括 XML。
- 使用必需的 `blocks` 数组按顺序描述内容。
- 使用可选 `page` 设置首节页面；默认 A4、上下 25.4mm、左右 31.8mm 页边距。通过 `page.orientation` 选择横向或纵向，使用 `section_break` 创建后续节并按节覆盖。详见 `<skill-root>/references/p2-layout.md`。
- 使用可选 `styles` 设置正文、四级标题及表格正文/表头的样式；未提供的字段保留对应默认值。
- 使用可选 `table_format` 设置所有表格的布局；允许单张表格使用 `format` 覆盖。
- 使用 `header`、`footer` 字符串设置普通页眉页脚；使用 `page_numbers: true` 插入 PAGE 字段。P2 可使用 `headers` / `footers`、`different_first_page`、`odd_even_headers`、`page_number_start`、`page_number_format`、`first_page_numbers`，以及 `section_break`、`reference`、`table.caption`。完整规则见 `<skill-root>/references/p2-layout.md`；不要把字段缓存值当作已计算的页码。

## 内容块

| type | 字段 | 约束 |
|---|---|---|
| title / subtitle | `text`, `format`, `align` | 文档标题与副标题，不接受 level，不占大纲层级。 |
| heading | `text`, `level`, `format`, `align` | 明确提供 level，范围为 1–4；format 和 align 可选。 |
| paragraph | `text` 或 `runs`, `format`, `align` | 不要同时给 text/runs；align 为 left、center、right、justify。 |
| list | `items`, `ordered`, `list_id`, `level` | items 为字符串数组；旧单层用法不变，命名实例可按明确层级续号。 |
| table | `rows`, `widths_mm`, `header`, `header_rows`, `format`, `body_format`, `header_format`, `caption` | rows 为规则二维逻辑网格，单元格为字符串或受限 text/image/borders/col_span/row_span 对象；被合并覆盖的位置必须为 null；可指定列宽及前置表头行数。参见 P1 排版（`<skill-root>/references/p1-layout.md`）；可用 `caption` 生成上方表题（见 P2）。 |
| image | `path`, `width_mm`, `height_mm`, `alt` | 仅嵌入 PNG/JPEG；至少给定宽或高，缺少的尺寸由尺寸头等比例推算。 |
| toc | `title`, `depth` | 生成目录标题和待更新的 Word TOC 字段；Word/WPS 更新域后才有实际条目及页码。 |
| page_break | 无其他字段 | 插入手动分页；不与标题 `page_break_before` 或分页分节相邻叠加。分页风险审计见 `<skill-root>/references/pagination-governance.md`。 |
| section_break | `break_type`, `page`, `header`, `footer`, `headers`, `footers`, `page_numbers`, `different_first_page`, `first_page_numbers`, `page_number_start`, `page_number_format` | 从此处开始新的一节，按节调整页面与页眉页脚。`next_page` 等分页分节不需另加手动分页；字段/语义见 `<skill-root>/references/p2-layout.md` 和 `<skill-root>/references/pagination-governance.md`。 |
| reference | `bookmark`, `kind`, `prefix`, `suffix` | 独立段落中的标题书签 REF/PAGEREF 字段，需要在 Word/WPS 中更新域。 |

在 `runs` 中使用 `text` 或 `{"tab":true}`（二者互斥），以及 `bold`、`italic`、`underline`、`font`、`font_east_asia`、`font_latin`、`size_pt`、`color`；颜色使用不带井号的六位十六进制 RGB。让 XML 库处理文本转义，禁止预先将 `&` 转成 `&amp;`。

将文本字段限制为单行，禁止插入控制字符；需要制表符时仅使用 `{"tab":true}` run，并配合 `format.tab_stops`；多段文字拆成多个 paragraph 块。提供 image 的 `alt` 描述（允许空字符串），只给宽或高时读取 PNG IHDR（含头部 CRC）或 JPEG SOF 尺寸头推算比例；两项都给时保持显式尺寸。尺寸头检查不等于像素解码，不按 EXIF 方向旋转。

按以下正反例构造输入；不要把自然语言需求直接当作生成器字段：

| 需求 | 使用 | 不要使用 |
|---|---|---|
| 加粗文字 | `{"text": "结论", "bold": true}` | `{"text": "结论", "bold": "true"}` |
| 两段内容 | 两个 paragraph 块 | 在单个 text 中嵌入换行 |
| 填写公司名称 | 单个 run 中完整的 `{{ company }}` | 将占位符拆分到多个 run |

```json
{
  "header": "季度进展",
  "footer": "内部资料",
  "page_numbers": true,
  "blocks": [
    {"type": "heading", "level": 1, "text": "项目报告"},
    {"type": "paragraph", "runs": [
      {"text": "结论：", "bold": true},
      {"text": "按期完成", "color": "008000", "size_pt": 12}
    ]},
    {"type": "list", "ordered": false, "items": ["完成复核", "安排交付"]},
    {"type": "table", "header": true, "widths_mm": [70, 70],
      "rows": [["项目", "状态"], ["验证", "通过"]]}
  ]
}
```

## P1 原生排版扩展（2026-10-31）

可选 `heading_numbering`：`none`（默认）、`chinese`、`decimal`。`heading` 可设唯一 ASCII `bookmark`。`table.rows` 中的单元格对象可用 `col_span` / `row_span` 声明合并，所有被覆盖的坐标必须为 JSON `null`；重复表头与数据区不允许垂直跨界合并。支持 `toc` 块（`depth`: 1–4，`title` 可选）生成原生 TOC 域和自动更新字段设置，但无法在标准库中计算真实目录页码。详细规范、完整示例和边界说明见 `<skill-root>/references/p1-layout.md`。本 P1 阶段当时尚不支持多节与交叉引用，但后续 P2 已实现；目前仍不支持浮动图形和嵌套表格，参见 `<skill-root>/references/p2-layout.md`。

## P2 原生高级版式扩展（2026-10-31）

现在可用 `section_break` 按节覆盖 `page` / 页眉页脚 / 页码及页面方向；顶层 `odd_even_headers` 控制奇偶页变体，逐节 `different_first_page` 控制首页变体。`reference` 块可生成指向唯一标题书签的 `REF` / `PAGEREF` 字段；`table.caption` 在表格上方创建带 `keepNext` 的标题。完整示例、数据校验、字段更新与视觉边界见 `<skill-root>/references/p2-layout.md`。
