# P2 原生高级版式：多节、分节页码、页眉页脚、交叉引用和表格标题

路径占位符说明：`<skill-root>` 是本 DOCX Skill 根目录的文档占位符，不是 Shell 变量。取得宿主本次实际读取主入口 `SKILL.md` 的真实 `file_path`；若为相对路径，先按本次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名，所得目录才是根目录，不能使用参考文档所在目录。读取资源或执行命令前，将 `<skill-root>` 整体替换为该真实目录，保留资源后缀，将完整路径作为一个参数正确引用；嵌入 execute 字符串时按其语法转义。无法确认主入口真实路径时停止依赖操作并报告，禁止猜测路径。示例的工作目录为宿主已确认的任务目录 `./`，任务输入和受控临时文件沿用该目录内相对路径规则；交付输出只传裸文件名，由脚本固定写入任务根目录 `./`，不写入 Skill 安装目录。

路径说明：`<skill-root>` 是宿主本轮实际读取主入口 `SKILL.md` 的真实绝对目录；调用前必须替换，不得通过当前工作目录或历史运行环境推测。该扩展只作用于 **新建 DOCX**，使用 Python 标准库写入 WordprocessingML；不依赖 python-docx / LibreOffice / WPS，不适用于原文件复杂编辑或视觉渲染。

## 能力清单及限制

| 能力 | 支持情况 | 适用范围和注意点 |
|---|---|---|
| 多节与横向附录 | 已支持 | `type: section_break`，在块之间创建新节，继承前节页边距和页眉页脚；支持纵横切换 |
| 分节断开方式 | 已支持 | `next_page`（默认）、`continuous`、`odd_page`、`even_page`；具体分页由 Word/WPS 决定 |
| 首页不同 | 已支持 | `different_first_page: true`；`headers.first`、`footers.first` 单独设定，未设定时第一页为空 |
| 奇偶页不同 | 已支持 | 顶层 `odd_even_headers: true`（文档级）；`headers.even`、`footers.even`；未给偶数文本时沿用默认文本 |
| 分节页码 | 已支持 | 各节 `page_number_start` 可重启，省略则继续前一节页码；`page_number_format` 可指定格式 |
| 字段交叉引用 | 已支持（字段生成） | `type: reference`，引用本文带 `bookmark` 的标题，支持 `REF` / `PAGEREF`，需阅读器更新域 |
| 表格标题与分页关联 | 已支持 | `table.caption` 产生表格上方单独的 `TableCaption` 段落样式，设置 `keepNext` |
| 任意位置 REF / 内联混合多字段 | 暂不支持 | 目前每个 `reference` 块生成一个独立段落，可设置 `prefix` / `suffix`；不支持 run 内交叉引用 |
| 真实页数、目录/REF/PAGE 实际结果缓存 | 不支持 | 仅写入原生字段和更新提示；Word/WPS 打开并更新域后才可确认结果 |

## 一体化使用示例

```json
{
  "profile": "business-report",
  "heading_numbering": "decimal",
  "header": "年度业务报告",
  "footer": "内部资料",
  "page_numbers": true,
  "odd_even_headers": true,
  "different_first_page": true,
  "headers": {"first": "报告封面", "even": "年度业务报告 · 偶数页"},
  "footers": {"first": "封面"},
  "page_number_start": 1,
  "blocks": [
    {"type": "heading", "level": 1, "text": "经营综述", "bookmark": "overview"},
    {"type": "paragraph", "text": "以下报告正文为示例内容。"},
    {"type": "reference", "bookmark": "appendix", "kind": "page", "prefix": "附录见第", "suffix": "页。"},
    {"type": "section_break", "break_type": "next_page", "page": {"orientation": "landscape", "margin_mm": 20},
     "header": "经营数据附录", "page_number_start": 1, "page_number_format": "lowerRoman"},
    {"type": "heading", "level": 1, "text": "统计附录", "bookmark": "appendix"},
    {"type": "table", "caption": "表1 机构存款数据", "header_rows": 2, "rows": [
      [{"text": "机构", "row_span": 2}, {"text": "存款规模", "col_span": 2}, null],
      [null, "上期", "本期"],
      ["机构A", "15", "18"]
    ]},
    {"type": "section_break", "page": {"orientation": "portrait"}},
    {"type": "paragraph", "text": "报告结束。"}
  ]
}
```

### 页面属性

顶层 `page` 作用于首节，`section_break.page` 只覆盖本节变动的页面字段（未提供则继承）。字段：`width_mm`、`height_mm`、`margin_mm`、`margin_top_mm`、`margin_bottom_mm`、`margin_left_mm`、`margin_right_mm`、`orientation`（`portrait` / `landscape`）。指定 `orientation` 后，如页面宽高与指定方向不符，会互换现有宽高尺寸；不给 `orientation` 则保留当前宽高。页面宽高不超过 558.8mm，边距不超过 250mm，内容区域换算后必须保持正宽度、高度。各节图片、表格宽度按**所在节**校验。

