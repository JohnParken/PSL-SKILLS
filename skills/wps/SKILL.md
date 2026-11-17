---
name: wps
version: "20261118.0325.5874"
description: 读取 .wps、.rtf 和旧版 .doc 的全文、表格、文本框及可提取对象文字，报告遗漏与修订边界。默认回传有用内容及可读取图表保存值，图片和排版证据按需读取。按明确要求创建 RTF 内容的 .wps/.rtf 文档或重排副本。原生 DOCX 优先使用 docx。
---

# WPS 文字读取、RTF 创建与受控重排

## 执行环境约束与首步自检

**部署运行与本地测试：** DeepAgents 通过宿主的 `execute(command=完整命令字符串)` 执行业务命令。使用本 Skill 部署方预置的 `"<skill-root>/scripts/.venv/bin/python"`，缺失或不可执行时停止，不自动安装、不回退到 PATH；在命令字符串内按外层语法转义引号。生产保留 `-I -S -B`，仅使用标准库。仓库本地实际测试使用默认 `python3`；不满足版本或依赖时报告，不将本地结果视作部署验收。

**最小必要检查：** 仅读任务只做本入口指定的读取自检；任务 `./` 顶层目标可读且必要自检通过后直接读取，不先测试写入。不得主动运行全量 `mount`、`findmnt`、`df`、`env` 或读取 `/proc/mounts` 做系统侦察，不把根挂载 `/` 的 `ro` 推断为任务目录不可读/不可写。只有实际需要生成、编辑、导出、缓存或受控临时文件时，才按对应流程验证所需写入能力；安全、输入保护和解析检查不省略。失败仅针对实际错误做一次有界定位，输出失败项及必要摘要，不倾倒系统日志；条件未改变不重复自检。

**归档展示边界（始终适用）：** 工具回执是内部核验材料，不是最终回复模板；完整或精简回执均不得整段转贴。面向用户的正文、进度、表格、代码块、链接文字及链接目标都不得包含归档路径、目录或任务 ID，也不展示带这些信息的原始警告/错误。不能仅把链接文字改为文件名而仍链接归档副本。

用户归档提示只采用已核对完整状态的 `archive_user_notice` 或等义安全话术，不追加“归档位置”等说明。主文件链接仅使用宿主确认可交付的实际主文件引用；`archive_output`、`files[].archive_output` 及任何归档位置字段不得用作下载地址，也不得据其拼接链接。缺少主文件交付引用时如实说明，不用归档地址代替。

回复前分别核对业务、归档与清理状态，并检查可见文字和链接目标均无内部归档信息。失败、部分成功、跳过、待交接、未知与清理未完成仍须说明实际影响；必要原因用不含路径/ID的自然语言概括，不因隐藏路径省略失败。内部机器字段、完整诊断和原退出码保留供核验，不修改或删除来伪装成功；纯文本和只读任务不新增归档。

**宿主大结果读取：** 宿主返回的 `/large_tool_results/` 引用属于宿主结果路径，仅用宿主提供的 `read_file` 按其真实 schema 读取返回的准确引用，按实际续读能力核对覆盖、结束与截断。不要交给 `execute`、`cat`、Shell 或 Python，也不猜测映射、替代路径或将路径掩码当作真实路径；缺少 `read_file`、读取失败或完整性无法确认时报告具体缺口并停止依赖该结果的操作。这不改变普通业务文件和 Skill 资源原有的读取及路径边界。

脚本由宿主 `execute(command=完整非空命令字符串)` 执行，按实际 schema 提交；包含 stdin 时在同一命令完整提供。替换路径并安全引用参数，不把 `execute(...)` 嵌入命令。执行工具或路径映射不可用时停止并报告；独立调用逐项核对结果，状态未知不盲目重试。

**用户文件定位：** 优先附件映射和用户指定文件名。上传文件和待查交付物只定位任务 `./` 顶层，不递归或到子目录查找；无映射仅列顶层候选，歧义询问，无候选或指定位置不在顶层时报告。内部归档、Skill 资源与程序输入安全契约不变。

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

宿主路径与执行器：使用宿主提供的真实入口位置，在默认任务目录 `./` 执行；入口路径可被执行器访问时，以本节指定自检确认可运行，不另做系统探测。宿主明确提供不同路径空间时仅使用其映射；没有可用位置或实际访问失败时报告具体缺口，不猜测映射、不换目录。读取工具可读不等于执行器已可运行，仍核对实际自检结果和任务输入可读性。

