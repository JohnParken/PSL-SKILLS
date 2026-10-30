# DOCX 维护测试与历史验证记录

路径占位符说明：`<skill-root>` 是 DOCX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 DOCX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

本页仅在修改、审查、测试或发布 Skill，或用户明确要求追溯历史时读取。普通新建、编辑、读取、归档和清理任务不读取本页，也不执行这里的维护测试。

现行归档参数仅为 `questionId`。历史记录中的 `requestId`、`idRecord`、旧路径及旧退出码只描述当时行为，均不能用作现行参数或命令依据；现行规则以主入口及业务参考为准。历史验证命令中的 `skills/docx/`、其他 `skills/` 及 `tests/` 路径以当时已确认的仓库根目录为基准，保留为执行证据，不是安装后调用资源的路径；测试筛选参数中的文件名也不代表资源定位路径。

## 目录

- 段落排版第一批增强（2026-10-30）（`<skill-root>/references/maintenance-validation.md#段落排版第一批增强2026-10-30`）
- DOCX路径统一（2026-10-29）（`<skill-root>/references/maintenance-validation.md#docx路径统一2026-10-29`）

- 受控编辑清单收尾（2026-10-29）（`<skill-root>/references/maintenance-validation.md#受控编辑清单收尾2026-10-29`）

- 运行条件与命令（`<skill-root>/references/maintenance-validation.md#运行条件与命令`）
- 固定预期与失败保护（`<skill-root>/references/maintenance-validation.md#固定预期与失败保护`）
- 已执行与未验证（`<skill-root>/references/maintenance-validation.md#已执行与未验证`）
- 任务清理工具验证（`<skill-root>/references/maintenance-validation.md#任务清理工具验证`）
- 归档用户提示验证（2026-10-28）（`<skill-root>/references/maintenance-validation.md#归档用户提示验证2026-10-28`）
- 自动新建收尾验证（2026-10-28）（`<skill-root>/references/maintenance-validation.md#自动新建收尾验证2026-10-28`）
- 重试事务与控制器可靠性验证（2026-10-28）（`<skill-root>/references/maintenance-validation.md#重试事务与控制器可靠性验证2026-10-28`）
- 安全维护验收（`<skill-root>/references/maintenance-validation.md#安全维护验收`）
- 临时文件平铺维护（2026-10-28）（`<skill-root>/references/maintenance-validation.md#临时文件平铺维护2026-10-28`）
- 自动环境判断维护（2026-10-28）（`<skill-root>/references/maintenance-validation.md#自动环境判断维护2026-10-28`）
- 回执清理异常修复（2026-10-29）（`<skill-root>/references/maintenance-validation.md#回执清理异常修复2026-10-29`）
- 参数两端空白裁剪（2026-10-29）（`<skill-root>/references/maintenance-validation.md#参数两端空白裁剪2026-10-29`）
- 工具调用提示与按需排查（2026-10-29）（`<skill-root>/references/maintenance-validation.md#工具调用提示与按需排查2026-10-29`）
- execute调用形式补充（2026-10-29）（`<skill-root>/references/maintenance-validation.md#execute调用形式补充2026-10-29`）
- 常见环境与交付故障说明（2026-10-29）（`<skill-root>/references/maintenance-validation.md#常见环境与交付故障说明2026-10-29`）
- Bash执行示例统一（2026-10-29）（`<skill-root>/references/maintenance-validation.md#bash执行示例统一2026-10-29`）

## 段落排版第一批增强（2026-10-30）

版本 `20261030.1802.1732`。增加可选分页布尔字段、左右及悬挂缩进、原生制表位和普通段落文本/run 的制表符；统一生成器段落属性序列化，生成验收独立核对对应属性值、顺序、唯一性和文本。修复读取器把段落属性中的制表位误作正文 Tab 的问题。保持原主题、默认分页及旧规格语义；不改任务执行、归档、清理、编辑入口或公共生命周期实现，不增加依赖。Profile、标题自动编号、复杂表格、多节和目录不属于本批实现。

实际环境为 macOS、宿主提供 Python 3.12.14 / Expat 2.8.3，保留 `-I -S -B`。默认 `python` 不存在、系统 `python3` 为 3.9.6，均未用作验收解释器；通过宿主依赖定位取得兼容解释器后，维护解释器/检查器入口自检与 DOCX 只读自检通过，后者包含 `read-check-ok`。版本取纽约时间 2026-10-30 06:02:17.325016，整体加 12 小时并截断到百分之一秒。

以下维护命令从已确认仓库根目录执行，`python` 指上述宿主提供的兼容解释器；路径相对于仓库根，不是安装后业务示例：

- `python -I -S -B -m unittest discover -s skills/docx/scripts -p 'test_*.py' -q`：277 项通过，退出 0，无失败或跳过，包含新增 8 项段落排版测试。
- `python -I -S -B -m unittest discover -s tests -p 'test_artifact_lifecycle_parity.py' -v`：1 项通过，退出 0，公共副本及来源哈希保持一致。
- 主代理用 stdin 内存检查对比 HEAD 原生成器与本次实现：6 组合成旧规格覆盖三种主题、标题、列表、表格、页眉页脚及旧格式覆盖，全部包部件逐字节相同，退出 0；不是任意旧输入的穷举证明。
- 主代理提取生成说明中的新增 JSON 示例，通过真实标准 runner 子进程执行：退出 0、stderr 为空、主状态 verified，中文正文及 Tab 回读符合独立预期，归档副本与主文件字节相同，清理完成且清单移除。使用本轮独占维护目录和合成配置，不访问用户归档配置；完成后删除本轮独占目录。

新增测试覆盖显式 false、样式与局部缩进切换、不同位数位置的数值排序、制表位继承/clear 的 XML 表达、连续/首尾 Tab、空文本、表格文字格式、非法输入和输出保护，以及属性/顺序篡改拒绝。均为合成样本；未执行 Word/WPS 人工确认样本、视觉分页或字体替换验收。description 人工核对仍准确（135 字符），保留原文；未执行真实模型触发测试。目标 Linux 容器、Windows、专用命名 linter 和生产宿主通道联调未验证；本机生成及静态结果不代表这些范围通过。未更新分发 ZIP、提交或推送。

Subagent execution record：一次 spawn，`luna__paragraph_layout`（luna_worker / gpt-6-luna / max），独占生成器、生成验收器和新增测试；主代理发出澄清及一轮范围内排序修正，不新增代理。Main-agent work：范围与契约、文档/版本维护、读取器修复、源码与证据审查、兼容性对比、真实示例和全量集成验收、最终接受。最终严格结构、AST 及空白检查结果见本轮交付报告。

## 运行条件与命令

先按 首步自检（`<skill-root>/SKILL.md#执行环境约束与首步自检`） 确认 Python >=3.10、自带 zlib 和 Expat >=2.6.0。维护测试仅用标准库，测试产物置于独占任务目录；不安装包、调用办公软件、转换器或 OCR。

在宿主确认的任务目录执行，先替换 `<skill-root>`：

```console
python -I -S -B -m unittest discover -s "<skill-root>/scripts" -p 'test_*.py' -v
```

## 固定预期与失败保护

| 模块 | 实际覆盖 |
|---|---|
| `<skill-root>/scripts/test_docx_generation.py` | 中文和 XML 字面文字、表格、关联文字、默认样式及页面意图的固定值；占位符拒绝、预检不写文件、验证故障保留旧输出并清理暂存、UTF-8 CLI 输入、重复 JSON 键与同源拒绝 |
| `<skill-root>/scripts/test_docx_runtime.py` | 只读自检不解析输出配置、不创建目录或探针；旧Expat/zlib故障失败，参数冲突退出2；不可写任务目录中自检和真实中文读取不产生输出文件 |
| `<skill-root>/scripts/test_docx_delivery.py` | 实际 CLI 根目录主文件和配置归档副本、字节一致与回读、中文及最长文件名、摘要及配置跳过原因、双目录自检与探针清理、只读预检、非法文件名及符号链接拒绝、生成失败保护、归档复制/同步/权限设置/替换失败保留主文件和旧副本、清理失败诊断及残留定位、探针关闭失败、JSON 与正文/表格图片同源拒绝、权限保留及成功替换 |
| `<skill-root>/scripts/test_archive_deliverables.py` | 当轮显式多文件清单、跨轮更新与 ID 变化、不扫描历史、无配置跳过、同名冲突、输入硬链接及输出符号链接保护、已归档路径及部分失败 |
| `<skill-root>/scripts/test_edit_delivery.py` | 五个编辑 CLI 的实际归档、失败保护和只读批注不归档（本批执行结果见下） |
| `<skill-root>/scripts/test_output_location.py` | 两种 commonParam 表示、整数 ID、配置和字段异常、重复键/ID、非标准 JSON 常量、深层 JSON、非法 ID、固定且不泄露原文的原因码；调用独立性及无环境/文件访问 |
| `<skill-root>/scripts/test_docx_editing.py` | 替换次数、同时应用、重叠与跨 run/字段保护、同源拒绝、旧输出及非目标成员保留；结构、表格行、批注和同格式图片的成功与拒绝；实际任务目录内暂存和清理 |
| `<skill-root>/scripts/test_docx_io.py` | stored/deflate、小型有效 ZIP64 的明确拒绝；重复/别名/穿越/特殊成员/CRC 与中央目录异常；UTF-8/UTF-16、DTD/实体、深度和共享 XML 预算 |
| `<skill-root>/scripts/test_docx_routing.py` | .wps/.et/.ett 及伪装 CFB 的明确路由拒绝；原生生成→编辑→读取链路 |

