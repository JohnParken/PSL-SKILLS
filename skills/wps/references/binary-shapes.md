# 二进制 MS-DOC 的绘图对象 U+0008 锚点（只读）

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 能力范围

实际文件内容识别为 CFB+MS-DOC 时，统一入口 data 默认启用 recover 和对象占位。strict/legacy 兼容或显式 text_policy=strict 保留原文字策略。对象占位时，按 `FibRgFcLcb97.fcPlcSpaMom/lcbPlcSpaMom` 验证主正文中的 `U+0008` 锚点和 `PlcfSpa` 来源，并在可输出的位置用 `U+FFFC` 表示对象。返回 `partial`，保留源流偏移、原始 CP、形状索引与 SPA 偏移。文字策略见 `<skill-root>/references/format.md#fib与正文规则`。

`PlcfSpa` 布局为 `(n+1)*4+n*26` 字节，n 是该故事的形状数。各真实锚点 CP 必须递增、位于其故事内且对应存储字符为 `U+0008`。最后的 CP 是未定义的终止值，只验证它大于上一项。读取检查 Table Stream 区间、已解释结构之间的重叠及形状数上限。每个形状表最多 1000 条，已处理的图片及形状标记共用现有诊断预算。

在 `--reading-mode maximum` 下也读取页眉页脚故事的 `fcPlcSpaHdr`，将其局部 CP 转换为全局 CP。有可用记录时，`drawn_shape_anchor_not_rendered.locations` 还带 `shape_id` 和 `stored_rectangle_twips`。矩形数值相对于记录中的原点，原点代码随结果返回，实际页面和渲染坐标仍未知。形状编号可用于文本框范围匹配，见 `<skill-root>/references/ms-doc-stories.md#最大模式文本框`。

## 最大模式绘图说明

`drawing_objects.objects` 按正文、页眉页脚及各自的存储锚点顺序列出已验证并输出占位的对象。每项保留 `story` 与 `anchor`，含全局 UTF-16 CP、源偏移及可用的存储矩形。只有同一故事的 PlcfSpa 和 OfficeArt 均有唯一形状编号时，才添加该对象的 `officeart` 记录。已删除形状、绘图根组和字段指令内的锚点不返回说明文字。

- `officeart.source` 指向 Table Stream 中的 FSP 记录。若保存了预设类型，输出 `shape_type_code`，已识别的常用类型另有 `shape_type`。无预设类型的组合记录可返回 `shape_type=group`。组合、嵌入对象和连接线标志来自存储值。
- 主 FOPT 支持保存的名称 `name`、说明 `description` 和艺术字 `wordart_text`。`text_sources` 指向相应 UTF-16LE 数据；字段无效时报告 `text_status=invalid_or_unsupported` 并保留形状记录。普通文本框文字仍可从 `related_stories` 按锚点关联。
- `anchor_count` 与 `officeart_matched_count` 分别报告已输出的锚点和匹配数。缺少绘图区或唯一匹配时仍可查看锚点。结构位于有效独占流区内但绘图记录损坏时保留正文，并记录 `officeart_metadata_invalid`。流区越界、与其他已解析结构重叠或总预算超限时读取失败。

绘图元数据随完整 JSON 返回。读取评估摘要只保留诊断和位置。图形路径和最终版式仍未知，绘图使文档保持 partial。

## 存储 PNG

MS-DOC 最大模式在 `binary_images.images` 返回可验证的 PNG。浮动图片依据唯一形状及其 BStore 索引定位内嵌或延迟存储的 BLIP，组内图片保留组合路径。行内图片仅在输出的 U+0001 占位符有单字符直接 CHPX 及有效 Data/PICF 引用时读取。读取校验记录范围、PNG 分块、校验和及尺寸。字段指令、外链、非 PNG 和 OLE 对象不在此范围。

