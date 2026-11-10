# 格式、路由与精确边界

维护资源说明：文中 `docs/maintenance/wps/` 与 `skills/wps/scripts/test/` 均以宿主确认的仓库根目录为基准，仅供源码维护，不随运行包分发；运行任务不读取或执行。没有仓库时跳过维护资料，不按 Skill 安装目录拼接。

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 目录

- 能力与证据矩阵 (`<skill-root>/references/format.md#能力与证据矩阵`)
- FIB与正文规则 (`<skill-root>/references/format.md#fib与正文规则`)
- 兼容策略和来源 (`<skill-root>/references/format.md#兼容策略和来源`)
- 预算与输出 (`<skill-root>/references/format.md#预算与输出`)
- 附加文字与引用 (`<skill-root>/references/format.md#附加文字与引用`)
- OOXML结构化表格 (`<skill-root>/references/format.md#ooxml结构化表格`)
- 隐藏文字与修订策略 (`<skill-root>/references/format.md#隐藏文字与修订策略`)
- 更广OPC容器子集 (`<skill-root>/references/format.md#更广opc容器子集`)
- 统一读取可信度 (`<skill-root>/references/format.md#统一读取可信度`)
- CFB兼容头与容器检查 (`<skill-root>/references/format.md#cfb兼容头与容器检查`)

## 能力与证据矩阵

| 实际结构 | 当前结果范围 | 证据与缺口 |
|---|---|---|
| CFB + C1 MS-DOC | 主正文/中文/片段顺序/段落 | 独立规范样例、合成边界及既有POI DOC；不是WPS生产证据 |
| 有效FIB 101 | 按136对fc/lcb及2字扩展读取 | 合成固定预期；本地POI仅布局依据，正文遇其他控制符拒绝，CFB minor003B可在data/legacy路径接受并诊断；本轮仅合成回归，未重验原件 |
| 有效FIB 10C，包括baseC1/new10C | 按164对fc/lcb及2字扩展读取 | 公开sample.wps原字节已partial读取，608码元/24段；无全文独立语义对照 |
| 有效FIB D9/112 | 分别108对/2字/截止1024、183对/5字/截止1630 | 仅合成；完整扩展、片段界限及主正文范围测试，无对应真实WPS对照 |
| 精确legacy_word_clsid v3 | data自动或显式策略接受观察GUID，partial | 历史真实样本及合成；默认路径新增覆盖仅合成，不推广任意CLSID |
| 字段存储结果 | 显式cached_result、nested delimiter、partial | 仅合成；不验证sprmCFSpec/Plcfld、结果新鲜度或真实显示 |
| U+0001图片/数据标记 | 显式placeholder为U+FFFC，partial | 真实sample有1个；不读取/解释对象类型或内容 |
| U+0008绘图对象标记 | 显式placeholder，先验证主正文 PlcfSpa CP 与实际 U+0008，再替换为 U+FFFC，partial | 政府官网真实 WPS 原件回归：36 段；不验证有效 sprmCFSpec、SPA 全属性或绘图渲染，见 绘图锚点 (`<skill-root>/references/binary-shapes.md`) |
| MS-DOC附加故事 | 显式六类CP分区，正文保持独立 | 合成分区/脚尾注/批注作者CP映射和节页眉槽；真实六区为空未验；不能称有效编号或某页页眉恢复 |
| Strict/Transitional OOXML Word | 正文、表格文字、修订视图、显式关系/引用ID关联文字 | 合成；未知namespace/MC/绘图/字段等partial，需Expat>=2.6 |
| OOXML逻辑表格 | tables坐标/gridSpan/vMerge/空单元格/嵌套来源 | 仅合成；缺网格坐标unknown/partial，默认文字契约保持，不渲染页面 |
| MS-DOC直接PAPX行/单元格 | 一级行、文字、CP来源与初始列定义，partial | 仅合成；不归组表格、不恢复合并/嵌套，未知操作保留未解析诊断 |
| 其他FIB、私有WPS/Works | unsupported | 不猜测、不自动转换或软件回退 |

后缀不是格式证据。主入口按签名、容器、内部负载分流；同时存在WordDocument和Workbook/Book则拒绝歧义。电子表格（.et/.ett）使用专门的 `et` 技能。

## FIB与正文规则

csw14、cslw22、Windows envr0，nFibBack允许BF/C1。由cswNew决定使用base nFib或nFibNew，不只扩版本白名单：

