# 任务清单清理工具

先完成[主入口自检](../SKILL.md#执行环境约束与首步自检)。本工具只处理本次任务明确登记的辅助文件，不生成、归档或验收 DOCX 内容；原生成/编辑 API 保持兼容，不自动删除它们收到的 JSON、图片或其他输入。标准新建入口自动管理它创建的辅助文件；未使用该入口的自定义/编辑任务仍由助手显式登记并收尾，不能只完成生成就声称已清理。

## 目录

- [可复用核心与兼容入口](#可复用核心与兼容入口)

- [自动新建任务入口](#自动新建任务入口)
- [时机与保护](#时机与保护)
- [三阶段命令](#三阶段命令)
- [结果和残留](#结果和残留)
- [环境与中断边界](#环境与中断边界)

## 自动新建任务入口

正常新建只需主入口中的create一次调用和返回结果核对，无须先通读本页。下述所有权、重试事务及恢复细节供失败诊断、自定义调用和维护使用；自动清理成功后不再手工 plan/seal/clean。

标准新建直接执行 `run_docx_task.py create 文件名.docx`，从stdin接收规格（主入口有完整例子）。create/retry在代码中调用task_environment，检查Python及自带zlib/XML、Expat、真实文件锁、安全文件操作、根目录探针写入/同步/读回。必要条件失败返回environment_failed与具体原因，不创建任务辅助文件或启动生成；探针在必要检查通过后的清理异常只提示。无需模型先运行check或解读日志决定下一步，check仅供独立诊断，不提前检查归档。缺少/无效归档配置、归档目录不可用或归档复制失败不阻断生成。保持主入口文件保护和内容验收，规格与内部信封最多2MiB，已有最终文件名拒绝；大规格或定制流程继续用专用API及显式清单。

代码先在 `./` 建立独占 `docx-task-<随机值>.cleanup.json`，登记同前缀的 `.worker.py`、`.spec.json`、`.receipt.json`、`.task.lock`、`.retry-spec.json`，不创建工作子目录，然后用独占创建写入，立即记录创建完成的身份、SHA和修改时间。固定worker只生成并验收本次DOCX及处理归档，不执行输入里的任意Python。图片相对任务目录解析；用户输入通过create的重复 `--protect-input` 或API `protected_inputs` 显式保护，已有图片仍受生成器同源保护。辅助文件及manifest的同名既存路径绝不接纳。

验收回执采用 `receipt_mode=exclusive_v1`：在原plan已登记的 `.receipt.json` 上以O_EXCL/O_NOFOLLOW独占写入、flush/fsync/关闭，不再调用带暂存硬链接的new-save。写入后核对创建时inode、单链接数及预期内容SHA-256，再把完整 `receipt_fingerprint` 写入原manifest。只有该证明已经提交，回执才可被接纳；写入未完成、证明未提交、预存回执、符号链接或未知硬链接均不接纳，不自动清除来源不明文件。父进程等待worker退出（超时则终止并等待）后重读原清单，不用旧内存快照覆盖worker刚提交的证明。

外层等待worker退出后，核对创建指纹、生产回执、最终文件路径/非空/指纹，再自动seal并clean。 如果worker在回执证明提交后因晚期暂存清理等原因非零退出，父进程只在回执身份、大小、mtime、链接数、SHA及验收字段/原辅助文件锚点全部匹配，且主文件指纹匹配时恢复成功，返回退出0及worker-warning。原始诊断随交付记录保留；不重复生成、不重新采集变化后的回执指纹，也不放宽单链接保护。正常恢复可完成清理，仍有残留则cleanup_incomplete；二者都不能把历史警告说成从未发生。归档failed/skipped仍可收尾；已验收文档在收尾后指纹仍不变时，即使清单保存、seal或clean异常也保持verified、返回退出0和cleanup_incomplete警告，保留材料及真实诊断；用户成功归档提示仍仅“归档完成”。清理只删除登记项，保留manifest、输入、最终文件及未登记文件。不得把“归档完成”说成“清理完成”。

| 实际结果 | 处理 |
|---|---|
| `status=verified`、`task_status=completed`、cleanup_complete=true，退出0 | 本次helper已清理，主文件留存；仍按archive_message报告归档状态。 |
| `status=verified`、task_status=cleanup_incomplete，退出0 | 主文件已成功，清理有保留/失败；warnings提示原因，保留回执，继续交付，不重新生成。 |
| status=failed、environment_failed，退出1 | 必要环境条件失败，读取environment.errors；修复后继续。 |
| status=failed、retry_pending/recovery_pending，退出1 | helper保留，读取message和task_manifest；不得假定没有主文件。 |

修正可核实的生成规格后，用 `run_docx_task.py retry 本轮manifest` 从stdin传修正规格；只有原指纹未变、无生产回执和主文件时才更新本任务拥有的JSON，最多总计3次生成。不手改helper或manifest，不重新创建同名任务来重置计数。失败、待输入或超过次数仍保留材料，不自动猜测删除。

重试规格更新为可恢复事务：先持久化preparing意图，旧spec保持不变；在创建前登记的retry-spec.json写入新配置，记录其精确inode、SHA和元信息并持久化prepared后，才原子替换spec。最后提交新created指纹并清除意图。若最后清单保存失败，retry/resume只接纳“已记录旧spec＋已记录stage”或“已记录新spec＋stage不存在”，恢复到一致状态；其它身份、字节、链接数或路径变化均保留并拒绝，不重置所有权。保存抛错后先重读持久化记录，已提交prepared的stage不会被误回滚。

preparing期间若尚未创建stage，恢复可安全撤销空意图；若控制器仍存活且捕获到自己独占创建的精确stage身份，写入失败可回滚该stage。强杀发生在stage创建后、身份记录前，或状态无法核实时，仍保留并返回recovery_pending，不猜测删除或接纳。新v2任务的stage在创建前已登记；旧v1任务不允许retry创建旧布局临时文件，仅恢复已有状态。

清单/回执读取先校验JSON对象根、基础及runner版本、entries/protected/deliverables结构、非布尔整数attempts、指纹、事务和验收字段；损坏记录结构化失败，不执行生成、删除或自动重置。旧清单仍按原记录恢复：未记录exclusive模式的历史回执保留原单链接契约；不会扫描、认领旧随机暂存文件或接受未知双链接回执。新的create及可执行retry自动启用exclusive模式。运行中的旧清单应使用新版runner恢复，不混用不同版本控制器。

中断或部分删除后，用 `run_docx_task.py resume 本轮manifest`：它只恢复已完成交付的收尾，不重新生成。控制器在稳定的任务根目录描述符上加锁，同一任务目录内的create/retry/resume串行；锁忙时返回明确状态，不静默并行。获得锁后重新加载并校验phase、attempts和指纹；sealed收尾也在该锁内。worker继承目录及工作文件锁，父进程退出后仍不能提前清理；sealed状态沿用原指纹，可顺序重复执行。初始化未完成、回执缺失但已有主文件、指纹变化等返回recovery_pending，保留材料供明确核实；不能把不确定文件重新登记为本轮所有。强制终止与敌对并发写入不作自动恢复保证。

API为 `create_task(spec, output, protected_inputs=(), workspace=None)`、`retry_task(manifest,spec)`、`resume_task(manifest)`，位于scripts/run_docx_task.py；workspace保留为兼容关键字，仅作为本轮平铺文件名前缀（例如 `owned.worker.py`），不得包含路径分隔符；不再表示目录。新任务使用 `docx_auto_task_v2`。v1历史清单仅允许按原路径resume/收尾，不搬迁或重新认领；retry拒绝创建旧布局临时文件。该流程不自动接管其它脚本、用户原有JSON、多步编辑或外部程序产物；这些流程继续使用下方显式清单。manifest本身默认保留，不是漏删临时文件。

## 时机与保护

成功任务的顺序为：最终文档验收完成 → 所有最终交付物的归档步骤返回结果 → seal → clean → 核对清理结果和最终文件。归档 copied、already_archived、skipped、failed 或批量 completed 均可进入收尾；failed 仍须如实提示归档未完成，详细归档警告仅留内部执行记录，不展示归档路径，不为清理重跑生成。`--verified` 是调用方对已完成内容验收的声明，不是工具替代验收或证明归档发生。

任务最终失败或停止时使用 failed/stopped，清理不再需要的中间文件；必要诊断材料、待恢复备份以 `--retain 路径 原因` 明确保留。仍需重试或等待输入时用 retry，clean 返回保留状态且不删除任何候选文件，完成重试后可以再次 seal。用户原件、模板、用户提供的 JSON/图片、已有输出、最终文件及其依赖不得列入清理项。

清单只能由助手基于本次实际任务建立，不从用户文档、上传 JSON 或其他任务导入删除指令。创建前确认候选路径不存在，工具再次检查；随后由本任务创建这些文件。没有创建前清单的历史文件不得事后伪造 absent_at_plan 纳入自动清理。路径和文件身份只能提供检查证据，不能证明谁创建了文件；禁止并发修改或与其他任务共用候选路径，不将它用于敌对并发写入环境。

## 三阶段命令

以下均在同一宿主确认的任务目录执行，替换 `<skill_dir>` 为实际读取主入口确定的真实目录。数据参数使用规范化相对路径，不含开头 `./`、`..`、绝对路径或符号链接。清单文件的父目录须已存在；工具不创建业务目录、不扫描文件树。选择本次独占的清单名和临时文件名，不能照抄后覆盖旧任务。

**创建辅助文件前登记：**

```console
python -I -S -B "<skill_dir>/scripts/cleanup_task.py" plan cleanup-task.json --file docx-work.spec.json --file docx-work.build.py --file docx-work.intermediate.docx --protect 用户原件.docx --deliverable 客户服务改进方案.docx
```

`--file`、`--protect`、`--deliverable` 可重复。新plan的清单及临时文件必须是 `./` 下的单一文件名；`--directory` 仅保留参数兼容，传入会拒绝。历史清单仍可seal/clean目录项，但不能新登记临时目录或嵌套临时文件。保护路径可为输入目录；候选与受保护路径、最终文件或清单重叠时拒绝整个 plan。plan 不创建候选文件，已存在的候选和清单均拒绝。生成期间新增未规划的辅助文件时，先为它们建立另一份不重叠清单；不得改写旧清单来伪造所有权。把用户要求保留的辅助产物作为交付物或保护项，不登记为临时项。

**成功完成验收和归档后记录状态：**

```console
python -I -S -B "<skill_dir>/scripts/cleanup_task.py" seal cleanup-task.json --outcome success --verified --archive-status copied
```

`--archive-status` 必须使用本轮实际返回结果；有多个交付物时先逐项确认归档步骤完成，任何失败使用 failed，全部完成的批量结果可使用 completed。主文件缺失、为空或不安全时不能 seal success。seal 记录当前文件身份、大小、修改时间和 SHA-256；目录仅记录身份，不读取目录内容。最终文件之后若变化，需重新验收，不得手改指纹以跳过检查。

失败收尾和重试示例：

```console
python -I -S -B "<skill_dir>/scripts/cleanup_task.py" seal cleanup-task.json --outcome failed --retain docx-work.spec.json "定位生成错误所需"
python -I -S -B "<skill_dir>/scripts/cleanup_task.py" seal cleanup-task.json --outcome retry
```

上述是不同任务状态的替代示例，不能连续照抄。只有 retry 清单可在任务继续后重新 seal；最终 sealed 清单不会重新采集变化后的候选来扩大删除范围。

**执行清理并核对：**

```console
python -I -S -B "<skill_dir>/scripts/cleanup_task.py" clean cleanup-task.json
```

仅删除身份和指纹匹配的登记普通文件；所有文件先处理，目录按从深到浅用 rmdir 删除，仅空目录可移除。未知文件混入目录时保留该目录，不扫描、不递归删除。当前文件或父目录是链接、类型改变、文件变化、身份与输入/交付物重合或存在硬链接时保留并报告。清理前后均核对已记录的最终文件；前置核对失败则不开始删除，后置失败则报告失败，不声称交付完整。

API 对应 `plan_cleanup(manifest, files=(), directories=(), protected=(), deliverables=())`、`seal_cleanup(manifest, outcome=..., verified=False, archive_status=None, retain=None)` 和 `cleanup_task(manifest)`；模块位于 `<skill_dir>/scripts/cleanup_task.py`，所有数据路径和命令一致。

## 结果和残留

下表是独立手动清理CLI的退出契约，保持原行为。标准自动runner对已经验收且最终指纹确认不变的文档，把收尾异常转换为退出0及cleanup_incomplete警告；不要求模型因此阻断交付。主文件缺失/改变或验收证据不可信仍返回失败。

| 结果 | 处理 |
|---|---|
| plan/seal 成功，退出 0 | 只表示清单阶段完成，尚未清理。 |
| clean 的 status=cleaned，退出 0 | 核对 cleanup_complete=true、deliverables_unchanged=true 及逐项结果后，才能报告登记的临时项已清理。最终失败任务没有交付物时，不据此声称文档生成成功。 |
| status=retained，退出 3 | retry、明确保留、文件变化或非空目录等；读取 retained 的路径和原因，不把退出 3 当作清理完成。 |
| status=failed/blocked/error，退出 1 | 删除失败、最终文件核对失败、清单/平台等错误；核对返回内容并报告，不扩大范围重删。参数解析错误退出 2。 |

clean 返回 `deleted`、`retained`、`failed` 数组，每项含 path/reason；已不存在的项标为 already_absent，不能说是本轮新删除。正常返回时清单另保存 last_cleanup；若回执写入失败，仍返回已发生的删除及失败信息。清理是逐项操作，不能回滚已删除的无用中间文件。

清单自身默认保留为最小恢复记录，输出 `manifest_retained` 路径；其中只有任务路径、文件指纹、状态和原因，不保存文档正文。交付说明应列出保留清单及其他残留的用途；“已清理”指登记候选，不表示目录里只剩最终文档。用户明确要求不保留清单时，仅在确认清理完成、不再需要恢复后单独处理该确切清单，不把它加入自己的删除列表。

## 环境与中断边界

纯 Python 标准库，最多 2048 个候选、2048 个保护/交付路径，清单最多 2 MiB，单个文件指纹读取最多 128 MiB；超限不猜测删除。平台须提供目录描述符相对操作及 O_NOFOLLOW/O_DIRECTORY，缺少时明确拒绝，不降级为跟随链接的路径删除。当前验证环境为 macOS；未声明 Windows 支持。

进程强制终止无法保证即时清理。sealed 清单在首个删除前已落盘；重启后必须先确认清单仍属于原任务，再运行 clean，已删除项可跳过，其余仍按原指纹检查。只有 planned 清单时，不自动删除；先核对任务状态并 seal。写清单过程中被强制终止可能留下原子写入暂存文件，这些未登记路径不自动扫描删除，需另行确认归属。禁止通过重新生成文档或改写清单掩盖残留。

业务执行只按本页现行规则核对实际清理结果，不读取历史验证记录或运行维护回归。合成异常测试不能证明所有文件系统、断电场景或宿主自动收尾行为。

## 可复用核心与兼容入口

`controlled_cleanup.py` 是无 DOCX 内容依赖的生命周期核心：

- `plan_cleanup(manifest, files=(), directories=(), protected=(), deliverables=())`：在创建临时目标前登记；manifest及files只接受根目录文件名，directories必须为空，拒绝认领既存目标。
- `seal_cleanup(manifest, outcome=..., verified=False, archive_status=None, retain=None)`：按结果与指纹封存；重试保留，验收声明由可信调用者负责，不能从普通文件推断验收。
- `cleanup_task(manifest)`：仅删除登记且身份、内容及链接条件仍一致的目标，返回清理及交付物保护状态；不按扩展名发现目标，不接管未知历史文件。
- `TaskFiles`：描述符锚定的底层操作与清单读写，要求实际平台支持对应能力；缺失即拒绝，不降级删除。

旧 `cleanup_task.py` CLI 及 Python 导入保留，导出同一核心对象，退出码与 JSON 不变。既有 `docx_cleanup_v1` schema 保留，避免重构令历史清单失效；名称不证明内容已经过 DOCX 验收。其他技能接入前需定义自身验收、归档状态和调用顺序，本轮未接入。核心不执行生成、归档或任意脚本；自动 DOCX runner 继续负责这些步骤及锁内状态校验。独立分发时保持同级模块可达，不能依赖其他技能安装位置。
