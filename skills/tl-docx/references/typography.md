# 创建样式与页面

路径占位符说明：从宿主实际读取主入口 `SKILL.md` 的真实 `file_path` 去掉文件名确定 `<skill-root>`；相对路径按宿主确认的读取基准解析。使用前替换并引用完整路径，无法定位则停止。以下 JSON 通过已确认任务目录内的 `execute` 调用 `<skill-root>/scripts/tl_docx.py` 的 `create`，不是已有文件的编辑格式。

## Profile 与角色样式

`profile` 可选 `official-basic`、`business-report`、`academic`、`presentation-material`。省略时不改变已有默认公文行为。Profile 仅是排版预设，不表示公文标准认证；明确 `theme` 可覆盖 Profile 主题。

`styles` 接受 `body`、`title`、`subtitle`、`heading1`–`heading4`、`table_body`、`table_header`。每项为部分格式对象；普通段落和标题还可使用 `format`，块级 `align` 单独覆盖对齐。

覆盖顺序：主题默认值 → Profile → 对应 `styles` → 块 `format` → 块级对齐及分页开关 → run 字符字段。同层固定和倍数行距互斥；跨层切换时替换原模式。首行字符、首行磅值、悬挂缩进同理。缺省字段继承，`null`、未知键、非有限数、布尔冒充数字均不接受。

| 格式字段 | 范围 |
| --- | --- |
| `font_east_asia`、`font_latin` | 非空单行字体名，最多 64 字符；只记录名称，不安装字体 |
| `size_pt`、`bold`、`color` | 字号 1–200 pt；布尔加粗；六位 RGB |
| `align` | `left`、`center`、`right`、`justify` |
| `first_line_chars` | 0–20 字；0 清除首行字符缩进 |
| `left_indent_pt`、`right_indent_pt`、`first_line_indent_pt`、`hanging_indent_pt` | 0–400 pt |
| `line_spacing_pt` / `line_spacing_multiple` | 1–400 pt 固定行距 / 0.5–10 倍 |
| `space_before_pt`、`space_after_pt` | 0–400 pt |
| `keep_with_next`、`keep_together`、`widow_control`、`page_break_before` | 布尔值；保留显式分页意图 |

`runs` 支持 `font_east_asia`、`font_latin` 分别覆盖中西文字体；旧 `font` 同时指定两者，专用字段优先。`bold`、`italic`、`underline` 可用 `false` 取消。制表位继续使用块级 `tab_stops`，不是 `format` 字段。

```json
{"profile":"academic","styles":{"body":{"font_east_asia":"宋体","size_pt":12}},"blocks":[{"type":"paragraph","text":"正文","format":{"line_spacing_multiple":1.5},"align":"justify"}]}
```

## 页面参数

首节 `page` 与 `section_break` 支持 `width_mm`、`height_mm`、`margin_mm`、`margin_top_mm`、`margin_bottom_mm`、`margin_left_mm`、`margin_right_mm`。保留原有 `margins_cm`，不要混淆单位；同一侧同时用 cm 和 mm 时须一致，否则报错。单边设置优先于统一边距，显式用户设置优先于 Profile。尺寸和边距必须保留正的页面可用空间；`orientation` 调整宽高方向。

```json
{"page":{"width_mm":210,"height_mm":297,"margin_mm":20,"margin_top_mm":25.4},"blocks":[{"type":"paragraph","text":"自定义页面"}]}
```

角色格式写入原生 `styles.xml`，段落引用 BodyText、Title、Subtitle、Heading1–9、TableBody、TableHeader；这些角色基于 Normal。主题、Profile 与 `styles` 写入角色，块和 run 显式覆盖才使用直接格式，因此修改角色样式可传播到没有对应局部覆盖的内容。不提供任意自定义继承图；列表、图片说明、页眉页脚等仍沿用各自格式。实际字体替换、目录更新、断页和视觉排版须在阅读器验证。已有文档改字体时仍使用 `edit` 定点修改，不把读取内容重新 `create` 为替代品。
