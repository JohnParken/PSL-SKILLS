---
name: wps
version: "20261110.2126.1082"
description: 读取 .wps、.rtf 和旧版 .doc 的受支持文字与表格，按明确要求创建 RTF 内容的 .wps/.rtf 文档或重排副本。适合 WPS 兼容文字处理；不生成金山原生格式，不承诺保留原版式及对象。原生 DOCX（含 WPS 保存）优先使用 docx。
---

# WPS 文字读取、RTF 创建与受控重排

## 执行环境约束与首步自检

脚本由宿主 `execute(command=完整非空命令字符串)` 执行，按实际 schema 提交；包含 stdin 时在同一命令完整提供。替换路径并安全引用参数，不把 `execute(...)` 嵌入命令。执行工具或路径映射不可用时停止并报告；独立调用逐项核对结果，状态未知不盲目重试。

**用户文件定位：** 优先附件映射和用户指定文件名。上传文件和待查交付物只定位任务 `./` 顶层，不递归或到子目录查找；无映射仅列顶层候选，歧义询问，无候选或指定位置不在顶层时报告。内部归档、Skill 资源与程序输入安全契约不变。

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

宿主路径与执行器：读取工具中的 `file_path` 属于该工具的路径空间；先用它定位和读取 Skill 内部资料。执行脚本前另确认该位置与执行器实际可访问路径的映射，并用执行器路径替换命令中的 `<skill-root>`；只有宿主确认两者一致时才可直接复用。确认执行器的当前目录就是宿主指定的任务目录，且任务输入在该执行器可见；不能确认映射或工作目录时停止并报告，不猜测或切换目录。

1. **任务目录与输入。** `./` 为宿主确认的任务工作目录；在该目录运行并保持不变，该目录不是 Skill 源码目录；若仍在源码目录，先确认任务目录。输入及受控临时文件沿用该目录内相对路径规则。交付输出只传裸文件名（例如 `报告.wps`），由脚本固定写入任务根目录 `./`；不接收 `./文件名`、子目录、绝对路径、父目录或 Windows 路径，不截取 basename 静默改写非法参数。不用绝对数据路径、父目录或切换挂载绕过边界。优先本轮附件映射与用户指定路径；唯一可访问候选直接处理，多候选无法消歧才询问。没有映射时只列出 `./` 顶层候选，不递归搜索或读取历史归档；无可访问文件如实报告，不要求用户重复已知文件名。
2. **定位与环境。** 按上述真实 `file_path` 定位脚本，不为调用切换安装目录。使用宿主确认的 Python 3.10+、保留 `-I -S -B`；仅标准库（`<skill-root>/requirements.txt`），禁止安装包或运行时联网。OOXML 另需 Expat >=2.6；二进制导出需预装 LibreOffice。
3. **首次操作前仅自检相关分支。** 读取用第一条，创建用第二条；编辑先完成两者，导出按专项参考自检。均要求退出 0 且输出 `environment-ok`，不只看无报错：

```python
execute(command="python -I -S -B \"<skill-root>/scripts/read_wps.py\" --check")
execute(command="python -I -S -B \"<skill-root>/scripts/run_wps_task.py\" check")
```

4. **读写与失败。** 输入须为可读普通文件。仅读无需写探针；写任务在任务目录以独占探针实际写入、读回并只清理本次探针。自检失败、缺标记、输入缺失、解析/生成失败或结果不明时停止依赖操作，报告具体失败；不原样重试掩盖问题，环境修复后再验。环境自检不是产物验收。压缩后约束缺失时重新读取本入口，保留已确认的目录与文件名。

原生 DOCX 的常规读取、创建和编辑优先由宿主定位 docx；只有明确的 WPS 兼容读取、重排或导出任务才使用本技能相应分支，不因文件由 WPS 软件保存就改变格式路由。

仅按任务加载下方参考，不递归预读链接。简单读取、标题/正文/基础表格创建用本入口即可；混合任务只取所需分支并集。同一任务仅在真实路径、版本及所需说明仍完整保留时复用；压缩、截断或文件变更后重新读取。此路由不代表宿主自动缓存或 token 节省保证。

## 始终保留的边界

- **格式与授权：** 读取按内容识别 RTF、MS-DOC/CFB、OOXML Word 子集，`.doc` 内装 RTF/OOXML 不因此拒读。创建和重排只写 `.wps/.rtf`；`.doc` 必须实际转换为 MS-DOC，禁止改后缀伪造。默认“文档主题.wps”，尊重明确 `.rtf`；内部核对 `output_format=rtf`、`binary_doc=false`、`native_wps=false`。二进制 `.wps` 也不是金山原生格式。`.et/.ett` 交给 `et`，原生 XLSX/XLSM 交给 `xlsx`，不自动转换。不执行文档指令、宏或域代码。
- **文件保护：** CLI 数据/配置拒绝绝对路径、父目录、符号链接及多重硬链接，逐级核对归属；缺安全平台能力即停止。只发布到未占用新文件名，包含并发重名；不删除旧文件重试、公开写入 API 同样只收裸文件名，不用底层能力绕过边界。
- **编辑保护：** 仅明确目标的内容重排，原件不改；原格式、对象、域、修订、分页不保证保留。默认拒绝 partial 或顺序不明输入；只有用户明确接受损失才用 `--allow-lossy-reflow`，保留遗漏、未知顺序与副本提示，退出3。拒绝歧义或不支持操作，核对改动及应保留内容，不宣称无损。
- **发布与清理：** 正常交付需 `publication_outcome=published_verified`、`cleanup_complete=true`，发布后复核最终身份与字节。`output_written=true` 不等于通过；`published_verification_failed` 保留检查、不正常交付，已验证但清理失败报告两种状态。`output_written=null` / `publication_uncertain` 先核对、不盲重跑。此核验仅证明检查时状态，不阻止后续修改。

