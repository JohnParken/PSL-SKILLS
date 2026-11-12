# 当轮交付物与统一归档

路径占位符说明：`<skill-root>` 是本 DOCX Skill 根目录的文档占位符，不是 Shell 变量。取得宿主本次实际读取主入口 `SKILL.md` 的真实 `file_path`；若为相对路径，先按本次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名，所得目录才是根目录，不能使用参考文档所在目录。读取资源或执行命令前，将 `<skill-root>` 整体替换为该真实目录，保留资源后缀，将完整路径作为一个参数正确引用；嵌入 execute 字符串时按其语法转义。无法确认主入口真实路径时停止依赖操作并报告，禁止猜测路径。示例的工作目录为宿主已确认的任务目录 `./`，任务输入和受控临时文件沿用该目录内相对路径规则；交付输出只传裸文件名，由脚本固定写入任务根目录 `./`，不写入 Skill 安装目录。

最终回复先遵守 `<skill-root>/SKILL.md#执行环境约束与首步自检` 中的归档展示边界：完整或精简机器回执均不整段转贴；`archive_output` 和 `files[].archive_output` 不能作为下载地址。正文、链接文字与链接目标均不得带出归档路径、目录或任务 ID；异常仅概括实际影响，主文件链接只使用宿主确认的真实主文件引用。

路径定位与任务目录边界统一遵守 `<skill-root>/SKILL.md#执行环境约束与首步自检`；占位符必须用宿主本次已确认的真实目录替换，禁止猜测路径。

## 目录

- 维护与复用模块边界（`<skill-root>/references/delivery.md#维护与复用模块边界`）
- 范围与时机（`<skill-root>/references/delivery.md#范围与时机`）
- 用户展示与机器元信息（`<skill-root>/references/delivery.md#用户展示与机器元信息`）
- 公开交付 API（`<skill-root>/references/delivery.md#公开交付-api`）
- 归档目录使用边界（`<skill-root>/references/delivery.md#归档目录使用边界`）
- 自检与命令（`<skill-root>/references/delivery.md#自检与命令`）
- 路径、保护和状态（`<skill-root>/references/delivery.md#路径保护和状态`）

## 范围与时机

标准新建使用 `"<skill-root>/scripts/run_docx_task.py" create`。旧 `<skill-root>/scripts/create_docx.py` 生成 CLI 已弃用但保留兼容，仅供已有集成或需要自行管理辅助文件的定制调用；下文涉及它的生成示例及归档契约均以此为前提。弃用提示只写 stderr，不改变 JSON stdout、返回值和退出码。库函数、五种编辑入口、读取、预检、自检和显式清理流程均未弃用。

每轮只归档当次实际新建或修改、已完成相应验收并准备交付给用户的文件。新建 DOCX、编辑后的 DOCX/DOTX，以及用户要求交付的文本、JSON 等文件均适用。读取 stdout、查询批注、预检不产生交付文件，不归档。输入原件、操作 JSON、恢复备份、临时图片、验收日志和历史交付物不因存在于工作目录就归档。

新建 `<skill-root>/scripts/create_docx.py` 及五个编辑 CLI（`<skill-root>/scripts/edit_docx.py`、`<skill-root>/scripts/edit_structure.py`、`<skill-root>/scripts/edit_tables.py`、`<skill-root>/scripts/replace_image.py`、`<skill-root>/scripts/manage_comments.py` 的写操作）成功发布主文件后自动归档各自本次输出。编辑命令的输出参数只接受裸文件名，按 交付文件命名（`<skill-root>/references/generation-runtime.md#交付文件命名`） 确定有业务含义的名称，例如 `采购管理办法_修订版.docx`，不覆盖输入。公开交付 API 同样在主文件成功后自动归档，接口见下节；低层 Python 写文件 API 同样只收裸文件名，但保持不自动归档；内部临时处理原语不是交付入口。多步编辑中的中间文件使用低层 API 处理，最后一步调用公开交付 API，或在最终验收后只将已完成的最终文件交给显式清单入口；不要把会自动归档的 CLI 输出当作无需归档的临时步骤。读取保存结果和低层 API 等其他方式产生的最终文件，使用下方显式清单入口完成归档；它只复制文件，不代替格式或内容验收。