1. **任务目录与输入。** 任务工作目录固定为 `./`，不询问用户，也不另建或切换工作目录。上传文件、交付物和普通中间文件均在 `./` 顶层定位；通常不创建临时子目录。在该目录运行并保持不变，若当前目录与 Skill 安装目录冲突，停止相关写入并报告。输入及受控临时文件沿用该目录内相对路径规则。交付输出只传裸文件名（例如 `报告.wps`），由脚本固定写入任务根目录 `./`；不接收 `./文件名`、子目录、绝对路径、父目录或 Windows 路径，不截取 basename 静默改写非法参数。不用绝对数据路径、父目录或切换挂载绕过边界。优先本轮附件映射与用户指定路径；唯一可访问候选直接处理，多候选无法消歧才询问。没有映射时只列出 `./` 顶层候选，不递归搜索或读取历史归档；无可访问文件如实报告，不要求用户重复已知文件名。
2. **定位与环境。** 按上述真实 `file_path` 定位脚本，不为调用切换安装目录。使用宿主确认的 Python 3.10+、保留 `-I -S -B`；仅标准库（`<skill-root>/requirements.txt`），禁止安装包或运行时联网。OOXML 和二进制图表缓存另需 Expat >=2.6；二进制导出需预装 LibreOffice。
3. **首次操作前仅自检相关分支。** 读取用第一条，创建用第二条；编辑先完成两者，导出按专项参考自检。均要求退出 0 且输出 `environment-ok`，不只看无报错：

```python
execute(command="\"<skill-root>/scripts/.venv/bin/python\" -I -S -B \"<skill-root>/scripts/read_wps.py\" --check")
execute(command="\"<skill-root>/scripts/.venv/bin/python\" -I -S -B \"<skill-root>/scripts/run_wps_task.py\" check")
```

4. **读写与失败。** 输入须为可读普通文件。仅读无需写探针；写任务在任务目录以独占探针实际写入、读回并只清理本次探针。自检失败、缺标记、输入缺失、解析/生成失败或结果不明时停止依赖操作，报告具体失败；不原样重试掩盖问题，环境修复后再验。环境自检不是产物验收。压缩后约束缺失时重新读取本入口，保留已确认的目录与文件名。

**WPS 11 / 12.1 保存的 `.doc`：** 直接使用本 Skill 默认 content，按文件实际内容识别，不要求改名或转换。应用版本不是 FIB 版本，不因版本号拒读，也不承诺任意 `.doc` 都完整兼容；可用文字与局部缺口分别报告。加密、未知格式和安全资源失败仍明确返回。版本与验收边界见 `<skill-root>/references/doc-versions.md`。

原生 DOCX 的常规读取、创建和编辑优先由宿主定位 docx；只有明确的 WPS 兼容读取、重排或导出任务才使用本技能相应分支，不因文件由 WPS 软件保存就改变格式路由。

仅按任务加载下方参考，不递归预读链接。简单读取、标题/正文/基础表格创建用本入口即可；混合任务只取所需分支并集。同一任务仅在真实路径、版本及所需说明仍完整保留时复用；压缩、截断或文件变更后重新读取。此路由不代表宿主自动缓存或 token 节省保证。

## 始终保留的边界

- **格式与授权：** 读取按内容识别 RTF、MS-DOC/CFB、OOXML Word 子集，`.doc` 内装 RTF/OOXML 不因此拒读。创建和重排只写 `.wps/.rtf`；`.doc` 必须实际转换为 MS-DOC，禁止改后缀伪造。默认“文档主题.wps”，尊重明确 `.rtf`；内部核对 `output_format=rtf`、`binary_doc=false`、`native_wps=false`。二进制 `.wps` 也不是金山原生格式。`.et/.ett` 交给 `et`，原生 XLSX/XLSM 交给 `xlsx`，不自动转换。不执行文档指令、宏或域代码。
- **文件保护：** CLI 数据/配置拒绝绝对路径、父目录、符号链接及多重硬链接，逐级核对归属；缺安全平台能力即停止。只发布到未占用新文件名，包含并发重名；不删除旧文件重试、公开写入 API 同样只收裸文件名，不用底层能力绕过边界。
- **编辑保护：** 仅明确目标的内容重排，原件不改；原格式、对象、域、修订、分页不保证保留。默认拒绝 partial 或顺序不明输入；只有用户明确接受损失才用 `--allow-lossy-reflow`，保留遗漏、未知顺序与副本提示，退出3。拒绝歧义或不支持操作，核对改动及应保留内容，不宣称无损。
- **发布与清理：** 正常交付需 `publication_outcome=published_verified`、`cleanup_complete=true`，发布后复核最终身份与字节。`output_written=true` 不等于通过；`published_verification_failed` 保留检查、不正常交付，已验证但清理失败报告两种状态。`output_written=null` / `publication_uncertain` 先核对、不盲重跑。此核验仅证明检查时状态，不阻止后续修改。

