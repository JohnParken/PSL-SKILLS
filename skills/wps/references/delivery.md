# 自动归档与受控配置收尾

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 默认交付

创建、重排编辑和导出CLI在主文件发布后核验通过、内部候选清理完成后，自动调用随包公共归档实现；不用再调用外部artifact-lifecycle。裸读取、inspect、check及底层Python生成/编辑/转换API不归档。简单任务继续用原CLI的stdin或内联JSON，不为收尾创建额外文件。

主文件保留在任务目录，归档只是复制，不移动或回滚。配置从ROUTER_CONFIG_VARIABLES的commonParam/questionId读取；缺失、格式错误或ID无效返回delivery.archive_status=skipped，不假称云空间成功。归档冲突默认failed并保留历史，不能删除历史或重跑生成解决。归档复制和校验失败不改变原业务status和退出码；须单独核对delivery、publication和cleanup，不以退出0代替归档验收。归档前后复核主文件发布哈希；发现变化时停止归档或标failed，保留复制记录供检查，不据旧验收正常交付已变化的主文件。

partial结果可归档其已明确验收的可用子集，但原partial、源诊断、格式遗漏和退出3继续保留。发布/追加回读未通过或内部清理未完成时不归档，不能用归档掩盖失败。归档暂存删除失败的temporary_residuals须说明未完成，不能宣称目录已清干净。

归档用户提示使用delivery.archive_user_notice，原archive_message保留；全部成功、失败、跳过和部分成功按真实状态映射，不向用户转贴内部路径、ID或原始配置日志，主文件实际下载链接保留。代码只做本地受控复制，真实云空间展示依赖宿主映射，不进行网络上传；本机合成测试不能证明云平台展示可用。

## 确需落盘JSON时

已有用户JSON始终是输入，不能因作为--spec就删除。若本轮确需创建落盘配置，用受控job入口；它只执行随包三种交付CLI，不执行任意Python脚本。script字段的三个文件名是允许值，不是由工作目录查找的路径。

先自检，要求退出0、JSON status=environment-ok、task_writable=true；探针实际写入、读回并仅清理本次探针：

```console
python -I -S -B "<skill-root>/scripts/run_wps_job.py" --check
```

示例：由job在创建前登记自己的配置文件，子进程结束且取得归档结果后清理配置及清单：

```console
python -I -S -B "<skill-root>/scripts/run_wps_job.py" <<'JSON'
{"script":"run_wps_task.py","arguments":["create","./工作汇报.wps","--spec","@spec"],"output":"./工作汇报.wps","configs":{"spec":{"blocks":[{"type":"paragraph","text":"已确认的正文。"}]}}}
JSON
```

编辑可使用script=run_wps_edit.py及batch参数，将--spec对应值设为@ops，configs.ops给出操作列表；源文件自动保护。导出script=export_wps.py只接受convert并要求单独且唯一的--output参数，纯标准库任务使用--backend rtf；导出不需要配置时直接用CLI即可，不为归档套job。inputs可补充需保护的任务文件。output必须与子命令实际输出一致，所有数据路径相对任务目录；configs只允许最多8个命名JSON，@引用须与configs完全一致。

创建前plan登记尚不存在的根目录文件，独占写入；已有文件不认领，不按扩展名扫描。创建后记录实际文件身份与内容指纹，启动worker前封存原始指纹；文件在计划后由他者创建、归属不明或指纹变化时保留并报cleanup_incomplete，不能仅凭“计划时不存在”就认领。主文件和用户输入不清理。成功或已停止的业务失败均关闭本轮配置，不因可能重试而保留。job_cleanup只针对登记项，原cleanup_complete仍表示内部候选，delivery表示归档；不要将三个范围混为目录零残留。无configs不建清单。

正常任务保留子进程退出码0/3/失败；job配置清理失败另退出1、job_status=cleanup_incomplete，主文件及归档结果保留。child_execution记录真实退出/信号状态及通道长度，原始日志不重复回显。超时、强杀或子进程结果不确定返回recovery_pending，先核对文件及生产者状态；确认已停止并核对原清单创建指纹后才针对task_manifest原登记项收尾，不扫描历史文件，不重跑生成掩盖归档失败。job不生成临时Python，用户自有或未知脚本不删除；外层确需临时脚本时仍须由外层创建前登记，脚本退出后收尾。

## 独立分发与维护

公共实现随本包位于 `<skill-root>/scripts/_wps_artifact_runtime/`，来源及哈希见 `<skill-root>/references/lifecycle-upstream.json`。变换仅增加延迟注解及包内相对导入，不从仓库或相邻Skill运行时导入。仓库维护时从仓库根运行 `python -I -S -B -m unittest discover -s tests -p test_wps_lifecycle_parity.py`，审阅公共来源与副本差异并同步哈希，不能只改记录通过检查。该仓库测试不随独立包运行；独立包的 `<skill-root>/scripts/test/test_wps_delivery.py`验证只读安装、三入口和受控job，无需其他Skill。

仅Python标准库，不安装软件。受控文件操作依赖真实dir_fd/不跟随链接能力，不可用时停止，不降级删除。需宿主已确认的任务目录；Linux容器、macOS、Windows及真实宿主验收分别记录，不能将本机结果当作跨平台或云空间证明。
