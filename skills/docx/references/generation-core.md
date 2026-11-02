# 生成核心规范（常用轻量版）

> 仅在普通文档需要额外确认输入字段时加载本页。其他任务只读相关专项文档，不要顺序读完所有 references。路径和执行前置约束遵循 `<skill-root>/SKILL.md`。

## 基本流程

1. 获得宿主实际读取的 `SKILL.md` 的真实位置，取父目录为 `<skill-root>`；任务数据放宿主已确认的 `./`，不得把 `<skill-root>` 占位符原样用于命令。
2. 普通新建只调用 `python -I -S -B "<skill-root>/scripts/run_docx_task.py" create "真实主题.docx"`，用 stdin 传完整 JSON；无需先运行 preflight/check。调用时保留真实退出码与 stdout/stderr；不要重复提交状态不明的任务。
3. 结果中核对 `status`、`task_status`、实际 `output`、`archive_status`、`cleanup` 和 warnings。归档失败不否定已验收的主文件，但清理未完成必须如实说明；不得交付旧文件冒充新成果。

## 常用 JSON 字段

```json
{"theme":"official","blocks":[
  {"type":"title","text":"年度总结"},
  {"type":"heading","level":1,"text":"总体情况"},
  {"type":"paragraph","text":"本年度重点工作稳步推进。"}
]}
```

- `blocks` 必需、保持顺序；`title`、`subtitle` 为文档标题，`heading` 要求 `level` 为 1–4，`paragraph` 可用 `text` 或 `runs`（二者互斥），`list` 使用 `items` 和可选 `ordered`；复杂字段见 `generation-blocks.md`。
- `theme` 仅支持 `official`（默认）、`business`、`academic`。未明确指定排版时显式使用 `official`；不要仅凭“报告”字眼切换主题。固定 `profile` 仅在用户明确要求时使用 `official-basic`、`business-report`、`academic`、`presentation-material`。样式详情见 `generation-styles.md` 和 `typography.md`。
- `page` 控制页面（默认 A4，单位 mm）；`styles` 设置 `title`、`subtitle`、`body`、`heading1`～`heading4`、`table_body`、`table_header`；块级 `format` 和 run 显式字段只覆盖声明过的属性。不能传 `null`、未知字段或非法枚举。
- 单行文本不得包含真实换行或控制字符；多段正文写多个 `paragraph`；文字中的 `&` 等交给 XML 处理，不自行 HTML/XML 转义；中文 JSON 使用 UTF-8 和英文半角结构标点。复杂 JSON 用 Python `dict/list` + `json.dumps(ensure_ascii=False,allow_nan=False)`，不手拼 JSON 或把长文本写成不受保护的临时文件。
- `table` / `image`、`toc`、`page_break`、`section_break`、`reference` 等按需分别查 `generation-tables.md`、`generation-blocks.md`、`p1-layout.md`、`p2-layout.md`、`pagination-governance.md`。图片仅 PNG/JPEG，至少提供一个尺寸（mm），图片路径在标准 stdin 入口相对任务目录。
- 自动标题编号 `heading_numbering` 仅 `none`、`chinese`、`decimal`；目录/页码/REF、PAGEREF 字段的结果由 Word/WPS 更新，不承诺提前获得真实页数或视觉布局。

## 版式与输入冲突

- **默认 `official` 策略：** 用户未明确要求强制新页时，正文自然分页，标题依赖 `keep_with_next` 与后文同页；不因一级标题、报告/总结等名称自行添加 `page_break`、`section_break` 或 `page_break_before:true`。只有明确要求章节另起页，才给目标标题设置 `format.page_break_before:true`；封面边界可选择独立 `page_break`；横向附录或节级页面属性变化使用 `section_break`。同一位置不要重复使用两种换页机制，保留用户明确提供的合法分页；分页策略审计只读、默认警告。参考 `pagination-governance.md`。
- 表格合并需显式 `row_span` / `col_span` 且覆盖格为 `null`；重复表头不能跨表头/正文边界合并。长表跨页应设置适当 `header_rows`。参考 `generation-tables.md` 和 `p1-layout.md`。
- 不尝试使用本技能生成 PDF、WPS 原生格式、浮动图片、嵌套表格或任意 Office 图表；不可声称真实 Word/WPS 渲染已自动验证。

## 按需跳转

| 需求 | 仅需阅读 |
|---|---|
| 文件命名、归档、旧 CLI、预检、配置诊断 | `generation-runtime.md` 中对应章节 |
| 主题参数、默认字体和精细样式字段 | `generation-styles.md` |
| 列表、图片、结构块及原生目录/章节扩展 | `generation-blocks.md` / `generation-details.md` |
| 表格格式、边框、留白、多级表头 | `generation-tables.md` + 必要时 `p1-layout.md` |
| 段落格式覆盖与中文 JSON 实例 | `generation-examples.md` |
| 分节与页眉页脚、交叉引用 | `p2-layout.md` |
| 只读分页审计 | `pagination-governance.md` |

全部分流入口在 `generation.md`，按需读取，不因链接存在而预读。
