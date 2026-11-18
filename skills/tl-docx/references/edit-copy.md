# 修改副本

路径占位符说明：从宿主实际读取本主入口 `SKILL.md` 的真实 `file_path` 去掉文件名确定 `<skill-root>`。相对读取路径先按宿主确认的基准目录解析。调用前替换 `<skill-root>` 并引用完整路径。无法定位或确认执行器映射时停止并报告。

```sh
"<skill-root>/scripts/.venv/bin/python" -I -B "<skill-root>/scripts/tl_docx.py" edit "原报告.docx" "修订版.docx" <<'JSON'
{"operations":[{"type":"format_run","paragraph_index":0,"expected_text":"原文标题","run_index":0,"expected_run_text":"原文标题","format":{"font_east_asia":"黑体","size_pt":18}}]}
JSON
```

`operations` 按顺序应用，索引从零开始，按每步当时的位置核对。未另述时段落和表格索引取正文直属元素，run 索引取段落直属 run。错误会使整个批次停止发布。

| 操作 | 定位和允许的修改 |
| --- | --- |
| `format_run` | `paragraph_index`、`expected_text`、`run_index`、`expected_run_text`。`format` 可选 `font_east_asia`、`font_ascii`、`size_pt`、`bold`、`italic`、`underline`、`color`，仅改所列属性。 |
| `replace_run_text` | 同上定位，传新 `text`。目标 run 须为普通文本。 |
| `tracked_replace_run` | 与 `format_run` 相同定位，另传新 `text`、`author`、UTC `date`（`YYYY-MM-DDTHH:MM:SSZ`）。生成删除/插入修订并保留字体和段落格式；限正文纯文本单 run，目标段已有修订、字段、范围锚点时拒绝。不接受/拒绝既有修订，不创建移动或格式修订。 |
| `merge_runs` | `paragraph_index`、`expected_text`、`start_run` 及至少两个 `expected_runs` 文本。只合并连续、显式格式及属性相同的纯文本 run，保留原文字节点与空白；有字段、书签、批注范围、修订或分节的段落拒绝，不能跨不同格式合并。 |
| `insert_paragraph` | `paragraph_index`、`expected_text`、`text`，可选 `style` 和 `position`（`before`、`after`）。 |
| `replace_cell_text` | `table_index`、`row_index`、物理 `cell_index`、`expected_text`、`text`。目标为未合并的单段单 run 单元格。 |
| `format_cell_run`、`replace_cell_run_text` | `table_index`、`row_index`、物理 `cell_index`，再用该单元格内的 `paragraph_index`、`run_index`、段落 `expected_text` 和 `expected_run_text` 定位。前者传与 `format_run` 相同的 `format`，后者传新 `text`；支持未合并单元格的多个普通段落和多个普通 run；合并原点另须传下述 `expected_merge`，只改所选 run。 |
| `format_story_run`、`replace_story_run_text` | 实际 `part`，以及该部件直属 `paragraph_index`、`run_index`、段落 `expected_text` 和 `expected_run_text`。前者传 `format`，后者传新 `text`。仅支持下述单节、明确引用的现存纯文本默认页眉/页脚，必须作为独立批次提交。 |
| `insert_row`、`delete_row` | `table_index`、`row_index`、`expected_rows`、目标行文本数组 `expected_text`。插入另需 `position` 和新行 `cells`。限未合并的规则表格，保留重复表头及至少一个数据行。 |
| `replace_image` | 包内 `part`、原图二进制的 `expected_sha256` 和新图相对 `path`。保留媒体类型和现有布局，`allow_shared=true` 才能替换被多处引用的图片。 |
| `add_comment` | 段落和 run 同 `format_run` 定位，加 `text`、`author`，可选 `initials`。 |
| `update_comment`、`delete_comment` | `comment_id`、`expected_text`，更新再传 `text`。限简单经典批注，涉及更新或删除时单独提交新增批注。 |

`format_run` 每步使用独立差异守卫：先核对源正文 XML 与组件加载结果，再以本步编辑前快照核对整个正文树；序列化后的正文还须等于已验收快照。只允许指定 run 的对应属性变化，以及必要的新建 `w:rPr` / 属性节点。解析后既存节点的顺序、文本、空白、命名空间绑定、未请求属性、其他 run、段落、表格、字段、锚点和分节均须保持；重复的目标属性或其他超范围变化使整个批次在发布前失败。混合批次逐步保护 `format_run`，其他操作仍按各自边界执行，不宣称它们获得同等差异守卫。

字体只改指定通道，移除该通道 theme 引用，保留其他字体通道。显式 RGB 同时清除 `themeColor`、`themeTint`、`themeShade`，保留其他属性；下划线只改 `val`，保留颜色等属性。字号保持既有量化：普通字号截取到半磅，已存在的复杂文字字号按 Python `round(size_pt * 2)` 取半磅整数，不新增原本没有的 `szCs`；避免不必要差异时使用整数或半磅值。守卫验证 XML 结构和属性，不承诺修改部件的 XML 字节、格式化缩进、声明或属性排列不变，也不代替完整 OOXML schema 或阅读器视觉验收。

