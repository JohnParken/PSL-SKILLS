# OOXML 电子表格保存值子集

统一入口按安全 OPC 主关系及内容类型识别，不依据 .wps/.et/.ett 后缀猜测容器。支持 Transitional 与 Strict spreadsheet 命名空间及对应关系 URI；不把两者混用。包内复用既有 ZIP/XML 守卫，要求安全 Expat；Python3.9 的旧 Expat 明确拒绝而不是降级解析。

读取有界工作簿关系、工作表顺序/名称/隐藏状态、日期系统、明确坐标的数字/共享或 inline 字符串/布尔/错误/空单元格、保存公式缓存、合并范围和有限样式元数据。原数字按 XML 词法读取为 Decimal，并保留 raw_numeric_text；不经二进制 float。数值解释复用包内有限格式解释；样式继承不明则 unknown。公式文本及属性只作为存储元数据，表达式未解释，结果总有 partial 提示，不重算、不生成每格公式或执行外链。缺缓存标 missing，不填零。未知类型不当空值。

输出 schema_version 为 ooxml_spreadsheet_readonly_v1，spreadsheet_parsed=true；cells、merges、date_system 和 cache_status 与 BIFF 分析契约对应，不冒称 biff_parsed。默认全部行；保存隐藏行列的原因未知，筛选不执行。工作表可见性使用包内 select_rows 的同一字段契约。元数据未解释会明确诊断。

限制：工作表必须显式 row r / cell r，最多128表、200000单元格/行元数据、10000共享字符串、4096列元数据/样式、256合并、8Mi文字及1000诊断，另有既有ZIP/XML和最终JSON预算。拒绝重复ID/名字/坐标、错误共享索引、重叠合并、越界坐标、命名空间混淆、宏、外部关系和未知工作簿关系；工作表关联部件只支持严格验证的传统comments纯文本；其他绘图、线程批注、外部超链接及其他关联部件仍拒绝，不悄悄漏读。ISO日期类型d的有限词法支持见[日期边界](iso-date.md)。音标字符串、富样式渲染、计算链与高级表结构仍未实现。严格有限路径可能拒绝合法但超出范围的文件。

新增验证使用纯标准库合成 OPC/SpreadsheetML，不是改后缀的真实WPS来源。没有新增真实 .et/.ett 内部 OOXML 电子表格的语义对照；不能据测试推断多数WPS兼容率。开发依据见[来源记录](ooxml-spreadsheet-evidence.json)，运行时不访问网络。

格式来源与内部链接：[单来源行列数字格式](row-column-formats.md)保留格式出处，双来源仅已解析数字格式相同时作等价解释，不猜其他样式优先级；有效两层数字格式链接另见[有界样式来源](style-inheritance.md)；[location-only内部链接](internal-links.md)只保存原文本、单元格及未解析状态，不跟随目标或放宽关系限制。

[常用会计格式](accounting-formats.md)只解释位置分段、货币/千分/小数/负括号/零破折号，列宽填充省略。comments读取规则和预算见[传统批注](sheet-comments.md)；新行为仍为合成验证。

传统Transitional批注的[有限VML Note锚点](vml-note-anchors.md)只保留Row/Column和八整数矩形，严格一对一匹配，不渲染；Strict VML或未知活动内容拒绝。格式条件和区域边界见[有限格式指令](conditional-number-formats.md)。
