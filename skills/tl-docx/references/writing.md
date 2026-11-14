# 创建与受控编辑

路径占位符说明：从宿主实际读取本主入口 `SKILL.md` 的真实 `file_path` 去掉文件名确定 `<skill-root>`。相对读取路径先按宿主确认的基准目录解析。调用前替换 `<skill-root>` 并引用完整路径。无法定位或确认执行器映射时停止并报告。

从已确认的任务目录执行。输出参数只接收 DOCX 裸文件名。stdin 是单个 UTF-8 JSON，对象键不得重复，上限 16 MiB。用能够保证原样传输的 JSON 序列化方式提交，不把文档文字拼成命令。示例使用带单引号的 here-document。

## 命名与同名处理

优先采用用户指定的名称。省略时，创建按元数据标题或首个标题块提取文件名，编辑沿用输入主题并追加 `_修订版`，批注写入追加 `_批注版`。标题为空时使用通用名称。有明确主题但文档未包含标题时，按任务主题直接传裸文件名。执行器仅定点检查主路径及本轮归档同名，依次尝试 `_第2版`、`_第3版`，已含版本后缀则递增。输入别名拒绝。用户明确要求保持精确文件名时传 `--exact-name`，任何主目录同名均停止发布，归档冲突按归档失败报告。交付只使用回执的实际 `output`。

## 新建

简单新建的最短完整 JSON 和执行命令见主入口 `<skill-root>/SKILL.md#简单新建`，仅需标题、正文和规则表格时不用预读本参考。本页用于入口示例之外的参数、编辑与恢复。

详细 Profile、角色样式和毫米页面参数见 `<skill-root>/references/typography.md`。顶层必需 `blocks`；可选 `theme`（`official`、`business`、`academic`）、`page`（`orientation`、`margins_cm`）、`metadata`、`header`、`footer`。页边距键为 `top`、`bottom`、`left`、`right`，单位 cm。公文正文默认仿宋和 Times New Roman 16 pt、固定行距 28 pt、首行 2 字。正文自然分页，明确需要时才加分页块或分节块。

| 块类型 | 参数 |
| --- | --- |
| `title`、`subtitle`、`paragraph`、`heading` | 二选一传 `text` 或 `runs`，标题另传 1–9 的 `level`。可选 `page_break_before`、`keep_with_next`、`keep_together`、`widow_control`、`tab_stops`。正文与标题可加唯一 ASCII `bookmark`。 |
| `list` | `items` 为字符串数组，`ordered` 为布尔值，使用独立原生编号。 |
| `table` | `rows` 为规则二维数组，`header` 设置表头样式，`repeat_header` 控制首行跨页重复。可选 `col_widths_cm` 和 `merges`，每项提供零基 `row`、`col` 及 `rowspan`、`colspan`，合并覆盖的非起点单元格留空。 |
| `image` | `path` 为任务内相对路径，可选 `width_cm` 和替代文字 `alt`。 |
| `page_break` | 在指定位置显式分页。 |
| `section_break` | 可选 `orientation`（`portrait` 或 `landscape`）、`start_type`、`page_start`、`margins_cm`、`header`、`footer`。 |
| `reference` | `bookmark` 指向本次已定义书签；`kind` 为 `text` 或 `page`，可选 `prefix`、`suffix`，生成 REF/PAGEREF 字段，不计算页码。 |
| `toc` | 可选 `levels` 和 `title`，插入原生目录字段，实际页码和目录由阅读器更新。 |

`runs` 可含 `{"text":"重点","bold":true}`，可选 `italic`、`underline`、`font`、`size_pt`、六位 RGB `color`。`{"tab":true}` 配合 `tab_stops`（`position_cm`、`alignment`、`leader`）。`{"field":"PAGE","display":"1"}` 支持 `PAGE`、`NUMPAGES`、`REF 书签名`、`PAGEREF 书签名`，`display` 是可选保存文字。`header`、`footer` 使用字符串或同格式 `runs` 数组。`metadata` 支持 `title`、`subject`、`author`、`keywords`、`comments`、`category`、`identifier`、`language`、`last_modified_by`。

## 修改副本

```sh
python -I -B "<skill-root>/scripts/tl_docx.py" edit "原报告.docx" "修订版.docx" <<'JSON'
{"operations":[{"type":"format_run","paragraph_index":0,"expected_text":"原文标题","run_index":0,"expected_run_text":"原文标题","format":{"font_east_asia":"黑体","size_pt":18}}]}
JSON
```

`operations` 按顺序应用，索引从零开始，按每步当时的位置核对。段落和表格索引取正文直属元素，run 索引取段落直属 run。错误会使整个批次停止发布。