表格 run 编辑只定位正文直属表格的物理行/单元格，不把合并后的视觉列当物理索引。目标单元格须仅含普通段落和文本 run，允许空段、空 run、制表符与普通换行；默认拒绝合并；始终拒绝嵌套表格、内容控件、字段、超链接、范围锚点和修订，以及目标行/表格的修订属性。不重建整格，不增删段落，不扩大旧 `replace_cell_text` 和行增删的单段单 run 限制。`format_cell_run` 复用独立属性差异守卫；`replace_cell_run_text` 独立核对新文字对应的文本/制表/换行节点序列，保留 run 属性、原 `rPr` 与其余整棵正文树。两者均核对源树加载与最终序列化读回；文字替换可以重建目标 run 的文字载荷及必要 `xml:space`，不承诺原文字节点拆分不变。不将表格定位用于其他部件。


合并原点编辑必须显式加 `expected_merge`，例如 `{"grid_column":1,"colspan":2,"rowspan":2}`：`grid_column` 是零基视觉网格起列，`colspan`、`rowspan` 是预期完整矩形跨度；三项必须为整数，不接受布尔值，且实际至少一项跨度大于 1。原 `row_index`、`cell_index` 仍指物理 XML 行/格，不是视觉列；不自动把续格映射回原点。只支持有明确 `tblGrid`、每行完整覆盖同一网格的标准 `gridSpan` 横向原点、`vMerge restart` 竖向原点及两者组合；纵向链必须逐行同列同宽，所有续格必须为简单空段，不能有隐藏文字、字段或编号。缺少或错误的几何声明、续格目标、旧式 `hMerge`、缺格/行偏移、网格歧义、宽度不一致、孤立续格或相关修订均拒绝。先完整证明物理拓扑，再由独立差异守卫复核原点和跨度；合并属性、续格及其他非目标节点保持不变。不传 `expected_merge` 时保留此前合并格拒绝行为；旧整格替换和行增删仍不支持合并格。此能力不增删、重组或修复合并，不保证非规范来源文档或阅读器视觉结果。

页眉页脚 run 编辑仅限单节文档：正文末尾必须有唯一、无修订的 `sectPr`，目标必须是已有 header/footer 类型部件，并由本节唯一 `default` 引用明确指向，关系入边和实际引用都唯一。按真实 `part` 定位，不猜 `header1.xml`，不自动创建、解除链接或改变继承；拒绝多节、共享、继承、孤立部件、外部关系、重复/歧义引用以及 `first`/`even` 目标。所选整个部件须仅含普通文本段落/run；含 PAGE 等字段、图片、表格、超链接、范围锚点或修订均拒绝，不改字段指令或页码。支持多段、多 run 与逐步预期核对，可在同批次编辑多个明确页眉/页脚或同部件多次，但不能与正文、表格、图片、批注等操作混批。独立差异守卫保护目标之外的整棵部件树，并核对序列化读回；正文、节引用、关系、样式及所有其他部件的解压字节保持。以上是有限纯文本编辑，不是任意页眉页脚编辑或视觉版式保证。
受控编辑支持 DOCX，超出上述边界的复杂单元格、数字签名文档和未支持的对象拒绝修改。线程批注通过 `<skill-root>/references/comments.md` 中的独立入口处理。写回时校验目标部件并逐字节保护其他部件的解压内容。实际页数、行折叠和字体显示由阅读器决定。


从默认任务目录执行。输出参数只接收任务根目录下的新裸文件名：`create` 和 `edit` 输出 `.docx`；`replace`、批注写入和 `repack` 保留输入的 `.docx` / `.dotx` 扩展名，不执行类型转换。stdin 是单个 UTF-8 JSON，对象键不得重复，上限 16 MiB。用能够保证原样传输的 JSON 序列化方式提交，不把文档文字拼成命令。示例使用带单引号的 here-document。
优先采用用户指定的名称。省略时，创建按元数据标题或首个标题块提取文件名，编辑沿用输入主题并追加 `_修订版`，批注写入追加 `_批注版`。标题为空时使用通用名称。有明确主题但文档未包含标题时，按任务主题直接传裸文件名。执行器仅定点检查主路径及本轮归档同名，依次尝试 `_第2版`、`_第3版`，已含版本后缀则递增。输入别名拒绝。用户明确要求保持精确文件名时传 `--exact-name`，任何主目录同名均停止发布，归档冲突按归档失败报告。交付只使用回执的实际 `output`。

创建检查 `status=verified` 与 `result.content_verification.status=verified`，编辑和批注写入检查 `status=edited`。创建的输入内容门禁独立核对文字顺序、表格合并、图片、字段和分节页眉页脚，不代表视觉排版、编号标签或字段重算通过。核对 `publication.published`、`cleanup.cleanup_complete`、`cleanup.manifest_removed` 和实际主文件引用。归档独立核对 `archive_status`，只有 `success` 且非空清单全部 copied/already_archived 才显示“🎉 文件归档成功，请到云空间查看。 ☁️✨”。保留 `archive_message`，按实际结果展示 `archive_user_notice`，不向用户显示内部归档位置或诊断。缺少当前宿主归档信息时保留主文件；需重试时仅处理归档。只用宿主确认的主文件链接交付，禁止以归档副本替代。
