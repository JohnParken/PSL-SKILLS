# 线程批注

路径占位符说明：从宿主实际读取本主入口 `SKILL.md` 的真实 `file_path` 去掉文件名确定 `<skill-root>`。相对读取路径先按宿主确认的基准目录解析。调用前替换 `<skill-root>` 并引用完整路径。无法定位或确认执行器映射时停止并报告。

在已确认的任务目录运行 `comments`，输入为 DOCX 或 DOTX。写入只发布同扩展名的副本。通过 stdin 提交单个 UTF-8 JSON 对象；按 `<skill-root>/references/writing.md` 核对输出名、归档及恢复状态。

```sh
python -I -B "<skill-root>/scripts/tl_docx.py" comments "报告.docx" <<'JSON'
{"action":"list"}
JSON
```

查询输出 `mode`、`mutable`、`diagnostics`、批注文字、作者、父 ID 和正文段落位置。遇到无法验证的复杂内容或线程关系时检查诊断；修改要求结构可验证。普通全文读取返回存储文字，核对线程使用本命令。

| `action` | 所需字段与语义 |
| --- | --- |
| `add` | `paragraph_index`（正文直属段落，0 起始）、`expected_text`、唯一的 `anchor_text`、`text`、`author`，可选 `initials`。锚点必须覆盖连续完整普通 run。空白文档批注状态下建立现代四部件。 |
| `reply` | `id`、`expected_text`、`text`、`author`，可选 `initials`。限直接回复现代根批注。 |
| `update` | `id`、`expected_text`、`text`。核对批注内容，保留标识和非目标部件。 |
| `delete` | `id`、`expected_text`。删除单项及其对应锚点，父批注有回复时先逐条处理回复。 |

```sh
python -I -B "<skill-root>/scripts/tl_docx.py" comments "报告.docx" "报告批注.docx" <<'JSON'
{"action":"add","paragraph_index":0,"expected_text":"需核实费用上限","anchor_text":"需核实费用上限","text":"请补充依据。","author":"复核人"}
JSON
```

范围为正文中的简单单段批注和最多一级回复。经典简单批注可查询、更新和删除。已有经典部件保持原格式；复杂内容、活动范围、未知线程元数据或签名文档拒绝写入。不涉及人员/提及、云端同步或真实 Word/WPS 显示。写后重新执行 `list` 核对目标及父子关系，并确认主业务回执。