| 有效版本 | fc/lcb对数 | cswNew | FIB截止 |
|---|---:|---:|---:|
| 0x00C1 | 93 | 0 | 900 |
| 0x00D9 | 108 | 2 | 1024 |
| 0x0101 | 136 | 2 | 1248 |
| 0x010C | 164 | 2 | 1472 |
| 0x0112 | 183 | 5 | 1630 |

扩展中cQuickSavesNew必须0..15；版本/数组/扩展必须一致。112的三个主题语言字完整读取但按规范忽略；其他版本或布局组合继续unsupported。详见分版本依据 (`<skill-root>/references/doc-versions.md`)。模板/AutoText、非Unicode标志、非零宏故事保留字段不在范围。fEncrypted置位返回encrypted；否则fObfuscated按规范忽略，非零lKey违反MUST规则返回corrupt。

偏移24/28是必须忽略的保留字段。片段仍主动限定在[FIB截止,cbMac)，cbMac不超过流长度；检查重叠、CP顺序、总故事数，拒绝FIB/数据交叉。这是有限子集，不是完整格式认证。

CLX取fc/lcb索引33，按标志选0Table/1Table；Prc有界跳过，复杂Prm检查零基索引后不应用格式。唯一末尾Pcdt必须长度/边界正确。压缩片段按FcCompressed映射，UTF16按逻辑片段拼接，严格代理对解码。默认仅读CP0,ccpText)，其他故事仅边界检查。显式include-related按公开FIB顺序输出六区及CP半开范围；最后额外CP不输出；每区独立代理对/字段闭合，不能借下一故事修复。有界脚注尾注引用表见 CP映射 (`<skill-root>/references/binary-note-references.md`)，批注见作者/引用 (`<skill-root>/references/binary-comments.md`)，节与页眉存储槽见分节 (`<skill-root>/references/binary-headers.md`)，其他引用未知partial，见存储故事 (`<skill-root>/references/ms-doc-stories.md`)。

CR划分逻辑段落，软换行转LF，单元格标记转TAB；附加直接属性支持的一级行/单元格证据，不恢复完整网格或页面布局；见二进制表格 (`<skill-root>/references/binary-tables.md`)。合法空片段表成功空正文。统一read/read_bytes/CLI的data模式默认对MS-DOC使用text_policy=recover：未指定的field_policy取cached_result、object_policy取placeholder；显式reject仍生效。strict/legacy兼容模式、底层parse_streams默认保持strict文字策略；显式text_policy=strict可在data模式保留原拒绝行为。RTF 显式 recover 与默认恢复相同；RTF strict 及 OOXML 的该选项不适用并拒绝。

cached_result只按13/14/15分隔符和有界嵌套保留保存结果；隐藏指令不输出，缓存新鲜度和sprmCFSpec/Plcfld未验证，始终partial。不带结果的闭合域省略并报告；缺损、重复分隔、跨故事未闭合域仍corrupt，不用后续文字猜边界。显式field_policy=reject仍拒绝域。

U+0001在placeholder模式替换U+FFFC，不推断图片/对象类型。U+0008有经校验的主正文PlcfSpa锚点时保留原形状来源；recover模式缺少锚点时仅作为未知标记占位，绝不伪造形状或读取其可能指向的字节。已有PlcfSpa结构损坏仍拒绝。其他未支持C0码元在recover模式替换U+FFFC，记录uninterpreted_control_markers_not_decoded、原marker、WordDocument物理偏移、piece和UTF-16 CP、placeholder_emitted；不复制控制符、不推断其关联内容。域指令内的标记不输出占位，诊断明确未输出。所有替换都partial，未知语义及可能关联内容未读取；损坏UTF-16、CLX/FIB/片段、加密与资源超限仍拒绝。

新增控制符诊断最多1000个，绘图/对象标记沿用1000个预算；超限停止，不静默截去后续正文。域位置最多保留20个并给locations_truncated，数量和嵌套上限仍生效。转换后paragraph_sources为空且unavailable_after_text_transform，不把变化后的字符串位置冒充原始CP。

普通文字不含需要转换的标记时结果正文不变；结果新增text_policy字段用于说明实际策略。CLI的0/3/1/2及read_bytes抛异常、read返回失败JSON等旧契约保留；调用者不得因partial有文字而认为全文/页面完整。

## 兼容策略和来源

