# 受控替换、批注与修订的 OOXML 编辑规则

路径占位符说明：`<skill-root>` 是本 DOCX Skill 根目录的文档占位符，不是 Shell 变量。取得宿主本次实际读取主入口 `SKILL.md` 的真实 `file_path`；若为相对路径，先按本次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名，所得目录才是根目录，不能使用参考文档所在目录。读取资源或执行命令前，将 `<skill-root>` 整体替换为该真实目录，保留资源后缀，将完整路径作为一个参数正确引用；嵌入 execute 字符串时按其语法转义。无法确认主入口真实路径时停止依赖操作并报告，禁止猜测路径。示例的工作目录为宿主已确认的任务目录 `./`，任务输入和受控临时文件沿用该目录内相对路径规则；交付输出只传裸文件名，由脚本固定写入任务根目录 `./`，不写入 Skill 安装目录。

路径定位与任务目录边界统一遵守 `<skill-root>/SKILL.md#执行环境约束与首步自检`；占位符必须用宿主本次已确认的真实目录替换，禁止猜测路径。

文件位置统一遵循 文件定位与读取规则（`<skill-root>/SKILL.md#文件定位与读取规则`）：文档及操作文件参数相对任务目录；后续读取使用本次实际输出路径，不按输入目录推导。
写入前按 交付文件命名（`<skill-root>/references/generation-runtime.md#交付文件命名`） 确定实际名称；以下业务名称仅为示例，须按本次任务替换，用户指定名称优先。

交付归档：编辑 CLI 和 公开交付 API（`<skill-root>/references/delivery.md#公开交付-api`） 的写操作成功后由代码自动归档本次输出；只读操作不归档。低层 API 或读取保存等其他方式生成的最终交付文件，按 当轮统一归档（`<skill-root>/references/delivery.md`） 显式列出本轮文件归档；不把验收中间文件或历史产物加入清单。


## 目录

- 字体修改与候选文件保护（`<skill-root>/references/ooxml-editing.md#字体修改与候选文件保护`）
- 受控编辑与占位符填充（`<skill-root>/references/ooxml-editing.md#受控编辑与占位符填充`）
- 放置批注锚点（`<skill-root>/references/ooxml-editing.md#放置批注锚点`）
- 标记插入和删除（`<skill-root>/references/ooxml-editing.md#标记插入和删除`）
- 使用低层辅助工具（`<skill-root>/references/ooxml-editing.md#使用低层辅助工具`）

## 字体修改与候选文件保护

**本参考不是任意 XML 改写的保真授权。** 已有文档只改字体/字号，优先使用 `<skill-root>/scripts/run_docx_edit.py` 的 `structure` 模式和 `format_run`，详见 `<skill-root>/references/structure-editing.md#已有文档字体修改`。在原 run 上仅合并目标属性；禁止提取文字重建段落、整体替换 `w:pPr` 或 `w:rPr`，也不改 `styles.xml`/`numbering.xml` 来间接重设格式。`edit_docx.py` 只接受文字替换，不能传字体字段。

正式 `structure` 入口在写出前自动执行独立格式差异核验。若已有低层 XML 编辑候选，不把候选直接交付或归档；仅对可可靠核对的普通正文 run 使用以下受控发布模式。将用户明确要求的变更写成原文定位的 `operations.json`，例如：

```json
{"operations":[{"type":"format_run","paragraph_index":0,"expected_text":"现有正文","run_index":0,"expected_run_text":"现有正文","format":{"font_east_asia":"黑体"}}]}
```

在已确认的任务目录执行（候选、原件和规格均为任务内相对路径，最终输出仅裸文件名）：

```python
execute(command="python -I -S -B \"<skill-root>/scripts/run_docx_edit.py\" format-checked ./原文档.docx 字体修订版.docx --candidate ./字体候选.docx < ./operations.json")
```

该模式保护原件和候选，只在差异核验通过后将同一内存候选快照发布为新主文件，再自动归档并清理 runner 自建配置。核验失败不发布最终文件、不归档已有输出；按失败回执收尾，不用 `package.py pack` 或显式归档绕过检查。原件、用户提供候选/规格不作为 runner 可清理项；自行生成的候选/规格继续按创建前登记的本轮清单收尾，不扫描删除。

只读排查可单独运行以下命令，退出 0 仅证明该时刻指定文件和规格通过检查，不发布或归档；最终交付仍走上面的受控模式，不能检查后另读、替换候选再直接交付：

```python
execute(command="python -I -S -B \"<skill-root>/scripts/format_guard.py\" ./原文档.docx ./字体候选.docx ./operations.json")
```

保护范围与拒绝条件：

