# 工作表内部超链接元数据

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

仅支持没有关系ID的location-only hyperlink，保存单元格ref、原location/display/tooltip及部件来源。不解析sheet或definedName、不跟随或执行目标、不改变单元格显示文字。标记partial与target_resolved=false；字符串不是已验证链接。

ref必须是一个合法单元格，重复单元格或集合、关系ID及未知属性拒绝。每表最多4096条，各属性32767字符并计入已有输出文字预算。所有worksheet关系仍拒绝，外部目标策略不变。

Microsoft Spreadsheet Hyperlink说明location为目标位置，ref为单元格，display独立于共享字符串；公开依据：
https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.spreadsheet.hyperlink?view=openxml-3.0.1

新增test_wps_internal_links.py是标准库合成例，不是真实WPS来源；尚无真实内部超链接独立内容对照。