显式strict要求CFB零头CLSID和minor003E；data及显式legacy额外接受已观察的minor003B并报告partial，其他minor仍拒绝。MS-CFB 2.2对003E使用SHOULD；该兼容不改变major/sector及分配验证。legacy_word_clsid只允许v3精确头字节0609020000000000c000000000000046，根WordDocument必须A5EC，且无Workbook/Book；不改写输入头。异常头永远记录partial，GUID不证明生成软件或完整MS-DOC。Word专用策略不与Excel专用策略交叉使用；默认data对OOXML沿用strict解析，显式legacy仍不适用。data按精确GUID自动选择已知Word头策略；Word头路径仍要求v3、有效WordDocument标识且不含Workbook/Book，零GUID仍遵守原头部约束，默认data另允许下述经声明长度验证的末扇区缺填充兼容。链、分配、重叠、目录、预算检查均保持。

CFB已迁至wps包内唯一实现。CFB迁移基线hash为00dcd269218d94737e9ebad48c06fafa62b1fae72c3807a037fe7f1cd0bfb42a，此后新增只读打开及精确Word策略。OOXML必要读取守卫来自docx。不得自动复制覆盖形成漂移。

官方依据 (`docs/maintenance/wps/format-evidence.json`)仅开发资料，运行时不联网。Word头例外是明示经验兼容策略，并非宣称符合MS-CFB零CLSID要求。

## 预算与输出

统一输入32MiB，非阻塞普通文件打开，fstat预算后读取并复核变化。CFB流8MiB、总32MiB、目录4096、深度32。MS-DOC码元8Mi、片段100000、CLX4MiB、Prc4096、段落100000；字段嵌套64/数量100000、对象诊断1000，超限明确resource_limit。

OOXML解压前检查目录/成员/命名冲突，部件32MiB、累计128MiB、成员4096；XML每部件16MiB、深度128并累计节点/属性/名字预算，拒绝DTD/实体。必须Expat>=2.6；所有ZIP成员有界长度及CRC检查，不解包到磁盘。

CLI选项以 `"<skill-root>/scripts/read_wps.py" --help` 为准；含 `--hidden-text include|exclude|annotate`，分支适用性见主入口任务路由。统一CLI、Python API、失败字典与输出阶段例外详见结果与失败契约 (`<skill-root>/references/reading-assessment.md#入口与失败行为`)。CFB诊断仍仅阶段/消息，并非每处都有精确偏移。

## 附加文字与引用

--include-related仅JSON，附加related_stories不并入顶层主正文。MS-DOC按FIB分区，脚注/尾注Ref/Txt表可验证CP映射，属性/编号和页面仍未知partial；页眉页脚保留合并故事文字并额外映射存储槽，未计算有效页面继承。自动U+0002仅显式include-related且匹配验证引用时占位，其余仍拒绝。OOXML按主document显式关系及引用ID读取页眉/页脚/脚注/尾注/批注，忽略无关联部件，验证目标/类型/根/ID；不计算有效页眉继承或页码，嵌套引用partial。见OOXML关系范围 (`<skill-root>/references/ooxml-related-stories.md`)。默认main兼容，新关联行为仅合成验证。

## OOXML结构化表格

JSON的tables是独立逻辑结构，保留唯一ID、父表/父单元格、来源部件和路径、行与单元格、段落/run顺序以及合并锚点；嵌套表文字不复制入父cell.text。附加故事表格只在include-related时收集。缺失网格或无法可靠解释的网格扩展明确partial，保留文字且不猜列坐标；结构冲突拒绝。二进制MS-DOC正文保持文字/TAB，另可读取有限一级行/单元格证据；不恢复合并和表格归组，见二进制表格 (`<skill-root>/references/binary-tables.md`)。新表格能力尚无真实WPS独立内容对照，见精确范围 (`<skill-root>/references/ooxml-tables.md`)。

## 隐藏文字与修订策略

OOXML hidden-text默认include，显式exclude/annotate共用XML预处理覆盖正文、related和tables；来源与字符计数不包含排除全文。直接vanish/webHidden布尔、styles关系及有界链可验证；继承true toggle保守unknown并partial，不恢复完整样式级联。accepted/original/all沿用存储修订视图，属性历史变更partial不应用；trace标revision_excluded。MS-DOC返回visibility_unknown，显式annotate读取有界BTE/CHPX的原始属性操作与CP来源，但有效隐藏/修订仍unknown；不删文字，exclude及accepted/original仍拒绝。详见证据边界 (`<skill-root>/references/ms-doc-charprops.md`)。详见策略说明 (`<skill-root>/references/word-visibility.md`)。仅合成证据，无真实隐藏文字独立显示对照。

## 更广OPC容器子集

按officeDocument主关系及内容类型识别Word文档。Strict Word逐已选部件验证家族一致后仅内存规范化复用契约，主部件仍固定word/document.xml；不是全Strict认证。全部OOXML需Expat>=2.6；未知类型、关系、外部目标、宏拒绝。详见Word (`<skill-root>/references/strict-word.md`)。新增覆盖仅合成，无真实WPS容器语义对照。