- 每份图片按 PNG 字节的 `sha256` 去重。`width`、`height`、`byte_count` 和 `source` 描述原始载荷，`references` 保留每处的 `kind`、故事、全局 UTF-16 `anchor_cp`、原始记录来源及适用的 `shape_path`。
- 需要字节时，在任务目录调用 `python -I -S -B "<skill-root>/scripts/read_wps.py" ./input.wps --reading-mode maximum --include-image-bytes`。返回 `data_base64` 和 `bytes_status=included`。默认 `bytes_status=not_requested`。超过 4 MiB 总原始字节配额的图片保留元数据，标为 `omitted_budget`。`images_omitted_by_byte_budget` 与诊断给出遗漏量。整个 JSON 仍受 16 MiB 上限约束。
- `reference_count`、`unresolved_reference_count` 统计已检查的链接。`binary_images.status=extracted` 仅描述已检查链接和当前字节请求。已识别的坏记录或不支持的记录保留 `binary_image_record_unread`，关联不明时保留占位。无该字段不表示不存在图片。
- 行内 PNG 可用时，诊断位置含 `stored_image_status=png_available`。所有对应位置均已读取时，返回 `inline_png_placeholder_not_rendered`。正文仍使用占位。PNG 字节读取不涉及像素解压、图形渲染或 OCR。

## 存储图表缓存

在任务目录使用 `python -I -S -B "<skill-root>/scripts/read_wps.py" ./input.wps --reading-mode maximum`。此路径适用于内容为 MS-DOC 的输入，解析图表兼容记录需 Expat >= 2.6。浮动图表使用正文或页眉页脚的已确认 U+0008 锚点及唯一形状路径。行内图表使用正文或已读取故事的 U+0001、直接 CHPX 和内嵌 PICF，图表缓存可独立于预览 PNG 读取。仅解析两条路径的直接形状三级 FOPT 中的 `metroBlob`（0x03A9），沿 OPC 的 Graphic Frame 与内部 Chart 关系验证部件类型。此路径不扫描流中的 ZIP 签名。

- `binary_charts.charts[].references` 指出正文故事、UTF-16 `anchor_cp`、`kind`（`floating` 或 `inline`）和形状编号。级联组合另带 `shape_path`，行内另带字符属性和 PICF 来源。`source` 给出二进制流及偏移，`graphic_frame_part` 和 `chart_part` 给出包内来源。
- 每个系列保留图表类型、存储 `index`、`order`、可用的系列名，以及 `categories`、`values` 两组点。用各自的 `points[].index` 对齐分类和值，数值以原始字符串返回，保留小数精度。支持经典饼图、环形图、柱状图、折线图和面积图等分类/值结构中的字符串、数值缓存及直接存储点。
- `complete` 表示该组数据的声明点均可读。`partial` 保留可核实的点和缺口。`unavailable` 表示该组数据无法读取。`missing_ranges` 是含两端的零基索引区间，`null` 表示总范围未知。没有数值缓存时报告 `cache_missing`，缺值保持缺失。
- `binary_charts.status=stored_data` 仅针对已解析的图表缓存范围。损坏或歧义的包显示 `not_decoded`，有其他可用图表时保留并返回 `partial`。缺少该字段表示当前路径未产出已识别记录，纯图片中的图表仍可能存在。顶层正文状态保留对象占位及其他诊断。
- `workbook_status` 记录无引用、外链未访问、内部工作簿补读或未解析状态。外链不访问，符合下述限定的内部 XLSX 可补读保存值，公式不返回或重算。保存值的新鲜度、纯图片图表、OLE 图表、图形渲染、新式及多层分类图表数据仍不在此能力范围。
- 单包 2 MiB、所有图表包共 16 MiB，最多检查 64 个不同来源候选包，单包最多 128 个成员。被读取的 XML 单个最多 1 MiB，共 8 MiB。图表系列与点分别限制为 256 和 25,000 个候选，文本字段另有限额。仅候选数达到64时停止可选图表检查，保留正文与已读结果，标 partial 并报告未检查候选/引用数及非连续来源范围；不得把未检查候选当成图表或断言不存在图表。字节、解压、XML、点数等其他资源门禁仍明确失败。同来源冲突载荷继续拒绝。该路径在内存读取，用既有 JSON 通道输出。

格式依据为 MS-ODRAW 第 2.3.4.41 节的 metroBlob、MS-OI29500 的 Graphic Frame Doc，以及 ISO/IEC 29500-1 的 DrawingML 数值缓存。

## 不支持和诊断

