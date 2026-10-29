# 自检结果与执行回传

## 入口盘点

本次盘点覆盖仓库全部7个Skill。下表脚本路径均相对于各Skill真实目录，执行前替换实际路径，任务工作目录由宿主确认。开发规范本身未修改；纯文本写作不增加无用脚本。

| Skill | 入口 | 检查类型与原结果契约 |
|---|---|---|
| docx | `scripts/read_docx.py --check` | 只读环境检查；stdout的read-check-ok，失败stderr的check-failed，退出0/1 |
| docx | `scripts/run_docx_task.py check` | 环境、目录探针与锁；stdout的check-ok，失败stderr的auto-cleanup-check-failed，退出0/1 |
| docx | `scripts/create_docx.py --check`、`scripts/archive_deliverables.py --check` | 环境与目录能力；stdout的check-ok，失败stderr的check-failed，退出0/1；可选归档警告不等于主环境失败 |
| xlsx | `scripts/stdlib_xlsx.py check` | 导入、模板及有限计算；单个JSON的ready/failed/unsupported，退出0/1 |
| wps | `scripts/read_wps.py --check` | 依赖与版本；stdout的environment-ok及OOXML可用性；受控失败stderr的environment-failed，依赖失败退出1，Python不足退出2 |
| ofd | `scripts/read_ofd.py --check` | 版本、zlib往返、安全文件操作能力；成功文本environment-ok；失败仍为JSON的failed/unsupported及diagnostics，退出1/4 |
| artifact-lifecycle | `scripts/lifecycle.py check` | 安全文件操作与本轮写入探针；单个JSON的environment-ok/error，退出0/1 |
| bank-writing | 无脚本 | 人工确认任务、素材及资源可读；缺条件时说明具体缺口。无进程、stdout/stderr或退出码，不虚构执行证据 |
| skill-development-standards | 入口Python版本检查、检查器`--help`；`scripts/check_skill.py 目标 --strict` | 版本命令已打印environment-ok，失败为断言诊断/非零退出；help是入口可用性信息。结构检查输出OK或ERROR/WARNING，不是环境/业务产物验收 |

8个可执行环境自检入口增加 `check_kind=environment artifact_validation=not_run`。文本成功标记仍在stdout，文本受控失败标记在stderr；JSON入口只追加同名字段，不在JSON前后插入普通文字。未进入main的导入失败、语法错误或参数错误可能只有stderr诊断及非零退出，仍必须完整回传，不能冒称自检已运行。

环境自检不产生业务文档，也不证明产物存在。DOCX的`office/validate.py`已有结构验收通过/失败提示及未执行XSD/视觉验证的边界；WPS样本验收脚本已有JSON结果；XLSX的写入/计算结果使用原status、output_written及计算诊断。这些不当作环境自检，也不因增加自检标记而扩大验收能力。XLSX legacy检查工具仍不进入维护后的业务流程。

## 仓库级回传适配器

`tools/run_skill_check.py`用于仓库维护与宿主接入测试，标准库实现，不随独立Skill分发，不是任意命令执行器。它只调用固定的上述8个环境入口或现有结构检查器，使用启动它的已确认解释器及`-I -S -B`，不安装依赖、不改变Skill原输出、不生成业务文档。使用前确认相应入口所需环境；不同Skill的版本及平台要求仍各自适用。

在宿主已确认的任务目录运行以下命令，先将`<repo_dir>`替换为已确认的仓库真实路径，不能按独立Skill安装位置猜测仓库路径：

```console
python -I -S -B "<repo_dir>/tools/run_skill_check.py" docx-read --task-dir .
python -I -S -B "<repo_dir>/tools/run_skill_check.py" xlsx --task-dir .
python -I -S -B "<repo_dir>/tools/run_skill_check.py" skill-structure --skill docx --task-dir .
```

以上是三次独立调用示例，按需要选择，不要求全部执行。环境入口标识还有docx-task、docx-create、docx-archive、wps、ofd和artifact-lifecycle。`--task-dir`必填，仅允许当前已确认工作目录及其子目录，不接收绝对/父目录路径；不为适配器另建无用目录。不得把独立Skill安装目录当任务目录。`--timeout`默认30秒，必须大于0且不超过60秒。

适配器输出一个JSON，包含：

| 字段 | 含义 |
|---|---|
| invocation_id、check_id、argv | 对应调用标识、固定入口、实际参数；仅供内部诊断 |
| check_kind | environment或skill_structure，不冒充业务产物验收 |
| artifact_validation | 固定not_run，不能据自检声称产物已验收 |
| execution_status | completed、not_started、timed_out、signaled |
| exit_code | 子进程正常结束的真实退出码；无正常退出码时null |
| returncode、signal | 实际取得的子进程返回值及信号（如有）；信号终止不伪装成正常退出 |
| stdout、stderr | 分离的原输出文本，非零退出仍保留；空通道为字符串空值 |
| stdout_bytes、stderr_bytes | 实际捕获的通道字节数；超时仅表示已捕获部分，不声称进程全部输出长度 |
| *_truncated、*_decode_errors | 每通道最多回传65536字节；截断或UTF-8替换明确标记，不把残缺JSON判成功 |
| check_status | passed需正常退出0、完整可解码输出及预期标记/JSON；非零为failed；输出不全/标记缺失/超时/信号为unconfirmed；未启动为not_run |
| message | 适配层诊断，不伪装为子脚本stdout/stderr |

