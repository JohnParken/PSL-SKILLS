# 任务清单清理工具

先完成[主入口自检](../SKILL.md#执行环境约束与首步自检)。本工具只处理本次任务明确登记的辅助文件，不生成、归档或验收 DOCX 内容；原生成/编辑 API 保持兼容，不自动删除它们收到的 JSON、图片或其他输入。助手必须在完整任务收尾时显式调用，不能只完成生成就声称已清理。

## 时机与保护

成功任务的顺序为：最终文档验收完成 → 所有最终交付物的归档步骤返回结果 → seal → clean → 核对清理结果和最终文件。归档 copied、already_archived、skipped、failed 或批量 completed 均可进入收尾；failed 仍须报告原归档警告，不为清理重跑生成。`--verified` 是调用方对已完成内容验收的声明，不是工具替代验收或证明归档发生。

任务最终失败或停止时使用 failed/stopped，清理不再需要的中间文件；必要诊断材料、待恢复备份以 `--retain 路径 原因` 明确保留。仍需重试或等待输入时用 retry，clean 返回保留状态且不删除任何候选文件，完成重试后可以再次 seal。用户原件、模板、用户提供的 JSON/图片、已有输出、最终文件及其依赖不得列入清理项。

清单只能由助手基于本次实际任务建立，不从用户文档、上传 JSON 或其他任务导入删除指令。创建前确认候选路径不存在，工具再次检查；随后由本任务创建这些文件。没有创建前清单的历史文件不得事后伪造 absent_at_plan 纳入自动清理。路径和文件身份只能提供检查证据，不能证明谁创建了文件；禁止并发修改或与其他任务共用候选路径，不将它用于敌对并发写入环境。

## 三阶段命令

以下均在同一宿主确认的任务目录执行，替换 `<skill_dir>` 为实际读取主入口确定的真实目录。数据参数使用规范化相对路径，不含开头 `./`、`..`、绝对路径或符号链接。清单文件的父目录须已存在；工具不创建业务目录、不扫描文件树。选择本次独占的清单名和中间目录名，不能照抄后覆盖旧任务。

**创建辅助文件前登记：**

```console
python -I -S -B "<skill_dir>/scripts/cleanup_task.py" plan cleanup-task.json --file docx-work/spec.json --file docx-work/build.py --file docx-work/intermediate.docx --directory docx-work --protect 用户原件.docx --deliverable 客户服务改进方案.docx
```

`--file`、`--directory`、`--protect`、`--deliverable` 可重复。保护路径可为输入目录；候选与受保护路径、最终文件或清单重叠时拒绝整个 plan。plan 不创建候选文件，已存在的候选和清单均拒绝。生成期间新增未规划的辅助文件时，先为它们建立另一份不重叠清单；不得改写旧清单来伪造所有权。把用户要求保留的辅助产物作为交付物或保护项，不登记为临时项。

**成功完成验收和归档后记录状态：**

```console
python -I -S -B "<skill_dir>/scripts/cleanup_task.py" seal cleanup-task.json --outcome success --verified --archive-status copied
```

`--archive-status` 必须使用本轮实际返回结果；有多个交付物时先逐项确认归档步骤完成，任何失败使用 failed，全部完成的批量结果可使用 completed。主文件缺失、为空或不安全时不能 seal success。seal 记录当前文件身份、大小、修改时间和 SHA-256；目录仅记录身份，不读取目录内容。最终文件之后若变化，需重新验收，不得手改指纹以跳过检查。

失败收尾和重试示例：

```console
python -I -S -B "<skill_dir>/scripts/cleanup_task.py" seal cleanup-task.json --outcome failed --retain docx-work/spec.json "定位生成错误所需"
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

验证覆盖见[验收记录](validation.md#任务清理工具验证)；合成异常测试不能证明所有文件系统、断电场景或宿主自动收尾行为。