ZIP64 fixture 先由标准库 ZipFile 正确读取，再验证本技能按已声明范围拒绝，不能将拒绝写成支持 ZIP64。预算测试通过缩小限额覆盖边界，不制造大资源负载。暂存目录测试检查实际创建路径和调用结束后的清理，不只匹配源码参数。

## 已执行与未验证

以下按维护批次记录，旧批次中的归档失败退出码和状态仅为当时行为，以最新附加归档契约为准；较早的配置输出路由证据属于当时实现，当前根目录主文件及归档契约以本节最新记录和生成说明为准。

2026-10-26，Python 3.12.12 / Expat 2.6.3、`-I -S -B`：51 项通过，无跳过；其中生成 9、编辑 17、ZIP/XML 20、路由及原生链路 5。ASCII locale 且 UTF-8 模式关闭时，路由及原生链路 5 项另行通过，测试输入显式使用 UTF-8。

2026-10-27，Python 3.12.14 / Expat 2.8.3、`-I -S -B` 且子进程 `PATH` 为空：81 项通过，0 失败、0 跳过；其中输出交付 13、配置解析 17、原生成 9、编辑 17、ZIP/XML 20、路由及原生链路 5。首次调用前已完成解释器、检查器入口及原生成器自检。当前命令的自检分别确认有效配置的 `output_dir=record-123/output writable=True`、配置缺失的 `output_dir=. writable=True`，均退出 0 且有 `check-ok`；阻塞目录和注入权限异常明确失败，没有切换输出位置。

另将完整 DOCX 目录复制到本次独占临时目录并设为只读，在独立任务目录及空 `PATH` 下执行自检、生成和读取：有效配置及配置缺失两种情况各重复生成 2 次，均通过集成验证及中文回读。核对输出分别为 `12345/output/report.docx` 和 `report.docx`；执行前后源码及安装副本的文件名和 SHA-256 均未变化，临时副本已清理。该检查验证目录独立安装，未构建或更新分发 ZIP。

本次六个涉及的 Python 文件通过 AST 语法检查，DOCX 严格结构检查及变更空白检查通过；新增标识符及引用已人工核对命名，未运行专用命名 linter。description 保留原文，140 个 Unicode 字符，人工核对其新建/受控编辑/读取范围与本次路径变更相符；输出路由规则放入正文，未执行真实宿主的 Skill 自动触发测试。依赖清单无需变更，主流程继续只使用标准库。

2026-10-27，公文默认与交付引导维护：仅更新 Skill、生成说明、README 和本验证记录，未修改 Python 脚本、排版数值或依赖。主入口及 README 的生成示例均显式使用 `theme: official`，两份实际示例通过 CLI 集成验收。另以省略主题、显式公文、商务、学术、自定义字体/字号/页边距共五种输入实际生成并核对 XML 字体、字号、行距、页边距及原始正文，均通过；文档正文不包含交付引导。上述操作前自检退出 0 且出现 `check-ok` 和 `writable=True`；原生成回归 9 项通过，无跳过，使用 Python 3.12.14 / Expat 2.8.3、`-I -S -B` 及空 `PATH`。

本次人工核对未指定格式、明确选择主题、自定义格式、失败/自检/预检、已有文档读取/编辑及用户仅需文件等交付情形：引导在成功交付回复中按实际格式给出，其他情形遵循主入口规则，不写入文档。它是 Skill 对助手的交互约定，CLI 不新增提示文本或修改成功摘要；未执行真实宿主中的模型交互或自动触发验收。description 的选择范围仍准确，保留原文；结构检查和人工语义核对不能证明模型每次都按规则回复。

2026-10-27，description 维护：将描述改为以制作 Word 公文/报告/信函、读取 DOCX/DOTX 内容、模板填充及局部修改等用户场景为入口，强调可交付文档、按需排版与生成内容核对；移除标准库等技术实现描述，保留 WPS 专有格式和 PDF 导出的选择边界。新描述为 129 个 Unicode 字符。人工核对新建报告、提取正文表格、填充占位符、指定内容修改及排版调整等适用场景，以及 WPS 专有格式读取、PDF 导出等不适用场景；与主入口、任务路由、生成说明、README、依赖清单和现有 CLI 能力一致。仅修改元信息、README 版本及本记录，未修改 Python 行为；本次未重复业务回归，也未执行真实宿主的自动触发测试。

较旧 Python/Expat 的跳过只说明环境不满足要求，不能计为对应行为通过。未安装或运行专用 Python 命名 linter；仓库结构检查和 AST 检查不能替代它。

这些是小型合成输入的固定语义预期；环境配置通过测试进程注入，尚未与实际路由宿主联调，也未测试 Windows 文件系统或恶意并发目录修改。不证明完整 OOXML/XSD、Word 打开兼容、字体可用、像素解码或实际分页。`--verify` 核对的是支持范围内的内容与排版意图，不是视觉验收。详细威胁模型和资源边界见 安全说明（`<skill-root>/references/security.md`）。


2026-10-27，任务自检与渐进披露维护：增加 `"<skill-root>/scripts/read_docx.py" --check`，共享Python、zlib往返和Expat/XML检查，不读取新建输出配置、不创建目录或写探针；退出0及 `read-check-ok` 是只读运行条件通过标记。原生成器 `--check` 继续检查已配置输出目录的实际写入能力及 `check-ok`，旧调用不变。读取保存结果、编辑和解包仍按实际输出目录检查写入。Python3.12.12 / Expat2.6.3、标准库隔离运行DOCX全量86项通过，0失败、0跳过；新增5项固定行为覆盖只读不写、参数冲突、旧Expat/zlib失败和不可写任务目录中的实际中文回读，旧生成交付回归保持通过。入口由259行缩到89行，条件性受控替换、验收/有限重试/清理、生成后引导分别移入已有参考；不改其规则或扩大能力。description保持129字符，仍准确区分生成、读取、受控修改和WPS/PDF边界。结构及资源链接检查通过，专用命名linter未运行；不将上述检查视为真实宿主自动触发或视觉验收。


2026-10-27，主文件与归档分离维护：CLI 主文件固定在任务工作目录，成功后复制到有效 `{idRecord}/output/`；无有效配置跳过归档。归档失败保留已发布主文件和旧副本，返回退出码 2 及 `archive_failed` 摘要。原配置解析 API 和低层生成 API 保持不变；description 仍为 129 字符，人工核对新建、读取、受控编辑与 WPS/PDF 边界后保留，依赖无变化。

本批使用宿主提供的 Python 3.12.14 / Expat 2.8.3，先通过解释器及检查器入口自检；系统 Python 未满足最低版本，未用于业务验收。由仓库根目录运行 `python -I -S -B -m unittest discover -s skills/docx/scripts -p 'test_*.py' -v`，94 项通过、0 失败、0 跳过；CLI 子进程保持空 PATH。覆盖根目录与归档字节一致、中文回读、整数零 ID、未验证摘要、配置跳过、双目录探针、归档目录与目标符号链接（含悬空链接）、输入硬链接/同路径保护、生成验证失败保留旧文件、复制及替换故障清理暂存、重复生成及最长文件名。初次测试发现旧目录列表预期未同步，修正后全量通过，未降低断言要求。

主代理另将当前完整 Skill 复制到只读安装目录，在独立任务目录中分别使用有效配置和缺失配置执行自检，各重复生成 2 次并集成验证、中文回读；有效配置的主文件与副本逐字节相同，无配置时无归档。源码及安装副本运行前后文件名和 SHA-256 不变，独占临时目录已清理。此次未修改资源名称映射、未重新打包分发 ZIP。

严格结构检查、Python AST 语法检查与变更空白检查通过；人工审阅新增 Python 标识符、调用引用、失败路径及说明一致性。未运行专用命名 linter、真实宿主自动触发/路由联调、Windows 文件系统或视觉验收；本批合成行为测试不替代这些验收。两个输出按顺序独立原子发布，不保证跨文件事务或恶意并发目录修改下的安全性。


2026-10-27，归档流程复审：主代理复现并修复三项问题：归档未检查本次输入图片同源（正文与表格图片均可能被覆盖）；归档暂存清理失败掩盖原始错误且未报告路径；写入探针关闭/清理失败后仍可能输出 `check-ok`。CLI 与低层 API 共享已验证输入的发布逻辑，归档复用图片路径，保留 API 签名及每次只读一遍图片的行为；主文件与归档均拒绝与输入图片路径/硬链接同源。归档清理失败现在同时报告原始错误、清理错误和任务内暂存路径。同步明确归档失败不回滚主文件、不自动重跑生成器，权限保留规则及失败清理的实际边界。description 人工核对仍准确，129 字符，未改动；无新增依赖。

使用 Python 3.12.14 / Expat 2.8.3、`-I -S -B` 在仓库根目录运行 DOCX 全量测试，100 项通过，0 失败、0 跳过。新增回归先复现缺陷，再通过修复；新增图片测试最初的子用例路径复用产生连带错误，改用独立路径后确认六种图片冲突均能独立复现。另覆盖归档 fsync/chmod 失败、部分复制后的清理失败、主文件图片硬链接保护及 POSIX 权限。解释器、检查器入口和独立任务目录双目录自检通过；严格结构、AST 语法和变更空白检查通过。未运行专用命名 linter、真实宿主联调、Windows 或视觉验收；不将本次合成回归解释为这些项目通过。


2026-10-28，归档目录布局调整：归档位置由 `{idRecord}/output/` 改为 `output/{idRecord}/`，主文件仍生成到任务根目录。配置解析函数的返回字段保持不变，有效 ID 的 `output_dir` 按新布局返回；配置无效仍跳过归档。主入口、生成说明、安全说明及 README 同步；已有旧布局文件不迁移、不删除。description 仍为 129 字符，人工核对适用范围无需调整；依赖不变。

