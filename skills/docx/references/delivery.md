# 当轮交付物与统一归档

## 范围与时机

每轮只归档当次实际新建或修改、已完成相应验收并准备交付给用户的文件。新建 DOCX、编辑后的 DOCX/DOTX，以及用户要求交付的文本、JSON 等文件均适用。读取 stdout、查询批注、预检不产生交付文件，不归档。输入原件、操作 JSON、恢复备份、临时图片、验收日志和历史交付物不因存在于工作目录就归档。

新建 `create_docx.py` 及五个编辑 CLI（`edit_docx.py`、`edit_structure.py`、`edit_tables.py`、`replace_image.py`、`manage_comments.py` 的写操作）成功发布主文件后自动归档各自本次输出。编辑命令保留现有相对输出路径参数，按 [交付文件命名](generation.md#交付文件命名) 确定有业务含义的名称，例如 `./采购管理办法_修订版.docx`，不覆盖输入。公开交付 API 同样在主文件成功后自动归档，接口见下节；低层 Python API 保持不自动归档，仅用于中间处理。多步编辑中的中间文件使用低层 API 处理，最后一步调用公开交付 API，或在最终验收后只将已完成的最终文件交给显式清单入口；不要把会自动归档的 CLI 输出当作无需归档的临时步骤。读取保存结果和低层 API 等其他方式产生的最终文件，使用下方显式清单入口完成归档；它只复制文件，不代替格式或内容验收。

归档处理必须执行；“附加步骤”只表示失败不影响主文件，不表示可以省略。CLI 和公开交付 API 共用 `archive_outputs()`；`--check` 只执行自检，不能代替归档。每轮交付前核对本轮清单，确认每个交付物的归档结果；跳过或失败仅作为警告报告，不阻止已完成主文件的交付。多轮不维护全目录累计清单，不扫描目录，不自动搬迁旧布局文件。按 [命名规则](generation.md#交付文件命名) 在写入前处理已知同名冲突；脚本不判断业务归属，相同归档文件名表示更新该副本；需要保留多个版本时，给当轮交付物使用不同文件名。

## 公开交付 API

从已确认的 `<skill_dir>/scripts/` 加载下列模块。在宿主确认的任务目录中调用；各类路径基准统一见 [文件定位与读取规则](../SKILL.md#文件定位与读取规则)；文件命名、环境自检和归档读取边界与 CLI 相同。所有交付 API 均返回字典，主操作失败会抛出异常且不归档旧输出；主操作成功后自动调用统一归档入口，归档异常转换为 `archive_status=failed` 和 `warnings`，不抛出为主操作失败。API 不自动打印结果，调用方须记录返回摘要；CLI 将摘要写入 stdout，并把失败警告写入 stderr。

| 模块与交付函数 | 位置参数及专用选项 |
|---|---|
| `create_docx.create_document_delivery` | `spec, output, base_dir=None`；`verify=True`, `require_no_placeholders=False` |
| `edit_docx.replace_text_delivery` | `source, destination, specification`；`parts=None`, `require_no_placeholders=False` |
| `edit_structure.edit_structure_delivery` | `source, destination, specification`；`require_no_placeholders=False` |
| `edit_tables.edit_tables_delivery` | `source, destination, specification`；`require_no_placeholders=False` |
| `replace_image.replace_image_delivery` | `source, replacement, destination, specification` |
| `manage_comments.manage_comments_delivery` | `source, specification, destination=None`；只读 `list` 返回原结果，不归档 |

表中专用选项均为关键字参数；所有接口另接受 `protected_inputs=()`，列出从磁盘加载的 JSON、模板或其他需保护的输入。新建自动保护已验证的正文/表格图片；编辑自动保护源文档，图片替换还保护替换图片。CLI 自动补齐其输入清单；字典 API 不知道字典来自哪个磁盘文件，调用方必须通过 `protected_inputs` 提供这些路径。主文件写入前检查输入同源，归档前再次保护输入和其他交付物；合法同名普通输出及归档仍按既有规则原子更新，不新增自动改名或业务归属判断。

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

新建 `output` 仍只接收 DOCX 文件名，默认验收后交付；编辑 `destination` 仍接收相对任务目录的路径。编辑返回 `output="deliverables/采购管理办法_修订版.docx"` 时，后续读取该主文件，而归档路径为 `output/{requestId}/采购管理办法_修订版.docx`；不追加 `deliverables/` 层级。已有底层函数 `create_document`、`replace_text`、`edit_structure`、`edit_tables`、`replace_image`、`manage_comments` 的签名和返回值保持兼容，不自动归档；不得将其用于最终交付后省略归档。多步处理中间结果不归档；若最终文件已由低层或其他工具生成并验收，显式调用 `archive_outputs(outputs, protected_inputs)` 或对应清单 CLI，不为归档重跑编辑。

这些入口保证经过它们的主文件成功后触发归档；不能拦截任意外部代码写文件，也未接入宿主交付钩子。目录缺失由归档代码自动创建；配置缺失/无效时由代码返回 `skipped`，无须模型判断是否执行。归档记录须区分执行后跳过与从未调用，不能仅凭没有单独复制命令就判断未执行。

## 归档目录使用边界

`./output/{requestId}/` 默认仅存放当轮交付物副本，不作为输入来源、素材库或检索目录。不主动列举或递归扫描归档目录，不打开、提取、摘要或索引历史归档的文档内容；主文件缺失时也不擅自从归档选取替代文件。只有用户明确要求查看、比较、恢复或基于归档继续编辑时，才读取其指定文件；指定不清时先明确目标，不遍历归档寻找“最新版本”。获准读取的归档仍作为受保护输入，编辑输出另存新文件。

以下必要操作不受上述默认读取限制影响，但只针对本次已知目标路径及其父目录：

- 创建目录、自检探针的独占写入/读回/清理，以及文件存在性、类型、权限、符号链接和硬链接同源检查。
- 按候选文件名定点检查主输出与归档同名目标，不通过遍历目录或阅读旧文档判断业务归属；无法确认属于同一交付物时保留旧文件，按命名规则处理冲突。
- 复制当轮显式清单中的已验收主文件，并核对返回的归档路径、状态与警告。需要验证副本时，仅检查本次新复制的确切目标，或已确认属于本轮交付清单且返回 `already_archived` 的文件，可核对大小或字节/摘要一致性；不提取正文或加载历史文件作为模型上下文。`already_archived` 只表示源路径已是目标路径，不证明内容正确或允许读取其他归档。常规内容验收和交付读取优先使用本轮主文件。

以上是助手的操作边界，不是读取脚本的目录访问禁令；现有脚本不自动扫描归档目录，也不自动执行副本内容回读。不得把可选的副本验证升级为主流程成功条件：归档或其验证失败仅报告警告，保留成功主文件，不回滚或重新生成；未完成的归档检查不得宣称通过。额外副本验证由调用方执行，其结果须单独报告，不能声称现有 CLI 已自动回读验证或会据此改写已返回的 JSON 状态。

## 自检与命令

先按 [主入口](../SKILL.md#执行环境约束与首步自检) 确认任务目录、解释器及 `<skill_dir>`。用实际 Skill 目录替换占位符，不切换到源码目录：

```console
python -I -S -B "<skill_dir>/scripts/archive_deliverables.py" --check
```

要求退出 0、`check-ok`、`output_dir=. writable=True`，有效配置会报告 `archive_dir=output/实际ID writable=True/False`；归档不可写或探针失败只警告，不阻止主流程自检通过。仍须按实际编辑输出目录确认写入能力；运行环境或主输出目录自检失败时停止，不换目录或降低保护。

本轮生成多个已验收交付文件后，显式列出文件和所有需要保护的输入原件/操作文件/图片：

```console
python -I -S -B "<skill_dir>/scripts/archive_deliverables.py" ./采购管理办法_修订版.docx ./采购管理办法_修改说明.txt --protect-input ./采购管理办法.docx --protect-input ./操作.json
```

不传通配符或目录；只传当前任务目录内的相对文件路径。助手负责核实清单属于本轮产物，脚本不会根据修改时间猜测。不同目录下同名交付物（含大小写折叠后相同）会整体拒绝，避免扁平归档时互相覆盖。单独调用或使用低层 API 时，调用方须列全 `--protect-input`；编辑 CLI 自动传入源文档及操作/替换图片等输入。

## 路径、保护和状态

每次调用读取 `ROUTER_CONFIG_VARIABLES` 的 `commonParam` 列表内 `name="requestId"` 的参数项，规则见 [配置约定](generation.md#输出目录与文件名)。有效配置归档到 `./output/{requestId}/原文件名`；配置缺失或无效则跳过并给出固定原因码。保护输入和其他本轮交付物，拒绝符号链接、硬链接别名冲突、目录输出、父目录或绝对输出路径。已在目标归档路径的交付物标为 `already_archived`，不会复制自身；它仍不能与受保护输入同源。

归档采用目标目录内独立临时文件、完整复制、同步、权限设置及原子替换。已有副本保留权限，新副本继承主文件权限。归档是主文件成功后的附加步骤。归档不可写、路径保护拒绝或复制失败均只警告，主文件仍保持成功状态和退出码 0，不回滚、不重跑编辑；保留旧归档。主文件写入前的输入/覆盖保护和编辑内容验证仍严格执行，失败仍非零退出。清理失败同时报告原始错误和临时路径。并发目录改写仍须由宿主隔离；批量文件不构成跨文件事务。

显式清单的 JSON `status` 为 `completed`，编辑 CLI 成功的 `status` 为 `edited`，新建生成器成功的 `status` 为 `created` 或 `verified`。上述主状态不因归档失败改变，归档步骤异常只警告并返回退出码 0。显式清单的 `completed` 表示处理结束，不是文档内容验收或文件创建证明；不能根据它猜测文件真实存在。

显式清单与编辑摘要的顶层 `archive_status` 为 `copied`、`skipped` 或 `failed`，并有 `used_fallback`、`fallback_reason`、`files` 和 `warnings`。每项 `files` 包含主文件 `output`、归档路径 `archive_output`、`archive_status`（`copied`、`already_archived`、`skipped` 或 `failed`）。全部文件已在归档位置时，汇总仍为 `copied`，单项为 `already_archived`。清单预检查失败时 `files` 可为空，原因在 `warnings`，未复制任何文件。复制过程中部分失败，其他文件仍逐项处理；顶层归档状态为 `failed`，主状态不变。CLI 同时将警告输出到 stderr，前缀 `archive-warning`。

编辑成功的原结果放在 `result`，顶层 `output` 是本次编辑文件；批注只读查询保持原输出格式。主文件生成/编辑本身失败仍维持原非零返回，不归档旧输出。参数用法错误仍非零退出，不属于归档步骤警告。

新建生成器保留单文件摘要字段，并包含统一入口的 `files` 明细，见 [生成说明](generation.md#输出目录与文件名)。不要混用摘要结构，也不要仅凭退出码 0 把 `skipped` 或 `failed` 宣称为已归档。交付已成功的主文件并单独报告归档警告，不把归档失败当成主文件失败，不隐藏未完成副本或清理残留。
