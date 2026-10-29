# 文件交付生命周期

路径占位符说明：`<skill-root>` 是 XLSX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 XLSX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 目录

- 自动归档覆盖 (`<skill-root>/references/delivery-lifecycle.md#自动归档覆盖`)
- 归档用户话术 (`<skill-root>/references/delivery-lifecycle.md#归档用户话术`)
- 受控配置与标准任务 (`<skill-root>/references/delivery-lifecycle.md#受控配置与标准任务`)
- 状态和恢复 (`<skill-root>/references/delivery-lifecycle.md#状态和恢复`)
- 自定义中间文件 (`<skill-root>/references/delivery-lifecycle.md#自定义中间文件`)
- 独立包与平台 (`<skill-root>/references/delivery-lifecycle.md#独立包与平台`)
- 子进程证据与保留材料 (`<skill-root>/references/delivery-lifecycle.md#子进程证据与保留材料`)

## 自动归档覆盖

以下CLI在实际写出文件后添加`delivery`，不改原业务status、字段及退出码：

| 入口 | 最终产物 |
|---|---|
| stdlib_xlsx.py create / edit / calculate | XLSX；编辑/计算保留原XLSM宏容器 |
| xlsx_tabular.py import / export | 新XLSX、CSV或TSV |
| xlsx_result_export.py | 已支持分析结果的新XLSX |
| xlsx_management.py、xlsx_dashboard.py、xlsx_deposits.py、xlsx_deposit_structure.py、xlsx_customers.py、xlsx_reconcile.py、xlsx_management_policy.py 的 --xlsx | 各自支持的报告XLSX |

纯read/check/profile、无--xlsx的分析、stdout结果不自动落盘或建立清单。低层`Workbook.save`和各export函数保持原API，不自动归档；集成方可在最终验收后显式调用 `xlsx_delivery.finish_delivery(result, output, protected_inputs)`。legacy脚本未接入，不作为回退。读取stdout后由宿主另存的JSON报告也未被CLI自动追踪，需用公共技能按显式清单归档。

归档读取ROUTER_CONFIG_VARIABLES的commonParam/questionId，配置缺失/无效则skipped；主文件位置不改变。按basename复制到任务内归档目录，已有同名归档原子替换，不移动主文件。全部输入/配置须保护；CLI自动传入其输入、query、spec、appearance及daily等文件。跨目录同basename的并发归档仍可能最后写者替换，不保证整批事务或跨任务锁隔离；任务须使用独立目录，勿并发操作同一清单。

`delivery.archive_status`、files、warnings和archive_message属于内部机器契约。copied仅表示副本完成；skipped/failed需如实提示，不回滚主产物，不因归档失败重跑生成。用户成功和失败提示统一按下方归档用户话术映射，不转贴机器文案和内部路径。`DOCX_DEBUG_ROUTER_CONFIG` 默认关闭，显式设为1也只输出是否配置及已脱敏标记，不输出原始值；勿回显环境配置猜路径。

## 归档用户话术

当前宿主面向用户将归档目的地称为“云空间”。核对完整归档结果后，助手在最终回复中使用以下话术；`archive_message` 中的“归档完成”等原机器文案、JSON、stderr及退出码保持不变，不逐字转贴机器文案。

- 全部交付物归档成功：**🎉 文档已经放到你的云空间啦，随时都能找到～ ☁️✨**
- 主文档已生成且可下载，但归档失败：**🥺 文档已生成，但这次没能放进你的云空间。先下载保存，别耽误使用呀～ 📄**

成功要求汇总copied且全部文件copied/already_archived；退出0、主文件成功或单个文件成功不能替代完整结果。skipped如实说明本次未放入云空间；未知或不完整结果说明尚未确认；部分成功分别说明各文件，不套用全部成功话术。主文件失败不得说已生成；编辑、提取、计算等按真实操作调整“已生成”，产物名称可按实际改为“表格”或“文件”。下载提示须有宿主可用交付方式支持，否则改为实际支持的交付说明。不将归档成功当成业务验收、公式计算完整或清理成功。

