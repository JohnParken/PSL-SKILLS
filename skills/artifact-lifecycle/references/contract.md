# 生命周期契约

路径占位符说明：`<skill-root>` 是 artifact-lifecycle Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 artifact-lifecycle 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 配置与归档

`ROUTER_CONFIG_VARIABLES` 支持外层 `{ "commonParam": [...] }` 或 `[{"name":"commonParam","value":...}]`；commonParam可以是列表或列表JSON字符串。通用取值函数对字符串参数值先执行 `.strip()` 去掉两端空白，保留中间内容及非字符串类型，参数名及原始环境变量不改写。唯一questionId裁剪后必须为非负整数或1–128位ASCII字母/数字/下划线/短横线字符串，拒绝布尔值、路径和设备名。重复JSON键、NaN、重复业务参数及错误结构拒绝；配置缺失/无效时跳过归档，不改主文件路径。

有效归档位置是任务目录内 `output/{questionId}/`，按主文件basename平铺，复制而非移动。批内同名（忽略大小写）拒绝；输入路径别名及硬链接保护，禁止路径组件符号链接。单文件经任务根目录 `./` 的独占临时副本、flush/fsync、权限设置后原子replace；已有同名归档会替换，保留原归档权限。不会合并目录或扫描历史。多文件不是整批事务，失败前已成功的副本保留；主文件不回滚。现有路径检查不承诺隔离同权限恶意并发写者。

环境读取保留 `DOCX_DEBUG_ROUTER_CONFIG` 开关，默认关闭。显式设为1时，stderr只输出configured和values_redacted两个布尔字段，不输出配置原文、参数值、ID或目录；不要为排错回显环境变量。程序接口 `read_environment_parameter` 保持无日志返回原值。

```console
python -I -S -B "<skill-root>/scripts/lifecycle.py" archive ./报告.txt --protect-input ./用户原件.json
```

输出JSON含archive_status、files、warnings及archive_message。退出0为copied，3为skipped，1为failed，2为参数错误；0/3/1均需读取完整JSON。原DOCX归档CLI的历史退出行为未更改。用户回复按下方归档用户话术映射，不逐字转贴archive_message，主交付物链接仍保留；失败不将内部路径泄露在用户提示中，内部警告用于定位。

## 归档用户话术

当前宿主面向用户将归档目的地称为“云空间”。核对完整归档结果后，助手在最终回复中使用以下话术；`archive_message` 中的“归档完成”等原机器文案、JSON、stderr及退出码保持不变，不逐字转贴机器文案。

- 全部交付物归档成功：**🎉 文档已经放到你的云空间啦，随时都能找到～ ☁️✨**
- 主文档已生成且可下载，但归档失败：**🥺 文档已生成，但这次没能放进你的云空间。先下载保存，别耽误使用呀～ 📄**

成功要求汇总copied且全部文件copied/already_archived；退出0、主文件成功或单个文件成功不能替代完整结果。skipped如实说明本次未放入云空间；未知或不完整结果说明尚未确认；部分成功分别说明各文件，不套用全部成功话术。主文件失败不得说已生成；编辑、提取、计算等按真实操作调整“已生成”，产物名称可按实际改为“表格”或“文件”。下载提示须有宿主可用交付方式支持，否则改为实际支持的交付说明。不将归档成功当成业务验收、公式计算完整或清理成功。

保留正常主文件链接，不展示内部归档目录、路径、ID、原始配置或日志。归档失败保留主产物，不为此重跑业务流程；归档、业务结果和清理状态分别报告。这是回复规则，不新增上传、分享或宿主下载能力。

## 登记、封存、清理

新plan的manifest和files只接受任务根目录 `./` 的单一文件名，directories必须为空；CLI `--directory` 保留参数但拒绝新登记。旧嵌套清单仍可seal/clean，不搬迁或补认领。所有新建临时文件直接位于 `./`，包括内部清单更新和归档暂存文件；跨文件系统发布失败时报告，不改用目标目录暂存。

先在任务目录确认输入/产物清单，且即将创建的临时路径不存在，再执行：

```console
python -I -S -B "<skill-root>/scripts/lifecycle.py" plan task.cleanup.json --file worker.py --file spec.json --protect 用户原件.json --deliverable 报告.txt
```

业务技能创建/运行worker和spec、发布并验收报告、archive返回后，才可执行：

```console
python -I -S -B "<skill-root>/scripts/lifecycle.py" seal task.cleanup.json --outcome success --verified --archive-status copied
python -I -S -B "<skill-root>/scripts/lifecycle.py" clean task.cleanup.json
```

上例copied仅适用于刚取得copied；实际skipped/failed须如实传入，不伪造。成功封存要求非空主交付物、verified声明及有效归档结果，但不验证声明真实性，调用方必须可信。清理核对任务根身份、受保护输入、交付物及临时项指纹；修改、链接、多硬链接或非空目录等保守保留/失败，先删除登记项，再核对并删除manifest回执，返回manifest_removed=true、manifest_retained=null。`plan/seal/clean` 退出0成功，3保留，1失败/阻止/错误，2参数错误；结果JSON不能仅靠退出0断言业务成功。

## 失败和恢复

生成或编辑失败同样立即清理本轮临时文件和清单，不因可能重试而保留；需要重试时从原始输入重新创建独占任务。删除受阻、锁仍被占用或归属无法确认时报告cleanup_incomplete，处理阻碍后继续清理，不能宣称已完成。 失败使用seal --outcome failed后clean；finalize_failed_cleanup供已退出生产进程的可信调用者使用。retry仅保留历史协议兼容，不作为新任务失败策略。中断后只用原manifest恢复；已删除清单不可再重入，不能补登未知旧文件。

普通plan/seal/clean不是自动生成runner，没有多进程任务锁、创建时强指纹事务、自动重试或任意脚本执行能力；同一清单仅一个可信控制者串行使用。plan证明登记时不存在，不能对抗登记后其他写者冒用路径；seal指纹不是单独的所有权证明。需自动生成/重试状态机时由业务技能集成，DOCX继续使用自己的runner。不得在临时Python自身尚未退出时clean；由外层控制者收尾。

`cleanup_complete`与`cleanup_scope=registered_entries_only`仅针对原清单登记项。内部原子暂存正常返回或可捕获异常时由创建函数清理，强杀可能留下未登记文件；不扫描删除，不将目录无残留作为此字段的含义。保留材料由原任务控制者核实后恢复，可能含业务数据的配置遵循宿主留存策略；本技能不自动到期删除。

归档是同任务目录、同配置下的当前副本，不是版本历史库：同basename的新交付会替换旧副本，输入保护项除外。需要保留多版时，写入前使用不同最终文件名；并发任务使用不同宿主任务目录，不依赖basename区分不同子目录的同名文件。不要为修复归档而删除旧文件或重新生成主文件。此行为保留兼容，不宣称归档永不覆盖。