- 规格只接受非空 `format_run` 操作，索引、原文锚点及六个格式字段沿用 `<skill-root>/references/structure-editing.md#修改普通-run-格式`；只列用户要求的字段。目标必须确实达到请求值；字体对应主题属性一并清除，未指定属性不得改变。
- 比较原件/候选的成员集合及非正文部件内容字节，保护样式继承、编号、关系、媒体、页眉页脚等；在正文中仅放行指定 run 的指定属性，保护所有段落属性、文字、非目标 run 及未知 XML 内容。不解析或重建完整 Word 样式级联。
- 拆分/合并 run、插入/删除段落、改文字或样式定义、重复目标、表格/页眉页脚/字段/修订/书签等复杂目标均不属于候选发布范围。命名空间重写、全 XML 重新排版或其他无法可靠证明的变化也可保守拒绝；不要将拒绝解释为文档一定损坏，更不能扩大白名单让它通过。
- `office/validate.py --original` 仍是结构与正文修订文字检查；`_serialize_checked` 是修改后 XML 的序列化保真检查。两者都不能代替原件与候选之间的格式差异核验。`package.py pack` 只负责包装，不证明格式保留。
- 这里的保护不是任意 OOXML 无损编辑、XSD 或 Word/WPS 视觉验证。字符单位缩进、自动行距、换行和字体替代仍可能随字体度量改变显示。

## 受控编辑与占位符填充

将已有模板放入 `./采购申请模板.docx`，将替换规则保存为 `./replacements.json`，例如：

英文占位符示例：

```json
{"replacements": [{"old": "{{ company }}", "new": "ACME & 中文", "count": 1}]}
```

中文占位符示例；`old` 必须与模板中的文字完全一致：

```json
{"replacements": [{"old": "{{ 公司名称 }}", "new": "星河科技有限公司", "count": 1}]}
```

```python
execute(command="python -I -S -B \"<skill-root>/scripts/edit_docx.py\" ./采购申请模板.docx ./replacements.json 采购申请.docx --require-no-placeholders")
```

- 使用输出新文件；禁止输入输出指向同一文件或通过链接指向同一文件。
- 受控替换、段落结构编辑及批注定义入口遇到数字签名部件或关系时停止；不保留失效签名后继续声称保真，也不擅自删除签名。
- 按原始文本同时应用各条替换；`count` 默认为 1，可显式设置更大的预期次数。匹配不足、过多或重叠时停止，保留已有输出。次数不符时区分普通文本次数异常、跨文本/run 拆分、受保护或复杂容器、选定部件中缺失等诊断；诊断不扩大可编辑范围。
- 默认仅编辑 `word/document.xml`；需要时用 `--parts word/document.xml word/header1.xml` 指定真实存在的正文、数字后缀页眉或页脚部件。
- 仅替换单个普通 `w:t` 节点内的文字，保留 run 格式；禁止跨 run、字段、修订、超链接、批注范围或复杂容器自动替换。替换值仅支持单行文本。
- 对跨段落的复杂字段、批注及移动修订范围同样禁止替换其内部文字；选定部件存在未闭合、错序、重复或缺失必要 ID 的受检范围标记时，停止编辑并保留已有输出。重叠检查包括同一搜索词自身的重叠出现。
- 按最近祖先的 `xml:space` 判断是否保留空白。目标节点含边缘 XML 空白（空格、制表、回车、换行）但未声明或继承 `preserve` 时，拒绝编辑，不自动裁剪或激活原有空白。保留全角空格、不换行空格及明确保留的空白；允许替换值显式新增边缘空格。
- 拒绝编辑含 XML 注释、处理指令或未知属性的目标文字节点，以及含未知扩展或复杂内容的目标 run。可编辑普通文字之外的内容控件、未知容器等不在受控替换范围内；不要通过删除这些内容强行替换。
- 将简单占位符完整放在同一个 run 中；不要模拟 Jinja 表达式、过滤器、条件或循环。动态行数据应在生成器的 JSON 表格数据中构造。
- 使用 `--require-no-placeholders` 检查剩余双花括号标记，包括段落内跨 run 拆分的标记。对字面双花括号文本省略此选项并人工核对。

保留未修改 ZIP 部件的内容字节；不要声称整个 ZIP 或修改过的 XML 字节不变。修改部件写出前重新解析并比较全部节点及属性值；序列化不能保真时拒绝编辑（例如某些字符引用的空白规范化），不以结构检查通过代替保真。未知扩展部件可原样保留，但不要声称已理解或验证其语义。

## 放置批注锚点

自动流程优先使用 批注事务管理（`<skill-root>/references/comments-management.md`），在其严格范围内自动生成锚点并核验元数据。下述 `<skill-root>/scripts/comment.py` 保留为低层定义工具，仍需手工补正文锚点；不用于自动生命周期管理。

`<skill-root>/scripts/comment.py` 目录入口在内存中暂存包内容，完成批注修改和检查后，先将原部件内容保存为任务根目录 `./docx-comment-backup-*.zip` 并flush/fsync，再逐个原子替换受影响部件。可捕获失败尝试恢复原部件；恢复失败保留ZIP备份并报告准确路径。输入目录不能包含当前工作目录，必须是可信且无并发读写者的任务数据。非目标部件内容字节保留；多文件发布不是单一原子事务。

强制终止或恢复失败可能留下部分安装的内容和根目录ZIP备份；先核对输入与备份，再人工恢复，不自动删除未知残留或覆盖已安装内容。安装成功后备份清理失败会报告实际残留位置。压缩文件入口在内存中修改，再用 `./` 的独占暂存文件原子发布目标包；不创建工作目录，输出必须同扩展名且不能是符号链接。