- `drawn_shape_anchor_not_rendered` 记录已验证的存储锚点。`placeholder_emitted` 表明该位置是否输出占位。
- 默认模式的行内图片或数据标记 U+0001 继续占位。有效字符样式和页面显示仍未知。
- 显式模式 `reject`仍拒绝`U+0008`；strict文字策略下的placeholder拒绝无锚点和未识别控制符，recover仅将缺锚点标记作为未知内容占位，不给shape_index或SPA证据。两种策略均拒绝非法/重叠表结构、超限形状及损坏的正文Piece Table；没有通用容错扫描或自动尝试其他模式。
- 显式使用 `--binary-paragraph-sources` 时，如果对象占位实际修改正文，沿用 `paragraph_source_status=unavailable_after_text_transform`，不保留伪精确的段落 CP 范围。

官方依据：Microsoft MS-DOC `PlcfSpa`、`FibRgFcLcb97` 和 `Character Properties`，维护记录以固定官网样本的哈希和 GitHub Actions 结果为准。历史严格文字策略下，真实中山市政府“佐证材料.wps”默认拒绝；显式占位后返回主正文 **36 段、partial**。这仍不是 WPS Office 实际图形显示的语义验收，也不证明原件的创建软件版本。

## 对照的 CFB 风险

农行广西南宁分行 CFB `.doc` 长度不对齐 512 字节扇区，实际余数 51，且检查对齐前缀时 FAT 仍宣告越界分配。已补充明确的 CFB 诊断原因码：`cfb_unaligned_physical_length` 及 `cfb_fat_unbacked_allocation`。这属于拒绝原因的**定位修复**，没有提供可以安全读取的兼容策略。不得对原件自动裁剪、填零或放宽 FAT 约束；需要先取得可靠生产/软件验证及更完整的 FAT/链证据。

## 图表可读结果

`binary_charts.charts[].presentation` 是原结果的派生展示，不替代原 `series` 与 partial。按点的显式索引对齐分类和值；缺少分类或数值在行级 `missing_fields` 标出，未存储点的缺失区间原样保留，不展开巨大空区间。`raw_value` 保留数值字符串精度，`display_value` 仅支持 `0`、`0.0` 到八位小数及对应百分号格式；使用十进制四舍五入，不做占比归一化。其他格式回退原值并标 `format_not_rendered`，不是完整 Excel 格式引擎。

`values_refreshed=false` 表示只读文件保存值；`visual_series_verified=false` 表示存储顺序不证明最终显示顺序。分类可读但值缺失时仍展示分类，不把缺值补零、不从其他百分比反算、不执行公式。仅有内部引用不代表已有可用缓存；外部关联路径不输出、不访问。展示重复文本计入原图表共享输出预算。

## 内嵌工作簿补读

仅在图表合法 `internal package` 关系指向真实 XLSX 类型时，按包根关系定位 workbook 和 worksheet；不猜目录、不扫描 ZIP 签名、不调用其他 Skill。仅单工作表 A1 单格、单行或单列范围，拒绝函数、名称、多区域、三维、外部引用及旧 XLS/宏格式。读取已存数值、字符串、共享字符串、布尔值；数值系列只补数值。公式单元格仅取已有保存值并标 `formula_cached`，不执行或保证新鲜度；日期/工作簿格式不应用。

只补没有缓存点的位置，不覆盖已有点。原通道 `status/issues/missing_ranges` 仍描述图表缓存，`workbook_enrichment` 和 `effective_missing_indices` 单独报告补读覆盖；新增点的 `source.kind=embedded_workbook`，保留单元格位置及类型。展示的 `cache_missing_fields` 与补读后 `missing_fields` 分开。坏包或不支持引用保留可读图表并说明缺口，安全资源超限仍失败。不能把缺单元格当零，不用补读把原 partial 伪装成全格式成功。

内部工作簿单个2 MiB、累计16 MiB；展开单个8 MiB、累计32 MiB，单包128成员。累计单元格100000、共享字符串25000；单引用范围25000点，工作表128个；还受现有共享 XML 字节/节点和图表点/文本预算限制。全部在内存完成，不写临时工作簿。`embedded_stored_values` 仅表示此补读子集完成；`embedded_partial`、`embedded_unresolved` 和 `embedded_format_unsupported` 明确局限，未读其他工作簿功能。
