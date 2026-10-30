# 生成器输入约定

路径占位符说明：`<skill-root>` 是 DOCX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 DOCX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

标准新建直接使用已集成环境判断的自动任务入口（`<skill-root>/references/task-cleanup.md#自动新建任务入口`）：规格JSON直接传stdin，内部创建的worker.py和spec.json在成功与失败时均按自动入口收尾。旧 `<skill-root>/scripts/create_docx.py` 生成 CLI 已弃用但暂时保留，仅在已有集成或明确需要定制调用且自行管理辅助文件时使用；生成时只向 stderr 提示，stdout JSON、返回值和退出码保持兼容。`--check`、`--preflight` 与库函数未弃用，不以自动入口替代这些专用能力。下文旧生成 CLI 示例均受此前提约束。下文JSON字段不变；create_docx.py的文件输入示例为兼容/定制调用，调用方辅助文件不会因此自动删除。

普通文字文档可直接按 主入口最短流程（`<skill-root>/SKILL.md#新建与受控编辑`） 执行；本页按需查阅字段和格式，不作为每次任务必须全文读取的清单。

## 目录

- 路径解析示例（`<skill-root>/references/generation.md#路径解析示例`）
- 预检与集成验收（`<skill-root>/references/generation.md#预检与集成验收`）
- 交付文件命名（`<skill-root>/references/generation.md#交付文件命名`）
- 输出目录与文件名（`<skill-root>/references/generation.md#输出目录与文件名`）
- 配置调试（`<skill-root>/references/generation.md#配置调试`）
- 主题与文档标题（`<skill-root>/references/generation.md#主题与文档标题`）
- 顶层字段（`<skill-root>/references/generation.md#顶层字段`）
- 默认格式与页面（`<skill-root>/references/generation.md#默认格式与页面`）
- 样式字段与覆盖顺序（`<skill-root>/references/generation.md#样式字段与覆盖顺序`）
- 内容块（`<skill-root>/references/generation.md#内容块`）
- 表格配置与示例（`<skill-root>/references/generation.md#表格配置与示例`）
- 显式覆盖示例（`<skill-root>/references/generation.md#显式覆盖示例`）
- 中文 JSON 与引号（`<skill-root>/references/generation.md#中文-json-与引号`）
- 生成后格式引导（`<skill-root>/references/generation.md#生成后格式引导`）

先按 执行环境约束（`<skill-root>/SKILL.md#执行环境约束与首步自检`） 定位脚本并完成自检。在宿主确认的任务工作目录调用生成器，输入使用 `./document.json` 等相对路径，输出参数只填写文件名；不要切换到 skill 源码目录。脚本和内部资源均基于入口定义的 `<skill-root>` 定位，执行前替换为真实目录。路径基准统一见 文件定位与读取规则（`<skill-root>/SKILL.md#文件定位与读取规则`）：标准自动入口的 stdin 图片相对任务目录；仅下文兼容文件输入 CLI 的图片相对输入 JSON 所在目录；字典 API 图片相对 `base_dir`（省略为任务目录），均与主文件输出目录无关。API 模块从 `<skill-root>/scripts/` 加载；路径接口区别见下节。禁止传入未声明的字段，生成器会拒绝未知字段、错误类型和不支持的块。

## 路径解析示例

本例工作目录固定为宿主已确认的 `./`。先确认本次实际输入为 `./document.json`、图片为 `./logo.png`，JSON 内图片字段为 `"path": "logo.png"`；这些是示例文件名，使用时替换为宿主确认的真实名称，不据此创建输入子目录。完成自检后，从同一 `./` 调用兼容入口：

```python
execute(command="python -I -S -B \"<skill-root>/scripts/create_docx.py\" ./document.json \"客户服务改进方案.docx\" --verify --require-no-placeholders")
```

读取 `./logo.png`，主文件为 `./客户服务改进方案.docx`。有效配置下归档副本由程序写入 `./output/{questionId}/客户服务改进方案.docx`；该归档目录不是工作目录，不进入其中执行命令。若自行读取同一 JSON 后调用字典 API，仍从 `./` 执行并保护原始输入：

```python
import json
from pathlib import Path
from create_docx import create_document_delivery

spec = json.loads(Path("./document.json").read_text(encoding="utf-8"))
result = create_document_delivery(
    spec, "客户服务改进方案.docx", base_dir=".",
    protected_inputs=["./document.json"],
)
```

模块从已确认的 `<skill-root>/scripts/` 加载；`base_dir="."` 只将图片基准指定为任务目录，不改变工作目录或输出位置。标准stdin入口的图片也相对 `./`。仅当实际输入 JSON 确实位于宿主确认的子目录时，旧兼容文件 CLI 才按该 JSON 的父目录解析图片；字典 API 不自动继承 JSON 位置，需要调用方显式传入正确的 `base_dir`。这是既有兼容接口差异，不是要求创建或切换到输入子目录。

## 预检与集成验收

先按主入口执行环境自检。以下命令中的 `<skill-root>` 替换为已确认的真实目录；输入输出位于任务工作目录：

```python
execute(command="python -I -S -B \"<skill-root>/scripts/create_docx.py\" ./document.json --preflight")
execute(command="python -I -S -B \"<skill-root>/scripts/create_docx.py\" ./document.json \"客户服务改进方案.docx\" --verify --require-no-placeholders")
```