Python 3.12.14 / Expat 2.8.3、`-I -S -B`：仓库根目录 DOCX 全量 101 项测试通过，0 失败、0 跳过。新布局覆盖配置解析、双目录自检、中文及整数 ID、归档失败保护、路径别名/符号链接和权限检查；新增测试确认新归档与主文件字节一致且旧布局文件不变。独立任务目录实际自检返回 0，包含 `output_dir=. writable=True`、`archive_dir=output/12345 writable=True` 和 `check-ok`，探针清理完成。严格结构、AST 语法和变更空白检查通过；新增及修改标识符人工核对，未执行专用命名 linter、真实宿主联调、Windows 或视觉验收。未更新分发 ZIP。


2026-10-28，当轮统一归档维护：五个编辑 CLI 写操作成功后自动归档本次输出，保留相对输出路径和低层 API；批注只读 list 不解析归档配置或调用归档函数。新增 `<skill-root>/scripts/archive_deliverables.py` 对当轮其他交付物使用显式文件清单归档，支持多文件和不同扩展名，不扫描历史/中间文件；目标为 `output/{idRecord}/文件名`，每次调用重新读取配置。编辑前保护所有源文档、操作 JSON、选择文件和替换图片；归档前保护输入及其他交付物，拒绝扁平化同名冲突，已在目标路径的文件标为 `already_archived`。复制复用现有原子发布与失败清理，批量部分失败逐项报告，整体退出 2，不回滚已完成的文件。编辑 CLI 的成功输出改为包含原 `result` 的归档 JSON，主入口和各任务参考已同步。description 的新建/读取/受控编辑选择范围仍准确，129 字符，保留原文；依赖保持标准库。

Python 3.12.14 / Expat 2.8.3、`-I -S -B`，在仓库根目录执行 `python -I -S -B -m unittest discover -s skills/docx/scripts -p 'test_*.py' -q`：115 项通过，0 失败、0 跳过。新增通用归档 6 项与编辑交付 8 项测试覆盖五个真实 CLI 的语义与归档字节一致、跨轮 ID 更新、编辑失败不归档旧结果、复制失败保留新主文件/旧归档/输入且清理暂存、缺配置跳过、只读批注不归档、输入别名与同名冲突、显式多文件部分成功。测试初次调用曾因 unittest 模块路径错误失败，改用 discovery；故障注入曾误覆盖编辑打包复制，收窄到本次主文件的归档复制后验证，未降低断言。

主代理实际执行显式清单 CLI 自检与中文文本/JSON 两文件归档，输出字节一致；另将完整 Skill 置于独立只读安装副本，在任务目录完成自检、新建、编辑、归档及中文回读，源文档和安装副本文件名/SHA-256 不变。任务临时目录已清理。严格结构、相关 Python AST 和变更空白检查通过；主代理已审阅实现、标识符、依赖及证据。未执行专用命名 linter、真实宿主自动触发/路由联调、Windows 或视觉验收，未更新分发 ZIP。


2026-10-28，归档改为附加步骤：主文件生成后才读取生成器归档配置，生成/验证成功的 `status` 保持 `created`/`verified`；编辑 CLI 成功保持 `edited`；显式归档处理完成为 `completed`。归档不可写、路径保护拒绝、复制/清理失败或配置解析异常均不改变主流程成功状态和退出码 0，只返回独立 `archive_status` 及 `warnings`，CLI 输出 `archive-warning`。归档自检不可写只警告；运行条件或主输出目录自检失败仍非零、无 `check-ok`。所有输入/图片/操作文件/链接保护、原子发布和旧归档保护保持有效；只停止违规归档，不绕过保护来求副本成功。更新主入口、生成/统一归档/安全说明及 README；description 人工核对仍准确，129 字符，保留原文，依赖不变。

使用 Python 3.12.14 / Expat 2.8.3、`-I -S -B`，从仓库根目录运行 `python -I -S -B -m unittest discover -s skills/docx/scripts -p 'test_*.py' -q`：118 项通过，0 失败、0 跳过。交付相关 45 项覆盖主目录与可选归档探针的不同失败语义、归档解析器异常、生成/编辑成功状态、复制/替换/同步/权限/清理失败的警告与旧文件保护，阻塞归档目录下实际自检、新建及五种编辑仍成功；主流程非法输入、同源保护与验证失败仍非零。主代理另在独立任务目录将 output 设置为普通文件，验证自检、新建、编辑、中文回读均成功且阻塞文件不变。临时任务文件已清理。主代理完成代码及证据审阅、AST 语法、严格结构和变更空白检查。未执行专用命名 linter、真实宿主联调、Windows 或视觉验收，未更新分发 ZIP。


2026-10-28，历史 requestId 参数统一与归档复审（已被后续 questionId 契约替代）：该批实现仅识别 commonParam 列表中 name 为 `requestId` 的参数，当时以该参数值作为 output 目录下的归档子目录名；内部变量/函数/常量及 missing/duplicate/invalid_request_id 原因码同步。旧参数 `idRecord` 不作兼容别名：仅旧参数时主文件成功且跳过归档；新旧同时出现只使用新参数，重复或无效的新参数不回退旧值。历史记录保留当时名称，现行说明、README 和测试均使用新契约。description 仍为129字符，人工核对适用范围不变，依赖无新增。

主代理审阅配置校验、目录自动创建、原子复制、失败清理、输入同源/符号链接及旧归档保护，未发现需要额外改动的归档发布逻辑。新增测试覆盖未自检且目录不存在时自动创建并复制、注入真实 mkdir 权限异常时自检和主文件生成仍成功（无旧归档及有旧归档两种情况），强化复制测试为写入部分字节后失败，核对主文件可读、输入字节与旧归档不变、暂存已清理。既有五个编辑 CLI、显式多文件、图片保护与失败返回语义继续通过。

Python 3.12.14 / Expat 2.8.3，仓库根目录执行 `python -I -S -B -m unittest discover -s skills/docx/scripts -p 'test_*.py' -q`：122 项通过，0失败、0跳过。解释器及检查器入口自检通过；独立任务目录实际 `"<skill-root>/scripts/create_docx.py" --check` 返回0、`check-ok` 和 `archive_dir=output/req-123 writable=True`，确认按 requestId 创建目录并清理探针。相关 AST、严格结构和变更空白检查通过。主代理完成所有实现、审查和验证，本批未调用子代理。未执行专用命名 linter、真实宿主联调、Windows 或视觉验收；未更新打包文件。


2026-10-28，统一代码交付入口：新建及五种编辑 CLI 改为调用对应公开 `*_delivery()` API，主操作成功后统一通过 `finalize_delivery()` 调用 `archive_outputs()`；归档异常独立返回警告，保留主文件成功状态。低层函数保持原签名及不自动归档行为，供中间处理使用；只读批注查询不触发归档。新建 CLI 保留单文件字段并增加 `files` 明细；归档预检查失败时顶层 `archive_output` 为 null。归档保护错误使用统一输入冲突诊断，既有故障注入同步定位到共享入口，保留实际输入/旧归档字节保护断言。说明、API 示例和 README 同步；description 人工核对仍准确，129 字符，依赖不变。

主代理负责实现、文档、审查及集成验收；`luna__delivery_api_tests`（luna_worker）仅新增 `<skill-root>/scripts/test_delivery_api.py`，11 项测试通过，主代理已审阅。新增覆盖新建和五种编辑真实调用自动归档且只调用一次、可读取内容与副本字节一致、输入/JSON/图片别名保护、复制与 mkdir 失败保留主文件和旧归档、主操作失败不归档、统一归档入口意外异常隔离、底层函数不归档及只读查询。初次回归暴露诊断和模拟注入位置变更，以及文字编辑 CLI 未传入操作文件保护清单，已修正；无效配置测试按已有 skipped 契约核对，未降低失败保护断言。

Python 3.12.14 / Expat 2.8.3，仓库根目录执行 `python -I -S -B -m unittest discover -s skills/docx/scripts -p 'test_*.py' -q`：133 项通过，0 失败、0 跳过。独立任务目录的新建、只读及归档自检均退出 0 且含必需成功标记；AST、严格 Skill 结构检查和变更空白检查通过。人工检查新增 Python 命名与调用引用，未执行专用命名 linter、真实宿主交付钩子/模型路由评测、Windows 或视觉验收。没有新增宿主拦截能力，不自动归档任意外部代码写出的文件；未更新 ZIP。


2026-10-28，统一归档复审：主代理核对六种 CLI/公开交付 API 到统一入口的调用、主文件发布前的输入保护、归档异常隔离和同名更新行为，未发现新的运行逻辑缺陷。修正新建 CLI 帮助中的“可选归档”为“自动尝试归档”，避免把失败可忽略误解为调用可省略。新增 CLI 故障注入测试确认新建及文字编辑在统一归档函数抛异常时仅调用一次、退出 0、主状态保持成功、正文可读取且源文件不变。

仓库根目录执行 `python -I -S -B -m unittest discover -s skills/docx/scripts -p 'test_*.py' -q`：134 项通过，0 失败、0 跳过；新建/只读/归档环境自检、变更 Python AST、严格结构与差异检查通过。description 适用范围仍准确，无需修改。此次审查及修复均由主代理完成，未调用子代理；未执行专用命名 linter、真实宿主交付钩子/模型路由、Windows 或视觉验收，未更新 ZIP。