归档处理必须执行；“附加步骤”只表示失败不影响主文件，不表示可以省略。CLI 和公开交付 API 共用 `archive_outputs()`；`--check` 只执行自检，不能代替归档。探针和归档复制暂存文件均直接位于任务根目录 `./`；归档目录自检仅核对路径及访问权限（`access-only`），真实写入以复制结果为准。跨文件系统替换失败时保留主文件和已有副本并报告，不改用归档目录暂存。每轮交付前核对本轮清单，确认每个交付物的归档结果；跳过或失败仅作为警告报告，不阻止已完成主文件的交付。多轮不维护全目录累计清单，不扫描目录，不自动搬迁旧布局文件。按 命名规则（`<skill-root>/references/generation-runtime.md#交付文件命名`） 在写入前处理已知同名冲突；脚本不判断业务归属，相同归档文件名表示更新该副本；需要保留多个版本时，给当轮交付物使用不同文件名。

## 用户展示与机器元信息

内部结果继续保留 `archive_output`、`files`、配置原因及 `warnings`，供程序校验、清理和排错；CLI JSON/stderr不是直接给用户展示的回复模板，不删除或脱敏这些机器字段。`archive_message` 保留原机器状态文案（不是当前用户回复要求）：所有文件 copied/already_archived 且汇总 copied 时为“归档完成”；skipped 为“归档未执行”，failed 为“归档未完成”，其它或不完整结果为“归档状态未确认”。可用纯函数 `archive_user_message(result)` 从既有摘要取得同一机器文案；最终用户回复统一使用下列云空间话术。

用户已明确后续宿主上传机制，归档成功即提示去云空间查看，无需等待映射确认；不宣称已验证上传完成或展示时效。保留机器 `archive_message`，用户展示 `archive_user_notice`，按以下完整结果核对：

- 非空完整清单所有项 copied/already_archived 且汇总 copied：“🎉 文件归档成功，请到云空间查看。 ☁️✨”。
- 汇总 failed 且同时有成功与未成功项：“部分文件归档成功，请到云空间查看已归档文件；其余文件归档未完成。”。failed 但条目全成功可能是复核失效，不称部分成功。
- 归档失败：“🥺 本次归档未完成，请先核对文件结果。 📄”。
- skipped、未尝试或无有效配置：“本次未执行归档，请先核对文件结果。”，不据此断言外部云状态。
- 待交接：“文件待交接归档，归档完成后再提示到云空间查看。”。
- 未知或缺少可靠结果：“归档结果尚未确认，请先核对文件结果。”。空清单不得成功；入口拒绝或汇总 failed 按失败报告，只有状态无法确认时使用未知提示。

退出 0、主文件成功或单项成功不能替代完整归档结果。主业务失败不得称生成成功，归档与清理状态独立。隐藏归档物理路径、目录、ID和原始日志，保留真实主文件链接，不虚构下载能力。

## 公开交付 API

归档结果返回后仍需完成任务收尾：最终内容已验收时，按清理清单流程（`<skill-root>/references/task-cleanup.md`） seal/clean，归档 skipped/failed 也执行。这里的交付 API 不会删除调用方JSON、脚本或中间文档；标准新建由自动任务入口（`<skill-root>/references/task-cleanup.md#自动新建任务入口`）包裹，自动管理其创建的helper，其他任务仍显式登记并收尾。清理结果与主文件成功状态分开报告，不能因清理失败重跑生成或把有残留说成已全部清理。

