# 工作表内部超链接元数据

仅支持没有关系ID的location-only hyperlink，保存单元格ref、原location/display/tooltip及部件来源。不解析sheet或definedName、不跟随或执行目标、不改变单元格显示文字。标记partial与target_resolved=false；字符串不是已验证链接。

ref必须是一个合法单元格，重复单元格或集合、关系ID及未知属性拒绝。每表最多4096条，各属性32767字符并计入已有输出文字预算。所有worksheet关系仍拒绝，外部目标策略不变。

Microsoft Spreadsheet Hyperlink说明location为目标位置，ref为单元格，display独立于共享字符串；公开依据：
https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.spreadsheet.hyperlink?view=openxml-3.0.1

新增test_wps_internal_links.py是标准库合成例，不是真实WPS来源；尚无真实内部超链接独立内容对照。
