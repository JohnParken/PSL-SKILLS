# P1 原生排版扩展：标题编号、复杂表格、目录和书签

路径占位符说明：`<skill-root>` 是本 DOCX Skill 根目录的文档占位符，不是 Shell 变量。取得宿主本次实际读取主入口 `SKILL.md` 的真实 `file_path`；若为相对路径，先按本次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名，所得目录才是根目录，不能使用参考文档所在目录。读取资源或执行命令前，将 `<skill-root>` 整体替换为该真实目录，保留资源后缀，将完整路径作为一个参数正确引用；嵌入 execute 字符串时按其语法转义。无法确认主入口真实路径时停止依赖操作并报告，禁止猜测路径。示例的工作目录为宿主已确认的任务目录 `./`，任务输入、输出和临时文件均在该目录及其子目录内，不写入 Skill 安装目录。

本页记录 **P1 阶段基线**；多节、交叉引用等能力已在后续 P2 实现，详情请查 `<skill-root>/references/p2-layout.md`，本页表格中的“未实现”专指 P1 阶段。

本页针对新建 DOCX；所有实现只用 Python 标准库写入 OOXML，**无须 python-docx、WPS 或 LibreOffice**。必须使用 `"<skill-root>/scripts/run_docx_task.py" create` 标准入口，沿用原有安全边界、归档和清理契约。`create_docx.py` 的字典/JSON 输入是底层兼容格式，不可把读取结构 JSON 不经转换直接作为生成输入。

## 功能矩阵

| 能力 | P1 状态 | 语义和限制 |
|---|---|---|
| 1–4 级中文标题自动编号 | 已实现 | `heading_numbering: "chinese"`；依赖 Word/WPS 编号布局引擎决定显示文字。标题 `text` 不含编号前缀 |
| 1–4 级数字标题自动编号 | 已实现 | `heading_numbering: "decimal"`，格式 `1`、`1.1`、`1.1.1`、`1.1.1.1`；初始值来自 OOXML 级别定义 |
| 普通列表独立编号 | 兼容保留 | 与标题编号使用不同 `numId`，不串号；`list_id` 原契约不变 |
| 表格跨列及跨行 | 已实现 | `col_span`、`row_span` 仅供单元格对象使用，所有被覆盖的输入坐标必须写 JSON `null` |
| 多行重复表头 | 已实现 | `header_rows` 继续有效；跨页重复是属性意图，不保证打印效果；垂直合并不能越过表头/数据行边界 |
| 三线表及斑马纹 | 已实现 | 复杂表头情况下保留分隔线、表格预设及行底色，合并表格的自定义共享边冲突未做完整邻接推导 |
| 显式标题书签 | 已实现 | `heading.bookmark` 为唯一的 ASCII 标识，大小写不敏感；将 `w:bookmarkStart/End` 包围标题文字 |
| 目录字段 | 已实现（字段生成） | `type: "toc"` 写入 TOC 字段和 `updateFields` 提示；**不能自动计算目录条目和页码**，须在 Word/WPS 更新域 |
| 多节/不同页眉页脚 | 未实现 | 此版本仍为单节；横向附录与不同首页/奇偶页页眉页脚留待下一阶段 |
| 浮动图片/嵌套表格/交叉引用 | 未实现 | 不要在生成规格中使用未声明字段；书签并不等于 REF/PAGEREF 字段 |

## 示例：中文编号、目录与复杂统计表

```json
{
  "profile": "business-report",
  "heading_numbering": "chinese",
  "page_numbers": true,
  "blocks": [
    {"type": "toc", "title": "目录", "depth": 3},
    {"type": "heading", "level": 1, "text": "经营概况", "bookmark": "business_overview"},
    {"type": "heading", "level": 2, "text": "规模变化"},
    {"type": "paragraph", "text": "各机构数据如下。"},
    {
      "type": "table",
      "header_rows": 2,
      "format": {"preset": "three_line", "repeat_header": true},
      "widths_mm": [60, 40, 40],
      "rows": [
        [{"text": "机构", "row_span": 2}, {"text": "存款余额", "col_span": 2}, null],
        [null, "本期", "上期"],
        ["机构 A", "125", "110"],
        ["机构 B", "96", "90"]
      ]
    }
  ]
}
```