适配器自己的进程退出码：检查确认通过为0，其余结果为1，适配器参数解析错误为2。不要把适配器退出码当成JSON里的子进程exit_code。成功但有可选stderr警告仍保留该通道，不能因有stderr就宣称全部失败。

对JSON入口，原JSON以完整字符串保存在stdout，只有通道未截断且解码完整时才进一步解析。适配器使用`check=False`，不会因非零退出丢弃stdout。超时会终止并等待它启动的子进程，保留已捕获输出；仅覆盖这些不启动后台后代的固定自检命令，不宣称通用进程树管理能力。

## 专属execute的接入边界

本仓库没有用户项目专属execute实现。维护验证直接执行`execute(command=...)`里的Python命令，不调用execute表示式，也不修改其专属模式。独立Skill继续使用原命令；若宿主原生支持可靠的stdout/stderr/退出码结果，优先直接使用，不要求额外包装。

宿主需把完整结果作为对应调用的工具返回消息送给模型，不能仅打印到服务端日志或只返回“执行成功”。可以接入上面的适配器，或让现有执行器直接构造等价结果；仓库脚本不能替代该最后一步。参数验证失败、进程未启动与脚本执行失败必须分层。宿主在适配器之外取消、强杀或截断时也须明确状态，不能依赖已被中止的脚本自行报告。

模型同时检查执行状态、真实退出码、成功标记和检查范围，缺字段或缺少工具返回时报告无法确认，不重跑生成来掩盖不确定性。最终用户只看到必要结论，内部路径、配置、敏感正文和完整诊断不直接转贴。现有归档提示边界继续适用。

## 验证证据与限制

2026-10-29，macOS，宿主提供Python3.12.14、Expat2.8.3，全部命令在仓库根运行并保留`-I -S -B`，未新增依赖。以下`python`为该已确认解释器，不是系统默认旧版Python：

- `python -I -S -B -m unittest discover -s tests -p 'test_check_execution.py' -v`：12项通过，退出0。真实受控子进程覆盖双通道/非零退出、超时已有输出、信号终止、标记缺失、截断、非UTF-8输出；模拟启动失败；真实WPS/XLSX适配器CLI及结构检查。测试不代表远程宿主送达验证。
- `python -I -S -B tests/test_self_check_contracts.py -v`：8项通过，退出0。每项包含独立子进程成功与故障注入，覆盖全部8个环境入口、WPS旧Python、OFD不支持状态、JSON可解析、历史文件保持、没有业务产物生成。
- `python -I -S -B -m unittest discover -s skills/<目标>/scripts -p 'test_*.py' -q`：docx 249、xlsx 293、wps 424、ofd 38、artifact-lifecycle 15项均通过，退出0，无跳过。既有生成/编辑/读取JSON与产物行为回归保持。
- 5个修改Skill的严格结构检查通过；新增Python标识符及引用人工复核，未执行专用命名linter。description的使用范围不变，保留原文，依赖不变。
- `python -I -S -B -m unittest discover -s tests -p 'test_artifact_lifecycle_parity.py' -v`：DOCX与公共副本/来源哈希一致，1项通过，退出0；`git diff --check`通过。
- 额外执行`python -I -S -B -m unittest discover -s tests -p 'test_xlsx_lifecycle_parity.py' -v`：退出1，router_config.py、artifact_files.py、controlled_cleanup.py三个子项失败。公共版本与XLSX内置副本已有差异；相关双方源码及该测试相对HEAD无修改，确认不是本次自检变更引入。保留为范围外既有例外，不更新哈希或复制实现掩盖失败。

三个层次分别记录：脚本层由子进程测试捕获stdout/stderr/真实退出码；适配层JSON载荷包含分离通道及状态；本次工具实际返回8个环境检查载荷和1个参数未启动载荷，主代理收到全部字段（包括DOCX可选归档警告的stderr和空通道）。这只证明当前本地工具链；用户专属execute、真实Linux宿主及其模型消息投递未验证。

未执行Linux容器、Windows、真实用户文档验收、视觉验收或自动选Skill测试，未修改分发ZIP。公共生命周期修改仅涉及自有CLI与说明，共享实现及来源哈希保持原样。没有修改开发规范规则/检查器，也没有接管或删除历史任务文件。

Subagent execution record：一次调用luna__check_contract_tests，luna_worker / gpt-6-luna / max，仅负责tests/test_self_check_contracts.py。Main-agent work：入口盘点、行为与边界决定、实现、适配器及其测试、说明、回归、证据解释、代码审查和最终验收。