2026-10-28，文件定位说明统一：主入口集中定义任务工作目录及输入、主文件、归档、图片和内部资源的路径基准，参考说明统一引用。移除“下载工作目录”“文档工作目录”的歧义称呼；明确新建输出位于根部、编辑输出可用相对子目录，后续读取使用实际 output。正文和表格图片均区分 CLI 的 JSON 所在目录与字典 API 的 base_dir（省略为任务目录）；替换图片参数相对任务目录，ZIP 成员名不是磁盘路径。补充子目录 JSON 的 CLI/API 示例，保留归档默认不读内容及必要检查例外。说明属于 Skill 调用边界，不声称底层 API 全部实现目录沙箱。description 人工核对仍准确，129 字符，无需修改，运行代码及依赖不变。

主代理在独立任务目录完成新建、只读及归档自检；实际运行子目录 JSON 预检/生成、API 显式与省略 base_dir、编辑子目录输出、按返回 output 读取保存、替换图片及扁平归档验证。任务根部与 JSON 目录使用不同图片字节，核对 DOCX 媒体及副本字节，确认实际读取基准；临时文件已清理。严格 Skill 检查及变更空白检查通过。此次仅说明调整，未重复全量单元测试，未执行模型多轮路径选择评测、Windows 或视觉验收，未更新 ZIP。未调用子代理。

## 任务清理工具验证

2026-10-28，新增标准库 `<skill-root>/scripts/cleanup_task.py`：创建辅助文件前显式登记，任务结束时记录身份与指纹，再逐项清理。成功任务先完成文档验收及归档步骤；归档失败仍执行收尾，最终失败/停止可保留必要恢复材料，retry 不删除候选。用户输入、最终文件、清单及其重叠路径受保护；不扫描目录，目录只用 rmdir 移除。清理前后核对最终文件，返回 deleted/retained/failed 与原因，保留原指纹和清单供中断后恢复。主入口、收尾说明、归档说明、工具指南及 README 已同步，版本更新；description 人工核对仍准确，129 字符，未修改，无新增依赖。

使用 macOS、Python 3.12.14 / Expat 2.8.3，在仓库根目录运行 `env PATH='' python -I -S -B -m unittest discover -s skills/docx/scripts -p 'test_*.py' -q`（python 使用宿主已确认的绝对解释器路径）：151 项通过，0 失败、0 跳过。新增 `<skill-root>/scripts/test_cleanup_task.py` 共17项，覆盖真实生成后归档失败仍清理、最终失败/停止、重试保留、删除权限异常、目录混入其他文件、用户输入/最终文档保护、硬链接/符号链接/父目录替换、候选变化、最终文件前后变化、清单错误/独占创建、平台能力缺失、回执写入失败及 CLI 状态码。子进程在首项删除后 os._exit(17)，验证原 sealed 清单保留且再次清理可恢复；这是进程中断模拟，不代表断电耐久性验证。

主代理另在独立任务目录执行新建、只读及归档自检，均返回0及必需成功标记；实际生成中文 DOCX、成功归档、清理辅助脚本并回读，确认主文件及副本字节不变、输入不变、清单保留。首次验收脚本误把归档自检标记写为 archive-check-ok，实际入口返回 check-ok；修正验收脚本后通过，未修改运行入口或降低要求。此次测试和烟测创建的临时目录均已清理。

新增 Python 的 AST 语法、严格 Skill 结构及变更空白检查通过；主代理人工审阅命名、调用引用、删除边界及验证证据。专用命名 linter 不可用，未执行；未验证 Windows、敌对并发写入、真实断电、宿主自动调用/模型路由或视觉版式，不将清理工具视为这些能力的保证。工具要求助手显式调用，不自动推断任意脚本的输入归属；未更新分发 ZIP。本批全部实现及审查由主代理完成，未调用子代理。


2026-10-28，适配实际宿主配置：`ROUTER_CONFIG_VARIABLES` 支持外层 name/value 参数列表，提取唯一 commonParam 项的 value，再解析其 JSON 编码内层列表取得唯一 requestId。保留旧字典外层及内层已解码列表兼容。外层参数项不合法、缺失或重复 commonParam 分别返回 invalid_router_config_entry、missing_common_param、duplicate_common_param；两层 JSON 的重复键、非法常量、内层 ID 与路径校验继续执行，不回退旧 idRecord。主文件/归档路径保护、失败警告及默认调试日志不变。主入口、生成/归档说明和 README 同步，description 人工核对仍准确，依赖不变。

先复现用户示例返回 invalid_router_config_shape，再完成修复及新增四项解析测试；新建、编辑和交付 API 测试配置改用宿主实际外层列表，使既有成功、失败及输入保护场景同时覆盖真实格式，旧格式解析测试继续保留。Python 3.12.14，仓库根目录运行 `python -I -S -B -m unittest discover -s skills/docx/scripts -p 'test_*.py' -q`：156 项通过，0 失败、0 跳过。独立任务目录使用用户完整示例验证运行自检、新建、编辑、回读及 output/abc123 下副本字节一致；缺失/重复外层参数仍保持主文件 verified、归档 skipped、旧归档不变。相关 AST、严格 Skill 检查及差异检查通过；任务临时目录已清理。主代理完成实现与验收，未调用子代理。未执行专用命名 linter、真实宿主集成、Windows 或视觉验收；未更新 ZIP。


2026-10-28，归档参数改为 questionId：解析匹配、内部常量/函数/变量、日志原因码、现行说明、README 和测试配置统一更新。仅旧 requestId 或 idRecord 时跳过归档，原因 missing_question_id；新旧同传仅取 questionId，新值无效或重复不回退。两种外层配置格式兼容、路径校验及原子复制行为保持不变。实际验证发现共享归档入口直接读取环境，绕过此前的调试函数；改为共用 read_router_config_from_environment，补充实际生成日志回归，默认调试日志输出宿主真实配置，不改写其中旧字段。归档失败仍不影响主文件成功。

仓库根目录执行 `python -I -S -B -m unittest discover -s skills/docx/scripts -p 'test_*.py' -q`：159 项通过，0失败、0跳过；新建/只读/归档自检通过。实际使用外层列表和兼容字典两种 questionId 配置完成新建、编辑、回读、日志核对与 output/abc123 副本字节一致性验证。相关 AST、严格结构和差异检查通过；description 人工核对仍准确，依赖不变。本批由主代理实现及验收，未调用子代理；未运行专用命名 linter、真实宿主集成、Windows 或视觉验收，未更新 ZIP。


## 归档用户提示验证（2026-10-28）

本节仅保留历史实现与测试证据，不作为当前回复模板；现行成功和失败话术统一见用户展示规则（`<skill-root>/references/delivery.md#用户展示与机器元信息`）。

历史版本 `20261028.2205.5362` 当时将用户展示与内部机器契约分开：原有归档路径、files、warnings、CLI JSON/stderr均保留；新增archive_message及纯函数archive_user_message，机器字段在完整成功时返回“归档完成”，跳过/失败/不确定不误报成功。主文件路径和正常交付链接不移除。入口和相关引用明确不将内部归档路径、ID和原始日志展示给用户。

Python3.12.12/Expat2.6.3、标准库 -I -S -B 下DOCX全量163项通过，无失败或跳过；新增4项实际清单/交付行为覆盖成功和already_archived提示、保留机器路径、部分失败、无配置跳过、不完整状态及意外归档异常。新增成功提示不含路径，失败不冒充成功，故障仍保留主文件及内部详细诊断。结构与语法检查另行执行，不宣称真实宿主回复端已联调。专用命名linter未安装或运行；未更新分发ZIP。


## 自动新建收尾验证（2026-10-28）

当前版本 `20261028.2224.4840` 标准新建使用`<skill-root>/scripts/run_docx_task.py`的check/create、按需retry/resume；创建前登记独占worker.py/spec.json，创建后保存身份和指纹，固定子进程强制文档验收及占位符检查，归档结果返回且worker退出后自动seal/clean。保留旧CLI/API和手动清单接口，不接管用户已有或未登记文件，不扩展跨技能公共模块。

Python3.12.12/Expat2.6.3、-I -S -B下DOCX全量181项通过，0失败、0跳过；新增18项覆盖真实中文DOCX、两个helper移除、失败保留/修正重试、既存同名/输入/未登记保护、指纹及软硬链接、最多3次生成、活动子进程继承锁、重复恢复、seal/部分清理故障、父进程在seal后os._exit(17)的实际中断恢复，以及缺平台能力/真实锁操作失败的自检拒绝。所有删除探测只操作测试创建的独占临时数据，不清理用户实际残留。

独立复核发现新入口最初只做旧生成器自检、未核对自动清理的平台能力，已修复并实际验证；还发现主入口可能引导自动成功后重复seal，已明确自动路径只核对回执、其它流程才手动收尾。独立只读安装副本以空PATH完成check、stdin生成、重复resume与中文回读，确认用户输入/未登记Python和JSON保留、helper移除，源码及安装副本哈希不变。独立测试和代码审阅后无未修阻断发现。

仅验证macOS/POSIX锁与目录描述符能力，不声明Windows、敌对并发写入、真实断电或宿主自动触发支持。初始化/回执不完整且已有主文件时保留材料并要求恢复核实，不盲目重生成；manifest保留作最小恢复记录。专用命名linter不可用未运行，执行AST、手工标识符/调用检查、strict及本地资源链接核对；未生成发布ZIP、清理历史包、提交或推送。仓库维护证据位于tests/docx_auto_task_verification.json，不属于独立Skill运行依赖。


## 重试事务与控制器可靠性验证（2026-10-28）