从已确认的 `<skill-root>/scripts/` 加载下列模块。在宿主确认的任务目录中调用；各类路径基准统一见 文件定位与读取规则（`<skill-root>/SKILL.md#文件定位与读取规则`）；文件命名、环境自检和归档读取边界与 CLI 相同。所有交付 API 均返回字典，主操作失败会抛出异常且不归档旧输出；主操作成功后自动调用统一归档入口，归档异常转换为 `archive_status=failed` 和 `warnings`，不抛出为主操作失败。API 不自动打印结果，调用方须记录返回摘要；CLI 将摘要写入 stdout，并把失败警告写入 stderr。

新建交付验收产生的静态排版提示也保留在 `warnings`，以 `layout-warning:` 区别于归档诊断；最多保留 100 条及明确的省略数量。标准 runner 的完整和精简回执均回传，不影响 `archive_status`，不自动修改用户显式分页。只读审计和低层 Path 返回接口的诊断范围见 `<skill-root>/references/typography.md#静态排版检查`。

| 模块与交付函数 | 位置参数及专用选项 |
|---|---|
| `create_docx.create_document_delivery` | `spec, output, base_dir=None`；`verify=True`, `require_no_placeholders=False` |
| `edit_docx.replace_text_delivery` | `source, destination, specification`；`parts=None`, `require_no_placeholders=False` |
| `edit_structure.edit_structure_delivery` | `source, destination, specification`；`require_no_placeholders=False` |
| `format_guard.publish_format_candidate_delivery` | `original, candidate, output, specification`；仅受控 run 格式候选，核验后发布同一内存快照 |
| `edit_tables.edit_tables_delivery` | `source, destination, specification`；`require_no_placeholders=False` |
| `replace_image.replace_image_delivery` | `source, replacement, destination, specification` |
| `manage_comments.manage_comments_delivery` | `source, specification, destination=None`；只读 `list` 返回原结果，不归档 |

表中专用选项均为关键字参数；所有接口另接受 `protected_inputs=()`，列出从磁盘加载的 JSON、模板或其他需保护的输入。新建自动保护已验证的正文/表格图片；编辑自动保护源文档，图片替换还保护替换图片。候选格式交付同时保护原件和候选。CLI 自动补齐其输入清单；字典 API 不知道字典来自哪个磁盘文件，调用方必须通过 `protected_inputs` 提供这些路径。主文件写入前检查输入同源，归档前再次保护输入和其他交付物；标准runner按主路径和归档冲突自动递增版本名称；表中兼容API的主输出行为保留，归档统一默认拒绝覆盖。

新建示例（完成主入口自检，模块从上述真实脚本目录加载后）：

```python
from create_docx import create_document_delivery

result = create_document_delivery(
    {"theme": "official", "blocks": [
        {"type": "title", "text": "客户服务改进方案"},
        {"type": "paragraph", "text": "完善服务流程。"}
    ]},
    "客户服务改进方案.docx",
)
# result 同时包含主文件成功状态和归档结果，不再另行复制。
```

编辑示例：

```python
from edit_docx import replace_text_delivery

result = replace_text_delivery(
    "采购管理办法.docx", "采购管理办法_修订版.docx",
    {"replacements": [{"old": "旧条款", "new": "新条款", "count": 1}]},
)
```

新建 `output` 与编辑 `destination` 均只接收裸文件名，主文件固定写入任务根目录，拒绝 `./文件名`、子目录、绝对或父目录参数。编辑返回 `output="采购管理办法_修订版.docx"` 时，后续读取任务根目录中的该主文件；内部归档副本为 `output/{questionId}/采购管理办法_修订版.docx`，不把归档路径作为业务输出参数。已有底层函数 `create_document`、`replace_text`、`edit_structure`、`edit_tables`、`replace_image`、`manage_comments` 的签名和返回值保持兼容，不自动归档；不得将其用于最终交付后省略归档。多步处理中间结果不归档；若最终文件已由低层或其他工具生成并验收，显式调用 `archive_outputs(outputs, protected_inputs, *, replace_existing=False)` 或对应清单 CLI，不为归档重跑编辑。

