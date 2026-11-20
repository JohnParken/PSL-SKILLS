# OOXML 关联文字与引用边界

维护资源说明：文中 `docs/maintenance/wps/` 与 `skills/wps/scripts/test/` 均以宿主确认的仓库根目录为基准，仅供源码维护，不随运行包分发；运行任务不读取或执行。没有仓库时跳过维护资料，不按 Skill 安装目录拼接。

显式 include-related 时，主 text/paragraphs 仍只包含正文，附加区域置于 related_stories，不再拼接标签和部件文字。附加内容不拼接主正文；显式 standard 未请求附加内容且发现引用时以 partial 和 related_content_not_requested 明确告知未读取范围。source 保留部件、定义 ID、关系 ID；references 保留正文 Word 元素序号、引用种类和 ID。元素序号不是字符 CP 或页码，页码/CP 映射均未知。

页眉页脚由正文 sectPr 中 headerReference/footerReference 的 r:id 定位实际主部件所属的关系文件，检查内部关系类型和部件内容类型/根元素。按显式分节引用提取，不计算前节继承、奇偶页或首页显示。相同部件的同一关系可供多节引用；不同关系别名指向同一部件当前明确 unsupported。合法自定义部件名按关系支持，不按 header1.xml 等文件名枚举。

脚注、尾注和批注由正文可见修订视图中的引用 ID 及对应唯一关系定位，验证定义 ID 唯一和范围，再仅提取被引用定义。空定义成功空文字；独立部件缺失、XML 损坏、错误类型、缺失或重复定义返回该区域 partial/text=null，并保留正文和其他可读部件。缺关系、重复关系 ID、主文引用非法等未分类结构故障仍拒绝。批注范围的起止字符未映射，不声称恢复批注范围。特殊脚注分隔符不充当普通注释。

未关联 ZIP 成员不作为附加文字解析，仍经过原有成员长度与 CRC 检查。外部关系不访问；目标拒绝 URI scheme/query/fragment/编码转义及越出包根。关联内的未知内容沿用 partial 诊断；嵌套引用不递归追踪，明确 partial。standard 模式不新增文本框、图形、布局、分页、字段执行和跨修订语义。

`--reading-mode maximum` 另从已经通过相同 ZIP/XML 检查的正文及已选关联区域中定位外层 `w:txbxContent`，将其存储段落和有限表格作为 `related_stories[type=textboxes]` 返回。它不解释 DrawingML/VML 锚点，不选择 Markup Compatibility Choice/Fallback，不推断页面或主文顺序；多个未选择分支中的相同文本可分别返回。每个 story 固定为 partial，`anchor_status=stored_textbox_subtree_only`，不能并入正文后声称阅读顺序准确。

引用数100000、区域/定义4096，文本框 story 4096、文本框文字累计 8 Mi UTF-16 码元；原有 ZIP/XML 累计预算和最终输出限额保持。所有关联 XML 使用同一个累计 XML budget，不按部件重置。无需写文件或网络。

公开依据见ooxml-related-evidence.json (`docs/maintenance/wps/ooxml-related-evidence.json`)，开发时核对，运行时不访问。测试均为标准库合成的固定预期，覆盖自定义部件名、未关联成员、三类 ID、修订、空区域、缺失/重复/越界、外部目标及配额；目前没有真实 WPS 关联文字的独立语义对照。

主文按包根 officeDocument 关系与唯一受支持内容类型定位，支持合法自定义主部件路径。所有相对关联目标相对实际主部件解析。数学公式单独报告 math_content_not_decoded，未知块内文字报告 unknown_block_content_not_decoded；不把数字拼接冒充公式。独立区域恢复采用明确错误白名单，DTD、实体、资源和共享包安全检查仍失败。


## 原生图表与兼容分支

content/maximum 在正文及已请求附加区域中追踪原生 `c:chart` 关系，返回 `ooxml_charts`，不按文件名扫描孤立图表。`charts[].title` 与 `series[]` 保留富文本片段、系列名、分类/数值/XY/气泡缓存、原始字符串、格式代码、点索引、公式来源及缺值范围；公式仅为来源，不执行。覆盖不全、缺失部分、损坏局部 XML、未知格式和多级分类仍为 partial；不补零、不反算、不读取嵌入工作簿或外链，不把未刷新的缓存当作已验证当前视觉内容，图片图表不做 OCR。显式 standard 不增加图表工作。

每次读取最多128个图表引用、50000个缓存点、10000个系列/缓存记录及2 Mi字符图表文本；同一图表多次引用仍累计预算，保持独立对象。新读取与现有 Word 部件共享 XML 预算；ZIP、DTD/实体、外部关系、越界、安全及资源错误仍拒绝，不降级为局部 partial。

MC Choice 的 Requires 前缀在各自 XML 作用域解析，选择第一个全部命名空间受支持的 Choice，否则 Fallback。当前支持 Word、VML、Word 2010 wps/wpg 文本框包装及经典 chart URI；不靠前缀拼写猜测扩展，c14/c15 等不冒充支持。不选分支不贡献对象或诊断。未知且无 Fallback 或分支结构歧义保留存储分支来源、标 partial 与 `current_view_available=false`。这是读取器支持视图，不证明客户端版式；独立相同文本对象不会被合并。`ooxml_compatibility` 报告选择数及未知数，`related_stories[].source.alternate_content` 只描述未解决分支。