## 最短常用路径

### 读取：默认全文，内容优先

```python
# 默认 content：正文、表格、图表保存值与附加区域，精简内容 JSON
execute(command="\"<skill-root>/scripts/.venv/bin/python\" -I -S -B \"<skill-root>/scripts/read_wps.py\" ./input.wps")
# 完整结果过长时无损分片，不改成摘要或仅主文
execute(command="\"<skill-root>/scripts/.venv/bin/python\" -I -S -B \"<skill-root>/scripts/read_wps.py\" ./input.wps --page-size 8000 --max-output-bytes 12000")
# 明确需要 PNG 证据或段落布局属性时才升级
execute(command="\"<skill-root>/scripts/.venv/bin/python\" -I -S -B \"<skill-root>/scripts/read_wps.py\" ./input.wps --reading-mode maximum")
# 只有用户明确只需概况或这段主文时才用 standard
execute(command="\"<skill-root>/scripts/.venv/bin/python\" -I -S -B \"<skill-root>/scripts/read_wps.py\" ./input.wps --reading-mode standard --summary")
execute(command="\"<skill-root>/scripts/.venv/bin/python\" -I -S -B \"<skill-root>/scripts/read_wps.py\" ./input.wps --reading-mode standard --paragraph-range 1:20")
```

CLI 默认 `--profile content` 返回 `wps_content_v1`：正文、保留分组/合并的精简表格、可确认父子单元格的 `nested_content`、附加区域、图表保存值与归并遗漏提示，不倾倒关系ID、XML路径、二进制记录及密集诊断。OOXML 含表格时用 `body` 顺序引用 `tables`，表格文字不再重复进入正文 `text`；嵌套单元格的 `content` 保留段落/子表顺序，同文但独立段落不会删除。`--profile full` 返回原详细结构；显式 standard/maximum 未指定 profile 时保持原详细输出。API `read()`、`read_bytes()` schema 不变。分页对所选 profile 的完整结果无损分片，游标不能跨 profile 复用。

CLI、`read()`、`read_bytes()` 默认 `reading_mode=content`，不先跑摘要、渲染、转换或图片提取。正文、表格、文本框与附加区域分别按既有结构返回，保留原始文字、来源、可确认分组/合并及缺口，不把 `text` 一栏当作全文。`drawing_objects` 保留支持的 OfficeArt 描述/艺术字，`related_stories` 保留文本框、页眉页脚、脚尾注等支持区域；不猜页面、阅读顺序或未知对象内容。二进制 `binary_table_structure` 保留单元格文字、表格分组和可确认合并，嵌套内容按 `nested_row_evidence` 和 `nested_tables` 读取，缺父范围或边界不猜归属。

二进制 content 默认有界读取浮动/内联图表保存缓存，不解析 PNG，也不为纯段落读取 PAPX 布局；含表格时仍验证必要属性、结构和共享范围，只省略非必要布局明细。`content_reading.not_requested` 和 `binary_optional_content_not_requested` 明示未检查项目；图片/内嵌图表中独有的文字或数值可能遗漏，不能称全格式完整。PNG或布局证据需显式 maximum，图片专有内容仍不做OCR；`binary_images` 为有界 PNG 元数据，原始字节再加 `--include-image-bytes`；`binary_charts.charts[].presentation` 为可用保存值、格式及缺值范围，不补零、不反算、不访问外部工作簿。两模式都不做 OCR、渲染或版式复原。CFB/ZIP/XML、加密、重叠、大小和资源门禁不降低；没有新增依赖。

OOXML 的 content/maximum 同时返回 `ooxml_charts` 中原生图表的标题、系列名、类别与数值保存缓存，保留原始字符串、格式代码、点索引和缺值诊断；不刷新、反算、打开外链或内嵌工作簿，不把缓存等同已验证可见值。图片图表不做 OCR。MC AlternateContent 按 Requires 在真实 XML 作用域解析的命名空间选择受支持 Choice，否则 Fallback；未能选择时明确 partial 与分支来源，不称已确认当前视图，不按相同文字删除独立对象。