格式候选交付使用 `<skill-root>/scripts/run_docx_edit.py` 的 `format-checked` 模式及 `--candidate`，流程和拒绝边界见 `<skill-root>/references/ooxml-editing.md#字体修改与候选文件保护`。先检查后另行直接归档不具备候选快照保护；格式检查失败不进入主文件发布和归档。

这些入口保证经过它们的主文件成功后触发归档；不能拦截任意外部代码写文件，也未接入宿主交付钩子。目录缺失由归档代码自动创建；配置缺失/无效时由代码返回 `skipped`，无须模型判断是否执行。归档记录须区分执行后跳过与从未调用，不能仅凭没有单独复制命令就判断未执行。

## 归档目录使用边界

`./output/{questionId}/` 默认仅存放当轮交付物副本，不作为输入来源、素材库或检索目录。不主动列举或递归扫描归档目录，不打开、提取、摘要或索引历史归档的文档内容；主文件缺失时也不擅自从归档选取替代文件。只有用户明确要求查看、比较、恢复或基于归档继续编辑时，才读取其指定文件；指定不清时先明确目标，不遍历归档寻找“最新版本”。获准读取的归档仍作为受保护输入，编辑输出另存新文件。

以下必要操作不受上述默认读取限制影响，但只针对本次已知目标路径及其父目录：

- 创建归档目录、任务根目录自检探针的独占写入/读回/清理及归档目录访问权限检查，以及文件存在性、类型、权限、符号链接和硬链接同源检查。
- 按候选文件名定点检查主输出与归档同名目标，不通过遍历目录或阅读旧文档判断业务归属；无法确认属于同一交付物时保留旧文件，按命名规则处理冲突。
- 复制当轮显式清单中的已验收主文件，并核对返回的归档路径、状态与警告。需要验证副本时，仅检查本次新复制的确切目标，或已确认属于本轮交付清单且返回 `already_archived` 的文件，可核对大小或字节/摘要一致性；不提取正文或加载历史文件作为模型上下文。`already_archived` 只表示源路径已是目标路径，不证明内容正确或允许读取其他归档。常规内容验收和交付读取优先使用本轮主文件。

以上是助手的读取边界，不是脚本的目录访问禁令。脚本不扫描历史归档；复制后自动回读本轮确切主文件与新副本的字节并核对 SHA-256，匹配才报告 copied 与 archive_verified=true，不提取正文。归档及摘要核验失败保留成功主文件，不回滚、不重生成；调用方另做的格式/内容验收独立报告，不追改旧回执。

## 自检与命令

先按 主入口（`<skill-root>/SKILL.md#执行环境约束与首步自检`） 确认任务目录、解释器及 `<skill-root>`。用实际 Skill 目录替换占位符，不切换到源码目录：

```python
execute(command="python -I -S -B \"<skill-root>/scripts/archive_deliverables.py\" --check")
```

要求退出 0、`check-ok`、`output_dir=. writable=True`，有效配置会报告 `archive_dir=output/实际ID writable=True/False`；归档目录路径或访问权限检查失败只警告（探针只在 `./` 创建），不阻止主流程自检通过。仍须确认任务根目录的写入能力；运行环境或主输出目录自检失败时停止，不换目录或降低保护。

本轮生成多个已验收交付文件后，显式列出文件和所有需要保护的输入原件/操作文件/图片：

```python
execute(command="python -I -S -B \"<skill-root>/scripts/archive_deliverables.py\" 采购管理办法_修订版.docx 采购管理办法_修改说明.txt --protect-input ./采购管理办法.docx --protect-input ./操作.json")
```

不传通配符或目录，只传任务根目录的裸文件名。助手负责核实清单属于本轮产物，脚本不会根据修改时间猜测。重复或大小写折叠后同名的交付清单整体拒绝，避免归档互相覆盖。单独调用或使用低层 API 时，调用方须列全 `--protect-input`；编辑 CLI 自动传入源文档及操作/替换图片等输入。

