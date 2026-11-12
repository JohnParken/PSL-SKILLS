# 工作表超链接元数据（OOXML 与 BIFF8）

路径占位符说明：`<skill-root>` 是 ET Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 ET 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

OOXML 仅支持没有关系ID的 location-only hyperlink，保存单元格 ref、原 location/display/tooltip 及部件来源。不解析 sheet 或 definedName、不跟随或执行目标、不改变单元格显示文字。标记 partial 与 target_resolved=false；字符串不是已验证链接。

ref必须是一个合法单元格；重复单元格或集合、未知属性保留为有界证据并附 issue，不中止整表。每表最多4096条，各属性32767字符并计入已有输出文字预算。带关系ID的外部超链接只记录 `target_resolved=false`、`followed=false` 与有界原始目标文本 `target_raw`，从不联网或跟随；其他工作表关系同样只列名、记 issue，不读取其部件。

维护依据见 `docs/maintenance/et/comments-hyperlinks-evidence.json`（BIFF8 HLink/HLinkTooltip、MS-OSHARED Hyperlink Object/HyperlinkString、Ref8U、FrtRefHeaderNoGrbit 与 record type 表）。Microsoft Spreadsheet Hyperlink 说明 location 为目标位置、ref 为单元格、display 独立于共享字符串；OOXML 公开依据：
https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.spreadsheet.hyperlink?view=openxml-3.0.1

## BIFF8 HLink / HLinkTooltip

BIFF8 工作表的 `HLink (0x01B8)` 与相邻 `HLinkTooltip (0x0800)` 在长度、Ref8U 行列、streamVersion=2、头字段、UTF-16 字符串、可选 GUID/FileTime、tooltip 终止符和匹配 Ref8U 校验通过后，返回 `sheets[].hyperlinks`。每项含 `kind='biff8_hlink'`、`cell`（多格时另有 `range`）、`target_kind`、`display`、`target_frame`、`moniker`、`location`、`target`、`tooltip`、`target_resolved=false`、`followed=false`、`link_executed=false`，以及来源记录偏移、Ref8U 原始字节、CLSID、streamVersion 和 flags。

`target_kind` 为 `internal`（location-only）、`external_string`（字符串 moniker）或 `ole_moniker_opaque`：未解码的 OLE moniker 只保留有界原始记录和结构标记，不猜测目标；字段顺序无法安全解析时保留 raw 并标记 `unparsed_remaining_fields`。不解析工作表/definedName 名称、不联网、不下载、不执行、不跟随任何目标，也不改变单元格显示文字。目标未解析的 HYPERLINK 会使结果保持 partial。每表最多 4096 条；各文本最长 32767 字符（tooltip 最长 255 字符）并计入输出文字预算；非法记录保留 raw 并附 issue，后续单元格继续读取。

新增 `test_wps_biff8_hyperlinks.py` 与 `test_wps_internal_links.py` 均为标准库合成例，不是真实 WPS 来源；尚无真实 WPS 内部/外部超链接独立内容对照。