版本 `20261028.2341.0954` 修复三项已复现问题：retry规格与清单跨文件更新使用预登记stage及preparing/prepared日志，保存结果不确定时重读持久化状态，只恢复记录的旧/新精确身份；控制器在稳定任务根目录锁内加载并验证最新phase、attempts和指纹，包含sealed恢复，worker继承锁；新增docx_task_state对manifest/receipt/config的对象类型、schema、必需字段、非布尔次数、指纹及验收记录做预校验，损坏记录结构化失败，不生成、删除或重置。

Python3.12.12/Expat2.6.3、标准库 -I -S -B：DOCX全量192项通过，0失败、0跳过。本轮新增9项可靠性测试及2项旧入口兼容测试，覆盖清单保存失败、prepared已提交后异常、preparing/prepared/spec替换后进程退出恢复、两个独立控制器屏障交错、损坏清单/回执、未知修改拒绝、旧四文件清单登记stage或拒绝已占用stage，以及sealed恢复锁。原18项自动任务测试继续通过。独立审阅实际复跑9+18项并检查新实现，无未修阻断。

旧`<skill-root>/scripts/create_docx.py`仅生成CLI标记deprecated，以stderr提示迁移到`<skill-root>/scripts/run_docx_task.py`；JSON stdout、结果、退出码与旧调用兼容。库create_document/create_document_delivery仍被新runner或定制/多步调用使用，不弃用；五类编辑、读取、自检、预检及显式清理尚无完整替代，均保留。只读仓库调用盘点不能证明外部调用者已迁移；未来移除旧生成CLI须先确认外部迁移、等价定制流程与明确版本公告，不能据本次提示直接删除。

strict、122条本地资源/锚点和Python AST通过，新增标识符及调用人工复核；专用命名linter未安装或执行。仅在独占合成临时目录验证，不清理实际残留或历史ZIP。新分发包另核对CRC、源码逐成员SHA及只读安装副本，新标准入口check/失败retry/重复resume/中文回读与完整回归独立执行；包SHA保存在仓库维护验收记录，不写入包内造成自引用。运行平台为macOS；未验证Windows、敌对并发、真实断电或宿主自动激活。准备期强杀在身份记录前等不确定状态仍保留材料，不猜测认领。


本批生成性能审查：在同一独立本地任务目录、同一标准库解释器、有效合成 questionId 配置且 debug=0 下，比较兼容 CLI（verify＋require-no-placeholders＋归档）和标准自动入口（同等验收归档＋子进程＋清理）。每组3次取中位数：8段约2KiB规格为0.0667s/0.1422s；300段约388KiB规格为0.0797s/0.1600s；1000段约1.94MiB规格为0.1757s/0.2529s。均验收成功、归档字节一致，自动清理完成；文本重复且压缩率高，不代表真实图片或复杂文档。自动自检约0.0668s，兼容自检约0.0504s。另一次300段自动入口API采样总0.0947s，子进程等待0.0857s；父进程累计13次fsync约0.0004s、48次inspect约0.0022s，包含关系不作求和。结果说明本地脚本新增约0.08s固定成本，不能据此归因用户端数秒/分钟延迟；网络盘、宿主工具调度及模型准备时间未测。

说明审查发现主路由要求全文读取大型生成参考，且参考前部以弃用CLI示例为主；按普通生成使用旧命令的措辞与自动入口冲突。主入口增加普通文字文档最短路径与最小规格，默认不读取实现、恢复和维护验证历史，条件不变的本轮自检及成功后的集成验收/归档/清理不重复；复杂功能按需查对应章节，失败才读恢复说明。同步限定兼容CLI的JSON图片基准，标准stdin入口仍以任务目录为图片基准。运行代码、所有权/输入保护、强制验收、归档和清理机制均未改；默认debug=1继续保留。合成32KiB配置在debug=1时单次check stderr为32977字节，debug=0时为0，仅证明日志量增长，未测模型处理耗时。

使用 `python -I -S -B -m unittest discover -s skills/docx/scripts -p 'test_run_docx_task.py' -q`：18项通过，0失败、0跳过。新旧实际自检、上述18次真实生成、一次性能采样及日志体积核对完成，独占临时目录均已清理；严格结构与差异检查通过。仅修改说明，未重复全量测试或Python命名检查；description人工核对仍准确、依赖不变。未进行真实宿主/模型端到端耗时评测、Windows或视觉验收，未更新ZIP。本批审查、说明修正及验收由主代理完成，未调用子代理。


## 安全维护验收


维护时先补小规模失败回归，再修复；在 `-I -S -B`、第三方不可见且 `PATH` 为空的环境中运行仓库测试。固定预期覆盖及命令见 固定预期覆盖（`<skill-root>/references/maintenance-validation.md#固定预期与失败保护`），包括生成、编辑、ZIP/XML 和路由；不要假定旧的仓库 `skills/tests/` 目录存在。需要解包的维护流程把暂存目录放在任务 cwd 下，不能沿用系统临时目录。同步正式包，验证成员逐字节一致，解包后重新运行测试和中文生成/编辑/读取冒烟。恶意样本及输出只放在任务临时目录，不访问无关文件，不使用大负载攻击证明限额。


本批参考范围拆分：业务validation.md仅保留现行验收、有限重试和清理收尾；维护回归命令、固定预期覆盖和历史验证记录逐字迁移至本页，安全维护验收一并迁入。业务路由只链接现行规则，主入口维护区和README维护测试区指向本页；清理业务说明移除历史证据链接。业务使用questionId，旧requestId/idRecord仅保留弃用说明，拒绝测试不变；不改运行代码、依赖或description。

实际验证：严格Skill检查和差异格式检查通过；从主入口排除明确维护区后遍历业务Markdown链接，可达12个文件，不能到达本页；现行业务中requestId仅存在于明确弃用说明。迁移时确认原维护/历史正文逐字保留。运行test_output_location.py的24项及test_docx_delivery.py的34项回归，共58项通过，0失败、0跳过。仅说明调整，未重复全量测试、真实宿主模型读取评测、Windows或视觉验收，未更新ZIP。由主代理完成拆分与验证，未调用子代理。


## 临时文件平铺维护（2026-10-28）

版本 `20261029.1048.3900`：按用户要求，所有新建运行时临时文件直接位于宿主任务根目录 `./`。自动runner采用v2前缀文件，无工作目录；新plan拒绝临时目录、嵌套文件和嵌套清单。旧v1按原清单resume/清理，拒绝retry及worker重新生成，不搬迁已有文件。清单更新、探针、主文件及归档发布暂存、目录批注恢复ZIP均落在根部。编辑、批注、合并和修订对比使用有界内存包；明确请求的解包目录作为输出直接写入，不作为临时工作区。目录批注改为根目录ZIP备份及逐部件发布/回滚，仍不承诺单一原子多文件事务。内存打包保留成员/大小门禁、落盘CRC检查及既有输出权限规则；跨文件系统replace失败不回退到目标子目录暂存。归档目录自检明确为路径/访问权限检查（access-only），实际复制结果决定归档成功。

主入口、生成、交付、安全、清理和验收说明与README已核对；description人工核对仍准确，未改任务触发范围，未做真实模型触发测试。仅标准库，依赖不变。公共artifact-lifecycle的共享源码、版本与来源哈希同步，不修改旧分发ZIP。

实际平台macOS 27.2 arm64，Python 3.12.14 / Expat 2.8.3；仓库根目录执行，Python保留 `-I -S -B`。系统python不存在，系统python3版本不足；确认改用宿主依赖工具返回的兼容解释器后自检通过。以下python均指该已确认入口，不是对任意系统解释器的保证。

- 主代理执行unittest discover发现DOCX测试，并明确排除两个由子代理负责的自动任务/可靠性模块：177项通过，退出0。首次集成检查因旧目录断言及尚未落盘的runner出现失败，按新规则修正并保留等价保护断言后复验。
- `python -I -S -B -m unittest discover -s skills/docx/scripts -p 'test_flat_temporary.py' -v`：最终7项通过，退出0；涵盖不创建暂存目录、根目录文件清理、显式解包/CLI往返、批注回滚及恢复失败保留ZIP、内存预算、落盘损坏拒绝发布、权限保护。
- `python -I -S -B skills/docx/scripts/test_run_docx_task.py`：20项通过，退出0（luna__flat_runner执行）。
- `python -I -S -B -m unittest discover -s skills/docx/scripts -p 'test_docx_task_reliability.py' -v`：10项通过，退出0（luna__flat_reliability_tests执行）。该套首次运行持有了错误的helper锁，按实际controller根目录锁修正测试后通过。
- `python -I -S -B -m unittest discover -s skills/artifact-lifecycle/scripts -p 'test*.py' -v`：14项通过，退出0。
- `python -I -S -B -m unittest discover -s tests -p 'test_artifact_lifecycle_parity.py' -v`：1项通过，退出0。
- `python -I -S -B skills/skill-development-standards/scripts/check_skill.py skills/docx skills/artifact-lifecycle --strict`及变更空白检查通过。测试数量分套记录，专项与主流程有重叠，不累加为独立测试总数。

主代理决定架构、行为及兼容边界，审阅全部diff和测试证据并作最终验收。子代理执行记录：luna__flat_runner（luna_worker / gpt-6-luna / max）负责runner、状态校验及自动任务测试，因会话中断恢复一次，并完成一次范围内修正；luna__flat_reliability_tests（同配置）仅负责可靠性测试。未委派审查或审计。

测试使用隔离的宿主测试根目录，运行时代码不创建临时工作子目录。未在云端Linux、Windows、真实用户文档、恶意并发、真实断电、真实跨文件系统挂载或宿主自动触发中验收；未运行专用Python命名linter。源码命名和引用人工审阅，严格结构检查不替代命名检查。无新增第三方依赖，未更新发布ZIP。