`--preflight` 只接受输入路径，检查字段、图片引用、生成 XML 和共享预算，不写文档或执行写入探针；不是结构或视觉验收。以上两条仅用于前述兼容/定制调用，按需选择，不串行执行预检与生成；标准新建直接用 `"<skill-root>/scripts/run_docx_task.py" create`（已集成必要环境自检，不另调用check），不再执行这里的第二条生成命令。`--verify` 在临时 ZIP 写完后读取全部成员并检查 CRC、结构及正文文字序列、表格/图片数量、页眉页脚文字，再按输入核对排版意图：块顺序与分页符、标题样式与大纲、列表实例/层级/编号定义、样式及显式文字格式、页面尺寸/边距、表格行列/网格宽度/单元格内容归属/边框/留白/跨页标记、图片显示尺寸/替代文字及引用媒体的 SHA-256。通过后原子发布输出，失败保留已有输出。`--require-no-placeholders` 隐含开启验收，检查跨 run 的双花括号标记；正文需要字面花括号时省略该参数。验收遇到未验证警告同样拒绝发布。成功摘要增加 `layout_intent: true`；这是生成子集的输入与 XML 对照，不计算页码，不做 XSD、字体可用性或视觉验证。

低层 `create_document()` 的 `verify=False` 保持兼容；最终交付使用默认 `verify=True` 的 `create_document_delivery()`，由代码自动归档。所有生成调用在写出前检查共享 XML 预算，避免输出自身校验器拒绝的超限文件。调用 `preflight_document(spec, base_dir=None)` 获取预检摘要，返回块数、部件数和未压缩大小。使用 Python 字典可直接调用 API，无需先写 JSON。CLI 拒绝重复 JSON 键，未知字段在可确定时提示相近字段名；非法字符诊断包含字段路径、从 1 起的字符位置和 Unicode 码位，不静默清洗内容。

生成失败时执行 生成失败修正与有限重试（`<skill-root>/references/validation.md#生成失败修正与有限重试`）：由助手按诊断修正可确定的输入或调用错误，首次之外最多重试 2 次，每次重新完整验收；脚本本身仍为单次执行。安全、资源、环境或能力边界受阻时停止，不绕过校验，也不将旧输出交付为本次结果。

## 交付文件命名

这是助手调用前的命名要求，适用于新建、编辑及其他最终交付物。基础名称由助手按用户意图确定，标准runner负责合法性校验与冲突时的版本递增；代码不从正文推断名称，也不拒绝合法且无冲突的 `output.docx`。

1. 新建和编辑都优先采用用户明确指定的合法文件名；仅在未指定时按主题拟定基础名称，不擅自给指定名称加“修订版”；下述重名递增规则仍适用。名称不合法或与输入同源时停止；标准runner遇到其他同名文件时自动加版本后缀。用户明确禁止改名时，先确认候选名可用，冲突则说明情况，不调用会自动改名的入口。指定名称本身不等于授权覆盖无关文件，不通过改扩展名转换格式。
2. 未指定时，优先提炼文档标题，其次依据任务主题，采用简洁的“主题＋文种”；使用用户的语言，保留有辨识度的对象，去除标题中不适合文件名的标点。不臆造机构、日期、年份或版本，也不默认加入时间戳、随机串或 `questionId`。仅知道文种时可用 `会议纪要.docx` 等已知信息，不为命名虚构主题。
3. 未指定输出名称的编辑结果沿用有意义的原名主题，必要时加 `_修订版`；原名为 `input.docx`、`output.docx` 等泛称时，改从实际内容或任务提炼。输入输出必须不同源。标准runner保留已有文件并递增版本名称；不得以更新意图绕过输入保护。需要保留多轮版本时使用不同名称，不能靠归档目录自动保存历史。
4. 多个交付物分别体现用途，如 `客户服务改进方案.docx` 与 `客户服务改进方案_修改说明.txt`。先确定实际文件名，再调用生成、编辑和归档；归档保持主文件原名，返回实际文件链接。下列名称均为场景示例，不是固定默认值。

| 场景 | 应采用的名称 |
|---|---|
| 制作客户服务改进方案，未指定文件名 | `客户服务改进方案.docx` |
| 标题为“关于开展消防安全培训的通知” | `消防安全培训通知.docx` |
| 修订 `采购管理办法.docx` 并保留原件 | `采购管理办法_修订版.docx` |
| 输入为 `output.docx`，实际内容是设备采购申请 | `设备采购申请_修订版.docx` |
| 用户明确要求保存为 `output.docx`，且不与输入冲突 | `output.docx` |

标准run_docx_task.py新建与run_docx_edit.py编辑由代码同时定点检查主路径和当前配置下的归档同名路径，保留主题，重名时依次选用“报告_第2版.docx”“报告_第3版.docx”；已有版本后缀递增，不重复叠加。最多检查1000个候选，追加版本后缀将超出255字节时，仅缩短主题；原始名称本身超长仍拒绝。只查存在性，不扫描目录或读取历史正文。归档不可访问或配置无效不阻断主文件生成，后续归档按真实结果报告。名称选择不预留路径；DOCX任务锁内重查主路径，归档发布使用原子独占操作。宿主仍须隔离其他并发写者，不保证跨任务全局唯一。旧CLI/低层API不自动改名，调用者须选择新的主路径。最终交付只使用返回的实际output。

## 输出目录与文件名

归档目录默认只保存副本，不作为输入来源或主动检索范围；必要的同名、输入同源及本次副本检查按 归档目录使用边界（`<skill-root>/references/delivery.md#归档目录使用边界`） 执行，默认不读取历史文档内容；用户明确授权的指定归档输入按该边界处理。

