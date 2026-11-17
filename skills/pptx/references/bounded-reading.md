# PPTX 有界读取与续读

路径占位符说明：`<skill-root>` 是占位符；从本次主入口 `SKILL.md` 的真实 `file_path` 定位，相对路径先按宿主确认的读取基准解析，再去掉文件名。使用前整体替换为真实目录，将完整路径作为一个参数正确引用；无法定位时停止。该根目录不是当前参考文件目录。
以下命令在默认任务工作目录 `./`运行，输入为该目录下的相对路径；先完成主入口自检。

## 选择返回内容

```python
execute(command="\"<skill-root>/scripts/.venv/bin/python\" -I -S -B \"<skill-root>/scripts/pptx.py\" inspect report.pptx --summary")
execute(command="\"<skill-root>/scripts/.venv/bin/python\" -I -S -B \"<skill-root>/scripts/pptx.py\" inspect report.pptx --slides 1-3,5 --notes omit --max-output-bytes 12000")
```

- 默认保留原 text/notes/objects 字段，并返回支持的结构化表格、图表缓存及内容诊断；具体字段见 `<skill-root>/references/reading-content.md`。检测到数学标记的页另附加下述 text_assessment。新参数只控制返回内容，不改变安全检查或支持格式。
- `--slides` 为 1 起算、包含端点、升序无重叠的页号或范围列表；越界、倒序、重复与空选择拒绝。
- `--notes include|omit` 默认 include。备注按对象和段落顺序包含受支持的文本形状及原生表格文字，空段落保留；omit 删除逐页 notes、notes_tables、notes_charts 字段，不把遗漏备注写为空数组。
- `--summary` 返回每页 number、objects、text_paragraphs、notes_paragraphs，以及存在时的 text_assessment/content_assessment，保留文档标题和包摘要，不返回正文与备注原文。summary 优先于 notes，notes 只影响游标参数绑定。
- `coverage.validation=all_package_parts` 表示全包安全检查；`coverage.selected_slides` 是请求页号，`coverage.content` 区分概况和内容，`coverage.notes` 表示备注返回方式。未返回不代表不存在。
- 全包验证仍检查未选页、所有关系、媒体、嵌入工作簿和 MCE 分支。筛选不减弱拒绝条件，也不是流式输入或降低解析内存的承诺。

## JSON 预算与分页

`--max-output-bytes` 的合法范围是 512 至 67108864，约束整个 stdout JSON 的 UTF-8 字节数，包含外壳、诊断、游标及最后换行，不是字符数或 token 数。未设置预算时只做选择，不保证输出大小。
未启用 `--within-slide` 时每次仅返回可完整容纳的整页。`pagination.start/end` 是所选页列表的零起算半开位置；`total` 是所选页数。`complete` 仅表示已到所选结果末尾，不代表覆盖全演示文稿或结构完全通过。

如 `next_cursor` 非 null，原样带入 `--cursor`，重复相同路径、summary、slides、notes、预算和 PNG 容错选项。每次续读重新读取并验证全包。
按位置拼接各次 slides 数组；保留包元信息与诊断并核对 sha256 一致。不能仅取最后一页当作完整内容。请求的等价页范围可规范化为相同选择。
游标绑定源字节、所选页、返回模式、预算和输入路径。源文件变化、参数不符、无效位置或被修改的游标会拒绝；应重新开始，不混接不同源。
游标不保存服务器状态、不写安装目录，完整性校验不是访问授权或抵御持有源文件者自行构造游标的安全凭据。

默认整页模式若单页、长备注、标题或诊断无法容纳，退出 2 并返回 `output_budget_too_small`，说明下一整页所需字节，不截断内容。移除 cursor 后提高预算重新开始，或缩小范围、omit 备注、使用 summary；单页内容过大也可按下节显式启用页内续读。最大预算仍为 64 MiB，所需预算超过上限时只能改变返回选择或不使用预算。不得为恢复关闭安全检查。
合法预算同样限制错误 JSON；过长错误消息明确标记 message_truncated，并在 stderr 保留诊断。小于 512 或超上限的预算本身无效，拒绝时错误 JSON 不承诺满足该无效预算。参数解析尚未完成时同样不能建立预算契约。

## 页内无损续读（显式启用）

```python
execute(command="\"<skill-root>/scripts/.venv/bin/python\" -I -S -B \"<skill-root>/scripts/pptx.py\" inspect report.pptx --within-slide --max-output-bytes 12000")
```