## 自动环境判断维护（2026-10-28）

版本 `20261029.1110.2900`。用户要求把环境自检和新建执行合并到代码，必要条件失败才阻断环境阶段；缺少归档配置、归档不可用及收尾失败只提示。标准create/retry现自动调用task_environment，不要求模型另行调用check或逐项解读成功标记。必要条件为Python及自带zlib/XML、Expat、实际文件锁、安全描述符文件操作和任务根目录探针写入/同步/读回；失败在辅助文件登记及生成前返回environment_failed，environment.errors记录检查项和原因。诊断check复用同一判定，只作可选用途，不提前读取归档配置。探针在必要检查完成后的关闭/清理异常作为warning，避免以非必要收尾失败阻断生成。共享运行条件检查补充zlib及XML异常的具体诊断。

已验收回执可信且主文件指纹不变时，交付状态保存、seal或clean异常以及清理未完成均保持verified，标准runner退出0，cleanup_complete=false、cleanup_incomplete及warnings如实报告；异常中断的清理另标记state_unknown，不以空删除数组证明没有发生删除。清理前后继续核对主文件；文件缺失/改动、无可信回执、参数/内容验收错误或不安全的输入输出仍按拒绝契约返回失败，不把不存在或未经确认的文件当作成功交付。独立cleanup_task CLI的1/3退出契约保持原样。缺少/无效ROUTER_CONFIG_VARIABLES、归档路径阻塞和复制失败由原归档代码处理，均不阻断已验收主文件。未重复生成来掩盖归档或清理失败。

规则已同步主入口、生成/验收/清理/安全引用及README。description人工核对仍准确，不改变新建、有限编辑和读取的任务范围；依赖不变。未修改开发规范或公共生命周期核心，未更新分发ZIP。

实际平台macOS 27.2 arm64；宿主确认的Python 3.12.14 / Expat 2.8.3，命令使用 `-I -S -B`，工作目录为仓库根。使用宿主依赖入口，系统Python不被当作兼容解释器。环境自检的失败模拟分别核对没有生成主文件、manifest或辅助文件；成功分支实际生成并读取合成中文DOCX，不仅检查结构或mock返回值。

- 主代理以unittest discover发现所有DOCX测试，排除委派的test_run_docx_task与test_docx_task_reliability两个模块：179项通过，退出0。
- 主代理运行 `python -I -S -B -m unittest discover -s skills/docx/scripts -p 'test_docx_runtime.py' -v`：补充运行条件异常原因后6项通过，退出0；与前项有重叠，不累加。
- luna__auto_gate_tests运行 `python -I -S -B -m unittest discover -s skills/docx/scripts -p 'test_run_docx_task.py' -v`：26项通过，退出0，无跳过；`python -I -S -B -m unittest discover -s skills/docx/scripts -p 'test_docx_task_reliability.py' -v`：14项通过，退出0，无跳过。后者初次因测试辅助路径使用不当失败，修正后复验通过，未放宽运行时代码保护断言。
- 主代理运行 `python -I -S -B skills/skill-development-standards/scripts/check_skill.py skills/docx --strict`、`python -I -S -B -m unittest discover -s tests -p 'test_artifact_lifecycle_parity.py' -v`（1项）及git diff --check：均通过。

主代理负责环境/执行/退出策略、运行时代码、规则、集成及最终审查；luna__auto_gate_tests（luna_worker / gpt-6-luna / max）只执行两个测试模块的维护与验证，另跟进一次确认实际解释器及无跳过证据，未重复测试或编辑文件。主代理人工审阅diff、名称引用及验证证据并接受结果；未委派审查或审计。

未执行专用Python命名linter、云端Linux/Windows验收、真实用户样本、真实模型选技能、真实断电或敌对并发测试；不将macOS合成测试当作这些范围的通过证据。测试任务根目录隔离，运行时临时文件继续直接放在各自 `./`。


## 回执清理异常修复（2026-10-29）

版本 `20261029.1434.2500`。修复前主代理用真实合成DOCX和精确inode故障注入复现：worker用new-save发布回执后，删除同inode暂存别名失败，回执有两个硬链接；主文件可读、回执result=verified且zip_crc=true，但runner仍返回failed/recovery_pending。原有220项测试和结构检查均通过，说明缺少这条路径的覆盖。

本次改为exclusive_v1回执协议：在原plan预登记的回执路径O_EXCL/O_NOFOLLOW独占写入，flush/fsync/关闭；不创建回执暂存硬链接或执行其删除。创建时inode与写后单链接数、预期字节SHA吻合后，完整receipt_fingerprint才提交原manifest。父进程在worker退出后重新读取证明，逐项核对回执身份/大小/mtime/links/SHA、验收字段和辅助文件锚点、主文件指纹。worker非零退出或超时（已终止并等待）时，仅已持久化且核验通过的回执可恢复交付成功并报告worker-warning；警告写入交付记录，清理继续按真实结果报告。未提交证明、部分写入、回执篡改、相同字节不同inode替换、额外硬链接或主文件变化仍拒绝，不重采未知指纹。只要已经记录回执证明，即使文件丢失，也禁止retry重新生成覆盖历史状态。

现有v1和不含exclusive标记的历史记录按原兼容规则resume；不扫描或认领旧随机暂存别名，不放宽旧回执的单链接拒绝。一般TaskFiles.save及公共生命周期核心未修改，不引入公共副本分叉或新增第三方依赖。主入口、清理/安全/验收规则及README已同步。description人工核对仍准确，任务触发范围未变；未做真实模型触发验收，未更新分发ZIP。

验证平台macOS 27.2 arm64，Python 3.12.14 / Expat 2.8.3。使用宿主确认的bundled Python，保持 `-I -S -B`，工作目录为仓库根；解释器与结构检查器入口自检退出0。测试文件在隔离的测试任务根目录运行，运行时数据直接位于各自的 `./`；测试数据为标准库合成中文DOCX及固定故障注入，没有读取真实用户残留或认领未知历史文件。

- `python -I -S -B -m unittest discover -s skills/docx/scripts -p 'test_docx_receipt_cleanup.py' -v`：luna__receipt_tests执行，7项通过、退出0；涵盖原暂存别名删除故障、证明提交后的晚期错误及CLI退出0/只生成一次、回执/主文件篡改、同字节inode替换、未知额外硬链接、预存回执和证明未提交。初期测试钩子及路径参数错误导致失败，修正测试后复验，未降低保护断言。
- 主代理审阅新增测试、状态校验与运行时代码，并补充已记录证明但文件丢失时禁止重新生成的回归；v1合成fixture移除真实v1不存在的新协议字段，保留旧恢复断言。
- `python -I -S -B -m unittest discover -s skills/docx/scripts -p 'test*.py' -q`：主代理完整集成验证228项通过，0失败、0跳过，退出0。
- `python -I -S -B skills/skill-development-standards/scripts/check_skill.py skills/docx --strict`、`python -I -S -B -m unittest discover -s tests -p 'test_artifact_lifecycle_parity.py' -v`（1项）及git diff --check：均通过。

Subagent execution record：仅luna__receipt_tests（luna_worker / gpt-6-luna / max）负责新测试文件的执行与验证；主代理保留协议、兼容和风险决策，负责运行时代码、规则、集成审查与最终接受，没有委派审查或审计。已有其他修改均保留。

未运行专用Python命名linter；相关标识符、引用及协议字段人工核对，结构检查不替代命名检查。未在目标云端Linux、Windows、真实断电、恶意同权限并发写入、真实用户格式样本或只读安装副本中验证；本机合成回归不代表这些平台或场景已通过。部分写入及未提交证明有意保留为未确认状态，不承诺任意中断都能自动交付。


## 参数两端空白裁剪（2026-10-29）

版本 `20261029.1442.4400`。按用户要求，在get_common_parameter唯一参数值返回前，对str执行strip；内部内容、JSON非字符串类型和嵌套容器值保持原样，不修改参数名、原始环境变量或原始配置调试日志。questionId使用裁剪后值执行原有ASCII/长度/设备名/路径校验，空白字符串变为空串后仍无效。重复项检查在裁剪前按参数名执行，未放宽重复键、重复参数或非法JSON契约。

同步公共artifact-lifecycle的router_config.py自包含副本、两个Skill版本、来源哈希及规则说明，更新README中这两个Skill的版本；description人工核对仍准确，未改变任务选择范围或依赖。不改其他Skill，不更新既有ZIP。

macOS / Python3.12.14，使用已确认的宿主bundled解释器，保留-I -S -B；仓库根运行。解释器/检查器入口自检退出0。test_output_location.py的25项、test_runtime_modules.py的7项通过；公共test_lifecycle.py的15项通过（包括带首尾空格/制表符/换行的questionId实际归档到裁剪后目录且字节相同）；tests/test_artifact_lifecycle_parity.py的1项通过；两个Skill严格检查及git diff --check通过。各命令均退出0。原来把leading/trailing空格当作非法ID的两项示例移到正向边界测试；补充内部空格、裁剪后路径和设备名仍拒绝的独立预期，未删除安全校验。通用参数测试覆盖两种外层和两种内层表示、Unicode空白、中间空格、全空白、非字符串值、嵌套容器与原始环境值不变。

本轮为小范围参数规范化，未重复DOCX全量回归；未运行专用Python命名linter、目标云端Linux/Windows、真实宿主模型触发或真实用户文档验收。相关命名及引用由主代理人工审阅。Main-agent work：需求解释、实现、规则同步、测试、差异审查和最终接受均由主代理完成。Subagent execution record：未调用子代理。

## 工具调用提示与按需排查（2026-10-29）