保留正常主文件链接，不展示内部归档目录、路径、ID、原始配置或日志。归档失败保留主产物，不为此重跑业务流程；归档、业务结果和清理状态分别报告。这是回复规则，不新增上传、分享或宿主下载能力。

## 受控配置与标准任务

首次按主入口完成 `"<skill-root>/scripts/stdlib_xlsx.py" check`；写任务确认目标目录独占探针可写。需要受控JSON时，在已确认任务目录调用：

```console
python3 -I -S -B "<skill-root>/scripts/run_xlsx_task.py" <<'JSON'
{"script":"stdlib_xlsx.py","arguments":["create","@spec","经营报告.xlsx"],"output":"经营报告.xlsx","configs":{"spec":{"sheets":[{"name":"Data","cells":{"A1":{"value":"项目"},"B1":{"value":42}}}]}}}
JSON
```

允许脚本即上表10个写文件CLI；arguments沿用原CLI参数；为可靠定位输出，主命令和位置参数放在选项之前。必须明确输出，与output完全一致。具名配置引用写为`@spec`、`@query`或其他合法键；configs为JSON对象而非磁盘文件。输入正文最多2MiB、最多16个配置，仅创建任务根中的独占平铺配置文件，不写临时Python。inputs可额外列出需要保护的任务相对路径；所有既存文件参数也被保守保护。未引用配置、未知脚本、输出已存在或声明不匹配会拒绝，不执行任意命令。

runner创建前plan其专属平铺JSON和清单；写入使用独占、不跟随链接方式。等待同一Python子进程退出后，主CLI已完成格式/计算流程和归档处理，业务退出0且归档步骤返回后封存并清理，归档失败/跳过也收尾。业务语义仍须调用者验收；runner不证明用户要求已满足、公式等同Excel或视觉效果正确。无configs时直接调用已有CLI，不建立清理清单。

## 状态和恢复

- 原CLI业务退出0/1/2保持不变；incomplete/2即使写出并归档也不得称计算完整。
- runner业务成功且清理完成退出0；主产物成功但清理不完整退出3，保留原业务status并返回manifest及清理失败诊断。
- 业务失败、部分计算、异常及120秒子进程超时均清理配置和清单；保留原退出码和业务状态，清理成功返回failed_cleaned，清理失败返回cleanup_incomplete。已写出的输出保持原状态，不冒称完整成功。
- manifest是恢复回执，不是最终业务交付物。本轮全部成功后按单链接与精确指纹删除本次清单，manifest返回null且manifest_removed=true；删除失败保留清单并报告cleanup_incomplete。尚保留的封存清单可按原manifest重复clean；planned或retry状态先核实文件和本轮归属，按明确结果seal后再clean，不能盲目重跑任务或认领历史文件。仅原指纹未变才能安全重入；强杀、异常和变化项保守保留。

公共清理是可信单控制者模式，不是恶意并发写者沙箱，也没有DOCX的完整持久重试事务。runner不提供自动retry/resume生成命令；需修正任务时保留证据、选择新主文件名，不在原有主文件上静默重跑。归档failed或skipped时照常清理配置与清单；主文件、归档和清理状态独立。实际清理异常返回cleanup_incomplete；业务成功时退出3，业务失败或部分计算保留原非零退出码。归档报告temporary_residuals时，即使没有临时配置或业务结果不完整，也不得报告清理完成；须处理确切残留后核实收尾。历史清单不自动认领或退休。

## 自定义中间文件

现有CLI传入的JSON、图片、原表、用户脚本都是输入，不自动删除。若调用者另建临时Python等，必须在创建前登记尚不存在的规范相对路径：

```console
python3 -I -S -B "<skill-root>/scripts/xlsx_lifecycle.py" plan task.cleanup.json --file worker.py --file query.json --protect 原件.xlsx --deliverable 分析报告.xlsx
```

