# 多级编号、表格与分节版式

路径占位符说明：从宿主实际读取本主入口 `SKILL.md` 的真实 `file_path` 去掉文件名确定 `<skill-root>`。相对读取路径先按宿主确认的基准目录解析。调用前替换 `<skill-root>` 并引用完整路径。无法定位或确认执行器映射时停止并报告。

在已确认的任务目录运行 `<skill-root>/scripts/tl_docx.py` 的 `create`。普通段落、字段与分节参数见 `<skill-root>/references/writing.md`。下列设置产生 Word 原生标记；实际编号显示、分页和字段值在阅读器中核对。

## 多级编号

顶层 `heading_numbering` 为 `none`、`chinese` 或 `decimal`，只作用于 1–4 级标题。标题文字无需手工再加编号。普通列表使用单独的编号实例。`list_id` 指定续号实例，`level` 从 0 到 8，首次必须从 0 开始，后续不能跳过父层级。一个实例内保持 `ordered` 一致。省略 `list_id` 时各块继续独立编号。

```json
{"heading_numbering":"chinese","blocks":[{"type":"heading","level":1,"text":"工作计划"},{"type":"list","list_id":"事项","ordered":true,"items":["第一项"]},{"type":"paragraph","text":"补充说明"},{"type":"list","list_id":"事项","level":1,"ordered":true,"items":["子项"]}]}
```

## 复杂表格

- `header_rows` 指定连续前置表头行，`repeat_header` 控制跨页重复。`caption` 在表上方增加题注并设置与下段同页。
- 列宽可用 `widths_mm` 或原有 `col_widths_cm`。`header_rows` 与旧 `header` 同传时须一致。合并覆盖格使用 `null`，可用 `merges` 或单元格对象 `row_span`、`col_span`，不要跨越表头与数据行。
- 顶层 `table_format` 以及单表 `format` 接受 `preset`（`grid`、`three_line`、`zebra`）、`column_sizing`（`equal`、`content`）、`repeat_header`、`allow_row_split`、`align`、`vertical_align`、填色、留白、边框等。单表覆盖顶层。边框 `borders` 支持 `top/left/bottom/right/insideH/insideV`，每边可设 `style`、`width_pt`、`color`。
- 单元格可使用 `{"text":"说明","image":{"path":"现成图片.png","width_mm":15,"height_mm":10,"alt":"示意图"}}`。图片在原单元格段落内，按任务相对路径读取，并核对尺寸。规则未合并表可逐格设置四边 `borders`，共享边冲突时报错。合并表可用表级边框。

```json
{"type":"table","caption":"指标对照","header_rows":2,"widths_mm":[50,60],"format":{"preset":"three_line","borders":{"top":{"width_pt":1.5}}},"rows":[[{"text":"汇总","col_span":2},null],["指标","数值"],["示例","1"]]}
```

## 首页、奇偶页和分节页码

顶层 `odd_even_headers:true` 开启奇偶变体。首节和 `section_break` 可设 `different_first_page`，以及 `headers`、`footers` 对象的 `default`、`first`、`even`，值为文字、run 数组或显式空值 `null`。`first` 要求开启不同首页，`even` 要求文档已开启奇偶变体。`default` 可继续使用原 `header` 或 `footer` 字段。

`page_numbers:true` 使用原生 PAGE 字段。`first_page_numbers:true` 要求启用不同首页。`page_number_start` 或旧 `page_start` 用于显式重启，`page_number_format` 支持 `decimal`、`upperRoman`、`lowerRoman`、`upperLetter`、`lowerLetter`。新节可覆盖或清空页眉页脚，未重置页码起始值时继续编号。目录与页码需要阅读器更新字段。