`section_break` 不能是第一块、最后一块或连续出现。分节与普通 `page_break` 不同；分节块在 OOXML 中写出前一节的 `<w:pPr><w:sectPr>`，最后一节在文档末尾的 `w:body/w:sectPr`，下一节的 `break_type` 写在下一节属性内。`continuous` 只是 OOXML 的“连续分节”意图，横竖方向变化等页面级属性在阅读器中可能产生换页或重排，建议此时使用 `next_page`。

### 页眉、页脚与页码

- 原有顶层 `header`、`footer`、`page_numbers` 语义保留；新节默认继承前节设置，`section_break` 可以用同名字段覆盖。
- 新节显式 `page_numbers:false` 会移除该节各启用变体的 PAGE 字段，同时保留页脚文字；无文字时使用空页脚切断前节页码继承。后续节可重新启用。全文未启用页码且未指定页脚时，仍不生成默认页脚部件。
- 顶层与每节均可声明 `headers` / `footers` 对象，支持 `default`、`first`、`even` 三种变体。字符串设置内容，`null` 显式清空；默认变体与旧 `header` / `footer` 并存时，新对象的 `default` 优先。设置首页变体须启用 `different_first_page`；设置偶数页变体须在顶层启用 `odd_even_headers`。
- `different_first_page` 为该节开关，默认 `false`；开启后 `first` 不设置时自动为空白（不意外继承正常页页眉）。`first_page_numbers` 默认 `false`，如需首页页码，必须同时开启 `different_first_page`。
- `odd_even_headers` 是**文档级开关**，不可在某一节单独启停；指定 `even` 文本优先，未指定时复制默认文本。页码字段默认写到普通页/偶数页的页脚；首页是否写入由 `first_page_numbers` 决定。
- `page_number_start` 是 1–32767 的整数，首节可设置，后续节**只有明确重新设置时才重启**；缺省不是重新开始。`page_number_format` 支持 `decimal`、`upperRoman`、`lowerRoman`、`upperLetter`、`lowerLetter`，后续节不提供则继承前节格式。页码显示是一个 `PAGE` 字段，不是 Python 计算出的最终数字。
- 为了避免 Word 对省略页眉页脚部件的隐式继承造成“上一节页眉串到下一节”，当需要清空某个已经出现过的变体时，生成器会建立明确的空部件。相同内容的页眉/页脚会复用同一 ZIP 部件。

### 交叉引用

标题块通过 `{"type":"heading", "level":1, "text":"附录", "bookmark":"appendix"}` 建立书签，再通过 `{"type":"reference", "bookmark":"appendix", "kind":"page", "prefix":"见第", "suffix":"页"}` 引用。`kind: text` 输出 `REF` 字段，`kind: page` 输出 `PAGEREF` 字段，默认 `text`。支持向后引用；生成前验证目标书签确实存在（大小写不敏感），无效书签或不存在的目标拒绝生成。

交叉引用字段使用安全的 ASCII 书签名，仅接受字母开头、长度 1–40 的字母/数字/下划线，写入 `w:instrText`，不支持用户自定义域指令，避免域代码注入。缓存是“请更新交叉引用域”占位文字；生成器写入 `word/settings.xml` 的 `updateFields`，但 **无法在标准库内生成准确页码或标题编号显示值**。Word/WPS 打开后请更新所有字段，并检查生成结果。如果 `heading_numbering` 启用，书签当前仅包围标题文本，`REF` 引用标题内容不保证包括渲染出来的章节自动编号。

### 表格标题

`table.caption` 是可选的非空字符串，生成一个**位于表格上方**的 `TableCaption` 段落，默认居中、12pt、段后 6pt，启用 `keepNext` 尽量与表格首行同页。表格主体依旧支持 P1 的 `row_span` / `col_span` 及 `header_rows`。不支持表格下方标题或自动图表题注编号，`keepNext` 不保证超长表格在同一页完整显示。

## 静态验收范围及限制

`create_document(..., verify=True)` 在原结构、内容、编号与合并网格验收之外，逐节核验 `sectPr` 的页尺寸、页边距、页码、不同首页、页眉页脚关系及文字，校验交叉引用字段和表格标题样式。`validate_layout.py` 逐节估算表格及图片宽度，检查书签目标、页码起始值、节属性和相关结构。检查不等于 OOXML 全量 XSD 校验，亦不能证明 Word/WPS 视觉排版、字段求值、精确分页或字体实际存在。

P2 专项和完整回归仅在完整源码仓库中维护，不随运行包分发；维护者按仓库级 DOCX 说明执行，普通文档任务不运行维护测试。

## 分页策略风险检查（P3 增量）

分页、分节和段前分页共同使用时，参照 `<skill-root>/references/pagination-governance.md`。原 `section_break`、页眉页脚、页面方向语义不变；静态警告不代表真实空白页。