版本`20261029.1629.2399`。用户报告Qwen/DeepSeek调用execute时kwargs为空。主入口提前普通新建四步，区分执行工具参数与文档stdin规格，明确schema优先、完整非空参数、一次修正和未知状态不盲目重发；增加双层JSON示例及宿主流式聚合/参数门禁排查指南，仅在故障或接入维护时读取。保留文件定位锚点，将完整路径表移至controlled-flow.md并修正相对链接；编辑路由与已有统一入口对齐。原有受控流程、脚本、测试及其他未提交修改保留，本轮没有改运行时代码、依赖、权限或清理实现，没有宿主execute实现可供修改。

description人工核对仍准确，任务范围未变化，保留原文；未作真实模型触发测试。README版本及入口说明同步。版本采样为宿主确认的America/New_York时间2026-10-29 04:29:23.993902，整体加12小时后截断到百分之一秒。入口由本轮维护前22407字节缩减，未把字节减少等同于模型token或成功率改善。

实际平台macOS（darwin）、Python3.12.14、Expat2.8.3；使用宿主依赖工具确认的解释器，所有Python命令保留`-I -S -B`。维护解释器/检查器入口自检、`"<skill-root>/scripts/read_docx.py" --check`通过；写入在仓库已确认可写范围。结构检查从仓库根运行，以下python指该已确认解释器：

- `python -I -S -B skills/skill-development-standards/scripts/check_skill.py skills/docx --strict`：最终通过，退出0；初检的外部资料资源链接提示已通过收敛指南为接入核对原则解决，迁移路径表的一处相对链接修正后复验通过。检查器规则未修改。
- 独占维护任务目录内运行`python -I -S -B -m unittest discover -s "<skill-root>/scripts" -p 'test_run_docx_task.py' -q`：26项通过，0失败/跳过，退出0；`<skill-root>/scripts/test_docx_controlled_flow.py`同命令模式：9项通过，0失败/跳过，退出0。执行时已替换真实skill目录，测试根目录与源码分离。
- stdin辅助验证脚本解析tool-calling.md中的真实外层JSON，替换已确认解释器/skill路径后执行；合成短文verified、标题与正文XML文字逐项符合独立预期、归档副本字节相同、自动清理完成。另以双引号、反斜杠、多个段落、多行JSON及字面量`$()`/反引号验证双层编码与quoted here-document，原文未被展开，生成、归档与清理通过。有效合成questionId配置仅用于本次验证，未读取用户配置。
- 辅助验证脚本初次有语法笔误，未启动生成，修正后执行。单个text包含换行的试例被原校验拒绝为spec_validation_failed，未生成主文件；按原manifest的retry将原文拆为paragraph块后成功，未放宽断言或运行时校验。同步补充主入口与指南的分段要求。
- 差异空白检查、主入口与新/迁移资源链接和锚点人工核对；本轮前后文件哈希对照确认运行时Python和原测试文件未变。仅清理创建前登记并确认不存在的本轮独占维护任务目录，保留仓库原文件。

仅说明维护，未新增运行逻辑或永久测试；未重复全量DOCX回归、专用Python命名linter、云端Linux/Windows、真实Qwen/DeepSeek宿主A/B、真实用户文档或视觉验收。以上本机合成结果不能证明空工具参数已修复或跨平台可用。未更新发布ZIP、提交或推送。

Main-agent work：需求、提示设计、说明维护、示例执行、测试解释与最终审查均由主代理完成。Subagent execution record：未调用子代理，任务较小且无需独立执行流。

## execute调用形式补充（2026-10-29）

版本`20261029.1721.1473`。用户补充当前调用形态为execute(command=命令字符串)，主入口增加该形态的定点说明，tool-calling.md增加完整函数式表示及其与既有外层JSON的等价关系，区分工具原生调用、日志展示、Shell命令与内层文档JSON；不把函数表示再塞入command。明确需闭合JSON、here-document及外层字符串；用户给出的正文中止片段若只是省略，不能当作真实截断证据。继续以真实schema与宿主stdin能力为前提，不新增execute工具或替宿主实现解析器。

从当前工作树继续维护，保留此前输出通道维护及所有已有修改；description人工核对仍准确，依赖和运行逻辑不变，README版本同步。宿主确认America/New_York时间2026-10-29 05:21:14.734623，整体加12小时并截断到百分之一秒。

macOS/Python3.12.14、已确认的bundled解释器，保留-I -S -B。仓库根运行解释器/检查器入口自检，均退出0；stdin辅助检查以ast.parse/literal_eval读取文档中的execute表示，检查唯一command关键字、非空字符串、外层JSON解码等价、完整here-document边界及独立预期规格，输出call-form-and-json-equivalent与complete-command-and-spec-ok，退出0；未执行任意表达式或用户片段。本轮仅补充说明且等价命令不变，未重复生成或回归测试，未创建临时文件。

从仓库根运行`python -I -S -B skills/skill-development-standards/scripts/check_skill.py skills/docx --strict`及git diff --check，最终均退出0；本轮前后哈希对照核对运行时脚本、测试、AGENTS及开发规范未由本轮改动。未验证真实execute宿主是否支持该命令、框架实际返回载荷、模型接收结果、Qwen/DeepSeek空参数率、云端Linux/Windows或视觉效果；本地字符串检查不证明这些范围通过。未更新发布ZIP、提交或推送。

Main-agent work：接口信息解释、示例补充、静态验证、差异审查及接受由主代理完成。Subagent execution record：未调用子代理，属于小范围说明维护。


补充维护版本`20261029.1733.3742`：按用户要求移除tool-calling.md中独立的command参数JSON执行示例，统一保留一份完整execute(command=...)调用表示，同步主入口链接说明和README版本。区分展示形式与宿主底层参数序列化，传输对象不得再次作为command内容；实际宿主schema优先，不修改运行接口。description人工核对仍准确，依赖不变，保留全部已有修改。版本采样为America/New_York 2026-10-29 05:33:37.426841，整体加12小时后截断到百分之一秒。

macOS/Python3.12.14，仓库根执行，使用已确认的bundled解释器及-I -S -B。解释器与检查器入口自检通过；stdin静态示例检查确认唯一execute调用、唯一command关键字、完整字符串/here-document及独立预期文档规格，输出single-execute-example-ok和command-and-spec-ok，退出0。严格Skill检查和git diff --check通过，退出0。本轮为说明统一，不修改Python脚本、不新增测试或临时文件，未重复生成/全量回归；未验证真实宿主、Qwen/DeepSeek成功率、云端Linux/Windows或视觉效果。Main-agent work：修改、检查和审查均由主代理完成。Subagent execution record：未调用子代理。


## 常见环境与交付故障说明（2026-10-29）

版本`20261029.1746.5085`。按用户提供的常见故障表新增troubleshooting.md，涵盖模块缺失、只读目录、write_file/execute文件不可见、pip/403、掩码路径、版本刷新、长会话、流式中断与下载失败，并链接原空工具参数排查。主入口补充环境铁律与压缩后状态恢复，工具指南和README按需引用，避免常规生成加载全部故障资料。description人工核对仍准确，依赖及运行接口不变；保留所有已有修改，不改开发规范或运行时脚本。

人工核对已校正：本Skill无python-docx等第三方依赖，不固定猜测虚拟环境；标准create使用位置输出文件名而非--output；绝对路径、403和相对路径均不能单独证明只读、离线或跨工具共享；check不证明可下载；刷新按钮、完整历史与下载入口须有真实宿主证据；断流不代表未执行，状态不明先核实，不能盲目重发create。用户经验作为诊断线索保留，不写成已复现原因。正文新增自检和长会话恢复步骤与已有脚本/保护规则人工核对。

版本采样为宿主America/New_York 2026-10-29 05:46:50.853661，整体加12小时并截断到百分之一秒。实际验证macOS/Python3.12.14，使用已确认bundled解释器及-I -S -B；仓库根执行维护解释器与检查器入口自检，退出0。创建前显式登记并确认不存在的独占临时诊断目录内，以真实skill路径运行`python -I -S -B "<skill-root>/scripts/read_docx.py" --check`与`python -I -S -B "<skill-root>/scripts/run_docx_task.py" check`，均退出0；分别核对read-check-ok及auto-cleanup=available、output_dir=. writable=True、check-ok，每次后目录无残留；诊断结束移除本轮空目录。使用本轮合成/禁用调试环境，不读取或打印用户原始归档配置。

仓库根执行`python -I -S -B skills/skill-development-standards/scripts/check_skill.py skills/docx --strict`与git diff --check通过；本轮新增资源及锚点检查、差异和文件哈希核对确认未修改Python脚本、测试、依赖、AGENTS或开发规范。本轮仅维护说明和验证已有自检，不重复业务生成、全量回归或专用Python命名检查；未验证真实宿主的虚拟环境、工具共享挂载、网络/403、刷新按钮、下载入口、原始故障、Qwen/DeepSeek调用率、云端Linux/Windows或视觉输出。未把本机诊断通过当作这些场景的验收，也未更新ZIP、提交或推送。

Main-agent work：经验适用性核对、说明维护、诊断执行与最终审查由主代理完成。Subagent execution record：未调用子代理，范围为小型文档维护且评审由主代理负责。


## Bash执行示例统一（2026-10-29）

版本`20261029.1811.0069`。按用户确认的平台推荐写法，将DOCX主入口、业务参考及README的DOCX部分共27个Bash/console命令块、35条命令统一为execute(command=完整字符串)表示；每条仍是独立工具调用，不因同块展示而全部执行。主入口在首个自检前声明统一规则，参数名command不加引号，不将execute表示式再次包入命令；tool-calling.md同步平台契约。只改变调用包装，JSON数据、Python API/源码及维护历史终端证据保留各自用途；其他Skill及README其他业务段落未改。description人工核对仍准确，依赖、CLI参数及安全/重试/归档/清理规则不变。

