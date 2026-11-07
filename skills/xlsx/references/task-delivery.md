# 写文件任务的调用与归档

路径占位符说明：`<skill-root>` 是 XLSX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 XLSX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

主入口简单静态创建已经内联本页必需的 runner 与状态要求；满足该路径时无需预读本页。其他写入、配置或交付细节按需使用以下说明。

所有文件交付优先使用 `<skill-root>/scripts/run_xlsx_task.py`：stdin提供原CLI参数和按需具名配置；runner生成前同时检查主路径和归档同名，保留主题并自动选择“_第2版”“_第3版”等后缀，以返回的实际output交付。归档默认拒绝覆盖旧文件；直接CLI不自动改名。创建前登记本轮专属JSON；子进程业务成功且归档步骤返回后自动seal/clean；归档失败或跳过也清理，核对后删除本次清单，临时JSON均平铺于任务根。业务失败、部分计算、子进程超时或退出异常也清理配置及清单，保留原退出码；不自动覆盖已有输出重跑。无临时配置时不建立无用清单。它不运行用户Python脚本，不认领旧CLI传入的既存JSON；其他自定义辅助文件仍须创建前通过 `"<skill-root>/scripts/xlsx_lifecycle.py" plan` 显式登记。完整命令、入口覆盖、恢复及限制见交付生命周期 (`<skill-root>/references/delivery-lifecycle.md`)。

用户归档提示根据完整 `delivery` 结果按归档用户话术 (`<skill-root>/references/delivery-lifecycle.md#归档用户话术`)映射，保留原 `delivery.archive_message` 机器文案而不逐字转贴，不展示内部路径、目录、ID或原始配置日志；正常主文件链接保留。归档失败保留主产物，不为此重跑计算；归档、业务计算与清理状态分别报告。


## 复杂写入规格的构造

简单JSON（短文本、少量字段、浅层结构）可直接写；复杂JSON（长正文、多层对象或数组、表格批量数据、大量引号或反斜杠）必须用Python构造dict/list，再用json.dumps(..., ensure_ascii=False, allow_nan=False)序列化，不手工拼接JSON。Python语法与业务字段仍需核对，序列化成功不等于业务验收通过。 使用已验证的Python调用模板（`<skill-root>/references/complex-json.md`），默认源码及JSON均走stdin，不落盘额外文件。