| 操作 | 定位和允许的修改 |
| --- | --- |
| `format_run` | `paragraph_index`、`expected_text`、`run_index`、`expected_run_text`。`format` 可选 `font_east_asia`、`font_ascii`、`size_pt`、`bold`、`italic`、`underline`、`color`，仅改所列属性。 |
| `replace_run_text` | 同上定位，传新 `text`。目标 run 须为普通文本。 |
| `tracked_replace_run` | 与 `format_run` 相同定位，另传新 `text`、`author`、UTC `date`（`YYYY-MM-DDTHH:MM:SSZ`）。生成删除/插入修订并保留字体和段落格式；限正文纯文本单 run，目标段已有修订、字段、范围锚点时拒绝。不接受/拒绝既有修订，不创建移动或格式修订。 |
| `merge_runs` | `paragraph_index`、`expected_text`、`start_run` 及至少两个 `expected_runs` 文本。只合并连续、显式格式及属性相同的纯文本 run，保留原文字节点与空白；有字段、书签、批注范围、修订或分节的段落拒绝，不能跨不同格式合并。 |
| `insert_paragraph` | `paragraph_index`、`expected_text`、`text`，可选 `style` 和 `position`（`before`、`after`）。 |
| `replace_cell_text` | `table_index`、`row_index`、物理 `cell_index`、`expected_text`、`text`。目标为未合并的单段单 run 单元格。 |
| `insert_row`、`delete_row` | `table_index`、`row_index`、`expected_rows`、目标行文本数组 `expected_text`。插入另需 `position` 和新行 `cells`。限未合并的规则表格，保留重复表头及至少一个数据行。 |
| `replace_image` | 包内 `part`、原图二进制的 `expected_sha256` 和新图相对 `path`。保留媒体类型和现有布局，`allow_shared=true` 才能替换被多处引用的图片。 |
| `add_comment` | 段落和 run 同 `format_run` 定位，加 `text`、`author`，可选 `initials`。 |
| `update_comment`、`delete_comment` | `comment_id`、`expected_text`，更新再传 `text`。限简单经典批注，涉及更新或删除时单独提交新增批注。 |

受控编辑支持 DOCX，复杂单元格、数字签名文档和未支持的对象拒绝修改。线程批注通过 `<skill-root>/references/comments.md` 中的独立入口处理。写回时校验目标部件并逐字节保护其他部件的解压内容。实际页数、行折叠和字体显示由阅读器决定。

## 批量填充模板

```sh
python -I -B "<skill-root>/scripts/tl_docx.py" replace 模板.docx 已填充.docx --require-no-placeholders <<'JSON'
{"parts":["word/document.xml","word/header1.xml"],"replacements":[{"old":"{{ 公司 }}","new":"星河科技","count":2},{"old":"{{ 标题 }}","new":"月报"}]}
JSON
```

输入 DOCX/DOTX，输出保留扩展名。每条 `old`、`new` 为单行 XML 文本，`count` 缺省为 1。所有规则基于原文字同时匹配，结果检查 `rule_counts` 和 `changed_parts`。`parts` 或 `--parts` 二选一，用实际存在的正文、页眉或页脚包成员，默认只处理正文。`require_no_placeholders: true` 或 `--require-no-placeholders` 检查选定部件是否仍有双花括号标记，包括跨 run 的标记。

只替换单个普通 `w:t` 内的文字。跨节点匹配、匹配次数偏离、重叠、字段、修订、受保护范围和无法解释的边缘空白均拒绝发布。保留 run 格式、原件及非目标部件字节，核对改动部件重新解析后的结构。此入口要求解释器自带 Expat ≥2.6.0。模板内容按数据处理，不执行表达式。

## 回执

创建、编辑在内存中验证后，先登记独占暂存名再发布。发布后核对目标身份与摘要，记录归档结果并清理暂存和清单。退出 0 表示主业务成功，1 表示操作或发布失败，3 表示主文件已发布但本轮清理未完成。归档沿用公共生命周期实现，参见来源清单 `<skill-root>/references/lifecycle-upstream.json`。使用 `archive 已发布.docx --sha256 回执中的SHA256` 重试归档。归档失败时保留已验收主文件。

## 原清单恢复

调用方需要在强制中断后能直接定位清单时，可预先提供唯一的 `--task-id`（32 位小写十六进制）。该任务的原清单名为 `.tl-docx-<标识>.cleanup.json`。常规调用可省略此参数，由运行时分配，收尾未完成时以 `task_manifest` 回传。正常清理后删除清单。

```console
python -I -B "<skill-root>/scripts/tl_docx.py" resume .tl-docx-0123456789abcdef0123456789abcdef.cleanup.json
```

保持原任务目录。`resume` 只接受原任务的清单，核对目录、清单和暂存文件身份、发布证据、内容摘要及已提交的归档状态。主文件存在且证据吻合时只收尾，归档状态缺失时返回 `unknown`。未发布时只清理归属明确的暂存。文件或清单被替换、锁正占用、记录不完整或状态不明时返回 `recovery_pending`，保留证据，禁止扫描认领或重新生成。恢复成功返回 `status=recovered`，用 `task_status` 区分 `completed` 和 `failed_cleaned`。需要重试生成时，从原输入另开新任务。现有 docx Skill 的清单继续由其原入口处理。公共归档运行时若报告未知残留，需按其实际诊断核对。