命令行第二个参数是单个 DOCX 文件名，不接受 `./output.docx`、目录或完整路径。新建主文件始终生成到宿主确认的任务工作目录 `./`，不是脚本安装目录。生成器在每次写入自检或生成时读取 `ROUTER_CONFIG_VARIABLES`，不在导入时缓存；自检与生成须使用同一任务目录和配置。

| 配置结果 | 主文件 | 生成成功后的归档副本 |
|---|---|---|
| 有效 `questionId` 为 `12345` | `./报告.docx` | `./output/12345/报告.docx` |
| 配置缺失、JSON/字段结构异常、ID 缺失或无效 | `./报告.docx` | 跳过归档，报告固定原因码 |

`ROUTER_CONFIG_VARIABLES` 按宿主实际格式提供外层 JSON 参数列表。先找到唯一 `name="commonParam"` 的项，再取其 `value`；该值通常为 JSON 编码的参数列表字符串，代码再次解析，最后找到唯一 `name="questionId"` 的项并取其 `value`。例如：

```json
[
  {"name": "commonParam", "value": "[{\"name\":\"questionId\",\"value\":\"abc123\"}]"},
  {"name": "otherParam", "value": "something"}
]
```

该例归档目录为 `./output/abc123/`。外层各项和内层各项都须是字典，须有非空字符串类型的 `name`，并包含 `value` 字段，额外字段允许；其他参数不用于选择归档目录。`commonParam.value` 也接受已解码的列表。兼容旧配置 `{"commonParam": [...]}` 或 `{"commonParam": "JSON编码列表"}`，但不接受 `commonParam` 为按参数名索引的字典；不会从其他字段、聊天内容或旧 `requestId` / `idRecord` 推断 `questionId`。

外层缺少 `commonParam` 返回 `missing_common_param`，重复返回 `duplicate_common_param`，外层参数项不合法返回 `invalid_router_config_entry`；外层既非列表也非兼容字典返回 `invalid_router_config_shape`。内层 JSON、列表结构和参数项错误分别沿用 `invalid_common_param_json`、`invalid_common_param_shape`、`invalid_common_param_entry`。内层缺少或重复 `questionId` 分别返回 `missing_question_id`、`duplicate_question_id`。两层 JSON 均拒绝重复对象键及非标准常量；所有配置错误仅跳过归档，不阻止主文件生成。

ID 接受 1–128 个 ASCII 字母、数字、下划线或连字符，也接受非负整数并转成十进制字符串；字符串参数值在通用取值函数中先调用 `.strip()` 去掉两端空白（含空格、制表符、换行等），保留中间内容及非字符串类型；例如 `"  abc123  "` 作为 `abc123` 校验。裁剪后不接受布尔值、浮点数、空串、内部空白、路径或 Windows 保留设备名，无效返回 `invalid_question_id`。仅识别 `questionId`，不将旧参数 `requestId`、`idRecord` 当作别名；仅提供旧参数时跳过归档。两者同时提供时只使用新参数，重复或无效的新参数不回退到旧参数。配置调试默认关闭，开启时仅输出脱敏状态，见 配置调试（`<skill-root>/references/generation.md#配置调试`）。

文件名支持中文和空格，须以 `.docx` 结尾，UTF-8 长度不超过 255 字节；禁止附带目录、路径分隔符、控制字符、Windows 非法字符或保留设备名、尾点及尾空格。主文件和归档路径均拒绝符号链接，包括悬空链接；归档目录不存在时自动创建。拒绝主文件或归档目标与输入 JSON、正文图片或表格内图片同源，包括经符号链接解析的路径及硬链接。图片校验与生成复用同一份已验证输入，不为归档重复读取图片。路径检查依赖宿主隔离同一任务目录中的并发修改，不提供针对恶意并发改写目录的隔离。

先在任务根目录生成临时ZIP，验收成功后发布主文件。归档先在任务根目录./独占创建临时副本，完整复制、flush/fsync及设置权限，再以原子独占硬链接发布，随后删除临时链接；目标已存在或检查后出现时拒绝覆盖。文件系统不支持该能力或跨文件系统时报告归档失败，不回退到覆盖写入、不换暂存目录。只有单独归档调用显式授权更新才原子replace，并保留旧副本权限；新副本继承主文件权限。归档失败保留主文件及旧副本，不重跑生成；暂存删除失败返回temporary_residuals，runner按cleanup_incomplete处理。两份文件顺序发布，不构成跨文件事务。授权更新入口见统一归档（`<skill-root>/references/delivery.md`）。

每次生成成功后向标准输出返回 JSON：`output` 是主文件名，`output_dir` 固定为 `.`；`archive_output` 是副本相对于任务目录的路径，无有效配置时为 `null`；`archive_status` 为 `copied`、`skipped` 或 `failed`。兼容字段 `used_fallback` 和 `fallback_reason` 现在说明配置是否无法提供归档目录及原因，不代表主文件改变位置。正常完成时，未验证的 `status` 为 `created`，验证通过为 `verified` 并附验证字段。无论归档是否成功，`status` 和 `generation_status` 均保留主文件的 `created` 或 `verified` 状态，主文件成功时退出码为 0。归档失败仅设置 `archive_status=failed`、非空 `warnings`，stderr 输出 `archive-warning`；配置解析的异常也隔离在主文件发布后的归档步骤中。摘要使用标准 JSON 转义。后续读取及交付内部核对本次 `output` 和归档状态，可交付成功主文件的正常链接；用户归档提示根据实际状态及用户展示规则（`<skill-root>/references/delivery.md#用户展示与机器元信息`）映射，`archive_message` 保留原机器文案；跳过或失败不得误报完成，不展示内部归档路径和原始日志。