`--within-slide` 必须同时提供合法 `--max-output-bytes`。它不改变默认整页输出，不降低全包验证，不承诺流式解析或降低输入内存。顶层 `schema=psl.pptx.result.v1` 保留，另有 `fragment_schema=psl.pptx.fragments.v1`；此模式没有 `slides` 数组，而以 `fragments` 表示单页 JSON 的连续文本片段。

每次 `fragments` 恰有一项：

| 字段 | 含义 |
| --- | --- |
| `path` | `["slides", selected_index]`，第二项是所选页列表的零起算位置 |
| `source_slide` | 原演示文稿中 1 起算页号，与 path 的索引不同 |
| `encoding` / `offset_unit` | `json-text` / `unicode_codepoints` |
| `start` / `end` / `total` | 该页紧凑 JSON 字符串的 Unicode 码点偏移，半开区间及总长度；不是 UTF-8 字节、字形或 token |
| `text` | 使用 ensure_ascii=false 序列化后的 JSON 文本片段，可能切在 JSON 转义内部，不能独立 JSON 解析，也不是可直接转贴的正文 |

按同一 path 从 start=0 顺序拼接 text，逐项检查相邻 end/start、total、source_slide 和源 sha256 一致；end=total 后才对完整串执行一次 JSON 解码，恢复原页全部字段、顺序、空值和字符串。不得用残缺片段进行内容分析、猜补转义，或丢弃重复页。跨页依所选位置排列已恢复页，不把最后一段当全文。

`pagination` 为 `mode=within_slide`、`selected_index`、`total_slides`、`slide_complete`、`complete`、`next_cursor`。slide_complete 仅表示本页已到尾；complete 仅表示所选全部页已到尾，仍须核对先前片段确已接收。`coverage.representation=json_text_fragments` 明确返回形式，其余 coverage 继续标记全包验证、选页与备注范围。

包元数据、源哈希及完整包诊断每次重复返回；当前页的所有 `*_assessment` 另在 `slide_assessments` 完整重复，数学损失和内容缺口不必等到片段拼完才能发现，也不能在去重时丢弃。原页内 assessment 在重建页中仍保留。

下一次原样带回 next_cursor，重复相同路径、预算、选页、notes、summary、PNG选项和 `--within-slide`。此模式使用独立 v2 游标，不能与整页游标互用；它绑定源字节、路径、选项、预算、片段 schema 和位置，参数或源变化拒绝，移除 cursor 后重新开始。游标不提供访问授权。

元数据、完整诊断及至少一个推进码点无法放入预算时仍返回 `output_budget_too_small`、退出 2；提高预算重新开始，不丢弃诊断或静默截断。整页/页内分页未完成不等于结构 partial；原结构 passed/partial、PNG逐文件诊断和退出码保持不变。宿主自身截断仍须按主入口处理，不能当作合法片段。

## 结果状态

含数学标记的页返回 `text_assessment`：`status=math_structure_not_preserved`、`diagnostics=["math_text_flattened"]`，`math_paragraphs.text/notes` 分别计数含数学标记的正文/备注段落（空数学段落也计入，同段多个数学对象只计一次）。`scope=slide_text_and_notes_before_projection` 表示计数基于所选 MCE 语义分支与排除页眉页脚等占位符后的正文/备注；不是全包、公式对象或可见页面计数。`--notes omit`、summary、选页和分页均保留返回页的这项诊断，omit 不会将原备注计数清零；未返回页的诊断不能从当前页推断。诊断计入字节预算，不为缩小结果而丢弃。
OMML 文字仅拼接，分数 `12/15` 可能返回 `1215`，上下标也会丢失结构。消费方须报告公式读取限制，不将其作为准确公式或用于计算；缺少该字段不证明其他数学表达形式已准确读取。该诊断不改变 ok、结构状态和退出码，不能以退出 0 掩盖语义缺口。

分页不是读取错误，结构通过退出 0。显式允许有限 PNG 零填充时仍保留 `structural_validation=partial`、完整逐文件 diagnostics 和退出码 3；预算不足则拒绝本次输出，不丢弃诊断来凑预算。
`visual_validation=not_run` 始终保留。不将所选范围、概况、分页完成或结构检查当成完整格式支持、桌面显示验证或所有内容均已返回。
按主入口的结果消费规则检查实际可见输出、退出码及截断提示；stdout/stderr 可能合并并附有宿主说明，不假定两个独立字段。宿主自身截断不是合法分页，工具层 `status=success` 也不证明本页业务成功。转存引用仅用宿主提供的 `read_file` 按真实 schema 分段补读，核对完整范围后再解析本页 JSON；缺工具、未读全或退出状态未知时停止依赖该结果，不把预览或最后一段当作完整页面，不用 Shell/Python 读取宿主引用。
