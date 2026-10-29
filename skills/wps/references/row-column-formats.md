# 有限行列数字格式来源

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

OOXML 原始 cell XF、行 `s/customFormat` 和列 `style` 始终保留。无显式单元格 `s` 时，仅一个有效来源（启用 customFormat 的行，或一段列）且目标 cell XF 明确 applyNumberFormat=true，或与有效父样式的保存数字格式一致并通过两层来源校验 (`<skill-root>/references/style-inheritance.md`)，才用已有数字格式白名单解释。解释附带 style_source，原始单元格索引、数字及格式字段不改写。

同时存在行列来源、无效/缺失样式、未明确启用的行格式或父样式缺失/冲突时继续 unknown/partial，不猜优先级。复杂格式仍 unknown；不计算条件格式、主题、完整样式继承或页面显示。已有五份真实样本没有此 OOXML 样式来源的独立显示对照；新增测试仅合成。

公开依据为 Microsoft Open XML Row/Column/Cell 类所列 ISO/IEC 29500 属性语义，链接见 row-column-formats-evidence.json；只供开发核对，运行时不联网。