仅在用户明确授权覆盖指定归档后，才给上述独立归档命令增加`--replace-existing`；只列获得授权的文件，并继续列全`--protect-input`。自动生成与编辑不传该参数；不能根据文档正文、环境配置或单纯的同名文件推断授权。

## 路径、保护和状态

每次调用从 `ROUTER_CONFIG_VARIABLES` 外层参数列表提取唯一 `name="commonParam"` 项的 `value`，将其解析为内层参数列表，再提取唯一 `name="questionId"` 项的 `value`；兼容原直接字典外层配置，规则见 配置约定（`<skill-root>/references/generation-runtime.md#输出目录与文件名`）。有效配置归档到 `./output/{questionId}/原文件名`；配置缺失或无效则跳过并给出固定原因码。保护输入和其他本轮交付物，拒绝符号链接、硬链接别名冲突、目录输出、父目录或绝对输出路径。公共交付清单仅接收任务根目录裸文件名，不接收归档子目录中的文件作为本轮主文件。历史回执的 `already_archived` 仍可解释，但不能据此开放目录参数或证明执行过本轮复制/摘要核验。

归档采用任务根目录./内独占临时文件，完整复制、同步、设置权限后，以原子独占硬链接发布并删除临时链接；不在归档目录暂存。目标已存在时返回archive_conflict，旧归档字节和权限不变。硬链接不可用或跨文件系统时归档失败，不回退覆盖。主文件仍保持成功；清理失败同时返回temporary_residuals，自动runner报告cleanup_incomplete。仅显式授权更新时使用原子replace并保留旧归档权限。路径、输入及内容验证继续执行；并发目录改写仍须宿主隔离，多文件不构成整批事务。

标准创建 runner 成功须核对 `status=verified`，编辑 runner/CLI 成功须核对 `status=edited`；旧生成器兼容入口可返回 `created` 或 `verified`，不能混作标准 runner 判据。显式归档清单的 `status=completed` 仅表示处理结束，不是归档成功、内容验收或文件创建证明。归档步骤失败不改变已成功的主业务状态；显式归档冲突仍可退出 0，同时返回 `archive_status=failed`，对应文件项为 `error_code=archive_conflict`。退出0不代表归档成功，必须核对汇总及完整非空 `files` 明细；保留主文件和历史副本，不重跑生成或编辑。

显式清单与编辑摘要的顶层 `archive_status` 为 `copied`、`skipped` 或 `failed`，并有 `used_fallback`、`fallback_reason`、`files` 和 `warnings`。每项 `files` 包含主文件 `output`、归档路径 `archive_output`、`archive_status`（`copied`、`already_archived`、`skipped` 或 `failed`）。全部文件已在归档位置时，汇总仍为 `copied`，单项为 `already_archived`。清单预检查失败时 `files` 可为空，原因在 `warnings`，未复制任何文件。复制过程中部分失败，其他文件仍逐项处理；顶层归档状态为 `failed`，主状态不变。CLI 同时将警告输出到 stderr，前缀 `archive-warning`。

编辑成功的原结果放在 `result`，顶层 `output` 是本次编辑文件；批注只读查询保持原输出格式。主文件生成/编辑本身失败仍维持原非零返回，不归档旧输出。参数用法错误仍非零退出，不属于归档步骤警告。

新建生成器保留单文件摘要字段，并包含统一入口的 `files` 明细，见 生成说明（`<skill-root>/references/generation-runtime.md#输出目录与文件名`）。不要混用摘要结构，也不要仅凭退出码 0 把 `skipped` 或 `failed` 宣称为已归档。交付已成功的主文件并单独报告归档警告，不把归档失败当成主文件失败，不隐藏未完成副本或清理残留。

## 维护与复用模块边界