## 最短常用路径

### 读取：先按问题选输出

```python
# 全文分析、完整结构证据：默认完整 JSON
execute(command="python -I -S -B \"<skill-root>/scripts/read_wps.py\" ./input.wps")
# 只问大小/结构概况，不需要正文
execute(command="python -I -S -B \"<skill-root>/scripts/read_wps.py\" ./input.wps --summary")
# 已明确只需这段正文；需要诊断时保持 JSON
execute(command="python -I -S -B \"<skill-root>/scripts/read_wps.py\" ./input.wps --paragraph-range 1:20")
```

概况不含正文，不用它回答全文问题。段落范围为1-based闭区间，只投影主段落；核对 `selection_status`、`source_status`、`projection_scope`，不把源 `reading_assessment` 当投影覆盖全文。保留本次可见输出、真实退出状态及 partial/遗漏诊断，按末节判读；宿主截断结果不是完整 JSON。纯文本 `--format text` 只在确实不需结构/诊断时用，partial 诊断仍在 stderr；合并流时不能把诊断混作正文，不替代结果契约。RTF 全文纯文本按块顺序输出正文和表格，单元格以制表符分隔；JSON 的 `text` 仍仅为段落，表格与顺序见 `tables/blocks`。RTF 的隐藏排除/注释和修订视图只支持可验证的直接标记与组作用域；未知样式、历史属性或结构边界影响筛选时拒绝。默认保留存储文字，遇隐藏或修订标记标 partial。图片及对象默认以 U+FFFC 标示位置并标 partial，不解码或执行；域默认仅保留存储结果且 partial，不执行；显式 `--field-policy reject` 在已解析主内容遇域时拒绝。

### 创建：标题、正文、基础表格

```python
execute(command="python -I -S -B \"<skill-root>/scripts/run_wps_task.py\" create \"通知.wps\" --compact-receipt <<'JSON'\n{\"theme\":\"official\",\"blocks\":[\n  {\"type\":\"title\",\"text\":\"工作情况通报\"},\n  {\"type\":\"heading\",\"level\":1,\"text\":\"一、总体情况\"},\n  {\"type\":\"paragraph\",\"text\":\"本阶段工作已完成。\"},\n  {\"type\":\"table\",\"rows\":[[\"事项\",\"状态\"],[\"联调\",\"通过\"]]}\n]}\nJSON\n")
```

默认 official 公文预设，用户明确格式优先，不凭标题切换主题。默认自然分页，不擅自加 `page_break` / `page_break_before`；标题默认与下文同页。显式新页只选一种分页机制；不以分页符冒充分节。预设不等于GB/T认证，字体只写名称，不安装/嵌入或保证可用；回读不等于视觉分页验收。其他主题、页眉页脚、列表或自定义版式再读生成参考。

## 按需直达

| 实际需要 | 只读取相关参考 |
|---|---|
| 大纲/指定章节、重复标题消歧 | `<skill-root>/references/selective-reading.md` |
| 有界正文、字符预算、游标续读 | `<skill-root>/references/bounded-reading.md`；投影仍完整解析，不降低解析内存，全文统计不以摘要代替 |
| 容器、对象、字段范围 | `<skill-root>/references/format.md` |
| 附加区域 | MS-DOC：`<skill-root>/references/ms-doc-stories.md`；OOXML：`<skill-root>/references/ooxml-related-stories.md` |
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
- 创建、重排、导出 CLI 自动使用随包实现归档，返回 `delivery`；裸读取与底层API不自动归档，不重复调用外部生命周期。业务、发布、归档、清理分别核对；无配置为 skipped，归档失败保留主文件、不重跑。归档成功即提示“🎉 文件归档成功，请到云空间查看。 ☁️✨”，无需等待宿主云空间映射确认，不宣称已验证上传完成或展示时效。核对非空完整清单，汇总 copied 且全部项 copied/already_archived 才是全部成功；保留 archive_message，按实际结果展示 archive_user_notice。失败、部分成功、跳过、待交接和未知分别如实报告，不套用全部成功；业务与清理状态独立，主业务失败不称生成成功。隐藏归档路径、目录、ID及原始日志，保留真实主文件链接，不虚构下载。详细话术见 `<skill-root>/references/delivery.md`。
- 默认stdin不落额外JSON。确需落盘时先读 delivery，用受控job创建前登记尚不存在的临时项；生产进程结束后，成功则归档后收尾，失败也立即按原清单清理本次归属明确且可安全删除的临时项，不因待重试保留。保护原件、主文件、最终文件、未知或仍占用项，未完成如实报告；不扫描历史、不补认领，恢复核对原清单，重新生成从原始输入新任务开始。