版本采样为宿主America/New_York 2026-10-29 06:11:00.694550，整体加12小时后截断到百分之一秒。实际平台macOS、Python3.12.14，使用已确认bundled解释器并保留-I -S -B，仓库根运行维护自检、检查与转换。所有涉及路径在写入前核对权限和现有修改；维护基线导出首次因输出限长未能解析JSON，未写文件，收敛为哈希和命令块清单后成功，不把截断输出当完整基线。

验证结果：

- 转换时以AST解析包装并反解command，随后使用独立检查脚本与维护前命令块逐条对比，35条命令字符串一致（普通命令不保留作为分隔符的尾部换行，here-document保留完整换行），35条均只有唯一command字符串参数。31个原JSON/API/源码等代码块哈希及顺序保持；业务文档无残留Bash/console代码块，调用引用的真实脚本均存在。
- 创建前显式登记并确认不存在的独占维护任务目录中，提取主入口及controlled-flow.md的真实execute示例，以ast.literal_eval取得命令；仅替换已确认解释器、skill路径和指定的示例输入路径，通过本地POSIX命令入口实测。自检退出0且含read-check-ok；新建verified、正文/标题XML文字符合独立预期；读取文本符合预期；合成原件执行text编辑后正文为新文且原件字节不变。两项最终文件的归档副本与主文件字节相同，自动清理完成，相关命令退出0。仅用本轮合成配置、关闭原配置调试日志，验收后只清理本轮登记目录。
- 仓库根维护终端执行`python -I -S -B skills/skill-development-standards/scripts/check_skill.py skills/docx --strict`及git diff --check通过、退出0；本轮资源链接/锚点、全量转换差异与哈希审阅确认运行时脚本、测试、依赖、AGENTS及开发规范均未由本轮改动。此处为维护终端执行证据，不是业务工具调用示例。

未重复全量DOCX回归或专用Python命名检查；未验证真实execute平台的解析/返回载荷、模型实际调用、Qwen/DeepSeek空参数率、云端Linux/Windows或视觉输出。本地反解执行验证命令和文档行为，不证明真实平台已接受函数调用文本或成功率改善；仍由宿主原生工具接口调度。未更新ZIP、提交或推送。

Main-agent work：规则决策、批量转换、等价性验证、实际文档验证、差异审查及最终接受均由主代理完成。Subagent execution record：未调用子代理，转换可由确定性脚本直接完成，无需独立执行流。


## 受控编辑清单收尾（2026-10-29）

仅修改 `<skill-root>/scripts/run_docx_edit.py` 的本次调用收尾：编辑验收、归档成功、登记项清理成功及最终主文件指纹复核通过后，在原目录锁内核对清单指纹和单链接条件再删除清单。task_manifest 与 manifest_retained 返回 null，manifest_removed 为 true；删除失败仍保留主文件成功，返回 cleanup_incomplete 和诊断。归档 failed/skipped 保留配置和清单；编辑失败、锁忙及未知状态不删除恢复材料。没有扫描或迁移历史文件，通用清理、新建 runner 和公共副本均未修改；手动 clean 恢复仍保留清单。

独立预期为成功后没有本轮 docx-edit JSON，输入/主文件保持正确，旧文件字节不变；失败时保留可用恢复材料，不把清单删除失败报为完成。合成 DOCX 测试覆盖重复成功、前次失败待重试、未知历史清单、归档失败/跳过、清理异常、删除权限异常、清单内容变化/软硬链接替换及最终输出变化。真实 CLI 子进程测试核对 stdout JSON、空 stderr、退出0、清单字段与实际文件一致；此为本地工具集成，不代表云端执行器通道联调。测试临时目录由各测试独占创建并自行清理，未删除历史残留。

实际平台 macOS，宿主提供 Python 3.12.14 / Expat 2.8.3，使用 -I -S -B，无第三方依赖。系统默认 Python 未通过 >=3.10 自检，改用宿主明确提供的兼容解释器后自检通过。以下命令均从仓库根运行，python 指本轮已确认的兼容解释器：

- `python -I -S -B -m unittest discover -s skills/docx/scripts -p 'test_*.py' -q`：248项通过，退出0，无跳过；随后仅新增CLI测试。
- `python -I -S -B -m unittest discover -s skills/docx/scripts -p 'test_docx_controlled_flow.py' -q`：含新增CLI测试的最终16项通过，退出0，无跳过。
- `python -I -S -B -m unittest discover -s tests -p 'test_artifact_lifecycle_parity.py' -v`：1项通过，退出0，共享源码与公共副本/来源哈希未变化。
- `python -I -S -B skills/skill-development-standards/scripts/check_skill.py skills/docx --strict`：通过；`git diff --check`：通过。

主代理完成实现与审查，未调用子代理。新增 Python 标识符和引用人工核对为 snake_case；未运行专用命名 linter。description 选择范围未变化，保留原文；主入口、受控流程、验收说明和 README 版本同步，依赖不变。未执行真实样本、Linux容器、Windows、恶意并发写者/强杀恢复、视觉验收或真实宿主 Skill 自动触发测试；未更新分发 ZIP。本机通过不代替 Linux 目标环境验收。


## DOCX路径统一（2026-10-29）

本次按用户最终范围仅迁移DOCX：17份Markdown各自明确根占位符定位、相对读取基准、使用前替换、完整参数引用及无法定位时停止。155个原本地Markdown链接（含页内章节）改成带标题的根路径代码引用，保留实际目标及锚点；69个旧根占位符替换，3个裸资源路径及54处脚本名/命令缩写补齐脚本根路径。原文件名术语、任务数据、ZIP成员、外部URL和明确标注的仓库维护命令保留原语义；虚拟环境路径猜测的负例改为文字说明。普通Markdown不再直接点击跳转，必须先定位真实根目录。

全局AGENTS、开发规范和其检查器未在本轮改变；其他Skill此前已有修改保留。DOCX本包的当前路径约定见主入口（`<skill-root>/SKILL.md#维护资源`）。按用户进一步确认，本次路径统一仅保留Markdown说明，撤去本次新增的Python路径检查器及其测试，不改变已有Python脚本。此前清理逻辑和自检输出任务的代码修改保留。

路径迁移期间曾执行10项临时路径测试及259项全量测试并通过；其中10项属于现已撤去的路径检查器测试，该结果仅为过程记录，不代表最终保留这些入口。最终文档使用现有严格结构检查和变更空白检查，并复核17份Markdown的占位符说明及旧路径写法；不新增运行步骤或Python依赖。

description人工核对仍为原129字符，功能选择、运行依赖及业务处理代码均未因路径迁移改变，版本和README中的DOCX版本同步。验证平台为macOS、Python3.12.14；未在Linux/Windows、真实模型资源加载、下载或视觉验收环境测试，未更新ZIP。

Subagent execution record：此前一次spawn调用luna__path_docs（luna_worker / gpt-6-luna / max），范围收窄后interrupt并一次followup，仅写入DOCX的17份Markdown；本次撤去检查代码未调用子代理。Main-agent work：规则与范围决定、补齐路径引用、撤去本次新增检查器及测试、同步版本与说明、最终审查。

文档复核（2026-10-29）：再次遍历全部17份Markdown，补全维护记录中的14处裸脚本资源引用；逐文件明确以DOCX主入口的真实file_path解析根目录、替换整个占位符、保留后缀及引用/转义方法，禁止误用参考文档目录。历史仓库命令和任务数据保留原语义。本轮仅调整文档，未新增或修改Python脚本和测试；未运行业务生成或跨平台验收。

归档回复话术维护（2026-10-29）：按用户确认，将当前宿主归档成功/失败的最终回复映射为云空间表情话术，统一主入口、生成、受控流程、收尾及交付说明。原archive_message、JSON、stderr、退出码及Python代码不变；保留跳过、未知、部分成功和主文件失败的区分，下载提示须有真实交付方式支持。description选择范围及依赖不变；仅文档检查，不重复业务回归或声称真实宿主云存储已联调。Main-agent work：文案与状态边界同步及审查；Subagent execution record：本轮未调用子代理。

归档话术复核（2026-10-29）：遍历全部17份Markdown，修正收尾表格直接按archive_message报告的歧义，明确失败提示引用统一话术；历史记录及机器字段中的旧文案标记为非当前用户回复要求。仅改文档，现有Python及测试内容未变；Main-agent work：复核与文档修正；Subagent execution record：未调用子代理。

## 上线审查修复验证（2026-10-29）

版本20261029.2043.2946：任务CLI输入/输出和公开生成图片资源边界前置检查；路由配置默认静默、开启也仅报告脱敏状态；清理返回登记项范围，受控编辑归档受阻给出恢复下一步。保留新建与编辑的不同清单策略，明确强杀暂存残留和同名归档替换边界。未改变业务格式或引入依赖。

macOS/Python3.12.14：DOCX全量252项、公共生命周期15项、两个副本一致性回归通过。XLSX关联全量300项、自检契约8项及WPS提取结果交接2项通过。专用Python命名linter不可用，进行了本轮代码命名及引用人工核对、AST检查和业务回归；不将结构检查作为命名linter。目标Linux、Windows、真实Office视觉效果、生产宿主云空间映射及新description模型触发未验证。发布包及只读安装验证见仓库维护记录tests/production_readiness_20261029_20432946.json（相对于仓库根，不随本技能发布）。本轮主代理完成实现、验证与审查，未调用子代理。