- `<skill-root>/scripts/router_config.py`：`read_environment_parameter(name, environ=...)` 只读取变量，不记录内容；`get_common_parameter(raw, name)` 严格解析宿主外层列表或兼容字典、内层列表或 JSON 字符串，返回唯一参数并保留其JSON类型；字符串值返回前strip两端空白，中间内容及非字符串值不变，错误抛出 `RouterConfigError`。不决定输出路径或创建目录。`read_router_config_from_environment()` 保留原 DOCX 调试日志开关和 stderr 行为，日志属于内部诊断，不转贴用户。
- `<skill-root>/scripts/output_location.py`：保留 `OutputLocation`、questionId 文件名安全规则与跳过归档的原因码；旧环境读取导入仍兼容。其他技能可复用配置解析，但需明确自己的业务参数与路径策略，不能把任意参数直接作为路径。
- `<skill-root>/scripts/artifact_files.py`：集中既有路径组件检查、输入别名保护和原子归档复制。`<skill-root>/scripts/archive_deliverables.py` 的 `archive_outputs(outputs, protected_inputs, *, replace_existing=False)`、`finalize_delivery(...)`、`archive_user_message(...)` 是调用方使用的复用接口；不要求 DOCX 格式，也不导入生成器。仅旧 CLI 的 `--check` 按需加载 DOCX 自检。原生成器私有辅助名称保留兼容导出，调用方优先使用公开归档接口。
- `<skill-root>/scripts/controlled_cleanup.py`：独立提供 `TaskFiles`、`plan_cleanup`、`seal_cleanup`、`cleanup_task`，不依赖生成器或归档执行器；详见清理契约（`<skill-root>/references/task-cleanup.md#可复用核心与兼容入口`）。`<skill-root>/scripts/run_docx_task.py` 与 `<skill-root>/scripts/docx_task_state.py` 仍负责 DOCX 验收、worker、重试事务和锁，不属于通用文件清理模块。

以上模块均随本技能完整分发，仅依赖标准库；公共artifact-lifecycle与XLSX使用受追踪副本，不依赖仓库公共目录。迁移时须带齐模块及其同级依赖，保留本轮所有权、输入保护、任务目录和失败语义，不能仅复制函数后移除检查。归档成功的用户提示按本页“用户展示与机器元信息”映射；内部路径和警告保留于机器结果。模块拆分不提升原有并发写者隔离保证，也不把单文件原子替换变成整批事务。

归档默认保留同名旧文件；冲突返回archive_status=failed及对应files项的error_code=archive_conflict，主文件仍可交付，临时项照常清理。自动生成/编辑不会授权覆盖归档。只有用户明确授权更新所列归档时，才单独调用归档入口并显式传入--replace-existing（API为replace_existing=True），列全保护输入；该选项不绕过路径、链接、输入和其他交付物保护。不要为解决归档冲突删除旧文件或重跑生成。并发任务仍使用独立任务目录；显式更新不保证对抗同权限恶意并发写者。

归档增强：复制后以 SHA-256 核对主文件及副本，匹配后 files 项返回 archive_verified=true；不匹配报告失败并保留文件供排查。跨文件系统、权限不足及空间不足分别返回 archive_cross_device、archive_permission_denied、archive_storage_full。完整回执保留实际相对 archive_output；精简回执仅在归档 copied 时使用，skipped/failed 保留完整原因和文件明细。实际目录仍以宿主确认的任务工作目录为基准加 output/{questionId}，不得切换到输入或 Skill 目录执行，也不根据对话猜测其他目录。

宿主没有将 ROUTER_CONFIG_VARIABLES 传入子进程时，标准 create 可用 --question-id 传入已确认的任务 ID，归档仍到任务目录下 output/{questionId}/。参数按相同 ID 规则校验；已有配置无效或两处 ID 不一致时拒绝，不静默覆盖。配置只在该次调用中补入并恢复。缺少可信 ID 时仍明确报告跳过，不从聊天正文或历史文件猜测。

用户提示字段 archive_user_notice 与机器字段 archive_message 并存。归档汇总及交付回执自动返回成功、失败、跳过、部分成功或未知的对应提示，完整和精简回执均保留。失败使用不预设生成或下载成功的归档未完成提示，不虚构下载能力。主任务失败不得套用已生成话术；清理状态仍单独核对。
