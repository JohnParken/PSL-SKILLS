# OOXML 电子表格保存值子集

维护资源说明：文中 `docs/maintenance/et/` 与 `skills/et/scripts/test/` 均以宿主确认的仓库根目录为基准，仅供源码维护，不随运行包分发；运行任务不读取或执行。没有仓库时跳过维护资料，不按 Skill 安装目录拼接。

路径占位符说明：`<skill-root>` 是 ET Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 ET 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

统一入口按安全 OPC 主关系及内容类型识别，不依据 .wps/.et/.ett 后缀猜测容器。支持 Transitional 与 Strict spreadsheet 命名空间及对应关系 URI；不把两者混用。包内复用既有 ZIP/XML 守卫，要求安全 Expat；Python3.9 的旧 Expat 明确拒绝而不是降级解析。读取为内容优先容错：外来扩展属性和元素（`mc:*`、`xr:*`、`x14:*`、`x14ac:*`、`AlternateContent`）、未知工作簿/工作表关系、未知工作表元素等非值差异被忽略或保留为有界证据并附 issue，不因扩展元数据拒绝整包。

读取有界工作簿关系、工作表顺序/名称/隐藏状态、日期系统、明确坐标的数字/共享或 inline 字符串/布尔/错误/空单元格、保存公式缓存、合并范围和有限样式元数据。原数字按 XML 词法读取为 Decimal，并保留 raw_numeric_text；不经二进制 float。数值解释复用包内有限格式解释；样式继承不明则 unknown。公式文本及属性只作为存储元数据，表达式未解释，结果总有 partial 提示，不重算、不生成每格公式或执行外链。缺缓存标 missing，不填零。未知类型不当空值；单格未知子元素、未知错误文本或非法日期词法保留原值/文本并附 issue。

OOXML 保存文本中的 `_xHHHH_`（UTF-16 代码单元）转义会在共享字符串、内联字符串、富文本 run 和单元格文本值上解码一次；`_x005F_` 保护的字面序列保持为字面文本（例如 `_x005F_x000D_` 返回 `_x000D_`，不会再变成回车）。相邻代理对转义合并为一个 Unicode 字符，孤立代理项保留字面转义并附 issue。解码只发生一次，不对已解码结果二次解释。有转义的单元格附 `text_encoding`（方案、转义计数、字面保护计数；内联文本含原始 `raw_text`，共享文本经 `sst_index` 关联）；共享字符串的原始编码证据集中在工作簿 `shared_string_encoding`，避免每格重复保存。原始编码与解码后的 `value` 分开保留。

输出 schema_version 为 ooxml_spreadsheet_readonly_v1，spreadsheet_parsed=true；cells、merges、date_system 和 cache_status 与 BIFF 分析契约对应，不冒称 biff_parsed。默认全部行；保存隐藏行列的原因未知，筛选不执行。工作表可见性使用包内 select_rows 的同一字段契约。元数据未解释会明确诊断。

重复 `sheetData` 与重复行按原顺序继续读取不同坐标；重复坐标保留首个可读值，行元数据保留首条，返回 `duplicate_sheet_data_recovered` / `duplicate_row_cells_recovered` / `duplicate_cell_ignored`。缺失 `sheetData`、数据段或行内未解释元素及上述恢复均标记 `omissions_affect_cell_values=true`，不把缺口当作完整空表。资源预算继续累计，不因新增数据段重置。

`visibility` 保留可解释的保存行高和列宽，单位及缺失处理见 `<skill-root>/references/saved-visibility.md`。预览可直接返回工作表 XML 中已保存的公式文本，取值仍使用单元格缓存。

共享富文本使用单元格 `sst_index` 关联 `shared_string_formatting`。内联富文本在单元格的 `rich_text.segments` 中返回文字、字体名称、粗体、斜体和保存的颜色标识。颜色索引和主题色保留为来源值。整个工作簿最多附加 16384 段，超限时保留原文字并标记 `segments_status`。

完整读取的 `views` 提取各 `sheetView` 保存的冻结行列与活动窗格的单元格。`frozen` 与 `frozenSplit` 提供冻结行列计数。普通分割的距离不转换为冻结计数。字段和缺失含义见 `<skill-root>/references/saved-visibility.md#保存的工作表视图`。