标准自动入口 `"<skill-root>/scripts/run_docx_task.py" create/retry` 内置必要环境自检，失败时返回environment_failed和具体原因；不需要模型先运行check或判断标记。runner的check只提供相同必要条件的诊断，不读取归档配置。此处旧 `"<skill-root>/scripts/create_docx.py" --check` 仍保留兼容诊断行为：检查主目录和归档目录，探针只在 `./` 创建，归档路径/访问权限检查失败只提示。旧入口的诊断不作为标准runner生成前的附加门禁。`--preflight` 保持只读，不解析输出配置、不创建目录或复制文件。

配置解析保留在 `<skill-root>/scripts/output_location.py`：`resolve_output_location(router_config_str)` 不读环境、不操作文件、不打印日志，返回不可变 `OutputLocation(output_dir, used_fallback, fallback_reason)`。保留返回字段名，其 `output_dir` 改为有效配置的 `output/{questionId}` 或失败时的 `.`；命令入口仅在 `used_fallback=False` 时将其作为归档目录，不用它选择主文件位置。模块随 DOCX 完整目录分发，仅依赖标准库。

公开 `create_document_delivery(spec, output, base_dir=None, *, verify=True, require_no_placeholders=False, protected_inputs=())` 与新建 CLI 共用发布流程；主文件成功后自动调用统一归档入口，返回主状态和归档摘要。新建 CLI 保留原验证选项和单文件字段，并增加统一入口的 `files` 明细；归档预检查失败、无文件明细时 `archive_output` 为 `null`，以 `warnings` 解释失败，不猜测副本路径。API 默认开启验证；输入字典来自磁盘时将源 JSON 路径加入 `protected_inputs`。`base_dir` 仅用于图片路径解析，图片只验证读取一次，并自动加入保护清单。完整接口和示例见 公开交付 API（`<skill-root>/references/delivery.md#公开交付-api`）。

低层 `create_document(spec, output, base_dir=None, verify=False, require_no_placeholders=False)` 保持显式路径接口及返回 Path，不读取环境、不自动归档，仅用于中间处理或由调用方显式收尾的场景。五种编辑也提供自动归档的公开交付 API；不要将底层函数的成功误当成归档成功。

## 配置调试

`DOCX_DEBUG_ROUTER_CONFIG` 默认关闭，仅精确值1开启。读取配置时，stderr输出 `router-config-debug: {"configured": true, "values_redacted": true}`；configured表示环境变量是否存在，不证明配置有效。日志绝不包含原始配置、参数值、ID或目录，开启诊断也不恢复原文。stdout业务JSON、原配置解析和错误原因码保持不变；诊断写入失败不改变业务结果。只读检查和不归档的底层API不读取配置。

必要时仅在当前调用环境设置该开关，不修改全局环境，不回显环境变量。归档错误按实际archive_status与固定原因码定位，禁止为了排错将凭据写入提示或日志。

## 主题与文档标题

每次新建文档，用户未指定排版时，在输入中显式设置 `"theme": "official"`，直接使用公文默认值，不先要求用户选择主题。Python 对省略 `theme` 的调用仍默认使用 `official`。只有用户明确选择其他主题或给出格式要求时，才采用对应主题或显式覆盖；不要仅依据文档名称自动切换主题。

使用 `theme` 选择公文 `official`、商务 `business` 或学术 `academic`；显式样式覆盖优先于主题。这些选项均为 DOCX 内的排版预设，不代表公文或学术标准认证，也不代表其他文件格式的导出能力。编辑已有文档不应用新建默认主题。

成功生成、验收并核实文件后，按 生成后格式引导（`<skill-root>/references/generation.md#生成后格式引导`） 组织交付回复。

| 主题 | 正文与西文字体 | 正文排版 | 标题 |
|---|---|---|---|
| official | 仿宋 / Times New Roman | 保持 16pt、固定 28pt、首行 2 字 | 保持原有四级标题 |
| business | 微软雅黑 / Calibri | 10.5pt、1.25 倍、段后 4pt、无首行缩进 | 黑体、深蓝、章节左对齐 |
| academic | 宋体 / Times New Roman | 10.5pt、1.25 倍、段后 4pt、首行 2 字 | 黑体、黑色、章节左对齐 |

主题同时设置表格正文和表头的基础字体、字号、行距；后者保留加粗及居中。字体只记录名称，不安装或嵌入。`title` 和 `subtitle` 块支持 `text`、`format`、`align`，不接受 `level`，不写大纲级别；使用 `styles.title`、`styles.subtitle` 覆盖，默认分别为 22pt 和 14pt。章节继续使用原有 heading 1–4，不改变旧输入的语义。

商务和学术主题的页眉页脚使用独立的 `Header`、`Footer` 段落样式，采用对应主题基础中西文字体、10.5pt、1.25 倍行距、黑色、不加粗、左对齐、无首行缩进、段前段后 0pt。正文的 `styles.body` 覆盖不会改变这些样式；它们是内置 Word 样式，不是新增 JSON 样式字段。纯页码页脚及页码字段结果同样继承 `Footer`。公文主题保留原有基础样式：仿宋 / Times New Roman、16pt、1.15 倍行距、段前段后 0pt。省略页眉页脚且未启用页码时不生成相应部件；显式空字符串保留空部件。字体是否可用及页码实际值仍须在阅读器中核对。

以下仅为用户明确选择商务排版时的示例，默认新建仍使用 `official`：