`heading_numbering`：`none`（默认，不加编号）、`chinese`、`decimal`。启用时标题必须只包含标题文本，不能自己拼接“一、”等编号；`chinese` 使用第 1 层“一、”、第 2 层“（一）”、第 3 层“1.”、第 4 层“（1）”的原生多级编号定义。层级计数和复位依赖阅读器；如果实际结构跳级，输出数字可能与读者预期不同，应尽量保持从一级向下的正常顺序。与普通有序列表独立编号。

## 合并单元格规则

- `rows` 仍必须是**规则矩形逻辑网格**。合并以左上角真实单元格声明，`col_span`、`row_span` 默认 1，范围为 1–256，不能越过网格。
- 被合并覆盖的每一个逻辑单元格必须是 `null`，不能填空字符串、字典、图片或其他内容；非覆盖区域不可随意放 `null`。重叠、无效跨度和越界均拒绝生成。
- 垂直合并使用 `w:vMerge restart/continue`；续行输出必要的实际 DOCX 单元格及空段落。续行不能重复输入文字或图片。跨列合并使用 `w:gridSpan`；宽度为覆盖列网格宽度之和。
- 同一表格的单元格边框在非合并情况下沿用共享边冲突校验；**合并表格中的自定义相邻边目前不执行完整共享边冲突归一化**，复杂边框请使用表级预设为主，并在 Word/WPS 打开核对。
- `column_sizing: content` 是静态内容长度启发式，不考虑跨列文字的精准度量；复杂宽表推荐显式 `widths_mm`，否则可能出现不理想的视觉比例。
- `header_rows` 所对应的重复表头不能与数据行之间存在跨界的垂直合并。超高合并单元格可能导致跨页布局异常，此类风险无法由标准库准确判定。

## 目录字段和书签

- `toc` 一块生成**两个段落**：独立标题和一个完整 `TOC \\o "1-N" \\u` 域，`depth` 是 1–4 的整数，默认 3。
- 首次打开文档时，部分 Word/WPS 版本会提示更新域；**必须确认目录条目和页码完成更新**。标准库不计算页数，不内置 Word 字体渲染引擎。目录字段生成成功不等于目录结果已缓存。
- `bookmark` 仅支持 1–40 个字符，第一位必须为英文字母，其余为英文字母/数字/下划线。同一文档不允许大小写不敏感的重复名称；书签可作为后续交叉引用能力的基础，但本版不支持自动插入 REF/PAGEREF 字段。

## 验收和质量边界

- `--verify` 校验标题 `numPr` 与 `numbering.xml` 对应关系，检查表格真实物理单元格与合并跨度，确认原始文字不丢失、目录字段、设置部件与关系、书签标识。
- `validate_layout.py` 新增表格网格覆盖、非法 `gridSpan` 和非法 `vMerge continue` 的静态风险检查；对其他编辑产生的带 `gridBefore`/`gridAfter` 的复杂表格采用提示而不是假定错误。
- 不执行 Word/WPS/LibreOffice 渲染、分页或字体可用性判断。静态通过不意味着跨阅读器视觉完全相同。每次交付高要求排版时，需在 Word/WPS 中更新域并人工核对。

## 维护测试

`python -I -S -B -m unittest discover -s "<skill-root>/scripts/test" -p 'test_*.py' -q`，重点新增 `<skill-root>/scripts/test/test_docx_p1_layout.py`；运行环境仍需满足原 Skill 对 Python/Expat 等要求。仅在 Skill 维护/发布场景运行完整回归，不要为普通用户的每一份文档反复运行测试套件。