单元格文本的最终保留值统一受工作簿值文字预算约束：共享字符串、内联字符串、富文本和 `t="str"` 的文本都先解码，再由同一预算函数按 Unicode 代码点计费；超出时返回可保留的前缀并在单元格标记 `text_truncated`，同时设置 `text_limit_reached`、让覆盖变为不确定。共享字符串按每个单元格的展开值计费，不按唯一 SST 文本重复计费；富文本格式段属于结构化元数据，另受最终 JSON 输出预算约束。

自定义格式文本使用独立的展示预算（默认 2Mi 代码点），按 `numFmtId` 去重计费；超限时省略受影响单元格的 `custom_format_raw`、从顶层 `custom_number_formats` 移除该格式，并设置 `custom_format_raw_omitted`、`custom_number_formats_omitted` 与 `display_text_budget_truncated`，保存值继续返回。结果 `text_budget`（值预算）与展示预算字段分别报告单位、上限、实际用量和是否触发限制，不能把截断值或省略的展示字段当完整原值。

限制与容错边界：工作表应显式提供 row r / cell r，缺失坐标时按顺序推断并附 issue；最多 128 表、200000 单元格/行元数据、200000 共享字符串、16384 列元数据、65536 合并、8Mi 单元格保留文字及 256 个诊断代码（超出置 `issues_truncated`），另有 ZIP/XML 与最终 JSON 预算。`cell_value_coverage`/`omissions_affect_cell_values` 由解析状态显式记录，不从可能被截断的诊断条目反推；缺失公式缓存即使对应 issue 被截断也会保持 `missing_formula_caches`。重复 ID/名字/坐标、错误共享索引、重叠合并、越界坐标、混合值命名空间等保留已解析值并附 issue；宏内容类型仍拒绝（不执行宏）。未知工作簿关系（theme、calcChain、externalLink、connections、customXml、sheetMetadata、chartsheet、pivotCache 等）只列名不读取；外部目标记录 `target_resolved=false`、`followed=false`，**从不联网或跟随**。工作表关联部件只对传统 comments/VML 做尽力校验，其他绘图、线程批注、外部超链接及关联部件只记录不解析。模板主类型（非宏的 `template.main+xml`）按电子表格接受。ISO 日期类型 d 的有限词法支持见日期边界 (`<skill-root>/references/iso-date.md`)；非法日期词法保留原文并附 issue。音标字符串、富样式渲染、计算链与高级表结构仍未实现。值承载解析器位于 `wps_ooxml_worksheet.py`，主包/关系层仍由 `wps_ooxml_sheet.py` 驱动。

验证范围：新增容错行为使用纯标准库合成 OPC/SpreadsheetML 与仓库内 5 份真实 `.xlsx` 资产做读取回归（420/26546/401035/68495/1399 个单元格，均为 partial），不是改后缀的真实 WPS 来源。没有真实 WPS 原生 OOXML `.et` 的语义对照；不能据测试推断多数 WPS 兼容率。开发依据见来源记录 (`docs/maintenance/et/ooxml-spreadsheet-evidence.json`)，运行时不访问网络。

格式来源与内部链接：单来源行列数字格式 (`<skill-root>/references/row-column-formats.md`)保留格式出处，双来源仅已解析数字格式相同时作等价解释，不猜其他样式优先级；有效两层数字格式链接另见有界样式来源 (`<skill-root>/references/style-inheritance.md`)；location-only内部链接 (`<skill-root>/references/internal-links.md`)只保存原文本、单元格及未解析状态，不跟随目标或放宽关系限制。

常用会计格式 (`<skill-root>/references/accounting-formats.md`)只解释位置分段、货币/千分/小数/负括号/零破折号，列宽填充省略。comments读取规则和预算见传统批注 (`<skill-root>/references/sheet-comments.md`)；新行为仍为合成验证。

传统Transitional批注的有限VML Note锚点 (`<skill-root>/references/vml-note-anchors.md`)只保留Row/Column和八整数矩形，严格一对一匹配，不渲染；Strict VML或未知活动内容拒绝。格式条件和区域边界见有限格式指令 (`<skill-root>/references/conditional-number-formats.md`)。