## 统一读取可信度

reading_assessment (`<skill-root>/references/reading-assessment.md`)是统一入口的附加摘要，保留原诊断和数据。ok仅within_declared_scope，partial与失败严格分开；位置缺失保持unknown，不复制排除全文。

默认 MS-DOC 检测到非空未选附加故事时，返回 `omitted_regions` 的类型/CP范围并标为 partial，不读取或复制其文字。已知表格标记但结构不完整时同样 partial。统一范围摘要另提供有界 coverage；见结果契约 (`<skill-root>/references/reading-assessment.md`)。

## CFB兼容头与容器检查

默认 data 对已识别 MS-DOC 的末扇区缺填充作有界兼容：全部声明流字节必须实际存在，目录及分配元数据必须完整，并通过原有链、交叉引用、加密和资源检查；仅在内存补齐末扇区未声明填充，记录 `cfb_missing_final_sector_padding` 并返回 partial。严格模式仍拒绝物理长度不对齐。不得截去原件尾部或将缺失流内容补零当作正文；原件保持不变。当前农行项目需求真实原件（末扇区存73字节）读取6212字符、152段，与独立FIB/CLX正文解码一致；第二份农行报名原件（453256字节，末扇区存136字节）直接读取3268字符、122段并返回partial；独立FIB/CLX抽取的88个非域指令正文片段共2920字符按序匹配，完整语义对照因域/对象控制符未完成，不声明表格结构或全文完整。macOS/Python3.13验证，不代表客户端版式或Linux/Windows验收。

默认 data 按精确头 GUID 自动选择已有的旧版 Word CFB 兼容路径，并接受旧版 minor `0x003B`；仍检查对应负载、版本、扇区链和边界，并报告 partial。显式 strict 保留零头 GUID 与 minor `0x003E` 要求。字段、对象及正文布局等独立限制继续按下表处理，容器可读不等于任意 MS-DOC 都能提取。

## RTF部分恢复与发布边界

RTF 的直接字节与十六进制字节按文档代码页解码；未声明代码页的高位字节沿用 CP936 假设并标 partial，解码替换及无法可靠应用的字体级代码页也有诊断。双字节与结构定界符无法消歧时拒绝，不猜测或把潜在控制符当作正文。正文段落和表格输出有上限，超限保留有界内容并标 partial/遗漏；嵌套或结构资源预算耗尽则拒绝，不能以 partial 绕过安全限制。全文 `--format text` 按 `blocks` 顺序输出段落与表格，行内单元格用制表符；为兼容既有接口，JSON 的 `text` 仍仅含段落，表格及次序取 `tables/blocks`。RTF 的隐藏排除/注释及 accepted/original/all 文本视图仅支持可验证的直接标记和组作用域；样式级联、历史属性、字段或结构边界使筛选无法保证时拒绝。默认保留存储文字，遇隐藏或修订标记标 partial。显式 field-policy=reject 在已解析主内容遇域时拒绝，cached_result 保留存储结果且 partial。页眉、页脚、脚注与批注不并入主文；附加区域提取和二进制段落来源目前不支持。默认对已识别的图片/对象保留 U+FFFC 占位与源字节位置并标 partial，不执行或解码对象；显式 object-policy=reject 遇到已解析对象拒绝。显式文本 recover 使用相同有界恢复；strict 以及不适用的兼容/图表策略请求仍拒绝。

RTF读取在未闭合组、行或单元格、Unicode/十六进制截断时保留可证实的文字和物理单元格，返回partial及rtf_*诊断；不能确认的字符不猜测。未配对Unicode代理项以替代字符显示并诊断。页眉页脚和相关区域省略明确标记；图片及对象默认保留占位并报告内容未解码。域只保留存储结果，不执行、不证明新鲜度。未知可选destination保守跳过并诊断；字体/颜色等已知元数据不因未呈现而自动标记正文缺失。二进制长度确定时按字节跳过，不将其字节误作正文或组边界。

生成/重排编辑在发布前拒绝带结构或文字恢复诊断的候选，保留原目标；仅附加内容省略、域缓存未验证可带partial回执发布，不意味着版式和所有内容已验收。普通编辑仍拒绝partial输入，用户明确授权--allow-lossy-reflow时才生成标注损失的新副本。读取变为partial可能使原来静默省略页眉/对象的RTF不再进入默认重排编辑，这是缺口显式化；不是取消读取能力。
