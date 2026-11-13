# 生命周期契约

路径占位符说明：`<skill-root>` 是 artifact-lifecycle Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 artifact-lifecycle 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

最终回复先遵守 `<skill-root>/SKILL.md#执行环境约束与首步自检` 中的归档展示边界：完整或精简机器回执均不整段转贴；`archive_output` 和 `files[].archive_output` 不能作为下载地址。正文、链接文字与链接目标均不得带出归档路径、目录或任务 ID；异常仅概括实际影响，主文件链接只使用宿主确认的真实主文件引用。

## 统一归档契约

归档只使用本轮宿主环境提供的最新 questionId；缺失或无效返回 failed 并保留已验收主文件，不猜测、不复用历史回执中的 ID。宿主必须保证配置新鲜，脚本只能校验本次值是否合法，不能证明宿主未传旧值。归档清单仅列已验收的最终交付物，不包含中间文件。禁止覆盖既存归档；同一任务同名且内容、身份核验一致时返回 already_archived，异内容返回 archive_conflict。重试只重试归档，不重新生成主文件。归档目录及子目录禁止临时文件；暂存只在任务根目录。

批次 archive_status 为 success、partial、failed、not_required，单项为 copied、already_archived、failed。success 必须具有非空完整清单且全部条目核验成功；partial 表示部分成功，不能套用全量成功提示。not_required 仅用于明确无交付归档需求，不用于缺失配置或失败。统一归档 CLI 退出码为 0（成功或明确无需）、3（部分）、1（失败）、2（参数错误）；业务入口自身退出码仍按其契约解释。

发布后才进行最终摘要复核；本次不改变发布前校验架构。若复核失败，目标副本可能已存在，必须报告失败，不承诺云端从未观察到该副本。多文件不是整批事务。

## 配置与归档

`ROUTER_CONFIG_VARIABLES` 支持外层 `{ "commonParam": [...] }` 或 `[{"name":"commonParam","value":...}]`；commonParam可以是列表或列表JSON字符串。通用取值函数对字符串参数值先执行 `.strip()` 去掉两端空白，保留中间内容及非字符串类型，参数名及原始环境变量不改写。唯一questionId裁剪后必须为非负整数或1–128位ASCII字母/数字/下划线/短横线字符串，拒绝布尔值、路径和设备名。重复JSON键、NaN、重复业务参数及错误结构拒绝；配置缺失/无效时归档失败，不改主文件路径。


环境读取保留 `DOCX_DEBUG_ROUTER_CONFIG` 开关，默认关闭。显式设为1时，stderr只输出configured和values_redacted两个布尔字段，不输出配置原文、参数值、ID或目录；不要为排错回显环境变量。程序接口 `read_environment_parameter` 保持无日志返回原值。

```python
execute(command="python -I -S -B \"<skill-root>/scripts/archive_delivery.py\" 报告.txt --protect-input ./用户原件.json")
```


消费执行结果先按主入口 `<skill-root>/SKILL.md#执行环境约束与首步自检` 核对可见退出状态、完整性与宿主转存读取；合并 stdout/stderr 不改变以下业务契约，工具层 `status=success` 不代表归档成功。

输出JSON含archive_status、files、warnings及archive_message。退出0为success或明确not_required，3为partial，1为failed，2为参数错误；0/3/1均需读取完整JSON。统一归档CLI采用同一退出码契约。用户回复按下方归档用户话术映射，不逐字转贴archive_message，主交付物链接仍保留；失败不将内部路径泄露在用户提示中，内部警告用于定位。

## 归档用户话术

用户已明确后续宿主上传机制，归档成功即提示去云空间查看，无需等待映射确认；不宣称已验证上传完成或展示时效。保留机器 `archive_message`，用户展示 `archive_user_notice`，按以下完整结果核对：