```json
{"theme":"business","blocks":[
  {"type":"title","text":"项目报告"},
  {"type":"subtitle","text":"季度进展"},
  {"type":"heading","level":1,"text":"一、概况"},
  {"type":"paragraph","text":"正文内容。"}
]}
```

商务和学术列表使用内置 `ListParagraph` 样式，基础中西文字体与主题一致，10.5pt、1.25 倍行距、段前 0pt、段后 4pt、黑色、不加粗、左对齐；不写首行缩进属性，保留编号定义的悬挂缩进。正文覆盖不影响列表。公文列表仍使用原有基础样式，`ordered`、`list_id` 与 `level` 的语义不变；不增加列表样式输入字段。

## 顶层字段

- 使用可选 `theme` 选择命名主题；使用可选布尔值 `deduplicate_images` 复用相同图片字节，默认 `false`。
- 每次生成或预检独立缓存规范化路径的图片字节及尺寸头；同一路径读取一次，不跨调用保存缓存。缓存与包内去重独立，`false` 仍生成独立媒体，各引用的尺寸、替代文字和绘图 ID 独立。累计缓存和待写媒体仍受 128 MiB 预算约束，单图仍最多 32 MiB，最终包预算还包括 XML。
- 使用必需的 `blocks` 数组按顺序描述内容。
- 使用可选 `page` 设置单节页面；默认 A4、上下 25.4mm、左右 31.8mm 页边距。横向页面交换宽高。
- 使用可选 `styles` 设置正文、四级标题及表格正文/表头的样式；未提供的字段保留对应默认值。
- 使用可选 `table_format` 设置所有表格的布局；允许单张表格使用 `format` 覆盖。
- 使用 `header`、`footer` 字符串设置普通页眉页脚；使用 `page_numbers: true` 插入 PAGE 字段。不要把字段缓存值当作已计算的页码。

## 默认格式与页面

使用公文主题 `official` 且省略 `page`、`styles` 和局部格式时，生成以下公文排版预设；不要将其等同于完整公文标准认证。

| 对象 | 中文字体 | 字号 | 对齐 | 加粗 | 首行缩进 | 行距 |
|---|---|---|---|---|---|---|
| `heading1` | 方正小标宋简体 | 18pt | 居中 | 否 | 0 字 | 1.15 倍 |
| `heading2` | 黑体 | 16pt | 左对齐 | 否 | 0 字 | 1.15 倍 |
| `heading3` | 楷体 | 16pt | 左对齐 | 是 | 0 字 | 1.15 倍 |
| `heading4` | 仿宋 | 16pt | 左对齐 | 否 | 0 字 | 1.15 倍 |
| `body` | 仿宋 | 16pt | 左对齐 | 否 | 2 字 | 固定 28pt |

使用 Times New Roman 作为默认西文字体；使用零段前、段后间距。通过 `BodyText` 段落样式应用正文格式，不要插入空格模拟首行缩进。让标题、表格、列表、图片、分页及页眉页脚使用各自样式或基础样式，避免继承正文的缩进和固定行距。基础样式使用仿宋 16pt、Times New Roman、1.15 倍行距；保留列表自身的编号缩进。表格正文和表头分别使用独立的 `TableBody`、`TableHeader` 段落样式。

仅记录字体名称；不要安装、下载或嵌入字体，不要声称已验证阅读端字体可用。只为新建文档应用这些默认值；受控编辑保持已有文档样式。

| `page` 字段 | 单位 | 默认值与规则 |
|---|---|---|
| `width_mm`、`height_mm` | mm | 210、297；保留单节页面。 |
| `margin_top_mm`、`margin_bottom_mm` | mm | 25.4、25.4。 |
| `margin_left_mm`、`margin_right_mm` | mm | 31.8、31.8。 |
| `margin_mm` | mm | 可选，统一覆盖四边；仍允许用单边字段再次覆盖。 |

按“单边字段 > `margin_mm` > 默认单边值”计算边距。例如 `{"margin_mm": 20, "margin_top_mm": 25.4}` 表示顶部 25.4mm，其余三边 20mm。不要将厘米值误写为毫米。所有提供的字段均须有效，即使某字段随后被更高优先级覆盖；边距不得为负，换算后须保留正的可用宽高。

按扣除实际左右边距后的宽度限制表格和图片；默认可用宽度约为 146.4mm。省略 `widths_mm` 时均分可用宽度，显式列宽之和不得超过可用宽度；图片还须满足扣除上下边距后的可用高度。不要通过超出页面可用尺寸的值模拟浮动对象。

## 样式字段与覆盖顺序

仅在 `styles` 中使用 `title`、`subtitle`、`body`、`heading1`、`heading2`、`heading3`、`heading4`、`table_body`、`table_header`；不要传入任意 Word 样式 ID。每一项均为部分字段对象。标题及普通段落可使用 `format` 对象覆盖下列字段；表格文字的默认值和覆盖方式见下文：

| 字段 | 类型及范围 | 用途 |
|---|---|---|
| `font_east_asia` | 非空单行字符串，最多 64 字符 | 中文字体名称。 |
| `font_latin` | 非空单行字符串，最多 64 字符 | 西文字体名称。 |
| `size_pt` | 数值，1–200 | 字号，单位 pt。 |
| `bold` | 布尔值 | `false` 显式取消继承的加粗。 |
| `align` | `left`、`center`、`right`、`justify` | 段落对齐。 |
| `first_line_chars` | 数值，0–20 | 首行缩进字符数；0 取消首行缩进。 |
| `line_spacing_pt` | 数值，1–400 | 固定行距，单位 pt；不接受倍数或自动行距字符串。 |
| `line_spacing_multiple` | 数值，0.5–10 | 正文、标题及表格均支持倍数行距；与同层固定行距互斥。 |
| `color` | 六位十六进制 RGB，不带 `#` | 正文、标题及表格均支持文字颜色。 |
| `space_before_pt`、`space_after_pt` | 数值，0–400 | 段前、段后间距，单位 pt。 |

