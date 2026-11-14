# WPS：有限文字读取、RTF 创建与内容重排

> 当前 Skill 版本为 `20261114.2127.4001`。历史源码参考基线为 [9fcf9fc](https://github.com/JohnParken/PSL-SKILLS/tree/9fcf9fca339dd0f6491f7e64e4777f9dc57e61a7)。运行约束见 [SKILL.md](../../skills/wps/SKILL.md)，指定版本 WPS Office 12.1.0.26899 的生产者身份和客户端显示仍待验收。

所有业务交付文件入口只接收裸文件名，脚本固定写入已确认任务根目录 `./`；`./文件名`、子目录、绝对/父目录及 Windows 路径均拒绝。输入路径、内部归档与受控临时工作区保持原规则，多产物分别提供不同裸文件名。旧带目录输出的 CLI/API 调用需迁移，不通过切换到输入或安装目录规避。

宿主返回的 `/large_tool_results/` 是结果引用，仅使用宿主 `read_file` 和实际 schema 补读并核对覆盖；不交给 execute、cat、Shell 或 Python，不猜映射或使用路径掩码。读取工具缺失或结果不完整时报告缺口并停止依赖。普通业务文件和 Skill 资源仍遵循各自路径规则。

WPS 图表读取新增保存值展示：按分类索引对齐原始数值与简单百分比/小数格式，逐字段报告缺失，保留缓存来源和未刷新标记。不得自动访问外部工作簿、反算缺失计数或把存储系列当成已验证的视觉系列；不做图片 OCR。合法内嵌 XLSX 可按明确单行/列引用补读已存值，不覆盖图表缓存；外链、缺失单元格和不支持格式保持明确缺口。

## 功能与格式边界

WPS Skill 面向公文、报告、段落和基础表格，提供四条任务路径：

1. **声明式创建**：JSON `blocks` 生成 RTF，默认最终文件名为“文档主题.wps”；用户明确要求 `.rtf` 时尊重该后缀。
2. **内容读取**。按字节识别 RTF、MS-DOC 和 OOXML Word 的受支持子集。可读取全文或选定范围。maximum 模式使用完整 JSON，补读受支持的文本框、绘图说明、表格、段落属性、MS-DOC 内嵌 PNG 及图表缓存。
3. **受控重排编辑**：文字替换、段落替换/删除/追加、单元格更新及批量操作。读取后重建整份 RTF，不是对原格式的保留式编辑。
4. **从 DOCX 兼容导出**：将 DOCX 转为 RTF，或通过实际预装 LibreOffice 转为二进制 MS-DOC；每种后端都需候选校验和回读。此路径不是 WPS 转 DOCX。

**默认 `.wps` 后缀中的内容是 RTF，不是金山原生 WPS 格式。** 新建、重排和 RTF 导出的回执为 `output_format=rtf`、`binary_doc=false`、`native_wps=false`。读取能识别某种容器，不等于支持所有该容器内格式，更不证明文件来自 WPS 软件。

| 场景 | 接受的真实内容与后缀规则 |
| --- | --- |
| 输入读取 | 按内容签名判断；历史 `.doc` 文件实际包含 RTF 或 OOXML 时可按受支持内容读取。未知原生 WPS/Works/ET 私有格式不支持。 |
| 创建与重排 | 只输出 RTF，后缀限 `.wps/.rtf`。不能将 RTF 写成 `.doc`。 |
| 兼容导出 | RTF 可写 `.wps/.rtf`；受支持的 MS-DOC/CFB 可写 `.doc/.wps`；OOXML 不能改名冒充导出成功。 |
| 电子表格 | 按实际格式和任务交接：原生 XLSX/XLSM 交给 xlsx，ET/ETT 交给 et；识别到电子表格容器时不继续按文字处理，也不自动转换。 |

本轮合成与独立包验收见 [关联内容恢复记录](../maintenance/wps/related-recovery-20261114.md)。

最新默认模式与兼容迁移验收见 [默认最大化读取记录](../maintenance/wps/default-maximum-20261114.md)。

本轮完整结果分片验收见 [maximum分页记录](../maintenance/wps/maximum-pagination-20261114.md)。

## 默认最大化读取与修订边界

CLI 和公开 Python API 默认 maximum；显式 standard 保持原主文模式和旧窄范围能力。OOXML/RTF 默认当前 accepted 正文；不同的 all 存储视图单列 revision_content，不能当作变更清单或当前正文。MS-DOC 及已分类的无法过滤 RTF 修订只返回明确标识的存储内容，current_view_available=false、partial，不阻断可靠文字。修订补读失败保留已读正文并标缺口。

maximum 默认完整JSON仍受16 MiB限制；显式分页改用完整结果无损分片，覆盖附加区域及修订，单条记录可跨片。按连续字符偏移和摘要重组，不能把半截payload当业务JSON；256 MiB总传输上限，不静默截断或降级成仅主文。创建/编辑/导出内部10处API及1处子进程固定 standard，避免扩大重排/验收范围。旧API调用者如需保持原策略、纯文本或窄范围必须显式迁移。底层解析器未改成独立默认maximum入口。

## 本轮读取容错

OOXML 主文按包根关系及内容类型定位；standard 未请求附加内容时报告已发现的关联引用。局部关联部件缺失、XML 或定义损坏不丢弃可靠正文，返回 partial 与准确部件位置，并继续其他部件。公式与未知块文字明确报告未解码；不推断未知结构的阅读顺序。MS-DOC maximum 对明确分类的独立映射/文字故障保留其余内容，不伪造页眉或注释锚点。共享结构、安全、加密和资源门禁不降级。

maximum 的新分片是整个规范JSON的无损传输；标准主文分页不覆盖独立关联区域，不能混用游标。见 [完整结果分片](../../skills/wps/references/maximum-pagination.md)。标准有界视图保持原契约。合成测试不能替代真实 WPS 生产者和排版验收。

## 架构图

```mermaid
flowchart TD
    A["SKILL.md：任务分流与格式声明"] --> B["read_wps.py：按真实内容路由"]
    B --> C["wps_rtf_read：RTF 子集"]
    B --> D["wps_cfb 与 MS-DOC 解析：有界二进制证据"]
    D --> Q["maximum：表格、文本框、存储图片和图表缓存"]
    B --> PAGES["可选全结果无损分片与校验重组"]
    B --> E["wps_ooxml：OOXML Word 子集"]
    E --> ER["独立关联内容恢复与遗漏诊断"]
    B --> F["诊断与选择性读取；字符/UTF-8 字节预算及续页"]
    A --> G["run_wps_task：JSON 声明式创建"]
    A --> H["run_wps_edit：读取后受控重排"]
    H --> B
    G --> I["wps_rtf_builder：规范化文档树与 RTF"]
    H --> I
    A --> J["export_wps：后端和后缀约束"]
    J --> K["docx_to_wps_rtf 或预装 LibreOffice"]
    I --> L["候选回读与内容、格式控制核对"]
    K --> M["输出格式与读取结果校验"]
    L --> N["wps_task_paths：安全发布和候选清理"]
    M --> N
    N --> O["wps_delivery：随包生命周期归档"]
```

RTF 创建和重排共用构建器及内容账本。导出器具有自己的候选格式/回读验收，不能把创建/重排的完整计划账本能力套到导出流程。归档实现随包保存在 `_wps_artifact_runtime`，不隐式导入相邻 Skill。

## 主要模块与入口

| 入口或模块 | 主要职责 |
| --- | --- |
| [read_wps.py](../../skills/wps/scripts/read_wps.py) | 统一 CLI 及 `read(path)`、`read_bytes(data)`；格式分支路由、策略兼容检查、状态及通道输出。 |
| [wps_cfb.py](../../skills/wps/scripts/wps_cfb.py)、[wps_ooxml.py](../../skills/wps/scripts/wps_ooxml.py)、[wps_ooxml_textboxes.py](../../skills/wps/scripts/wps_ooxml_textboxes.py)、[wps_rtf_read.py](../../skills/wps/scripts/wps_rtf_read.py) | CFB 结构与流边界、OOXML 受限 ZIP/XML、maximum 文本框独立故事、RTF token/目标组和有限结构读取。MS-DOC 正文及附加区域通过入口和专门模块组合解析。 |
| [wps_binary_tables.py](../../skills/wps/scripts/wps_binary_tables.py)、[wps_binary_officeart.py](../../skills/wps/scripts/wps_binary_officeart.py)、[wps_binary_images.py](../../skills/wps/scripts/wps_binary_images.py) | 校验表格行和单元格，关联组合图形、文本框和图片，按请求返回有界 PNG 字节。 |
| [wps_binary_charts.py](../../skills/wps/scripts/wps_binary_charts.py)、[wps_binary_chart_cache.py](../../skills/wps/scripts/wps_binary_chart_cache.py) | 从已验证的锚点关系读取图表部件、系列缓存和缺值范围。 |
| [wps_diagnostics.py](../../skills/wps/scripts/wps_diagnostics.py)、[wps_selective.py](../../skills/wps/scripts/wps_selective.py)、[wps_read_view.py](../../skills/wps/scripts/wps_read_view.py) | 读取评估、标题/范围/章节投影、与原字节和选项绑定的分页游标；不把投影结果伪装为完整格式数据。 |
| [run_wps_task.py](../../skills/wps/scripts/run_wps_task.py) | `check/create`；接收 stdin 或 `--spec`，规范化生成、候选回读、发布并输出回执。 |
| [wps_rtf_builder.py](../../skills/wps/scripts/wps_rtf_builder.py) | `RtfRun/Paragraph/Table/Document` 与 `build_document_from_spec()`；文本转义、UTF-16 代理对、页面/段落控制与有限表格合并。 |
| [run_wps_edit.py](../../skills/wps/scripts/run_wps_edit.py) | `text/structure/table/batch`；`execute_edit_task()` 读取原件、应用操作、按新主题重建 RTF，并核对源文件在发布前未变化。 |
| [export_wps.py](../../skills/wps/scripts/export_wps.py)、[docx_to_wps_rtf.py](../../skills/wps/scripts/docx_to_wps_rtf.py) | `check/inspect/convert`；RTF 映射与可选 LibreOffice 导出，保留来源/候选 partial 和 fallback 诊断。 |
| [wps_content_validation.py](../../skills/wps/scripts/wps_content_validation.py)、[wps_output_contract.py](../../skills/wps/scripts/wps_output_contract.py) | 前者核对规范化计划与候选文字、块顺序、物理单元格及有限控制；后者约束后缀/真实格式、转换候选及读取状态。 |
| [wps_task_paths.py](../../skills/wps/scripts/wps_task_paths.py)、[wps_access.py](../../skills/wps/scripts/wps_access.py) | CLI 任务目录边界、不跟随链接读取、候选身份及字节核验、不覆盖发布、内部候选清理。 |
| [wps_delivery.py](../../skills/wps/scripts/wps_delivery.py)、[run_wps_job.py](../../skills/wps/scripts/run_wps_job.py) | 前者归档已核验主文件；后者仅在确需落盘 JSON 配置时登记、执行白名单 CLI 并清理自己的配置。 |

## 任务流程图

```mermaid
flowchart TD
    A["确认输入映射、任务目录与真实 Skill 位置"] --> B["只做当前分支的环境自检"]
    B --> C{"选择任务"}
    C -->|"读取"| D["按签名解析并保留范围和诊断"]
    D --> E{"读取结果"}
    E -->|"ok"| F["按选区/预算返回内容及续页标记"]
    E -->|"partial"| G["返回可靠内容并说明遗漏"]
    E -->|"失败"| H["停止依赖操作并报告原因"]
    C -->|"创建"| I["JSON 规范化并构建 RTF"]
    C -->|"编辑"| J["读取原件并检查重排授权与顺序"]
    J -->|"可继续"| I
    J -->|"partial 且未允许有损重排"| H
    C -->|"导出"| K["按后缀选择 RTF 或 LibreOffice"]
    I --> L["候选回读、内容账本与显式控制核对"]
    K --> M["来源、候选格式及回读校验"]
    L --> N["发布到未占用文件并复核身份和字节"]
    M --> N
    N --> O["清理自有候选并尝试归档"]
    O --> P["分别报告业务、partial、发布、归档和清理"]
    N -->|"不确定或验证失败"| H
```

读取与创建自检需退出 `0` 且输出 `environment-ok`；导出自检需退出 `0` 且 JSON `status=environment-ok`。读取用 `read_wps.py --check`，创建用 `run_wps_task.py check`，编辑须两项均通过，RTF 导出用 `export_wps.py check --backend rtf`；需要二进制后端时才检查 `libreoffice`。自检不代替输入或产物验收。

下例是调用形态而非本次执行记录。`<skill-root>` 须由本轮实际读取的主入口路径确定并整体替换；数据路径相对宿主确认的任务目录。

```sh
python -I -S -B "<skill-root>/scripts/run_wps_task.py" create "工作汇报.wps" --compact-receipt <<'JSON'
{"theme":"official","blocks":[{"type":"title","text":"工作汇报"},{"type":"paragraph","text":"本期工作有序开展。"}]}
JSON
```

## 输入输出与结果契约

### 创建输入与排版

创建输入为带非空 `blocks` 的 JSON 对象，块类型包括 `title/subtitle/heading/paragraph/list/table/callout/page_break`。主题为 `official/business/general`，默认 `official`。默认公文为 A4、上下 25.4 mm、左右 31.8 mm，正文仿宋 16 pt、固定 28 pt 行距、首行 2 字；这是一套预设，不是 GB/T 合规认证。

页面可通过 pt 字段指定宽高和四边距，须保留可打印区域。支持有界横向、纵向及矩形单元格合并，只恢复可证实的显式网格。正文默认自然分页，标题与后文尽量同页；明确新页时只选一种分页机制。WPS 生成器不支持 DOCX 的分节配置，不用分页符冒充分节。

`spec_warnings/spec_warning_count` 会揭示未知选项或显式未知字体替换等问题；即使产物可读，也不能据 `success` 宣称所有配置都已生效。字体只写名称，不安装、嵌入或保证存在。详见 [生成说明](../../skills/wps/references/generation.md)。

### 读取状态、partial 与有界输出

| 入口 | 成功、partial 与失败 |
| --- | --- |
| 读取 CLI | `status=ok` / 退出 `0`：声明子集内成功；`partial` / `3`：可靠内容伴随遗漏；读取失败 / `1`；参数错误 / `2`。 |
| 创建 CLI | 成功回执为 `status=success` / `0`，但仍可能有 `partial=true` 或 `spec_warnings`。必须同时审阅这些字段、发布及归档状态。 |
| 重排编辑 CLI | 成功通常 `0`；使用 `--allow-lossy-reflow` 且结果为 partial 时退出 `3`。回执仍需查看 `partial`、`losses` 和源诊断，不能只看退出码。 |
| 导出 CLI | `success` / `0`；来源或候选 partial、或者 `.wps` 的 auto 回退时 `partial` / `3`；失败 `1`；参数错误 `2`。 |
| 导出 `inspect` | 校验写入候选的后缀、格式及支持内容：完整声明子集 `0`、有效但 partial `3`、不符或无效 `1`。不是输入读取的替代入口。 |

默认读取 JSON 返回 `format/text/paragraphs` 及适用的 `tables/blocks/issues/diagnostics/omitted_regions`。`reading_assessment` 是范围摘要：`ok` 仅表示 `within_declared_scope`，`full_format_verified` 始终为 false。未知覆盖不表示该内容不存在。

`--reading-mode maximum` 使用完整 JSON，读取字段缓存、对象占位、隐藏及全部修订存储文字，并请求附加区域。MS-DOC 的 `drawing_objects` 提供可确认的锚点和形状说明，`related_stories` 提供文本框及其范围。`binary_images` 提供已验证的 PNG 元数据和引用，带 `--include-image-bytes` 可取得原始字节。`binary_charts` 返回可定位的浮动或行内图表缓存、缺值和来源，外部工作簿只记录引用状态。RTF 读取已验证的附加目标组，OOXML 另列受检的 `w:txbxContent`。 [绘图与 PNG](../../skills/wps/references/binary-shapes.md#最大模式绘图说明) · [图表缓存](../../skills/wps/references/binary-shapes.md#存储图表缓存) · [附加故事与文本框](../../skills/wps/references/ms-doc-stories.md#最大模式文本框)

`--reading-mode standard --format text` 成功时 stdout 为正文；RTF 全文按块顺序包含表格行，单元格以制表符分隔，JSON 的 `text` 保持段落口径。partial 的状态和 issues 摘要在 stderr。RTF 对可证实的直接隐藏和修订标记支持筛选或注释；样式、历史属性、字段或结构边界使筛选不能保证时拒绝。standard 保留原存储策略；默认maximum将当前支持视图与不同的all存储视图分开；图片和对象默认标 U+FFFC 及源位置，内容不执行或解码，显式 reject 遇对象拒绝。显式字段 reject 在已解析主内容遇域时拒绝，缓存结果不执行且标 partial。脚注和批注不混入主文；RTF 仅在显式附加/maximum 模式提取可验证目标组并独立标注未知映射。OOXML 默认 include 在可见性元数据损坏但正文可安全读取时保留正文并标 partial；标准 noBreakHyphen/softHyphen 保留为对应 Unicode 字符。失败 JSON 写 stderr，不用空正文伪装成功。默认 JSON 模式的读取失败写 stdout；参数解析错误只有用法/错误文本。单次完整JSON输出有16 MiB预算；全结果分页是独立传输契约，按连续片段校验和重组，不输出被截断的合法性未知外壳。

`read_bytes()` 可能抛 `ParseError`，也可能返回格式分支生成的失败字典；调用方既要捕获异常，也要检查 `status`。`read(path)` 捕获 `ParseError/OSError` 并返回失败结果，但不保证捕获所有编程异常。

选择性读取与有界投影不等于完整读取：

- 段落范围为 1-based、两端包含；章节按检测到的标题边界选择，重复标题可用 `--section-index` 明确定位。
- `wps_read_view_v1` 保留原状态、partial 和主要诊断，同时明确 `coverage/metadata_omitted/truncated/next_cursor`。游标绑定源 SHA-256、选项及 `projection_revision=2`，源文件或范围变化后拒绝复用。旧修订游标须重新开始；每次续读仍完整解析，不是流式读取或跨调用缓存。
- 有界视图可加 `--max-output-bytes`（512..16777216），与原字符预算同时生效，按真实 JSON 加换行的 UTF-8 字节核算，包含中文、emoji、转义及游标。仅指定字节预算也启用视图；默认字符预算仍12000。新增 `has_more` 与 next_cursor 一致，概况 has_more=false 不表示返回全文。诊断或单个输出单元放不下时完整返回 output_view_error，不裁剪；预算也约束失败 JSON，参数错误仍为 stderr/退出2。详见 [有界读取](../../skills/wps/references/bounded-reading.md)。
- MS-DOC 最大模式在 `binary_table_structure.tables` 给出可确认的一级表格分组、存储列边界、横向合并和纵向合并跨度，通过行索引关联 `rows`。遇到无可靠边界或网格的记录会标明缺口，保留可用的行和原文字。嵌套表格、完整样式及版式仍待解析。 [二进制表格分组与合并](../../skills/wps/references/binary-tables.md#表格分组与合并)
- 加密、结构损坏、安全校验或资源限制按失败契约停止；有可靠正文的 partial 不应被丢弃，但也不能自动用于无损编辑。

详细字段与例外见 [读取结果契约](../../skills/wps/references/reading-assessment.md) 和 [格式边界](../../skills/wps/references/format.md)。

## 重排编辑与导出边界

### 受控重排不等于无损编辑

`run_wps_edit` 的处理链是“读取 → 修改已恢复内容 → 构建新 RTF”。回执明确 `edit_mode=content_reflow_not_lossless`；原格式、域、对象和分页不保留。段落索引为 1-based，表格/行/列索引为 0-based。文字替换作用于段落和表格中的匹配文本，批量操作按顺序应用，应核对 `edit_stats`。

默认拒绝 partial 输入，以及无法确定段落/表格相对顺序的输入。只有显式 `--allow-lossy-reflow` 才允许将可恢复内容写入未占用的新文件；副本前置说明、回执的 `losses/source_issues/source_omitted_regions/order_status` 保留损失。读取成功也不代表重排可保留原版式。见 [受控编辑](../../skills/wps/references/editing.md)。

创建/重排在发布前用内容账本核对规范化计划与回读候选的主文字、块顺序、物理单元格和支持的合并标记；另核对页面尺寸、边距及有限正文段落控制。它不证明原始规格中所有格式被接受，也不认证空段落、相邻表格分界、字体字形、附加内容、域新鲜度或客户端分页。

### 导出后端选择

- 显式 `--backend rtf`：纯标准库映射，输出 `.wps/.rtf`；用户要 RTF 时不能仅因 `.wps` 后缀而转用二进制后端。
- `.wps` + `auto`：先尝试 LibreOffice，失败时可回退 RTF；成功回退仍标 `partial` 和 `fallback_used`，不能隐去后端变化。
- `.rtf` + `auto`：直接使用 RTF。
- `.doc`：必须实际得到受支持 MS-DOC 字节，不允许 RTF 回退；缺少 LibreOffice 或转换失败即受阻。
- `--verify-read` 调用真实读取 CLI；`readback-ok` 对应读取 `0`，`readback-partial` 对应 `3`。回读失败不能标完整成功，即使主文件已经存在。

导出验收分为转换/发布、格式/后缀、内容回读、真实客户端四层。前三层都不能替代 Word/WPS 打开、字体、分页和再次保存验收。导出不具备创建/重排同等的计划内容账本，关键金额、标题和表格仍须对照。见 [导出验收](../../skills/wps/references/export-acceptance-criteria.md)、[RTF 后端](../../skills/wps/references/export-rtf-backend.md)、[LibreOffice 后端](../../skills/wps/references/export-libreoffice-backend.md)。

## 安全发布、归档与清理

CLI 数据及配置使用任务目录内相对路径，拒绝父目录、绝对数据路径、符号链接和多重硬链接。内部脚本路径不受此数据参数规则限制。CLI 只发布到未占用的新名字，包括并发出现的目标也不覆盖；底层 API 保留自身路径/替换契约，嵌入方不能利用它绕过任务边界。

发布前后核对候选及最终文件身份和字节。必须分别解释：

- `publication_outcome=published_verified` 与 `publication_verification=identity_and_bytes_verified`：本次检查时发布文件已核验。
- `output_written=true` 只说明曾写出；若为 `published_verification_failed`，保留文件供检查，不正常交付。
- `output_written=null` / `publication_uncertain`：先检查目标与进程状态，不盲目重跑。
- `cleanup_complete=true` 只对应内部候选清理；`run_wps_job` 的配置清理由 `job_cleanup` 另行表示。

创建、编辑和导出 CLI 自动调用 `wps_delivery`；裸读取、`inspect/check` 和底层 API 不自动归档。只有发布核验及内部清理通过后才归档，归档前后再次核对主文件哈希。`delivery.archive_status` 单独表示 success、partial、failed 或 not_required；缺少有效宿主配置时 failed，不能据业务退出 `0` 声称云空间成功。

归档复制成功即按 archive_user_notice 提示去云空间查看，无需等待宿主映射确认；这不宣称已验证上传完成或展示时效。失败保留主文件，不重跑生成或删除历史文件解决冲突。默认 stdin 不产生额外配置；确需落盘 JSON 时使用受控 job，创建前登记，仅清理自己的已确认文件，保护用户原配置。详见 [交付契约](../../skills/wps/references/delivery.md) 与 [公共副本来源记录](../../skills/wps/references/lifecycle-upstream.json)。

普通成功交付保持简洁，内部格式与验收字段仍供结果判断；用户询问、明确要求特定格式或格式限制影响使用时准确说明，遗漏和异常按实际影响报告。

## 依赖与验证限制

- [requirements.txt](../../skills/wps/requirements.txt)：Python ≥3.10、标准库；OOXML 分支另需 Expat ≥2.6.0。保持 `-I -S -B`，禁止运行时联网或安装包。
- 纯 Python RTF 创建/编辑/导出无外部转换器依赖；二进制导出仅使用已预装 LibreOffice，不把可选能力写成零依赖。
- 安全路径与发布要求实际具备 `dir_fd`、不跟随链接及硬链接等能力；缺失时停止。Linux 容器是主要目标，macOS/Windows、实际宿主和客户端需要分别验证。
- 静态结构、内容账本和原始控制字段验收不等于视觉分页；`layout_verification=not_run`、`native_wps=false`、格式 partial 都必须如实保留。

仓库初版导读在固定源码基线上审阅实现与参考契约。当前二进制读取验证见 [最大模式二进制读取](../maintenance/wps/validation.md#最大模式二进制读取) 和 [绘图锚点与说明](../maintenance/wps/validation.md#绘图锚点与说明)。

## 源码追溯

- 历史快照：[WPS 源目录](https://github.com/JohnParken/PSL-SKILLS/tree/6e4cca7a78a57acebe38448962c99cbc4814b409/skills/wps)。
- 统一入口与格式路由：[SKILL.md](../../skills/wps/SKILL.md)、[read_wps.py](../../skills/wps/scripts/read_wps.py)、[wps_output_contract.py](../../skills/wps/scripts/wps_output_contract.py)。
- 创建、编辑及计划校验：[run_wps_task.py](../../skills/wps/scripts/run_wps_task.py)、[run_wps_edit.py](../../skills/wps/scripts/run_wps_edit.py)、[wps_content_validation.py](../../skills/wps/scripts/wps_content_validation.py)。
- 读取回归：[入口流程](../../skills/wps/scripts/test/test_wps_entry_workflows.py)、[二进制表格](../../skills/wps/scripts/test/test_wps_binary_tables.py)、[最大模式二进制读取](../../skills/wps/scripts/test/test_wps_msdoc_maximum.py)。
- 后端与状态传播：[export_wps.py](../../skills/wps/scripts/export_wps.py)、[wps_task_paths.py](../../skills/wps/scripts/wps_task_paths.py)、[wps_delivery.py](../../skills/wps/scripts/wps_delivery.py)。

## 归档提示更新（2026-11-07）

本节与全文均按 2026-11-07 的上述源码基线核对；历史测试记录不代表本轮重跑。

归档成功即提示“🎉 文件归档成功，请到云空间查看。 ☁️✨”，无需等待宿主云空间映射确认，不宣称已验证上传完成或展示时效。失败、部分成功、跳过、待交接和未知分别如实报告，不套用全部成功；业务与清理状态独立，主业务失败不称生成成功。隐藏归档路径、目录、ID及原始日志，保留真实主文件链接，不虚构下载。

## 执行结果与大结果读取说明更新（2026-11-09）

本轮仅更新结果判读与续读说明，不改执行路径、首步自检或 CLI 行为。工具调用成功、进程退出与业务 JSON 分别核对；允许宿主合并 stdout/stderr 并附状态文字，不要求不存在的字段或固定分隔符，退出码未知不补成0。保留非零退出的结果、partial、诊断以及业务/归档/清理边界；状态不明不盲目重跑写操作。

宿主外置预览与已截断捕获分别处理。结果引用按真实 `read_file` schema 逐段读取并核对覆盖，不作为 Shell 路径；已丢弃内容不能靠读取不完整文件恢复。业务投影按实际游标和来源绑定续读，末页只证明所选投影取完，不消除遗漏或源 partial。详见 [有界读取](../../skills/wps/references/bounded-reading.md#宿主大结果与业务分页分开处理)。本轮说明依据 CLI 与本机 Deep Agents 源码核对；真实目标宿主回传、Linux 容器和客户端验收仍需独立验证。

执行调用维护：业务脚本示例使用宿主的 `execute(command=...)`，执行前核对完整命令、stdin、路径及结果；这不改变 CLI 或协议，不代表真实宿主链路已经验收。


## 2026-11-10 描述与读取路径优化

本轮 description 明确原生 DOCX（含 WPS 保存）优先由 docx 处理，ET/ETT 和原生 XLSX/XLSM 仍分别使用 et、xlsx。RTF 内容的 .wps/.rtf 创建不等于金山原生格式，也不承诺原版式和对象保留。

当前变更基于 `65bff45`；本轮实际验证与未验证范围见 [维护记录](../maintenance/skill-review/optimization-451-20261110.md)。上文日期化记录继续代表各自历史基线。

本轮统一归档回复边界：完整/精简回执只供内部核验，正文及链接目标均不得暴露归档路径或任务 ID；保留主文件真实引用与失败/部分结果说明。能力范围、CLI 与内部机器字段未改；本轮核验见 [归档展示复核](../maintenance/artifact-lifecycle/archive-reply-privacy-20261112.md)。

本轮归档收尾补充：已声明最终交付但发布校验、业务结果或内部候选清理未就绪时，返回 failed 和具体 blocking_reasons，保留主文件，不误报 not_required；清理恢复并重新核验后仅重试归档，采用重试时最新宿主 questionId。CLI 业务退出码与归档结果仍须分别检查。

## 2026-11-13 纯 Python 最大化读取

新增 `--reading-mode maximum`，保持默认 standard 行为不变；目标是多返回有来源边界的存储文字，而不是放宽结构安全或伪造完整成功。实现、合成测试与未验证范围见 [最大化读取维护记录](../maintenance/wps/maximum-reading-20261113.md)。当前没有 WPS Office 12.1.0.26899 真实样本，因此不能称为该版本已认证兼容。

WPS 11 / 12.1 生成的 `.doc` 直接按实际内容读取，不以应用版本或扩展名代替格式判断。默认 maximum 保留可读内容与局部缺口；五种 FIB 结构和内容路由有合成回归，但不等于所有软件构建、对象及版式均获兼容认证。

WPS maximum 的可选图表检查保留64候选上限：达到上限不再解析后续候选，正文和已读图表保持可用，明确partial与未检查数量，不把候选数当图表数。其他共享结构和资源安全门禁保持拒绝。分页消费者区分standard主文text与maximum全JSON片段，不混用契约或游标。