两种全文模式均保留隐藏文字与域缓存但不执行。OOXML/RTF 正文用受支持 accepted 当前视图，不同的 all 存储视图在 full/API 另放 `revision_content`，内容投影仅提示另有存储修订，不混入当前正文；显式 all 标 `all_stored_revisions_not_current`，不可当当前内容。MS-DOC 与无法可靠过滤的已分类 RTF 修订返回存储文字，但 `current_view_available=false` 且 partial。关联区域缺失/损坏保留可靠正文并明确缺口；未知块、公式、图片与对象未解码仍报告，不以空数据伪装完整。完整边界与诊断见 `<skill-root>/references/reading-assessment.md#最大化读取模式`，仅需要细节时加载。

概况不含正文；段落范围只投影主段落，核对 `selection_status/source_status/projection_scope`。全文结果必须保留 JSON 与诊断；宿主截断不是完整 JSON。显式 standard 的 `--format text` 仅在确实不需结构时用，partial 诊断仍在 stderr；RTF 纯文本按块顺序包含表格，JSON `text` 仅段落，表格及顺序在 `tables/blocks`。standard 的附加区域需 `--include-related`，正文投影不得冒充全文。

### 创建：标题、正文、基础表格

```python
execute(command="\"<skill-root>/scripts/.venv/bin/python\" -I -S -B \"<skill-root>/scripts/run_wps_task.py\" create \"通知.wps\" --compact-receipt <<'JSON'\n{\"theme\":\"official\",\"blocks\":[\n  {\"type\":\"title\",\"text\":\"工作情况通报\"},\n  {\"type\":\"heading\",\"level\":1,\"text\":\"一、总体情况\"},\n  {\"type\":\"paragraph\",\"text\":\"本阶段工作已完成。\"},\n  {\"type\":\"table\",\"rows\":[[\"事项\",\"状态\"],[\"联调\",\"通过\"]]}\n]}\nJSON\n")
```

默认 official 公文预设，用户明确格式优先，不凭标题切换主题。默认自然分页，不擅自加 `page_break` / `page_break_before`；标题默认与下文同页。显式新页只选一种分页机制；不以分页符冒充分节。预设不等于GB/T认证，字体只写名称，不安装/嵌入或保证可用；回读不等于视觉分页验收。其他主题、页眉页脚、列表或自定义版式再读生成参考。

### 标准模式与长文档边界

只有明确只需主文摘要、段落/章节切片、旧纯文本或特定策略时才显式指定 `--reading-mode standard`（API 同名参数）。不要为了避开限制偷偷降级，也不要用标准模式的主文投影回答包含附加区域的全文问题。创建、编辑、转换及发布回读内部固定 standard，保持原验收语义。

默认不带输出参数返回完整内容投影 JSON（16 MiB上限），详细结构用 `--profile full`；需要分次读取时用 content 或 maximum 的 `--page-size/--cursor`，无损传输完整结果，包括该 profile 的正文、表格、图表、附加区域和遗漏；详细修订与诊断仅 full 返回。单条超长内容可跨片，不能把片段当完整业务JSON；必须从0连续收齐、核对源/选项/结果及分片摘要再重组。详见 `<skill-root>/references/maximum-pagination.md`。这不是物理文档分页或流式解析；分页总规范JSON另受256 MiB限制。宿主截断也不能当作完整结果。修订补读失败保留当前已读正文并标 partial 和具体缺口；`revision_content` 是另一个存储视图，不是变更清单。回答“当前内容”前必须核对 `content_view/current_view_available`，未知则说明不能确认，不能把存储文字直接当作当前版本。

## 按需直达

| 实际需要 | 只读取相关参考 |
|---|---|
| 大纲/指定章节、重复标题消歧 | `<skill-root>/references/selective-reading.md` |
| content/maximum 完整结果过长、需保留附加内容分次读取 | `<skill-root>/references/maximum-pagination.md`；按连续片段和摘要核对重组，不当主文摘要 |
| 显式 standard 有界正文、字符预算、游标续读 | `<skill-root>/references/bounded-reading.md`；投影仍完整解析，不降低解析内存，全文统计不以摘要代替 |
| 容器、对象、字段范围 | `<skill-root>/references/format.md` |
| 二进制对象文字来源，或按需 maximum PNG/布局证据 | `<skill-root>/references/binary-shapes.md#最大模式绘图说明` |
| 二进制图表的保存值、缺值和来源 | `<skill-root>/references/binary-shapes.md#存储图表缓存` |
| 附加区域 | MS-DOC：`<skill-root>/references/ms-doc-stories.md`；OOXML：`<skill-root>/references/ooxml-related-stories.md` |
| 尽量补读存储文字、文本框、附加区域或二进制表格属性 | `<skill-root>/references/reading-assessment.md#最大化读取模式`，使用完整 JSON 并核对 partial 和来源 |
| 隐藏文字/修订 | `<skill-root>/references/word-visibility.md` |
| 读取表格结构 | OOXML：`<skill-root>/references/ooxml-tables.md`；MS-DOC：`<skill-root>/references/binary-tables.md` |
| 自定义创建、列表/页眉页脚、标题/制表位、合并/边框/物理列宽 | `<skill-root>/references/generation.md`，支持范围和单位以该文件为准，不照搬 DOCX 参数 |
| 文字替换、段落增删改、单元格或批量重排 | `<skill-root>/references/editing.md` |
| DOCX 转 WPS 兼容格式或二进制 DOC | `<skill-root>/references/exporting.md` |
| 读取 partial/失败解释、来源证据 | `<skill-root>/references/reading-assessment.md` |
| 落盘配置、归档异常或收尾恢复 | `<skill-root>/references/delivery.md` |