按字段依次覆盖：内置主题默认值 → 对应 `styles` 项 → 当前块 `format` → 块级 `align`（仅对齐）→ run 字符字段（仅文字格式）。省略字段表示继承；禁止使用 `null` 表示默认值。不要用字符串、布尔值代替数值，不要使用 NaN、Infinity、负值或未知字段。

普通段落与标题通过 `BodyText`、`Heading1` 至 `Heading4` 继承对应样式；仅将块和 run 显式指定的格式写为直接属性。后续修改 Word 样式可影响未局部覆盖的属性；显式覆盖即使与样式当前值相同也保留。

使用 run 的 `font_east_asia`、`font_latin` 分别控制中西文字体。保留 run 的旧字段 `font`：它同时指定中西文字体；同一 run 中的专用字体字段再覆盖相应字体。例如 `{"text": "中文 ABC", "font": "Arial", "font_east_asia": "仿宋"}` 使用仿宋显示中文、Arial 显示西文。不要将 run 的 `font` 放入 `styles` 或 `format`。

允许 run 的 `bold`、`italic`、`underline` 显式设为 `false`；不要通过省略字段尝试取消已继承的格式。仅覆盖已声明字段，不要假定修改字号会自动调整行距或首行字符缩进。

## 内容块

| type | 字段 | 约束 |
|---|---|---|
| title / subtitle | `text`, `format`, `align` | 文档标题与副标题，不接受 level，不占大纲层级。 |
| heading | `text`, `level`, `format`, `align` | 明确提供 level，范围为 1–4；format 和 align 可选。 |
| paragraph | `text` 或 `runs`, `format`, `align` | 不要同时给 text/runs；align 为 left、center、right、justify。 |
| list | `items`, `ordered`, `list_id`, `level` | items 为字符串数组；旧单层用法不变，命名实例可按明确层级续号。 |
| table | `rows`, `widths_mm`, `header`, `header_rows`, `format`, `body_format`, `header_format` | rows 为规则二维数组，单元格为字符串或受限 text/image/borders 对象；可指定列宽及前置表头行数。 |
| image | `path`, `width_mm`, `height_mm`, `alt` | 仅嵌入 PNG/JPEG；至少给定宽或高，缺少的尺寸由尺寸头等比例推算。 |
| page_break | 无其他字段 | 插入手动分页。 |

在 `runs` 中使用 `text`、`bold`、`italic`、`underline`、`font`、`font_east_asia`、`font_latin`、`size_pt`、`color`；颜色使用不带井号的六位十六进制 RGB。让 XML 库处理文本转义，禁止预先将 `&` 转成 `&amp;`。

将文本字段限制为单行，禁止插入控制字符；多段文字拆成多个 paragraph 块。提供 image 的 `alt` 描述（允许空字符串），只给宽或高时读取 PNG IHDR（含头部 CRC）或 JPEG SOF 尺寸头推算比例；两项都给时保持显式尺寸。尺寸头检查不等于像素解码，不按 EXIF 方向旋转。

按以下正反例构造输入；不要把自然语言需求直接当作生成器字段：

| 需求 | 使用 | 不要使用 |
|---|---|---|
| 加粗文字 | `{"text": "结论", "bold": true}` | `{"text": "结论", "bold": "true"}` |
| 两段内容 | 两个 paragraph 块 | 在单个 text 中嵌入换行 |
| 填写公司名称 | 单个 run 中完整的 `{{ company }}` | 将占位符拆分到多个 run |

```json
{
  "header": "季度进展",
  "footer": "内部资料",
  "page_numbers": true,
  "blocks": [
    {"type": "heading", "level": 1, "text": "项目报告"},
    {"type": "paragraph", "runs": [
      {"text": "结论：", "bold": true},
      {"text": "按期完成", "color": "008000", "size_pt": 12}
    ]},
    {"type": "list", "ordered": false, "items": ["完成复核", "安排交付"]},
    {"type": "table", "header": true, "widths_mm": [70, 70],
      "rows": [["项目", "状态"], ["验证", "通过"]]}
  ]
}
```

## 表格配置与示例

在顶层 `table_format` 或单表 `format` 使用以下便捷字段；同层显式字段优先于预设默认值：

| 字段 | 规则 |
|---|---|
| `preset` | `grid`、`three_line`、`zebra`；省略保持原有框线表。显式选择预设重设六边的样式与粗细；同层全局边框字段再覆盖预设，逐边字段优先。 |
| `column_sizing` | `equal`（默认）或 `content`；显式 `widths_mm` 始终优先。内容估算按中西文字符权重平方根分配，保留每列留白及至少 8mm 内容宽；无法满足则报错。不是字体测量或阅读器 AutoFit。 |
| `band_fill` | 六位 RGB 或 `none`；每隔一个数据行设置底色，表头不计入数据行。单元格条纹覆盖表格统一底色；显式 `none` 可关闭。 |

`three_line` 默认顶底线 1.5pt、无竖线和内部横线；已声明表头且存在数据行时，在最后表头行之后加 0.75pt 分隔线。显式单元格共享边优先，原有冲突检测继续生效。`zebra` 默认无框线，第二个及后续偶数数据行底色为 `F2F4F7`。需要表头样式必须显式声明 `header` 或 `header_rows`，不猜测第一行。


