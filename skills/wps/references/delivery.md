# 自动归档与受控配置收尾

维护资源说明：文中 `docs/maintenance/wps/` 与 `skills/wps/scripts/test/` 均以宿主确认的仓库根目录为基准，仅供源码维护，不随运行包分发；运行任务不读取或执行。没有仓库时跳过维护资料，不按 Skill 安装目录拼接。

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

最终回复先遵守 `<skill-root>/SKILL.md#执行环境约束与首步自检` 中的归档展示边界：完整或精简机器回执均不整段转贴；`archive_output` 和 `files[].archive_output` 不能作为下载地址。正文、链接文字与链接目标均不得带出归档路径、目录或任务 ID；异常仅概括实际影响，主文件链接只使用宿主确认的真实主文件引用。

## 统一归档契约

归档只使用本轮宿主环境提供的最新 questionId；缺失或无效返回 failed 并保留已验收主文件，不猜测、不复用历史回执中的 ID。宿主必须保证配置新鲜，脚本只能校验本次值是否合法，不能证明宿主未传旧值。归档清单仅列已验收的最终交付物，不包含中间文件。禁止覆盖既存归档；同一任务同名且内容、身份核验一致时返回 already_archived，异内容返回 archive_conflict。重试只重试归档，不重新生成主文件。归档目录及子目录禁止临时文件；暂存只在任务根目录。

批次 archive_status 为 success、partial、failed、not_required，单项为 copied、already_archived、failed。success 必须具有非空完整清单且全部条目核验成功；partial 表示部分成功，不能套用全量成功提示。not_required 仅用于明确无交付归档需求，不用于缺失配置或失败。统一归档 CLI 退出码为 0（成功或明确无需）、3（部分）、1（失败）、2（参数错误）；业务入口自身退出码仍按其契约解释。

发布后才进行最终摘要复核；本次不改变发布前校验架构。若复核失败，目标副本可能已存在，必须报告失败，不承诺云端从未观察到该副本。多文件不是整批事务。

## 默认交付

普通成功时简洁告知文档已生成并提供宿主实际支持的主文件链接，不逐项复述机器回执、后端或内部格式。用户询问格式、明确要求原生或二进制格式，或格式及兼容限制影响使用时，准确说明真实格式与限制，不冒充满足要求；内容遗漏、有损重排、失败和收尾异常仍按实际影响报告。

创建、重排编辑和导出CLI在主文件发布后核验通过、内部候选清理完成后，自动调用随包公共归档实现；不用再调用外部artifact-lifecycle。裸读取、inspect、check及底层Python生成/编辑/转换API不归档。简单任务继续用原CLI的stdin或内联JSON，不为收尾创建额外文件。

主文件保留在任务目录，归档只是复制，不移动或回滚。配置从ROUTER_CONFIG_VARIABLES的commonParam/questionId读取；缺失、格式错误或ID无效返回delivery.archive_status=failed，不假称云空间成功。归档冲突默认failed并保留历史，不能删除历史或重跑生成解决。归档复制和校验失败不改变原业务status和退出码；须单独核对delivery、publication和cleanup，不以退出0代替归档验收。归档前后复核主文件发布哈希；发现变化时停止归档或标failed，保留复制记录供检查，不据旧验收正常交付已变化的主文件。

partial结果可归档其已明确验收的可用子集，但原partial、源诊断、格式遗漏和退出3继续保留。发布/追加回读未通过或内部清理未完成时不归档，返回 failed、delivery_not_ready 及 blocking_reasons，保留请求文件清单；不是 not_required，也不能用归档掩盖失败。清理须按原受控清单核实恢复，不能只手动改状态。恢复后重新核对主文件发布摘要，仅重试归档，使用重试时宿主当前 questionId，不重新生成或覆写主文件。归档暂存删除失败的temporary_residuals须说明未完成，不能宣称目录已清干净。

用户已明确后续宿主上传机制，归档成功即提示去云空间查看，无需等待映射确认；不宣称已验证上传完成或展示时效。保留机器 `archive_message`，用户展示 `archive_user_notice`，按以下完整结果核对：