完成实际业务验收、取得归档结果并等待worker退出后，seal/clean：

```console
python3 -I -S -B "<skill-root>/scripts/xlsx_lifecycle.py" seal task.cleanup.json --outcome success --verified --archive-status copied
python3 -I -S -B "<skill-root>/scripts/xlsx_lifecycle.py" clean task.cleanup.json
```

copied须换成实际归档结果。失败使用outcome failed后clean，不用retry/retain保留中间文件。禁止按.py/.json扩展名扫描、补登既存文件、删除输入或最终产物；指纹变化、符号/硬链接、非空目录等按公共核心保守处理。未验证清理结果不能声称已清理。

## 独立包与平台

`_artifact_runtime` 是公共artifact-lifecycle的受追踪副本，不从相邻技能导入；来源及哈希见lifecycle-upstream.json (`<skill-root>/references/lifecycle-upstream.json`)。仅增加延迟注解以保留Python3.9导入及包内相对导入，不改公共语义；仓库 `tests/test_xlsx_lifecycle_parity.py` 比较转换后源码与哈希，维护任一边须审阅和同步适用副本，不仅更新哈希。

XLSX原有标准库限制保留；项目维护工具是否使用标准库是独立事项。受控清理还需dir_fd、O_NOFOLLOW、O_DIRECTORY等实际平台能力，缺失则停止该流程，不降级删除；旧CLI写文件结果仍按原合同返回，归档问题只在delivery诊断。主要运行目标为云端Linux容器；本轮本机结果不能替代Linux或Windows验收。开发测试使用合成隔离文件，不删除真实残留，不宣称真实用户文件通过或宿主模型自动触发通过。

## 子进程证据与保留材料

runner新增`child_execution`，记录调用ID、exited/signaled/timeout/not_started、真实退出码或null、信号及两个输出通道的原字符数。合法JSON继续作为结构化业务结果返回；原始stdout不重复转贴。任意stderr和无效JSON可能包含配置、路径或业务数据，均不回显正文，明确标记omitted及原因；这不是完整原始日志，也不把空通道与省略混淆。参数解析失败返回child_argument_error；非JSON、超时及未启动分别报告，不伪造业务成功或正常退出码。超时、强杀后的主文件可能存在，先检查原任务。

归档failed/skipped不再保留配置和清单，也不返回旧的review_archive_then_seal_original_manifest。已验收主文件可单独重试归档，不重跑计算。业务失败或部分计算也立即清理中间材料，通用clean最后删除清单；已写出输出不因清理被删除。已sealed且非retry的清单仅可clean，成功删除后不再重复调用。

`cleanup_complete`只针对登记项；公共clean返回`cleanup_scope=registered_entries_only`。内部原子暂存正常返回或可捕获异常时自行清理；强杀可能保留未登记暂存，不扫描删除、不承诺目录零残留。无configs分支没有清单，仍检查业务、归档及child_execution。

归档是同任务目录、同配置下的当前副本，不是版本历史库：同basename的新交付会替换旧副本，输入保护项除外。需要保留多版时，写入前使用不同最终文件名；并发任务使用不同宿主任务目录，不依赖basename区分不同子目录的同名文件。不要为修复归档而删除旧文件或重新生成主文件。此行为保留兼容，不宣称归档永不覆盖。

任务结束时必须清理本轮全部中间文件，包括脚本、配置、回读结果、日志、暂存、锁、回执和清单；仅将当前与历史交付物作为任务生成的留存文件。用户原件与其他任务文件仍受保护，不属于可删除中间文件。创建前完整登记，普通内部暂存由创建函数收尾；若删除失败、归属不明或强杀留下未确认残留，报告收尾未完成，不声称只剩交付物，也不扫描删除未知文件。生成或编辑失败同样立即清理本轮临时文件和清单，不因可能重试而保留；需要重试时从原始输入重新创建独占任务。删除受阻、锁仍被占用或归属无法确认时报告cleanup_incomplete，处理阻碍后继续清理，不能宣称已完成。
