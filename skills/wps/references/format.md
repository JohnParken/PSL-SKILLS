# 格式、路由与精确边界

## 能力与证据矩阵

| 实际结构 | 当前结果范围 | 证据与缺口 |
|---|---|---|
| CFB + C1 MS-DOC | 主正文/中文/片段顺序/段落 | 独立规范样例、合成边界及既有POI DOC；不是WPS生产证据 |
| 有效FIB 101 | 按136对fc/lcb及2字扩展读取 | 合成固定预期；本地POI仅布局依据，正文遇其他控制符拒绝，原CFB minor003B仍被统一入口拒绝 |
| 有效FIB 10C，包括baseC1/new10C | 按164对fc/lcb及2字扩展读取 | 公开sample.wps原字节已partial读取，608码元/24段；无全文独立语义对照 |
| 有效FIB D9/112 | 分别108对/2字/截止1024、183对/5字/截止1630 | 仅合成；完整扩展、片段界限及主正文范围测试，无对应真实WPS对照 |
| 精确legacy_word_clsid v3 | 显式策略接受观察GUID，partial | 同一真实样本；默认strict拒绝，不推广任意CLSID |
| 字段存储结果 | 显式cached_result、nested delimiter、partial | 仅合成；不验证sprmCFSpec/Plcfld、结果新鲜度或真实显示 |
| U+0001图片/数据标记 | 显式placeholder为U+FFFC，partial | 真实sample有1个；不读取/解释对象类型或内容 |
| MS-DOC附加故事 | 显式六类CP分区，正文保持独立 | 合成分区/脚尾注/批注作者CP映射和节页眉槽；真实六区为空未验；不能称有效编号或某页页眉恢复 |
| Strict/Transitional OOXML Word | 正文、表格文字、修订视图、显式关系/引用ID关联文字 | 合成；未知namespace/MC/绘图/字段等partial，需Expat>=2.6 |
| OOXML逻辑表格 | tables坐标/gridSpan/vMerge/空单元格/嵌套来源 | 仅合成；缺网格坐标unknown/partial，默认文字契约保持，不渲染页面 |
| BIFF8 plain SST/String Continue | typed cells/长字符串/日期元数据/缓存 | 四个既有真实ET/ETT及合成；不重算缓存 |
| BIFF8 rich SST及Continue | 文字+原始格式run信息，partial | 仅合成；字符编码与run续段分开，不应用字体布局，phonetic仍拒绝 |
| BIFF8共享/数组公式 | 各单元格保存的缓存+组范围/锚点，partial | 仅合成；不恢复表达式、不重算，不将锚点缓存广播 |
| OOXML电子表格及ISO日期类型d | Strict/Transitional保存值、缓存、合并、原始日期字符串 | 仅合成；日期不转serial，复杂样式unknown，关联部件仍有限 |
| 其他FIB、私有WPS/Works | unsupported | 不猜测、不自动转换或软件回退 |

后缀不是格式证据。主入口按签名、容器、内部负载分流；同时存在WordDocument和Workbook/Book则拒绝歧义。docx/xlsx原生入口拒绝WPS家族后缀，CFB伪装OOXML也不会走二进制解码。

## FIB与正文规则

csw14、cslw22、Windows envr0，nFibBack允许BF/C1。由cswNew决定使用base nFib或nFibNew，不只扩版本白名单：

| 有效版本 | fc/lcb对数 | cswNew | FIB截止 |
|---|---:|---:|---:|
| 0x00C1 | 93 | 0 | 900 |
| 0x00D9 | 108 | 2 | 1024 |
| 0x0101 | 136 | 2 | 1248 |
| 0x010C | 164 | 2 | 1472 |
| 0x0112 | 183 | 5 | 1630 |

扩展中cQuickSavesNew必须0..15；版本/数组/扩展必须一致。112的三个主题语言字完整读取但按规范忽略；其他版本或布局组合继续unsupported。详见[分版本依据](doc-versions.md)。模板/AutoText、非Unicode标志、非零宏故事保留字段不在范围。fEncrypted置位返回encrypted；否则fObfuscated按规范忽略，非零lKey违反MUST规则返回corrupt。

偏移24/28是必须忽略的保留字段。片段仍主动限定在[FIB截止,cbMac)，cbMac不超过流长度；检查重叠、CP顺序、总故事数，拒绝FIB/数据交叉。这是有限子集，不是完整格式认证。