参照 GB/T 9704—2012 第 5.2.2 条的一般字体字号原则使用仿宋 16pt；将边框、留白、加粗、对齐、行距与跨页控制标为项目约定，不要声称这些具体参数均由国标规定。保留当前页面边距；不要将本预设宣称为整份文档的国标符合性检查。国标第 8 章的横排表格页码和单双页表头朝向需要另外的分节与分页能力，当前不提供。

为 `styles.table_body` 和 `styles.table_header` 分别设置表格文字样式；不要让它们继承 `styles.body` 或相互继承。默认均为仿宋 16pt、西文 Times New Roman、黑色、首行缩进 0、段前段后 0、1.15 倍行距；正文不加粗且左对齐，表头加粗且居中。

表格文字与普通正文共用上述格式字段；以下列出颜色及倍数行距的表格默认值：

| 字段 | 类型及范围 | 用法 |
|---|---|---|
| `color` | 六位十六进制 RGB，不带 `#` | 默认 `"000000"`。 |
| `line_spacing_multiple` | 数值，0.5–10 | 默认 1.15；使用倍数行距。 |

在同一对象中仅提供 `line_spacing_pt` 或 `line_spacing_multiple` 之一；不要同时传入。后续层显式指定一种行距时替换前一层的行距模式，例如从固定 24pt 改为 1.5 倍。普通段落、标题和表格文字样式均接受颜色及倍数行距。

按“内置表格文字默认值 → `styles.table_body` / `styles.table_header` → 当前表格的 `body_format` / `header_format`”逐字段覆盖。正文和表头分别计算，不要将 `body_format` 当作整张表格的通用文字覆盖；需要统一字体时分别设置两者。单元格默认用字符串，或按 列表与表格细节（`<skill-root>/references/generation-details.md`） 使用严格的 text/image/borders 对象；不接受任意 run、段落或合并布局。

将公共文字格式保存在 `TableBody` / `TableHeader`，各单元格仅引用样式；仅写出单表显式 `body_format` / `header_format` 的直接属性。显式覆盖即使与全局样式相同也保留，`false` 及固定/倍数行距切换仍有效。后续在 Word 中修改公共样式可影响未局部覆盖的属性；不要仅检查单元格直接属性来判断最终格式。

在顶层 `table_format` 和表格块的 `format` 中使用同一组布局字段；按“内置布局默认值 → 顶层 `table_format` → 单表 `format`”覆盖：

| 字段 | 类型及范围 | 默认值 |
|---|---|---|
| `align` | `left`、`center`、`right`；整张表的位置 | `left` |
| `vertical_align` | `top`、`center`、`bottom`；单元格内容位置 | `center` |
| `border_style` | `single` 或 `none`；作用于内外框 | `single` |
| `border_width_pt` | 数值，0.25–12；单位 pt | 0.5 |
| `border_color` | 六位十六进制 RGB，不带 `#` | `"000000"` |
| `cell_fill` | 六位十六进制 RGB 或 `"none"`；所有单元格底色 | `"none"` |
| `cell_margin_top_mm`、`cell_margin_bottom_mm` | 数值，0–50；单位 mm | 各 1 |
| `cell_margin_left_mm`、`cell_margin_right_mm` | 数值，0–50；单位 mm | 各 1.5 |
| `repeat_header` | 布尔值；对声明的所有前置表头行生效 | `true` |
| `allow_row_split` | 布尔值；是否允许单行跨页拆分 | `false` |

- 使用 `body_format.align` / `header_format.align` 控制文字水平对齐，使用 `format.align` 控制整张表的位置；不要混淆两者。
- 省略 `widths_mm` 时均分页面可用宽度；指定列宽时保留原有规则。列宽包含单元格留白，换算到文档单位后左右留白之和须小于每列宽度。需要窄列时显式减小留白，不要依赖阅读器自动挤压表格。
- 保持自动行高；不要传入 `row_height` 等未声明字段，不要以固定行高压缩内容。重复表头和避免拆行是分页请求，不保证超高行保持完整，也不验证实际页数。
- `header: true` 将第一行作为表头，`header_rows` 可明确指定连续前置表头数量；关闭 `repeat_header` 仅关闭重复，不取消表头格式。未声明表头时，不自动识别第一行。
- 使用 `border_style: "none"` 关闭边框，使用 `cell_fill: "none"` 清除全局底色；不要使用 `null` 表示关闭。所有提供的字段均须有效，即使之后被覆盖或当前不生效；拒绝未知字段、错误类型和非有限数值。

使用 `format.borders` 逐边设置表格边框，使用单元格对象的 `borders` 覆盖其 top/left/bottom/right；按 列表与表格细节（`<skill-root>/references/generation-details.md`） 核对继承、共享边冲突、层级续号、`header_rows` 和单元格图片规则。

保留上文原有中英文示例的内容。需要覆盖表格样式时，使用下面的完整中英混排示例；其中的 14pt 是显式覆盖，默认仍为 16pt：

```json
{
  "styles": {
    "table_body": {"size_pt": 14, "line_spacing_multiple": 1.2},
    "table_header": {"size_pt": 14, "bold": true}
  },
  "table_format": {"border_width_pt": 0.75, "cell_fill": "F2F2F2"},
  "blocks": [
    {"type": "heading", "level": 1, "text": "项目清单 / Project list"},
    {"type": "table", "header": true, "widths_mm": [70, 70],
      "format": {"cell_fill": "none", "cell_margin_top_mm": 0, "repeat_header": false},
      "body_format": {"line_spacing_pt": 22},
      "header_format": {"font_east_asia": "黑体", "bold": false},
      "rows": [["项目 / Item", "状态 / Status"], ["材料复核", "已完成 / Done"]]}
  ]
}
```

