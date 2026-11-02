# 生成器主题、页面与样式详细契约

路径占位符说明：`<skill-root>` 是本 DOCX Skill 根目录的文档占位符，不是 Shell 变量。取得宿主本次实际读取主入口 `SKILL.md` 的真实 `file_path`；若为相对路径，先按本次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名，所得目录才是根目录，不能使用参考文档所在目录。读取资源或执行命令前，将 `<skill-root>` 整体替换为该真实目录，保留资源后缀，将完整路径作为一个参数正确引用；嵌入 execute 字符串时按其语法转义。无法确认主入口真实路径时停止依赖操作并报告，禁止猜测路径。示例的工作目录为宿主已确认的任务目录 `./`，任务输入、输出和临时文件均在该目录及其子目录内，不写入 Skill 安装目录。

本页仅供对应任务按需加载。定位 `<skill-root>` 与任务目录、执行限制以主入口 `<skill-root>/SKILL.md` 为准；本页提供完整原始字段契约。

## 主题与文档标题

每次新建文档，用户未指定排版时，在输入中显式设置 `"theme": "official"`，直接使用公文默认值，不先要求用户选择主题。Python 对省略 `theme` 的调用仍默认使用 `official`。只有用户明确选择其他主题或给出格式要求时，才采用对应主题或显式覆盖；不要仅依据文档名称自动切换主题。

使用 `theme` 选择公文 `official`、商务 `business` 或学术 `academic`；显式样式覆盖优先于主题。另可在用户明确要求文种排版时使用 `profile` 选择四种固定内置排版配置（见 `<skill-root>/references/typography.md`），不传时旧行为完全不变。这些选项均为 DOCX 内的排版预设，不代表公文或学术标准认证，也不代表其他文件格式的导出能力。编辑已有文档不应用新建默认主题。

成功生成、验收并核实文件后，直接按 `SKILL.md` 的「验收与收尾」组织简短交付回复，不为此读取 `generation-runtime.md`。

| 主题 | 正文与西文字体 | 正文排版 | 标题 |
|---|---|---|---|
| official | 仿宋 / Times New Roman | 保持 16pt、固定 28pt、首行 2 字 | 保持原有四级标题 |
| business | 微软雅黑 / Calibri | 10.5pt、1.25 倍、段后 4pt、无首行缩进 | 黑体、深蓝、章节左对齐 |
| academic | 宋体 / Times New Roman | 10.5pt、1.25 倍、段后 4pt、首行 2 字 | 黑体、黑色、章节左对齐 |

主题同时设置表格正文和表头的基础字体、字号、行距；后者保留加粗及居中。字体只记录名称，不安装或嵌入。`title` 和 `subtitle` 块支持 `text`、`format`、`align`，不接受 `level`，不写大纲级别；使用 `styles.title`、`styles.subtitle` 覆盖，默认分别为 22pt 和 14pt。章节继续使用原有 heading 1–4，不改变旧输入的语义。

商务和学术主题的页眉页脚使用独立的 `Header`、`Footer` 段落样式，采用对应主题基础中西文字体、10.5pt、1.25 倍行距、黑色、不加粗、左对齐、无首行缩进、段前段后 0pt。正文的 `styles.body` 覆盖不会改变这些样式；它们是内置 Word 样式，不是新增 JSON 样式字段。纯页码页脚及页码字段结果同样继承 `Footer`。公文主题保留原有基础样式：仿宋 / Times New Roman、16pt、1.15 倍行距、段前段后 0pt。省略页眉页脚且未启用页码时不生成相应部件；显式空字符串保留空部件。字体是否可用及页码实际值仍须在阅读器中核对。

以下仅为用户明确选择商务排版时的示例，默认新建仍使用 `official`：

```json
{"theme":"business","blocks":[
  {"type":"title","text":"项目报告"},
  {"type":"subtitle","text":"季度进展"},
  {"type":"heading","level":1,"text":"一、概况"},
  {"type":"paragraph","text":"正文内容。"}
]}
```

商务和学术列表使用内置 `ListParagraph` 样式，基础中西文字体与主题一致，10.5pt、1.25 倍行距、段前 0pt、段后 4pt、黑色、不加粗、左对齐；不写首行缩进属性，保留编号定义的悬挂缩进。正文覆盖不影响列表。公文列表仍使用原有基础样式，`ordered`、`list_id` 与 `level` 的语义不变；不增加列表样式输入字段。

## 默认格式与页面

使用公文主题 `official` 且省略 `page`、`styles` 和局部格式时，生成以下公文排版预设；不要将其等同于完整公文标准认证。

| 对象 | 中文字体 | 字号 | 对齐 | 加粗 | 首行缩进 | 行距 |
|---|---|---|---|---|---|---|
| `heading1` | 方正小标宋简体 | 18pt | 居中 | 否 | 0 字 | 1.15 倍 |
| `heading2` | 黑体 | 16pt | 左对齐 | 否 | 0 字 | 1.15 倍 |
| `heading3` | 楷体 | 16pt | 左对齐 | 是 | 0 字 | 1.15 倍 |
| `heading4` | 仿宋 | 16pt | 左对齐 | 否 | 0 字 | 1.15 倍 |
| `body` | 仿宋 | 16pt | 左对齐 | 否 | 2 字 | 固定 28pt |

