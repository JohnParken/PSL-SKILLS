# 文件交付生命周期

## 目录

- [自动归档覆盖](#自动归档覆盖)
- [受控配置与标准任务](#受控配置与标准任务)
- [状态和恢复](#状态和恢复)
- [自定义中间文件](#自定义中间文件)
- [独立包与平台](#独立包与平台)

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

`delivery.archive_status`、files、warnings和archive_message属于内部机器契约。copied仅表示副本完成；skipped/failed需如实提示，不回滚主产物，不因归档失败重跑生成。用户成功只显示“归档完成”，失败也不转贴内部路径。原有 `DOCX_DEBUG_ROUTER_CONFIG` 日志开关从公共模块保留，默认原始配置写stderr；不向用户转贴，任务无需此诊断时可设置0，勿回显配置猜路径。

## 受控配置与标准任务

首次按主入口完成 `stdlib_xlsx.py check`；写任务确认目标目录独占探针可写。需要受控JSON时，在已确认任务目录调用：

```console
python3 -I -S -B "<skill_dir>/scripts/run_xlsx_task.py" <<'JSON'
{"script":"stdlib_xlsx.py","arguments":["create","@spec","经营报告.xlsx"],"output":"经营报告.xlsx","configs":{"spec":{"sheets":[{"name":"Data","cells":{"A1":{"value":"项目"},"B1":{"value":42}}}]}}}
JSON
```

允许脚本即上表10个写文件CLI；arguments沿用原CLI参数；为可靠定位输出，主命令和位置参数放在选项之前。必须明确输出，与output完全一致。具名配置引用写为`@spec`、`@query`或其他合法键；configs为JSON对象而非磁盘文件。输入正文最多2MiB、最多16个配置，仅创建新的独占任务子目录，不写临时Python。inputs可额外列出需要保护的任务相对路径；所有既存文件参数也被保守保护。未引用配置、未知脚本、输出已存在或声明不匹配会拒绝，不执行任意命令。

runner创建前plan其专属JSON和目录；写入使用独占、不跟随链接方式。等待同一Python子进程退出后，主CLI已完成格式/计算流程和归档处理，退出0才封存成功并清理。业务语义仍须调用者验收；runner不证明用户要求已满足、公式等同Excel或视觉效果正确。无configs时直接调用已有CLI，不建立清理清单。

## 状态和恢复

- 原CLI业务退出0/1/2保持不变；incomplete/2即使写出并归档也不得称计算完整。
- runner业务成功且清理完成退出0；主产物成功但清理不完整退出3，保留原业务status并返回manifest及清理失败诊断。
- 业务失败或部分计算保留受控配置，返回原退出码与retained_for_review；不自动修正规格、重算或覆盖输出。unexpected异常/120秒超时返回recovery_pending，主文件可能已写出，先核查再恢复。
- manifest是恢复回执，不是最终业务交付物。成功封存后可对原manifest重复clean；planned或retry状态先核实文件和本轮归属，按明确结果seal后再clean，不能盲目重跑任务或认领历史文件。仅原指纹未变才能安全重入；强杀、异常和变化项保守保留。

公共清理是可信单控制者模式，不是恶意并发写者沙箱，也没有DOCX的完整持久重试事务。runner不提供自动retry/resume生成命令；需修正任务时保留证据、选择新主文件名，不在原有主文件上静默重跑。归档failed可以在真实业务成功后封存并清理，状态仍保留failed。

## 自定义中间文件

现有CLI传入的JSON、图片、原表、用户脚本都是输入，不自动删除。若调用者另建临时Python等，必须在创建前登记尚不存在的规范相对路径：

```console
python3 -I -S -B "<skill_dir>/scripts/xlsx_lifecycle.py" plan task.cleanup.json --file worker.py --file query.json --protect 原件.xlsx --deliverable 分析报告.xlsx
```

完成实际业务验收、取得归档结果并等待worker退出后，seal/clean：

```console
python3 -I -S -B "<skill_dir>/scripts/xlsx_lifecycle.py" seal task.cleanup.json --outcome success --verified --archive-status copied
python3 -I -S -B "<skill_dir>/scripts/xlsx_lifecycle.py" clean task.cleanup.json
```

copied须换成实际归档结果。失败待重试使用outcome retry保留；最终停止可显式retain必要备份。禁止按.py/.json扩展名扫描、补登既存文件、删除输入或最终产物；指纹变化、符号/硬链接、非空目录等按公共核心保守处理。未验证清理结果不能声称已清理。

## 独立包与平台

`_artifact_runtime` 是公共artifact-lifecycle的受追踪副本，不从相邻技能导入；来源及哈希见[lifecycle-upstream.json](lifecycle-upstream.json)。仅增加延迟注解以保留Python3.9导入及包内相对导入，不改公共语义；仓库 `tests/test_xlsx_lifecycle_parity.py` 比较转换后源码与哈希，维护任一边须审阅和同步适用副本，不仅更新哈希。

XLSX原有标准库限制保留；项目维护工具是否使用标准库是独立事项。受控清理还需dir_fd、O_NOFOLLOW、O_DIRECTORY等实际平台能力，缺失则停止该流程，不降级删除；旧CLI写文件结果仍按原合同返回，归档问题只在delivery诊断。主要运行目标为云端Linux容器；本轮本机结果不能替代Linux或Windows验收。开发测试使用合成隔离文件，不删除真实残留，不宣称真实用户文件通过或宿主模型自动触发通过。
