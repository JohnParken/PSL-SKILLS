# DOCX：标准库文档生成、读取与受控编辑

> 文档核对日期：2026-11-07；源码基线：[9fcf9fc](https://github.com/JohnParken/PSL-SKILLS/tree/9fcf9fca339dd0f6491f7e64e4777f9dc57e61a7)；Skill 版本：`20261108.0102.2369`。本文是仓库级说明，不替代 [SKILL.md](../../skills/docx/SKILL.md) 的运行约束，不把说明本身当作业务验收；本轮定点回归见 [核验记录](../skill-docs-validation-20261107.json)。

所有业务交付文件入口只接收裸文件名，脚本固定写入已确认任务根目录 `./`；`./文件名`、子目录、绝对/父目录及 Windows 路径均拒绝。输入路径、内部归档与受控临时工作区保持原规则，多产物分别提供不同裸文件名。旧带目录输出的 CLI/API 调用需迁移，不通过切换到输入或安装目录规避。

## 功能与适用范围

DOCX Skill 用 Python 标准库构建 OOXML 文档包，适合公文、通知、报告、简单表格及声明式排版。它同时提供只读内容提取和严格限定的已有文档修改。

- **创建**：有序 JSON `blocks` 转为 `.docx`。基础块为标题、副标题、1–4 级标题、正文、列表、表格和分页；专项能力包括 PNG/JPEG 图片、富文本、合并表格、编号、目录、分节及交叉引用。复杂需求按主入口直达相应参考，不需预读全部资料。
- **读取**：原生 `.docx/.dotx`（含 WPS 保存的原生格式）的全文、标题、段落范围、章节、概览、精简结构或显式 OOXML 结构。可选读取页眉、页脚、脚注、尾注和批注；修订视图不修改原文档。
- **编辑**：普通文字节点替换、受控段落插入及 run 格式、规则表格数据行增删、同格式图片替换、有限批注事务。输出新文件，保留原件。
- **不提供**：旧二进制 DOC、WPS 专有格式转换、PDF 生成、任意无损编辑、完整修订接受/拒绝或真实视觉分页验证。CFB 内容即使伪装成 `.docx` 也拒绝并指向 WPS 读取能力；电子表格最终由 ET Skill 处理。

默认 `theme=official`，用户指定格式优先。未明确要求另起页时采用自然分页，不因一级标题自动插入分页或分节。标题 `keep_with_next` 是排版意图，不保证最终页码。目录、页码和交叉引用域需要阅读器更新。

## 架构图

```mermaid
flowchart TD
    A["SKILL.md：最短入口与按需参考"] --> B["run_docx_task.py：创建与恢复编排"]
    A --> C["read_docx.py：全文与有界读取"]
    A --> D["run_docx_edit.py：五类编辑编排"]
    B --> E["create_docx.py：规范化 JSON 与 OOXML 组包"]
    D --> F["文字、结构、表格、图片、批注模块"]
    E --> G["office：ZIP/XML 安全与结构、生成验证"]
    F --> G
    C --> G
    C --> P["只读源快照、UTF-8 预算与源绑定游标"]
    E --> H["formatting：样式、分节与静态分页审计"]
    G -->|"仅创建/编辑的核验路径"| I["新 DOCX；既有 DOTX 仅受控编辑副本"]
    B --> J["任务清单、锁、回执与归属核验"]
    D --> J
    I --> K["archive_deliverables：归档副本"]
    J --> L["controlled_cleanup：仅清理本轮登记项"]
```

运行时、编辑器及生命周期工具均随 DOCX 包分发，不需要从相邻 Skill 导入业务实现。图中 `office` 是本包 Python 模块目录，不是 Microsoft Office 服务。

## 主要模块与公开入口

| 入口或模块 | 职责与关键契约 |
| --- | --- |
| [run_docx_task.py](../../skills/docx/scripts/run_docx_task.py) | 常用入口 `create`，stdin 接收 JSON；内置环境检查、独占任务文件、固定子进程、验证回执、归档及清理。`resume` 核对原任务状态，`retry` 仅用于仍存在且满足条件的原清单，不是盲目重生成。 |
| [create_docx.py](../../skills/docx/scripts/create_docx.py) | `_validate_spec`、`_package_parts` 构建规范化规格与 ZIP 部件；`create_document_delivery()` 默认验证并归档。低层 `create_document()` 默认 `verify=False`，不能据此假定完成标准交付闭环。 |
| [read_docx.py](../../skills/docx/scripts/read_docx.py) | `read_docx()` 文本、`read_docx_structure()` 显式结构、`read_docx_compact()` 精简视图、`read_docx_page()` 源绑定分页与 `serialize_docx_page()` 精确序列化；CLI 默认文本，`--format json` 与 `--compact-json` 是不同契约。 |
| [run_docx_edit.py](../../skills/docx/scripts/run_docx_edit.py) | `text/structure/tables/image/comments` 五类写操作，stdin 接收操作规格；批注 `list` 应使用原只读入口，不走此写入编排。 |
| [edit_docx.py](../../skills/docx/scripts/edit_docx.py)、[edit_structure.py](../../skills/docx/scripts/edit_structure.py) | 文字精确匹配与次数校验；段落索引及 `expected_text` 双重定位；拒绝受保护或复杂锚点。 |
| [edit_tables.py](../../skills/docx/scripts/edit_tables.py)、[replace_image.py](../../skills/docx/scripts/replace_image.py)、[manage_comments.py](../../skills/docx/scripts/manage_comments.py) | 分别处理规则数据行、已知媒体及关系、经典/现代批注的有限动作。各自保留独立保护与验证范围。 |
| [office/archiveio.py](../../skills/docx/scripts/office/archiveio.py)、[office/xmlio.py](../../skills/docx/scripts/office/xmlio.py) | 受限 ZIP/XML 访问、解析预算与外来内容安全边界，不执行宏、域或文档内指令。 |
| [office/validate.py](../../skills/docx/scripts/office/validate.py)、[validate_layout.py](../../skills/docx/scripts/validate_layout.py) | OPC/显式结构检查与静态版式风险审计；不执行 XSD 或阅读器渲染。 |
| [archive_deliverables.py](../../skills/docx/scripts/archive_deliverables.py)、[controlled_cleanup.py](../../skills/docx/scripts/controlled_cleanup.py)、[docx_task_state.py](../../skills/docx/scripts/docx_task_state.py) | 文件保护、冲突命名、归档状态、身份与内容指纹、清单和回执校验；归档与清理分别报告。 |

## 任务流程图

```mermaid
flowchart TD
    A["确认目标、任务目录与 Skill 真实位置"] --> B{"任务类型"}
    B -->|"读取"| C["read_docx --check：退出 0 且 read-check-ok"]
    C --> D["选择全文、章节或有界结构"]
    D --> E["核对 coverage、源指纹与截断限制"]
    E --> O{"是否需要源绑定续读"}
    O -->|"是"| P["原样传 next_cursor，保持选项；字节预算可调"]
    P --> D
    O -->|"否"| Q["核对所选视图覆盖；页读完不等于完整 DOCX"]
    B -->|"创建或编辑"| F["完整 JSON 与新文件目标"]
    F --> G["runner 内置自检、保护输入、登记临时项"]
    G --> H["生成或限定编辑并验证候选"]
    H -->|"验证通过"| I["发布主文件并尝试归档"]
    H -->|"失败且生产者已停止"| J["按原清单清理可确认归属的临时项"]
    I --> K["清理本轮临时项并复核主文件未变"]
    K --> L["分别交付业务、归档、清理状态"]
    J --> M["报告 failed_cleaned 或 cleanup_incomplete"]
    G -->|"环境或状态无法确认"| N["停止依赖操作并核查原任务"]
```

常用创建只需主入口，不重复预跑 preflight；单独读取先做只读自检。调用始终在宿主确认的任务目录中，脚本位置由本轮实际读取 `SKILL.md` 的路径确定，不能把当前工作目录当成 Skill 安装目录。下例只说明调用形态，`<skill-root>` 必须替换为真实根目录；本次文档工作未执行该示例。

```sh
python -I -S -B "<skill-root>/scripts/run_docx_task.py" create "工作情况说明.docx" --compact-receipt <<'JSON'
{"theme":"official","blocks":[{"type":"title","text":"工作情况说明"},{"type":"paragraph","text":"本期工作有序开展。"}]}
JSON
```

## 输入、输出与 partial 边界

### 创建和编辑

创建 stdin 必须是完整 JSON；runner 限制自动任务 JSON 为 2 MiB。`blocks` 保持内容顺序，单行 `text` 不含真实换行或换页控制字符，正文分段须使用多个块。图片等任务资源相对任务目录，不写入 Skill 安装目录。

标准回执中，创建成功核对 `status=verified`，编辑成功核对 `status=edited`；二者再检查 `task_status`、实际 `output`、非空主文件及返回的 `warnings`。成功创建的检查项包括 ZIP CRC、结构、内容覆盖与静态排版意图；`visual_validation=false`、`xsd_validation=false` 是明确限制。冲突时 runner 可选择带“第 N 版”的新文件名，消费方必须使用返回路径，不能固定猜测请求名称。

`--compact-receipt` 只投影完整成功结果，且要求归档 `copied`、清理完成；错误、归档未完成或清理未完成会保留完整诊断。它不省略实际验证工作。

| 入口 | 退出码及结果含义 |
| --- | --- |
| 创建/编辑 runner | `0`：业务未失败且无 `cleanup_incomplete`；仍须检查归档状态及警告。`1`：`status=failed`。`3`：业务未失败但 `task_status=cleanup_incomplete`。参数解析失败通常为 `2`。 |
| `read_docx.py` | 成功 `0`、读取/安全检查失败 `1`、参数错误 `2`。不采用 WPS 的通用 `partial/3` 契约；有界结果须看截断及覆盖字段。 |
| `validate_layout.py` | 结构正常默认 `0`，可同时有风险警告；结构失败 `1`；显式 `--strict-pagination` 遇分页风险 `2`。该 `2` 不表示真实出现空白页。 |

错误优先看 `error_code`、`next_action` 与真实退出码。失败后若已成为 `failed_cleaned`、原清单已删除，纠正规格后建立新任务，不再对旧清单 `retry`。超时或发布状态不明时先检查原状态；安全或环境失败不能通过降低检查、切换路径或原样重试规避。

### 读取结果

- 旧 `--summary` 是概览，不含全文；最多返回 100 个标题，超出以 `headings_truncated` 标示。新 `--summary --max-output-bytes N` 可分页取全部识别标题，但仍不是正文全文。
- `--paragraph-range` 的索引为 **1-based 且两端包含**，只计正文级段落，不计表格单元格或附加部件。章节要求精确标题，重复标题使用 `--section-occurrence` 消歧。
- `docx-compact` 是有损只读视图；表格默认只预览前两行。需要完整表格可用全文文本，或 `--compact-json --tables rows` 配合下述源绑定游标续读；旧字符预算模式继续使用 `next_block`。`truncated/total_blocks` 必须保留。
- 旧精简 JSON 默认上限 12,000 字符，`--max-output-chars` 可设 128–2,000,000；按完整块分页，单块超限失败。`next_block/--start-block` 不绑定源身份，仅为兼容入口。纯文本默认不限长，字符截断带显式标记且没有游标；全结构 JSON 超字符预算失败，不交付半截 JSON。
- `docx-structure` 的 `schema_version=1` 描述显式 XML；未知元素以 opaque 节点保留。样式继承、自动编号、域结果和页面顺序未计算，不能直接作为生成器输入或无损编辑格式。
- `--revisions accepted/original/all` 只控制读取视图，不能表述为已保存净稿。

### UTF-8 有界输出与源绑定续读

```console
python -I -S -B "<skill-root>/scripts/read_docx.py" input.docx --compact-json --tables rows --max-output-bytes 4000
python -I -S -B "<skill-root>/scripts/read_docx.py" input.docx --compact-json --tables rows --max-output-bytes 6000 --cursor '上次next_cursor原值'
```

- 新模式只用于 `--compact-json` 或 `--summary`，预算 128–2,000,000 **UTF-8 字节**，包含完整元数据、诊断及末尾换行。仅传 `--cursor` 时默认 12,000 字节；与 `--max-output-chars`、`--start-block`（包括 0）、`--delivery` 互斥。
- 按完整块分页；元数据或单块放不下时 stdout 为空，完整错误在 stderr，不能裁剪诊断凑预算。该预算不约束 stderr，也不保证宿主回传不会截断。
- 结果增加 `page_version=1`、`source_sha256`、`has_more`、`next_cursor` 和 `coverage`。原样传递游标，保持模式、修订、相关部件、范围、章节及表格选项不变，**字节预算可以调整**。源字节变化、选项变化或损坏游标拒绝，不降级为旧数字索引。
- 按 `coverage.block_start/block_end_exclusive` 的零起算半开范围收集连续块，核对 `selected_total_blocks`，直到 `has_more=false`、`next_cursor=null`。`selected_view_complete` 仅表示本次响应是否覆盖整个所选视图；末页不等于已收集前页。
- `coverage.document_complete` 始终 false。保留 `limitations/omissions`：默认表格两行预览、相关部件计数或 200 字符预览不会因分页结束变成全文；完整表格文本需 `--tables rows`，仍非布局或合并保真。
- API `read_docx_page()` 与 CLI 复用安全快照及分页实现，`serialize_docx_page()` 返回用于预算核对的精确 JSON（含换行）；调用方重新美化可能超预算。`-o` 保存同一预算结果，不自动归档。
- 新模式对任务内相对路径逐层安全打开，拒绝绝对/父目录路径、符号链接及非普通文件；源指纹、ZIP/XML 校验与解析使用同一内存快照。游标校验不是签名、访问授权或防恶意伪造机制。

详见 [读取契约](../../skills/docx/references/inspection.md) 与 [读取实现](../../skills/docx/scripts/read_docx.py)。

[源绑定分页测试](../../skills/docx/scripts/test/test_docx_bound_pages.py) 覆盖多字节预算、独立行内容预期、换源/选项拒绝、覆盖及旧模式兼容；本轮实际执行范围见 [核验记录](../skill-docs-validation-20261107.json)，不代表全量回归或真实业务验收。

## 编辑保护范围

| 操作 | 可修改范围 | 主要拒绝条件与保留约定 |
| --- | --- | --- |
| 文字替换 | 单个普通 `w:t` 内文字，精确 `count`；默认正文，可明确选页眉页脚部件 | 不跨 run、字段、修订、超链接、批注范围或未知复杂容器；次数不足、过多、重叠或 XML 值无法保真时停止。 |
| 结构编辑 | 正文直属段落前后插入、普通 run 的有限格式 | 段落/run 索引为 **0-based**，核对原文；不是读取范围的 1-based 索引。复杂段落、节属性、范围锚点等拒绝。 |
| 表格数据行 | 有显式网格的规则矩形表中增删数据行 | 不支持合并、嵌套或复杂单元格；保护已标记的重复表头，不删除最后一条数据行。 |
| 图片替换 | 已知 PNG/JPEG 媒体的同格式替换 | 必须给出原 SHA-256、关系条数及实际引用次数；共享引用须明确同意全部替换。不改变尺寸、裁剪或布局，不解码像素。 |
| 批注 | 查询、严格锚点新增、更新/删除及有限现代一级回复 | 现代四部件需一致；经典批注不自动升级为现代线程。复杂元数据、任意云线程、提及和解决状态不在范围内。 |

受控写入拒绝数字签名包，不删除签名绕过限制。非目标 ZIP 成员内容字节可按对应操作保留；修改过的 XML 进行节点与属性值保真检查，**不承诺整份 ZIP 字节不变或任意文档无损编辑**。见 [文字编辑](../../skills/docx/references/ooxml-editing.md)、[结构编辑](../../skills/docx/references/structure-editing.md)、[表格与图片](../../skills/docx/references/table-media-editing.md)、[批注](../../skills/docx/references/comments-management.md)。

## 依赖、归档与验证限制

- [requirements.txt](../../skills/docx/requirements.txt) 声明 Python ≥3.10、标准库 zlib、Expat ≥2.6.0，不安装第三方包或 Office/转换器。使用 `-I -S -B`。
- 自动任务控制依赖 `fcntl`/POSIX 锁、`dir_fd` 和不跟随链接的安全文件操作；能力缺失即停止，不能从“仅标准库”推导为所有平台可运行。默认归档发布需要同文件系统硬链接。
- [归档](../../skills/docx/references/delivery.md) 是主文件之外的受控副本，不是移动原件。分别看 `archive_status` 和文件明细；归档失败保留主文件，只重试获准的归档步骤，不重跑生成。
- 清理需核对 `cleanup_complete`、`manifest_removed`、`deliverables_unchanged`。只处理创建前登记且身份、内容归属可核实的本轮临时项；未知或变化文件保留并报告，不按扩展名扫描清空目录。
- `validate_layout` 的分页风险是静态推断。字体名称只写入样式，不安装或嵌入字体；真实字体替代、页数、跨页表格和阅读器表现仍须实际客户端验证。历史参考中的验收记录不能自动扩展为本次测试结果或跨平台保证。

本页依据固定提交的代码与引用契约编写。本轮进行了源码/契约审阅和定点合成回归，具体通过项与范围见 [核验记录](../skill-docs-validation-20261107.json)。未对真实用户文档执行生成/修改、全量业务回归或 Word/WPS/LibreOffice 客户端验收。

## 源码追溯

- 固定基线：[DOCX 源目录](https://github.com/JohnParken/PSL-SKILLS/tree/6e4cca7a78a57acebe38448962c99cbc4814b409/skills/docx)。
- 入口和创建闭环：[SKILL.md](../../skills/docx/SKILL.md)、[run_docx_task.py](../../skills/docx/scripts/run_docx_task.py)、[create_docx.py](../../skills/docx/scripts/create_docx.py)、[生成核心规范](../../skills/docx/references/generation-core.md)。
- 安全与验收：[office/xmlio.py](../../skills/docx/scripts/office/xmlio.py)、[StructureValidator](../../skills/docx/scripts/office/validators/structure.py)、[生成验证](../../skills/docx/scripts/office/validators/generation.py)、[分页治理](../../skills/docx/references/pagination-governance.md)。
- 状态与恢复：[受控流程](../../skills/docx/references/controlled-flow.md)、[run_docx_edit.py](../../skills/docx/scripts/run_docx_edit.py)、[controlled_cleanup.py](../../skills/docx/scripts/controlled_cleanup.py)。

## 归档结果与用户提示

核对汇总 `archive_status=copied` 且完整、非空 `files` 清单全部为 `copied/already_archived` 后，保留 `archive_message` 并展示 `archive_user_notice`。归档成功即提示“🎉 文件归档成功，请到云空间查看。 ☁️✨”，无需等待宿主云空间映射确认，不宣称已验证上传完成或展示时效。失败、部分成功、跳过、待交接和未知分别如实报告，不套用全部成功；业务与清理状态独立，主业务失败不称生成成功。隐藏归档路径、目录、ID及原始日志，保留真实主文件链接，不虚构下载。