## 结果判断与交付

普通成功时简洁告知文档已生成，提供宿主实际支持的主文件链接及真实归档状态；不逐项复述回执字段、后端或内部格式。用户明确询问格式、要求金山原生格式或二进制 DOC，或实际格式、回退及兼容限制会影响使用时，准确说明实际格式和限制，不将 RTF 内容的 `.wps` 称为金山原生格式或二进制 DOC；无法满足明确格式要求时如实说明，不冒充满足。内容遗漏、有损重排、失败及清理或归档异常仍按实际影响报告，不能因简洁省略。

先核对调用是否执行、真实退出状态与宿主截断说明，再检查本入口的业务结果。工具 `success` 不等于文档成功；Deep Agents 可以合并 stdout/stderr 并附状态文字，不假定两个独立字段或固定分隔符。不把宿主前后缀当 JSON，也不靠截取首尾花括号猜正文；退出码缺失/未知不补成0。未启动、超时、发布或结果不明时先核实，不盲目重跑创建、重排或导出。

宿主结果引用（如 `/large_tool_results/`）仅用实际可用的 `read_file` 按真实 schema 读取，不能当 Shell 路径。需要分段时核对覆盖；宿主截断不等于业务视图 `truncated`。引用不可读或内容不完整时停止依赖，按 `<skill-root>/references/bounded-reading.md` 选择安全续读，不能以首屏、概况或主正文投影代替全文结构。

- 读取 `status=ok` / 退出0只表示声明范围内成功；`partial` / 退出3保留可靠正文并报告已知缺口；失败1、参数错误2。选择性歧义/未找到不能当已读到目标，partial源还可能保持退出3，须同时检查选择状态。
- 创建/编辑退出0仍须检查 `partial`、`issues`、`spec_warnings` / `spec_warning_count`，不能只凭 `status=success`。未知配置或显式未知字体替换要说明；显式有损重排退出3。`--compact-receipt` 仅创建支持，省去预览、不省略现有警告及发布/归档状态；编辑不要传此参数。
- 导出 success/partial/失败退出0/3/1，参数错误2。来源或候选partial、auto回退均保留诊断；正文回读成功不证明原版式、全格式或实机打开。交付前按任务核对标题、关键值、表格与遗漏，视觉未验收如实说明。
- 创建、重排、导出 CLI 自动使用随包实现归档，返回 `delivery`；裸读取与底层API不自动归档，不重复调用外部生命周期。业务、发布、归档、清理分别核对；无配置为 failed；已声明最终交付但业务、发布验收或内部清理未就绪也为 failed，检查 blocking_reasons，不误称无需归档。归档失败保留主文件、不重跑。归档成功即提示“🎉 文件归档成功，请到云空间查看。 ☁️✨”，无需等待宿主云空间映射确认，不宣称已验证上传完成或展示时效。核对非空完整清单，汇总 success 且全部项 copied/already_archived 才是全部成功；保留 archive_message，按实际结果展示 archive_user_notice。失败、部分成功、跳过、待交接和未知分别如实报告，不套用全部成功；业务与清理状态独立，主业务失败不称生成成功。隐藏归档路径、目录、ID及原始日志，保留真实主文件链接，不虚构下载。详细话术见 `<skill-root>/references/delivery.md`。
- 默认stdin不落额外JSON。确需落盘时先读 delivery，用受控job创建前登记尚不存在的临时项；生产进程结束后，成功则归档后收尾，失败也立即按原清单清理本次归属明确且可安全删除的临时项，不因待重试保留。保护原件、主文件、最终文件、未知或仍占用项，未完成如实报告；不扫描历史、不补认领，恢复核对原清单，重新生成从原始输入新任务开始。