使用 Times New Roman 作为默认西文字体；使用零段前、段后间距。通过 `BodyText` 段落样式应用正文格式，不要插入空格模拟首行缩进。让标题、表格、列表、图片、分页及页眉页脚使用各自样式或基础样式，避免继承正文的缩进和固定行距。基础样式使用仿宋 16pt、Times New Roman、1.15 倍行距；保留列表自身的编号缩进。表格正文和表头分别使用独立的 `TableBody`、`TableHeader` 段落样式。

仅记录字体名称；不要安装、下载或嵌入字体，不要声称已验证阅读端字体可用。只为新建文档应用这些默认值；受控编辑保持已有文档样式。

| `page` 字段 | 单位 | 默认值与规则 |
|---|---|---|
| `width_mm`、`height_mm` | mm | 210、297；作为第一节默认值，后续节可覆盖。 |
| `margin_top_mm`、`margin_bottom_mm` | mm | 25.4、25.4。 |
| `margin_left_mm`、`margin_right_mm` | mm | 31.8、31.8。 |
| `margin_mm` | mm | 可选，统一覆盖四边；仍允许用单边字段再次覆盖。 |

`orientation` 可选 `portrait` / `landscape`；显式设置时自动调整宽高方向。按“用户单边字段 > 用户 `margin_mm` > Profile 或主题默认单边值”计算边距。例如 `{"margin_mm": 20, "margin_top_mm": 25.4}` 表示顶部 25.4mm，其余三边 20mm，使用 `business-report` 时同样生效。不要将厘米值误写为毫米。所有提供的字段均须有效，即使某字段随后被更高优先级覆盖；边距不得为负，换算后须保留正的可用宽高。

按扣除实际左右边距后的宽度限制表格和图片；默认可用宽度约为 146.4mm。省略 `widths_mm` 时均分可用宽度，显式列宽之和不得超过可用宽度；图片还须满足扣除上下边距后的可用高度。不要通过超出页面可用尺寸的值模拟浮动对象。

## 样式字段与覆盖顺序

仅在 `styles` 中使用 `title`、`subtitle`、`body`、`heading1`、`heading2`、`heading3`、`heading4`、`table_body`、`table_header`；不要传入任意 Word 样式 ID。每一项均为部分字段对象。标题及普通段落可使用 `format` 对象覆盖下列字段；表格文字的默认值和覆盖方式见下文：

| 字段 | 类型及范围 | 用途 |
|---|---|---|
| `font_east_asia` | 非空单行字符串，最多 64 字符 | 中文字体名称。 |
| `font_latin` | 非空单行字符串，最多 64 字符 | 西文字体名称。 |
| `size_pt` | 数值，1–200 | 字号，单位 pt。 |
| `bold` | 布尔值 | `false` 显式取消继承的加粗。 |
| `align` | `left`、`center`、`right`、`justify` | 段落对齐。 |
| `first_line_chars` | 数值，0–20 | 首行缩进字符数；0 取消首行缩进。 |
| `line_spacing_pt` | 数值，1–400 | 固定行距，单位 pt；不接受倍数或自动行距字符串。 |
| `line_spacing_multiple` | 数值，0.5–10 | 正文、标题及表格均支持倍数行距；与同层固定行距互斥。 |
| `color` | 六位十六进制 RGB，不带 `#` | 正文、标题及表格均支持文字颜色。 |
| `space_before_pt`、`space_after_pt` | 数值，0–400 | 段前、段后间距，单位 pt。 |
| `keep_with_next`、`keep_together`、`widow_control`、`page_break_before` | 布尔 | 控制段落与下文同页、段内同页、孤行寡行和段前分页。 |
| `left_indent_pt`、`right_indent_pt`、`first_line_indent_pt`、`hanging_indent_pt` | 数值，0–400 | 控制磅值缩进；首行三种模式互斥。 |
| `tab_stops` | 最多32项的有序数组 | 自定义段落制表位，需配合显式 tab run。 |

按字段依次覆盖：内置主题默认值 → 可选 `profile` 对主题及样式的默认设置 → 对应 `styles` 项 → 当前块 `format` → 块级 `align`（仅对齐）→ run 字符字段（仅文字格式）。省略字段表示继承；禁止使用 `null` 表示默认值。不要用字符串、布尔值代替数值，不要使用 NaN、Infinity、负值或未知字段。

普通段落与标题通过 `BodyText`、`Heading1` 至 `Heading4` 继承对应样式；默认四级标题的样式均带 `keepNext`，不携带 `pageBreakBefore`，因此仅保持标题与下文尽量同页，**不会因为章节层级自动另起新页**。用户明确要求时才给目标标题增加 `format.page_break_before:true`；不同时在标题前插入 `page_break`。仅将块和 run 显式指定的格式写为直接属性。后续修改 Word 样式可影响未局部覆盖的属性；显式覆盖即使与样式当前值相同也保留。

使用 run 的 `font_east_asia`、`font_latin` 分别控制中西文字体。保留 run 的旧字段 `font`：它同时指定中西文字体；同一 run 中的专用字体字段再覆盖相应字体。例如 `{"text": "中文 ABC", "font": "Arial", "font_east_asia": "仿宋"}` 使用仿宋显示中文、Arial 显示西文。不要将 run 的 `font` 放入 `styles` 或 `format`。

允许 run 的 `bold`、`italic`、`underline` 显式设为 `false`；不要通过省略字段尝试取消已继承的格式。仅覆盖已声明字段，不要假定修改字号会自动调整行距或首行字符缩进。