CLX取fc/lcb索引33，按标志选0Table/1Table；Prc有界跳过，复杂Prm检查零基索引后不应用格式。唯一末尾Pcdt必须长度/边界正确。压缩片段按FcCompressed映射，UTF16按逻辑片段拼接，严格代理对解码。默认仅读CP[0,ccpText)，其他故事仅边界检查。显式include-related按公开FIB顺序输出六区及CP半开范围；最后额外CP不输出；每区独立代理对/字段闭合，不能借下一故事修复。有界脚注尾注引用表见[CP映射](binary-note-references.md)，批注见[作者/引用](binary-comments.md)，节与页眉存储槽见[分节](binary-headers.md)，其他引用未知partial，见[存储故事](ms-doc-stories.md)。

CR划分逻辑段落，软换行转LF，单元格标记转TAB；不重建表格行列/页面布局。合法空片段表成功空正文。字段默认拒绝；cached_result只按13/14/15分隔符与有界嵌套保留存储结果，隐藏指令不输出；缺损字段为corrupt。无分隔字段省略并记录partial，不执行任何指令。U+0001仅在placeholder模式替换U+FFFC，保留CP/偏移诊断，不能推断是图片还是其他二进制数据；其他未支持控制符仍拒绝。

## 兼容策略和来源

严格CFB头要求零CLSID，入口主动限定minor003E。legacy_word_clsid只允许v3精确头字节0609020000000000c000000000000046，根WordDocument必须A5EC，且无Workbook/Book；不改写输入头。异常头永远记录partial，GUID不证明生成软件或完整MS-DOC。legacy_excel_clsid只用于BIFF工作簿，允许v3精确头字节2008020000000000c000000000000046及对应受限元数据，具体范围见[BIFF兼容](biff8-readonly.md#observed-legacy-compatibility)。Word与Excel策略不可交叉使用；OOXML仅strict。链、分配、重叠、目录、预算检查均保持。

CFB/BIFF已迁至wps包内唯一实现；不依赖相邻xlsx。CFB迁移基线hash为00dcd269218d94737e9ebad48c06fafa62b1fae72c3807a037fe7f1cd0bfb42a，此后新增只读打开及精确Word策略，不再逐字节相同。OOXML必要读取守卫来自docx，纯表头helper来自xlsx；原生流程仍保留各自必需代码。不得自动复制覆盖形成漂移。

[官方依据](format-evidence.json)仅开发资料，运行时不联网。Word头例外是明示经验兼容策略，并非宣称符合MS-CFB零CLSID要求。

## 预算与输出

统一输入32MiB，非阻塞普通文件打开，fstat预算后读取并复核变化。CFB流8MiB、总32MiB、目录4096、深度32。MS-DOC码元8Mi、片段100000、CLX4MiB、Prc4096、段落100000；字段嵌套64/数量100000、对象诊断1000，超限明确resource_limit。

OOXML解压前检查目录/成员/命名冲突，部件32MiB、累计128MiB、成员4096；XML每部件16MiB、深度128并累计节点/属性/名字预算，拒绝DTD/实体。必须Expat>=2.6；所有ZIP成员有界长度及CRC检查，不解包到磁盘。

BIFF流/记录/单元格/字符串预算保持；重复单元格文字及格式文本累计8Mi UTF16单位，JSON在完整输出前以iterencode限制16MiB。日期保留原始数值及date_system/XF/format，另有直接格式白名单numeric_interpretation；复杂格式unknown，可验证的cellXfs/cellStyleXfs两层数字格式来源及单一行列来源可有限解释；不推断完整样式级联或显示。公式tokens及已有缓存分开保留，未重算且可能过时；共享/数组支持与拒绝条件统一见[公式组边界](formula-groups.md)，字符串续段见[富文本续段](biff-rich-continuation.md)。其他记录范围详见[BIFF参考](biff8-readonly.md)。

CLI选项以 `read_wps.py --help` 为准；含 `--hidden-text include|exclude|annotate`，分支适用性见主入口任务路由。统一CLI、Python API、失败字典与输出阶段例外详见[结果与失败契约](reading-assessment.md#入口与失败行为)。低层历史BIFF CLI沿用其成功status/退出0，优先使用统一入口。CFB及部分BIFF诊断仍仅阶段/消息，并非每处都有精确偏移。

## 数字解释与保存可见性

[numeric_interpretation](numeric-interpretation.md)为附加解释，不改变value/type/cache_status；Decimal金额/百分比、千分/缩放/编号和白名单日期时间均有边界，非完整渲染。公式缓存不重算。

[保存可见性](saved-visibility.md)解释Row/ColInfo隐藏标志；未知行状态保留unknown，隐藏原因不区分手动或筛选。筛选记录保留原字节、条件未解释且未执行，partial。默认全部数据；显式saved_visible选择也保留未知行。OOXML电子表格使用独立schema和同样的保守可见性约定；复杂样式继承不猜测。

## 附加文字与引用

--include-related仅JSON，附加related_stories不并入顶层主正文。MS-DOC按FIB分区，脚注/尾注Ref/Txt表可验证CP映射，属性/编号和页面仍未知partial；页眉页脚保留合并故事文字并额外映射存储槽，未计算有效页面继承。自动U+0002仅显式include-related且匹配验证引用时占位，其余仍拒绝。OOXML按主document显式关系及引用ID读取页眉/页脚/脚注/尾注/批注，忽略无关联部件，验证目标/类型/根/ID；不计算有效页眉继承或页码，嵌套引用partial。见[OOXML关系范围](ooxml-related-stories.md)。默认main兼容，新关联行为仅合成验证。

## OOXML结构化表格

JSON的tables是独立逻辑结构，保留唯一ID、父表/父单元格、来源部件和路径、行与单元格、段落/run顺序以及合并锚点；嵌套表文字不复制入父cell.text。附加故事表格只在include-related时收集。缺失网格或无法可靠解释的网格扩展明确partial，保留文字且不猜列坐标；结构冲突拒绝。二进制MS-DOC仍仅文字/TAB，不恢复表格结构。新表格能力尚无真实WPS独立内容对照，见[精确范围](ooxml-tables.md)。

## 隐藏文字与修订策略

OOXML hidden-text默认include，显式exclude/annotate共用XML预处理覆盖正文、related和tables；来源与字符计数不包含排除全文。直接vanish/webHidden布尔、styles关系及有界链可验证；继承true toggle保守unknown并partial，不恢复完整样式级联。accepted/original/all沿用存储修订视图，属性历史变更partial不应用；trace标revision_excluded。MS-DOC返回visibility_unknown，显式annotate读取有界BTE/CHPX的原始属性操作与CP来源，但有效隐藏/修订仍unknown；不删文字，exclude及accepted/original仍拒绝。详见[证据边界](ms-doc-charprops.md)。详见[策略说明](word-visibility.md)。仅合成证据，无真实隐藏文字独立显示对照。

## 更广OPC容器子集

按officeDocument主关系及内容类型识别Word/电子表格。Strict Word逐已选部件验证家族一致后仅内存规范化复用契约，主部件仍固定word/document.xml；不是全Strict认证。电子表格支持Strict/Transitional及保存值/缓存/合并/有限样式与可见性，明确schema ooxml_spreadsheet_readonly_v1，不伪称BIFF。全部OOXML需Expat>=2.6；未知类型、关系、外部目标、宏与混合家族拒绝。复杂格式unknown，公式表达式原样不解释并partial。详见[Word](strict-word.md)与[电子表格](ooxml-spreadsheet.md)。新增覆盖仅合成，无真实WPS容器语义对照。

## 统一读取可信度

[reading_assessment](reading-assessment.md)是统一入口的附加摘要，保留原诊断和数据。ok仅within_declared_scope，partial与失败严格分开；位置缺失保持unknown，不复制排除全文。ISO日期读取的精确支持范围见[日期类型d](iso-date.md)。

## 样式来源与关联部件

[行列格式来源](row-column-formats.md)单一明确来源或双来源数字格式等价时可解释；[两层格式链接](style-inheritance.md)检查父子保存值、apply标志和冲突，非法链循环/越界/深度均unknown。location-only[内部超链接](internal-links.md)保留原元数据并partial，不解析目标、不读取外部关系；不是工作表关联部件通用支持；当前另有[传统批注](sheet-comments.md)纯文本子集，另支持[有限VML Note锚点](vml-note-anchors.md)；其他绘图/外部或未知关系仍拒绝。新行为仅合成验证，实现边界及未验证范围见[验证记录](validation.md)。

## 会计与条件数字格式

[会计格式](accounting-formats.md)支持有限2–4位置段，保留Decimal原值与原格式；移除填充后的display_text不代表列宽或页面显示。[纯数值条件](conditional-number-formats.md)仅支持至多两个常量比较与后备段；区域指令仅元数据，颜色及复杂混合语法仍unknown。批注保留单元格、作者、文本与关系来源，不并入单元格值，布局未解码partial。所有新增能力只作标准库合成测试；真实Balance格式字节可检查，不等于独立显示对照。