使用 `<skill-root>/scripts/comment.py` 维护 `comments.xml`、`commentsExtended.xml`、`commentsIds.xml`、`commentsExtensible.xml`、关系和内容类型。不要只生成批注部件而遗漏正文锚点。

按脚本分配的 ID，在目标段落中添加以下标记；将其放在 `w:p` 的直接子节点中，禁止放入 `w:r`。

```xml
<w:commentRangeStart w:id="0"/>
<w:r><w:t>被批注的文字</w:t></w:r>
<w:commentRangeEnd w:id="0"/>
<w:r>
  <w:rPr><w:rStyle w:val="CommentReference"/></w:rPr>
  <w:commentReference w:id="0"/>
</w:r>
```

添加回复时，使用 `--parent`，并按脚本输出将回复锚点嵌入父批注范围。不要复用不属于目标批注的 ID。

## 标记插入和删除

在 run 外包裹 `w:ins` 或 `w:del`；提供 `w:id`、`w:author`、`w:date`。在删除范围内使用 `w:delText`，不要继续使用 `w:t`。

```xml
<w:del w:id="1" w:author="Editor" w:date="2026-01-01T00:00:00Z">
  <w:r><w:delText>旧文字</w:delText></w:r>
</w:del>
<w:ins w:id="2" w:author="Editor" w:date="2026-01-01T00:00:00Z">
  <w:r><w:t>新文字</w:t></w:r>
</w:ins>
```

不要自动处理删除的段落标记、移动修订或格式修订；保留已有结构并报告超出支持范围。禁止仅清空文字而声称段落已被删除。

使用文末修订对比命令检查遗漏的未追踪正文文字修改。以主入口定义的 `<skill-root>` 定位脚本，将文件放在宿主确认的任务工作目录，使用相对文件参数；不要声称该检查覆盖全部部件、格式变更或段落合并语义。

保留原作者的修订内容和元数据；对其他作者的插入做删除时，在其 `w:ins` 内嵌套自己的 `w:del`。恢复其删除时，在原 `w:del` 后添加自己的 `w:ins`；不要直接重写其他作者的修订历史。

禁止将提取器的 `accepted` 文本视图声称为保存后的净稿；完整修订接受和拒绝不在本 skill 的支持范围内。

## 使用低层辅助工具

先按 执行环境约束（`<skill-root>/SKILL.md#执行环境约束与首步自检`） 完成定位及自检，再从宿主确认的任务工作目录调用脚本。下文 `<skill-root>` 须替换为已确认的真实目录；修改前保留源文件，完成后执行基础检查，禁止为照抄示例切换目录：

压缩文档输入必须显式指定 `-o` 输出新文件；禁止与输入同路径或通过符号链接、硬链接指向输入。目录入口仍就地修改工作副本，且不接受 `-o`；先保留原目录再操作。`<skill-root>/scripts/comment.py` 默认作者为 `Editor`、缩写为 `E`，按需用 `--author` 和 `--initials` 指定真实作者。

```python
execute(command="python -I -S -B \"<skill-root>/scripts/comment.py\" ./采购管理办法.docx \"核对费用上限\" -o annotated.docx")
execute(command="python -I -S -B \"<skill-root>/scripts/merge_runs.py\" ./采购管理办法.docx -o merged.docx")
execute(command="python -I -S -B \"<skill-root>/scripts/office/validate.py\" ./采购管理办法_修订版.docx --original ./采购管理办法.docx")
```

添加批注定义后，先补正文锚点再验收。当前基础检查仅支持有起点、终点和引用的范围批注，不支持无范围的点批注，也不验证完整线程回复语义；不要把生成批注部件声称为已验证阅读器中的显示效果。

仅在明确理解原结构时合并相邻且格式相同的普通 run；该脚本只在受支持段落中清理已识别的 Word 校对标记及合并目标的 Word rsid，不删除同名未知扩展。字段、修订、范围、复杂容器或未知格式保持原样；未知内容未被合并不表示已理解其语义。使用继承的 `xml:space`，写出前检查 XML 值保真；目录入口原子替换正文单文件，压缩入口原子打包，拒绝符号链接及数字签名。不要为了让受控替换通过而盲目合并复杂结构。

保留命名空间声明、元素顺序、关系引用和 `xml:space`；禁止重新排版整个 XML 或删除未知扩展。使用 `<skill-root>/scripts/office/xmlio.py` 中的受限解析入口，不要直接用默认解析器处理外来 XML。

修订对比仅比较正文文字及有限的插入/删除标记，不检查所有格式、段落合并、移动、页眉页脚、字段、制表符或换行语义。指定 `--original` 不会豁免结构错误，校验器不提供自动修复或 XSD 检查。

指定 `--original` 即在结构检查通过后执行修订文本对比；发现未追踪文字修改时返回非零退出码。旧 `--author` 参数仅保留调用兼容性，仍要求 `--original`，其值不筛选修订作者；不要将它解释为特定作者的修订验收。
