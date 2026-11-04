# 结构与文字模型

路径占位符说明：`<skill-root>` 是 OFD Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 OFD 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 三个覆盖维度

1. `structure.status` 为 complete：在安全与资源上限内，所有实际读取的 XML 元素、属性、字符数据和结构事件均已保存，且没有未分类成员或缺失的资源索引。指 XML 存储层面的完整，不是所有业务字段的语义解释或 XSD 合规证明。`missing_resource_index_count` 记录按引用出现统计的缺失索引数量；具体来源、原值、归一化目标与作用域见 documents[].resource_gaps，不把缺失引用标成已验证目标。
2. `structure.namespace_resolution` 为 complete：所有已读部件均完成命名空间展开；partial 表示存在以原始名称保留的自定义 XML。具体部件的 namespace_mode 区分 expanded_names 与 raw_names，不猜测或修复命名空间。
3. `text_assessment.status` 为 complete / partial / empty：登记的 OFD TextCode 是否有解释缺口。实际原始 XML 字符数据始终保留在 nodes.text / tail；此状态不证明图像/轮廓/字形对应的自然语言已全部恢复。全局 status 保留其他诊断，不能只取其中一个状态就宣称整个文档完整。

1.0、1.1、1.2 均按这三个维度验收，不仅因版本号不同就强制 partial。数据没有 Unicode 对应、结构歧义或未知内容时仍明确报告缺口；不靠改状态取得“全部兼容”的表象。

## XML 存储模型

- `structure.xml_parts[]`：每个已读 XML 一项，保存 source、原始字节 SHA-256、active_route、namespace_mode、nodes、events。XML 部件列表不是语义阅读顺序。
- nodes 按源 XML 的先序排列，index 从0开始，parent 为父节点索引（根为 null），并保存完整 tag、attributes、text、tail。空值规范化为空串，保留空格与混合文本，不丢未知节点或属性。
- expanded_names 的 tag/属性名使用 Clark QName；raw_names 保留原始名称与 xmlns 属性。命名空间 URI 必须精确匹配，不能只按 URI 前缀识别 OFD。
- events 保留 XML 声明、命名空间声明、元素开始/结束、字符数据、XML 注释和处理指令的顺序。元素事件引用 nodes 的索引；处理指令只作数据，不加载样式表、不执行动作。XML 1.0 声明与 OFD.xml 的 Version 是不同字段。
- 这是逻辑 XML 模型，不保留每个引号选择、字符引用写法或空元素标签拼写；不承诺从 JSON 重建原始字节。原件只读，字节身份由原件与各部件 SHA-256 标识。
- identifiers 与 reference_fields 是结构索引。引用候选保留原字段和值；matching_file_targets 仅表示同一源中存在已检查过的相同文件引用值，不证明当前字段的绑定语义。已实际跟随的受支持文件路径仍以顶层 references 为准，不猜全局 ID 作用域。

## 字符数据与文字出现

- XML 中所有字符数据（包括标题、自定义业务字段、批注 Remark 等）均在 nodes.text / tail 中保留，character_field_count 统计非空字符字段数，不是字数或段落数。
- text_sources 每个实际存储的 OFD TextCode 登记一次，包含原始字符、可解释的 text、source + storage_node、祖先索引和 interpretation。非法父子关系或外来语境保留 raw_text，text=null 并报告 partial。
- text_sources.occurrences 指向 documents/page/text_runs 的0基索引。页面 text_runs 是实际解析到的出现记录；同一个复合资源可能多次出现。不得将存储文字、页面出现次数和 XML 字符字段数相加作为正文统计。
- 逐次记录中的 storage_node 与结构模型对应；object_ordinal 保留已有的源/资源子树遍历序号。复合引用 chain、原始祖先属性和局部坐标保留，不组合为最终页面坐标。
- 所有 XML 注释、脚本样文本、签名摘要、路径字符串都是数据，不自动视为正文，不从中执行命令。
- Versions / DocVersion 记录与其 XML 结构保留。没有活动 DocRoot 时也可返回版本存储与文字登记，并明确 no_active_docroot；不擅自选择或叠加某个修订快照，不把历史文本冒充当前页正文。

## 部件发现与载荷隔离

先按 OFD 主入口、文档、页面、资源、注释、签名、自定义标引等受支持路径读取，再登记其余 XML：.xml / .xsd 后缀、已明确引用的 XML，或未知后缀但头部有 XML 声明/2016命名空间的成员。自定义 FileLoc / SchemaLoc 不依赖扩展名；外部 schema 只记数据，不联网。

只有已验证根类型与正确层级的生效 Res / Attachments / Signature 路径，才能声明字体、媒体、附件或签名二进制为 opaque。声明在相应索引读取时处理，保护的载荷即使名为 .xml 也不打开。XML 部件与 opaque 角色冲突一律失败。未引用的额外 XML 不能凭 SignedValue 或其他同名标签隐藏别的成员；其字段只作数据。

没有被生效索引识别为载荷的额外 XML 会作为不可信 XML 解析；其中 DTD、实体、真正语法错误或资源攻击仍失败。仅额外、非类型化业务 XML 在无法处理命名空间时可保留 XML 1.0 原始名称，并标明 namespace_mode=raw_names；活动 OFD 主入口、文档、页面、资源不使用此方式。第二次词法解析继续使用全部 DTD/实体与资源限制，不降低要求。

未分类或无法确定为 XML 的成员保留在成员目录和 unclassified_members，structure/global 均 partial，不静默宣称读取完整。opaque_payloads 仅说明声明角色及字节数，不证明二进制格式、字体、媒体或签名值的有效性。