- 非空完整清单所有项 copied/already_archived 且汇总 success：“🎉 文件归档成功，请到云空间查看。 ☁️✨”。
- 汇总 partial 且同时有成功与未成功项：“部分文件归档成功，请到云空间查看已归档文件；其余文件归档未完成。”。failed 但条目全成功可能是复核失效，不称部分成功。
- 归档失败：“🥺 本次归档未完成，请先核对文件结果。 📄”。
- not_required（明确无需归档）：“本次未执行归档，请先核对文件结果。”，不据此断言外部云状态。
- 待交接：“文件待交接归档，归档完成后再提示到云空间查看。”。
- 未知或缺少可靠结果：“归档结果尚未确认，请先核对文件结果。”。空清单不得成功；入口拒绝或汇总 failed 按失败报告，只有状态无法确认时使用未知提示。

退出 0、主文件成功或单项成功不能替代完整归档结果。主业务失败不得称生成成功，归档与清理状态独立。隐藏归档物理路径、目录、ID和原始日志，保留真实主文件链接，不虚构下载能力。

## 登记、封存、清理

新plan的manifest和files只接受任务根目录 `./` 的单一文件名，directories必须为空；CLI `--directory` 保留参数但拒绝新登记。 `plan --protect` 和 `plan_cleanup(protected=...)` 对输入字符串先拒绝绝对路径、父目录、反斜杠、冒号、空路径和空路径组件（包括重复或结尾斜杠），再移除安全的 `.` 组件；因此 `./输入` 与 `目录/./输入` 分别记录为 `输入` 和 `目录/输入`。规范化后仍逐组件拒绝符号链接，并执行重叠和身份保护；不可将此规则用于manifest、临时文件或交付物。旧嵌套清单仍可seal/clean，不搬迁或补认领。所有新建临时文件直接位于 `./`，包括内部清单更新和归档暂存文件；跨文件系统发布失败时报告，不改用目标目录暂存。

先在任务目录确认输入/产物清单，且即将创建的临时路径不存在，再执行：

```python
execute(command="python -I -S -B \"<skill-root>/scripts/lifecycle.py\" plan task.cleanup.json --file worker.py --file spec.json --protect 用户原件.json --deliverable 报告.txt")
```

业务技能创建/运行worker和spec、发布并验收报告、archive返回后，才可执行：

```python
execute(command="python -I -S -B \"<skill-root>/scripts/lifecycle.py\" seal task.cleanup.json --outcome success --verified --archive-status success")
execute(command="python -I -S -B \"<skill-root>/scripts/lifecycle.py\" clean task.cleanup.json")
```

上例success仅适用于刚取得完整成功；实际partial/failed/not_required须如实传入，不伪造。成功封存要求非空主交付物、verified声明及有效归档结果，但不验证声明真实性，调用方必须可信。清理核对任务根身份、受保护输入、交付物及临时项指纹；修改、链接、多硬链接或非空目录等保守保留/失败，先删除登记项，再核对并删除manifest回执，返回manifest_removed=true、manifest_retained=null。`plan/seal/clean` 退出0成功，3保留，1失败/阻止/错误，2参数错误；结果JSON不能仅靠退出0断言业务成功。

## 失败和恢复

生成或编辑失败同样立即清理本轮临时文件和清单，不因可能重试而保留；需要重试时从原始输入重新创建独占任务。删除受阻、锁仍被占用或归属无法确认时报告cleanup_incomplete，处理阻碍后继续清理，不能宣称已完成。 失败使用seal --outcome failed后clean；finalize_failed_cleanup供已退出生产进程的可信调用者使用。retry仅保留历史协议兼容，不作为新任务失败策略。中断后只用原manifest恢复；已删除清单不可再重入，不能补登未知旧文件。

普通plan/seal/clean不是自动生成runner，没有多进程任务锁、创建时强指纹事务、自动重试或任意脚本执行能力；同一清单仅一个可信控制者串行使用。plan证明登记时不存在，不能对抗登记后其他写者冒用路径；seal指纹不是单独的所有权证明。需自动生成/重试状态机时由业务技能集成，DOCX继续使用自己的runner。不得在临时Python自身尚未退出时clean；由外层控制者收尾。

`cleanup_complete`与`cleanup_scope=registered_entries_only`仅针对原清单登记项。内部原子暂存正常返回或可捕获异常时由创建函数清理，强杀可能留下未登记文件；不扫描删除，不将目录无残留作为此字段的含义。保留材料由原任务控制者核实后恢复，可能含业务数据的配置遵循宿主留存策略；本技能不自动到期删除。
