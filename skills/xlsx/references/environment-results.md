# 自检与宿主回传

路径占位符说明：`<skill-root>` 是 XLSX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 XLSX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

Self-check reporting: `check` keeps its single JSON stdout and original `ready`/`failed`/`unsupported` statuses and exit codes; it adds `check_kind: environment` and `artifact_validation: not_run` on handled outcomes. It does not create or accept a workbook. Creation/calculation reports keep their existing contract, and `ready` must never be treated as `output_written`.

The harness must return this invocation's stdout, stderr and actual exit code together with an invocation identifier, including nonzero exits and empty channels. A library return or captured subprocess result is not a model-visible tool result until the harness returns it. Missing execution, timeout or signal termination must be explicit, with no invented normal exit code; truncation must identify the channel and known length. Missing or truncated evidence is unconfirmed, not success. Preserve JSON stdout without added prose; result acceptance still follows validation (`<skill-root>/references/validate.md`).

