# 复用、来源与验证

路径占位符说明：`<skill-root>` 是 artifact-lifecycle Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 artifact-lifecycle 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

源码测试位于仓库 `skills/artifact-lifecycle/scripts/test/`，运行包不包含测试。测试只用于维护，生产不得调用。

## 接口与部署

业务技能先负责内容生成及验收，本技能负责清单归档和安全收尾。归档调用已实际读取定位的 `<skill-root>/scripts/archive_delivery.py`；清理调用 `<skill-root>/scripts/lifecycle.py` CLI；每次使用保持任务目录不变，保留完整JSON。不要为执行脚本切换到技能源码目录，也不要扫描其他任务文件。

Python集成可从本独立包的scripts导入：

| 模块 | 接口与职责 |
|---|---|
| router_config | read_environment_parameter(name, environ=...)、get_common_parameter(raw,name)，返回原值、不拼路径 |
| output_location | resolve_output_location(raw)，questionId校验、相对归档位置及fallback原因 |
| archive_delivery | archive_delivery(outputs,protected_inputs)、finalize_delivery、archive_user_message（机器文案）、archive_user_notice（用户提示）、choose_delivery_output（仅选择名称，不预留路径） |
| artifact_files | 既有内部路径检查和单文件原子复制原语，公开调用优先archive_delivery |
| controlled_cleanup | TaskFiles、plan_cleanup、seal_cleanup、finalize_cleanup（最终收尾）；cleanup_task仅内部清理阶段 |
| cleanup_task | 保留DOCX来源的兼容CLI；公共默认使用lifecycle.py |

独立ZIP包含所有同级依赖，不从DOCX或仓库公共目录运行时导入。调用者安装本技能后通过harness确定其位置；未安装或无法定位时报告缺少能力，不猜相邻路径、不自动安装。DOCX、WPS、XLSX、PPTX、ET、OFD各自随包提供受追踪归档实现，无需相邻技能。

## 与DOCX的关系

本批经用户授权从已验证DOCX提取分发副本，文件来源及哈希见来源清单 (`<skill-root>/references/upstream.json`)。DOCX保留标准runner和CLI；已同步任务根目录暂存及新plan禁止临时目录的规则；本次同步字符串参数取值时strip两端空白，来源与哈希按当前副本记录。router_config、output_location、artifact_files、controlled_cleanup、cleanup_task与DOCX逐字节一致；archive_delivery包含共享CLI及真实自检，不加载DOCX生成器。旧docx_cleanup_v1 schema及内部docx临时前缀保留兼容，不意味着公共技能只处理DOCX。

仓库维护回归 `tests/test_artifact_lifecycle_parity.py` 同时比较DOCX来源、公共副本和来源清单哈希。任一边改变时必须先审阅语义差异、同步适用副本与清单、重跑双方回归再更新对应版本包；不能仅更新哈希把未知差异视为通过。该仓库测试不随公共技能包发布；运行包不含测试，维护时在独立解包副本外执行合成验收，不要求DOCX存在。此机制可检测分叉，不自动同步代码，也不宣称已经完成共享源码迁移。默认拒绝覆盖的归档和递增版本命名与DOCX来源同步；不再提供覆盖更新入口。

## 验证边界

源码测试、历史验证与平台限制位于仓库 `docs/maintenance/artifact-lifecycle/validation.md`；发布验收在独立只读解包副本上进行。测试不授权清理用户历史文件，不证明真实云空间同步或所有平台兼容。
