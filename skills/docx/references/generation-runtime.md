# 生成器执行、文件命名与归档详细契约

路径占位符说明：`<skill-root>` 是本 DOCX Skill 根目录的文档占位符，不是 Shell 变量。取得宿主本次实际读取主入口 `SKILL.md` 的真实 `file_path`；若为相对路径，先按本次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名，所得目录才是根目录，不能使用参考文档所在目录。读取资源或执行命令前，将 `<skill-root>` 整体替换为该真实目录，保留资源后缀，将完整路径作为一个参数正确引用；嵌入 execute 字符串时按其语法转义。无法确认主入口真实路径时停止依赖操作并报告，禁止猜测路径。示例的工作目录为宿主已确认的任务目录 `./`，任务输入、输出和临时文件均在该目录及其子目录内，不写入 Skill 安装目录。

本页仅供对应任务按需加载。定位 `<skill-root>` 与任务目录、执行限制以主入口 `<skill-root>/SKILL.md` 为准；本页提供完整原始字段契约。

## 目录

- 兼容旧生成入口（`<skill-root>/references/generation-runtime.md#兼容旧生成入口`）
- 路径解析示例（`<skill-root>/references/generation-runtime.md#路径解析示例`）
- 预检与集成验收（`<skill-root>/references/generation-runtime.md#预检与集成验收`）
- 交付文件命名（`<skill-root>/references/generation-runtime.md#交付文件命名`）
- 输出目录与文件名（`<skill-root>/references/generation-runtime.md#输出目录与文件名`）
- 配置调试（`<skill-root>/references/generation-runtime.md#配置调试`）
- 生成后格式引导（`<skill-root>/references/generation-runtime.md#生成后格式引导`）

## 兼容旧生成入口

标准新建直接使用已集成环境判断的自动任务入口（`<skill-root>/references/task-cleanup.md#自动新建任务入口`）：规格JSON直接传stdin，内部创建的worker.py和spec.json在成功与失败时均按自动入口收尾。旧 `<skill-root>/scripts/create_docx.py` 生成 CLI 已弃用但暂时保留，仅在已有集成或明确需要定制调用且自行管理辅助文件时使用；生成时只向 stderr 提示，stdout JSON、返回值和退出码保持兼容。`--check`、`--preflight` 与库函数未弃用，不以自动入口替代这些专用能力。下文旧生成 CLI 示例均受此前提约束。下文JSON字段不变；create_docx.py的文件输入示例为兼容/定制调用，调用方辅助文件不会因此自动删除。

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

`--preflight` 只接受输入路径，检查字段、图片引用、生成 XML 和共享预算，不写文档或执行写入探针；不是结构或视觉验收。以上两条仅用于前述兼容/定制调用，按需选择，不串行执行预检与生成；标准新建直接用 `"<skill-root>/scripts/run_docx_task.py" create`（已集成必要环境自检，不另调用check），不再执行这里的第二条生成命令。`--verify` 在临时 ZIP 写完后读取全部成员并检查 CRC、结构及正文文字序列、表格/图片数量、页眉页脚文字，再按输入核对排版意图：块顺序与分页符、标题样式与大纲、列表实例/层级/编号定义、样式及显式文字格式、页面尺寸/边距、表格行列/网格宽度/单元格内容归属/边框/留白/跨页标记、图片显示尺寸/替代文字及引用媒体的 SHA-256。通过后原子发布输出，失败保留已有输出。`--require-no-placeholders` 隐含开启验收，检查跨 run 的双花括号标记；正文需要字面花括号时省略该参数。验收遇到未验证警告同样拒绝发布。成功摘要增加 `layout_intent: true`；`--verify` 还检查排版属性的静态 OOXML 次序与重复风险；这是生成子集的输入与 XML 对照，不计算页码，不做 XSD、字体可用性或视觉验证。

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

ID 接受 1–128 个 ASCII 字母、数字、下划线或连字符，也接受非负整数并转成十进制字符串；字符串参数值在通用取值函数中先调用 `.strip()` 去掉两端空白（含空格、制表符、换行等），保留中间内容及非字符串类型；例如 `"  abc123  "` 作为 `abc123` 校验。裁剪后不接受布尔值、浮点数、空串、内部空白、路径或 Windows 保留设备名，无效返回 `invalid_question_id`。仅识别 `questionId`，不将旧参数 `requestId`、`idRecord` 当作别名；仅提供旧参数时跳过归档。两者同时提供时只使用新参数，重复或无效的新参数不回退到旧参数。配置调试默认关闭，开启时仅输出脱敏状态，见 配置调试（`<skill-root>/references/generation-runtime.md#配置调试`）。

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

## 生成后格式引导

> 本节保留作历史解释。日常成功交付直接使用 `SKILL.md` 的简短规则，不为这一引导加载完整 runtime 说明。


仅在新建文档生成、验收及文件核实成功后，先提供本次文件链接和必要的检查结果，再在同一交付回复中附一句简短引导，默认每次成功新建只提示一次。公文主题且没有样式覆盖时使用：

> 本次已按公文格式生成。还支持商务、学术排版，也可按需调整字体、字号和页边距。

用户选择其他主题时，如实说明实际采用的排版，并介绍其余可选主题；存在样式覆盖时说明已按其格式要求生成，不将其描述为未经修改的默认公文格式。上述选项都是 DOCX 内的排版方式，不代表新增 PDF 等文件格式导出能力。用户明确要求只返回文件或不再提示时省略引导。

引导仅出现在交付回复，不写入文档正文、页眉页脚或输入内容，不为展示选项自动生成多份文件，也不要求用户先确认才能拿到已完成的文件。预检、环境自检、生成失败、等待修正或已有文档的读取/受控编辑不触发该新建引导。主题选择及样式范围见 主题与文档标题（`<skill-root>/references/generation-styles.md#主题与文档标题`）。