- 非空完整清单所有项 copied/already_archived 且汇总 success：“🎉 文件归档成功，请到云空间查看。 ☁️✨”。
- 汇总 partial 且同时有成功与未成功项：“部分文件归档成功，请到云空间查看已归档文件；其余文件归档未完成。”。failed 但条目全成功可能是复核失效，不称部分成功。
- 归档失败：“🥺 本次归档未完成，请先核对文件结果。 📄”。
- not_required 仅限明确无最终交付归档需求：“本次没有需要归档的最终交付文件。”，不用于发布、清理或归档失败。
- 待交接：“文件待交接归档，归档完成后再提示到云空间查看。”。
- 未知或缺少可靠结果：“归档结果尚未确认，请先核对文件结果。”。空清单不得成功；入口拒绝或汇总 failed 按失败报告，只有状态无法确认时使用未知提示。

退出 0、主文件成功或单项成功不能替代完整归档结果。主业务失败不得称生成成功，归档与清理状态独立。隐藏归档物理路径、目录、ID和原始日志，保留真实主文件链接，不虚构下载能力。


## 确需落盘JSON时

已有用户JSON始终是输入，不能因作为--spec就删除。若本轮确需创建落盘配置，用受控job入口；它只执行随包三种交付CLI，不执行任意Python脚本。script字段的三个文件名是允许值，不是由工作目录查找的路径。

先自检，要求退出0、JSON status=environment-ok、task_writable=true；探针实际写入、读回并仅清理本次探针：

```python
execute(command="python -I -S -B \"<skill-root>/scripts/run_wps_job.py\" --check")
```

示例：由job在创建前登记自己的配置文件，子进程结束且取得归档结果后清理配置及清单：

```python
execute(command="python -I -S -B \"<skill-root>/scripts/run_wps_job.py\" <<'JSON'\n{\"script\":\"run_wps_task.py\",\"arguments\":[\"create\",\"工作汇报.wps\",\"--spec\",\"@spec\"],\"output\":\"工作汇报.wps\",\"configs\":{\"spec\":{\"blocks\":[{\"type\":\"paragraph\",\"text\":\"已确认的正文。\"}]}}}\nJSON\n")
```

编辑可使用script=run_wps_edit.py及batch参数，将--spec对应值设为@ops，configs.ops给出操作列表；源文件自动保护。导出script=export_wps.py只接受convert并要求单独且唯一的--output参数，纯标准库任务使用--backend rtf；导出不需要配置时直接用CLI即可，不为归档套job。inputs可补充需保护的任务文件。output与子命令实际输出必须为完全一致的裸文件名，不带 `./` 或任何目录；输入路径仍相对任务目录；configs只允许最多8个命名JSON，@引用须与configs完全一致。

创建前plan登记尚不存在的根目录文件，独占写入；已有文件不认领，不按扩展名扫描。创建后记录实际文件身份与内容指纹，启动worker前封存原始指纹；文件在计划后由他者创建、归属不明或指纹变化时保留并报cleanup_incomplete，不能仅凭“计划时不存在”就认领。主文件和用户输入不清理。成功或已停止的业务失败均关闭本轮配置，不因可能重试而保留。job_cleanup只针对登记项，原cleanup_complete仍表示内部候选，delivery表示归档；不要将三个范围混为目录零残留。无configs不建清单。

正常任务保留子进程退出码0/3/失败；job配置清理失败另退出1、job_status=cleanup_incomplete，主文件及归档结果保留。child_execution记录真实退出/信号状态及通道长度，原始日志不重复回显。超时、强杀或子进程结果不确定返回recovery_pending，先核对文件及生产者状态；确认已停止并核对原清单创建指纹后才针对task_manifest原登记项收尾，不扫描历史文件，不重跑生成掩盖归档失败。job不生成临时Python，用户自有或未知脚本不删除；外层确需临时脚本时仍须由外层创建前登记，脚本退出后收尾。

## 独立分发与维护

公共实现随本包位于 `<skill-root>/scripts/_wps_artifact_runtime/`，来源及哈希见 `<skill-root>/references/lifecycle-upstream.json`。变换仅增加延迟注解及包内相对导入，不从仓库或相邻Skill运行时导入。仓库维护时从仓库根运行 `python -I -S -B -m unittest discover -s tests -p test_wps_lifecycle_parity.py`，审阅公共来源与副本差异并同步哈希，不能只改记录通过检查。该仓库测试不随独立包运行；源码测试 `skills/wps/scripts/test/test_wps_delivery.py`验证只读安装、三入口和受控job，无需其他Skill。

仅Python标准库，不安装软件。受控文件操作依赖真实dir_fd/不跟随链接能力，不可用时停止，不降级删除。需宿主已确认的任务目录；Linux容器、macOS、Windows及真实宿主验收分别记录，不能将本机结果当作跨平台或云空间证明。