核对该例：表格文字 14pt；正文使用固定 22pt 行距，表头仍为 1.15 倍行距；表头改为黑体、不加粗，继续居中；边框为 0.75pt、无底色、顶部留白为 0；表头不跨页重复。普通标题和正文仍沿用原有公文预设，不要把单表覆盖保存为下一份文档的默认值。

## 显式覆盖示例

保留默认字体，分别覆盖正文、一级标题、单个段落和局部文字。下面的JSON仅展示完整字段结构；实际复杂输入使用Python构造并序列化后传stdin，见复杂JSON模板（`<skill-root>/references/complex-json.md`），不要手写拼接或直接落盘：

```json
{
  "page": {"margin_mm": 20, "margin_top_mm": 25.4},
  "styles": {
    "body": {"size_pt": 14, "line_spacing_pt": 24},
    "heading1": {"size_pt": 20, "align": "left"}
  },
  "blocks": [
    {"type": "heading", "level": 1, "text": "项目报告 / Project report",
      "format": {"size_pt": 18}, "align": "center"},
    {"type": "heading", "level": 2, "text": "一、工作进展"},
    {"type": "heading", "level": 3, "text": "（一）阶段成果"},
    {"type": "heading", "level": 4, "text": "1. 复核情况"},
    {"type": "paragraph", "text": "负责人说：“已完成复核。” Status: reviewed."},
    {"type": "paragraph", "format": {"first_line_chars": 0, "bold": true},
      "runs": [
        {"text": "说明："},
        {"text": "此句取消加粗，西文使用 Arial。", "bold": false, "font_latin": "Arial"}
      ]}
  ]
}
```

核对生成结果：顶部边距 25.4mm、其余三边 20mm；一级标题按块覆盖为 18pt 居中；普通正文为 14pt、固定 24pt；最后一段取消首行缩进，第二个 run 取消加粗并仅覆盖西文字体。二至四级标题继续使用各自默认值。不要把一次调用的覆盖保存成下一份文档的新默认值。

## 中文 JSON 与引号

使用 UTF-8 编码保存输入。保留英文半角结构符号 `"`、`:`、`,`、`{}`、`[]`；中文文字、中文标点和中文引号可以作为字符串内容。不要翻译字段名和 `type` 等枚举值，不要加入注释或尾随逗号。

使用下面的合法段落块；正文中的中文引号无需转义，正文中的英文双引号使用 `\"` 转义：

```json
{"type": "paragraph", "text": "负责人说：“请在周五前完成复核。”"}
```

```json
{"type": "paragraph", "text": "负责人说：\"请在周五前完成复核。\""}
```

将这些段落块放入完整输入的 `blocks` 数组，不要直接把单个段落块作为生成器的顶层输入。

不要使用下列写法；它们是错误示例，不是可运行 JSON：

```text
{“type”: “paragraph”, “text”: “中文内容”}
{"type": "paragraph", "text": "负责人说："请完成复核。""}
```

第一例使用了中文语法引号；第二例没有转义正文中的英文双引号。不要把正文中合法的中文引号全部替换掉。出现 `JSONDecodeError` 时，根据报错的行号、列号检查该位置及其前面的引号、冒号和逗号；仅凭“有中文引号”无法确定原始输入的具体错误。

复杂输入统一使用Python调用模板（`<skill-root>/references/complex-json.md`）：构造dict/list后通过json.dumps(..., ensure_ascii=False, allow_nan=False)序列化，传给标准runner的stdin，不另建document.json。ensure_ascii=False保留可读中文，序列化器负责必要转义，但不会修复已写错的JSON或Python源码，也不代替业务校验。

仅在已确认的兼容/定制流程确需配置文件时，先按任务清单（`<skill-root>/references/task-cleanup.md`）登记尚不存在的任务根目录文件名，再独占创建；成功、失败或停止都由外层在子进程退出后seal/clean并核对清单删除，不能用write_text覆盖既存输入。用户提供的既存JSON属于受保护输入，可位于任务目录及其子目录；本轮新增的临时JSON只能平铺于`./`。新建主文件位于任务根目录，编辑输出遵循主入口的相对路径规则；不得将任务数据写入Skill安装目录。

## 生成后格式引导

仅在新建文档生成、验收及文件核实成功后，先提供本次文件链接和必要的检查结果，再在同一交付回复中附一句简短引导，默认每次成功新建只提示一次。公文主题且没有样式覆盖时使用：

> 本次已按公文格式生成。还支持商务、学术排版，也可按需调整字体、字号和页边距。

用户选择其他主题时，如实说明实际采用的排版，并介绍其余可选主题；存在样式覆盖时说明已按其格式要求生成，不将其描述为未经修改的默认公文格式。上述选项都是 DOCX 内的排版方式，不代表新增 PDF 等文件格式导出能力。用户明确要求只返回文件或不再提示时省略引导。

引导仅出现在交付回复，不写入文档正文、页眉页脚或输入内容，不为展示选项自动生成多份文件，也不要求用户先确认才能拿到已完成的文件。预检、环境自检、生成失败、等待修正或已有文档的读取/受控编辑不触发该新建引导。主题选择及样式范围见 主题与文档标题（`<skill-root>/references/generation.md#主题与文档标题`）。
