# 原生 DOCX 排版增强（标准库限定）

路径占位符说明：`<skill-root>` 是从本次宿主已实际读取的 `SKILL.md` 文件真实位置解析得到的 DOCX Skill 根目录。执行前须替换成真实绝对路径；不能从工作目录、历史对话路径或本页所在目录猜测。

本参考仅用于**新建 DOCX** 的排版规格。已有文档的读取、受控编辑、交付与清理遵守各自现有入口与限制；没有添加新依赖或修改工作目录规则。只使用 Python 标准库的 `zipfile`、`json`、`xml.etree.ElementTree` 等组件；不加载 Word/WPS/LibreOffice，不安装字体，也不宣称可精确计算页数或渲染检查视觉效果。

## 内置排版 Profile

可以用可选顶层字段 `profile` 指定以下**固定名称**，不接受绝对/相对路径或用户提供的外部 JSON：

| profile | 行为与适用范围 |
|---|---|
| `official-basic` | 保留旧 official 主题的字体、页边距和行距，在正文添加孤行寡行控制，在标题添加同页/段内约束；**不代表**符合 GB/T 9704 的全部规范。 |
| `business-report` | 主题 business，较紧凑页面边距、适度增加段前段后、标题同页约束及三线表默认预设。 |
| `academic` | 主题 academic、正文1.5倍行距、首行缩进与孤行寡行控制，标题保持紧邻下文。 |
| `presentation-material` | 文字汇报材料用商务主题、较大正文/章节间距，表格使用斑马纹预设；不生成 PPT。 |

兼容规则：**不写 `profile` 时，旧输入及旧 `theme` 行为不变**；写入时，先加载该 Profile 的 `theme/page/styles/table_format` 默认项，再叠加输入同名字段。显式输入优先于 Profile；`page`、`table_format` 按字段合并，`styles.<name>` 按格式字段合并。`line_spacing_pt` 与 `line_spacing_multiple` 互斥，首行字符缩进与首行磅值/悬挂缩进互斥；用户提供其中一种时将覆盖/移除相应的 Profile 默认冲突属性。其他未知字段仍会在普通生成校验阶段拒绝。

例如：

```json
{
  "profile": "business-report",
  "styles": {"body": {"size_pt": 12}},
  "blocks": [
    {"type": "heading", "level": 1, "text": "年度经营分析"},
    {"type": "paragraph", "text": "经营总体保持稳健。"}
  ]
}
```

不允许 Profile 自动填充正文、调用远端模板、推断合规认证或改变原文语义；只操作固定的四类排版默认值。

## 段落属性新增字段

所有字段均适用于可指定 `format` 的段落、标题，或 `styles` 对应样式，表格正文/表头文字格式同样可用。

| 新字段 | 类型和范围 | OOXML 结果 | 含义 |
|---|---|---|---|
| `keep_with_next` | bool | `w:keepNext` | 与下一段尽量保持同页；显式 false 可关闭标题默认设置。 |
| `keep_together` | bool | `w:keepLines` | 段内各行尽量在同一页。 |
| `page_break_before` | bool | `w:pageBreakBefore` | 段落之前分页。 |
| `widow_control` | bool | `w:widowControl` | 孤行寡行控制。 |
| `left_indent_pt` / `right_indent_pt` | 数值 0–400 | `w:ind@w:left/right` | 左右缩进，单位 pt。 |
| `first_line_indent_pt` | 数值 0–400 | `w:ind@w:firstLine` | 首行缩进，单位 pt；与 `first_line_chars`、`hanging_indent_pt` 互斥。 |
| `hanging_indent_pt` | 数值 0–400 | `w:ind@w:hanging` | 悬挂缩进，单位 pt；与两种首行缩进互斥。 |
| `tab_stops` | 最多32项，位置递增且不重复 | `w:tabs/w:tab` | 左/右/居中/小数点制表位。 |

`tab_stops` 每项为 `{"position_pt": 144, "align": "right"}`；位置允许 0–1000pt，`align` 为 `left/right/center/decimal`。这定义的是制表位位置，**不会自行插入正文制表符**。需要显式 tab 内容时，在 paragraph 的 `runs` 中插入 `{"tab": true}`；`text` 与 `tab` 不能同时出现，`{"tab": false}` 不允许。其他 run 字符格式字段不变。

```json
{
  "blocks": [
    {
      "type": "paragraph",
      "runs": [{"text":"项目"},{"tab":true},{"text":"金额"}],
      "format": {
        "keep_with_next": true,
        "keep_together": true,
        "widow_control": true,
        "left_indent_pt": 12,
        "tab_stops": [{"position_pt": 144, "align": "right"}]
      }
    }
  ]
}
```

其他 `text` 字段仍只允许合法的单行文本，不接受直接输入制表符、换行符或控制字符；多段请使用多个 paragraph。显式 `tab` 读回时是 `\t`，不应再把段落属性下的 `w:tabs/w:tab` 错当作正文字符。

## 静态排版检查

生成 `--verify` 除原有 CRC、结构、内容和布局意图检查外，新增针对新生成文档的 `w:pPr` 节点顺序/重复属性等**静态错误**检测。检查已有 DOCX 可独立使用只读入口：

```python
execute(command="python -I -S -B \"<skill-root>/scripts/validate_layout.py\" ./报告.docx")
```

输出 JSON 包括 `structure_valid`、`issues`、`layout_validation="static_only"`、`visual_validation="not_run"`。非零退出代表结构或静态错误；`warning` 提示表格/图片可能越过版心、标题可能孤立，但不是渲染确认。需要保存 JSON 时追加 `-o ./布局报告.json`，该文件需遵循当前任务产物登记、归档和清理规则；不要为普通生成任务再跑相同的检查。

当前检查**不覆盖**完整 OOXML XSD、Word/WPS 的实际分页、字体可用性、目录页码更新、跨阅读器视觉一致性。多节文件现按各节可用宽度分别估算表格与图片溢出风险。排版检查错误不得通过关闭 verify 绕过。

## 后续阶段与边界

当前已落地 P0：独立 Profile、按规范顺序集中写入段落属性、分页/制表位/缩进、静态排版检查。P1 已增加 `heading_numbering` 的原生 1–4 级中文/数字自动编号、跨行跨列表格、标题书签、待更新的 TOC 目录字段，并扩展静态网格验证。具体字段与已知限制见 `<skill-root>/references/p1-layout.md`。P2 已增加多节页面、首页/奇偶页页眉页脚、分节页码、REF/PAGEREF 交叉引用及上方表格标题，详见 `<skill-root>/references/p2-layout.md`。**仍未实现**：浮动图片、嵌套表格、真实视觉分页和自动字段结果计算。不能将 TOC/REF/PAGEREF 字段等同于已计算显示值。
