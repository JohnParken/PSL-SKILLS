# 电子表格格式、路由与精确边界

路径占位符说明：`<skill-root>` 是 ET Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 ET 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 目录

- 能力与证据矩阵 (`<skill-root>/references/format.md#能力与证据矩阵`)
- 兼容策略和来源 (`<skill-root>/references/format.md#兼容策略和来源`)
- 预算与输出 (`<skill-root>/references/format.md#预算与输出`)
- 数字解释与保存可见性 (`<skill-root>/references/format.md#数字解释与保存可见性`)
- 更广OPC容器子集 (`<skill-root>/references/format.md#更广opc容器子集`)
- 统一读取可信度 (`<skill-root>/references/format.md#统一读取可信度`)
- 会计与条件数字格式 (`<skill-root>/references/format.md#会计与条件数字格式`)
- CFB兼容头与容器检查 (`<skill-root>/references/format.md#cfb兼容头与容器检查`)

## 能力与证据矩阵

| 实际结构 | 当前结果范围 | 证据与缺口 |
|---|---|---|
| BIFF8 plain SST/String Continue | typed cells/长字符串/日期元数据/缓存 | 四个既有真实ET/ETT及合成；不重算缓存；未知记录保留原始字节并 partial |
| BIFF8 展示文字预算 | 值文字 8Mi 与格式/批注/链接展示文字 2Mi 分开；格式按 ID 去重，展示超限省略展示字段并继续读值 | 合成 32,897 格重复格式回归；未用真实超大格式原件验收 |
| BIFF8 plain Label及Continue | 内联单元格文字，保留来源与类型；RString（0x00D6）返回纯文字与有界 run 证据 | 合成回归；普通Unicode兼容 (`<skill-root>/references/biff8-readonly.md#pure-table-unicode-compatibility`)忽略保留位并报告partial；不支持实际富格式/注音 Label 尾布局，不按GBK猜解码 |
| BIFF8 与 OOXML 保存视图 | 窗口视图中的冻结行列与活动窗格的单元格 | 支持两种格式的合成样本，不参与表头推断 |
| BIFF8 调色板、独立窗格及分页元数据 | 保留 Palette、独立 Pane 及横纵分页原始字节 | 校验记录边界，不应用布局；异常布局降级为 issue |
| BIFF8 DXF（0x088D） | FrtHeader及XFProps/属性边界校验、原始属性保留，partial | 不应用差异格式；新增仅合成，无反馈原件验收 |
| BIFF8 rich SST及Continue | 文字+原始格式run信息，partial | 仅合成；字符编码与run续段分开，不应用字体布局；phonetic ExtRst 另保留原始字节且未解码 |
| BIFF8共享/数组公式 | 各单元格保存的缓存+组范围/锚点，partial；真实 WPS 的 cUse=0 作为未指定变体保留并诊断 | 合成回归 + 1 份宁夏公开 ET 真实变体；不恢复表达式、不重算，不将锚点缓存广播；组语义/使用次数未认证 |
| BIFF8 零尾缀/非标准容器头 | 有效扇区前缀与保存值照常读取，尾缀/头差异记 issue | 2 份真实 Excel-97 零尾 BIFF8 原件已 partial 可读；其余生产者未验证 |
| BIFF8 已知非值元数据损坏 | 合并、公式组、条件格式/验证、HLink、Note 等损坏保留 issue 与原始证据，保存单元格仍读取；不单独设置值遗漏，值覆盖可为完整 | 合成元数据故障回归；未知记录/未知 OfficeArt/值承载记录仍保守标记不确定 |
| BIFF8 Note 批注 | `sheets[].comments` 返回已确认链的单元格、作者与纯文字；原始记录与 `note_drawings` 证据保留 | 仅合成 note 链回归；不渲染气泡，显示属性与完整格式未验证 |
| BIFF8 HLink/HLinkTooltip 超链接 | `sheets[].hyperlinks` 返回 ref、display、target、location、tooltip 等只读元数据，目标不解析/不跟随；OLE moniker 保留有界原始证据 | 仅合成 BIFF8 回归；无真实 WPS 超链接原件对照 |
| OOXML电子表格及ISO日期类型d | Strict/Transitional保存值、缓存、合并、原始日期字符串 | 仅合成及 5 份真实 xlsx 的读取回归；日期不转serial，复杂样式unknown，扩展关系不读取 |
| OOXML `_xHHHH_` 保存文本 | 共享/内联/富文本/单元格文本单次解码，`_x005F_` 字面保护，代理对合并；保留 `text_encoding`/`shared_string_encoding` 原始编码证据 | 合成 XML 与独立 XlsxWriter 3.2.9 生成样本复现；无真实 WPS 原生 OOXML 语义对照 |
| 单元格保留文字预算 | 共享、内联、富文本和 `t="str"` 统一按 Unicode 代码点计费；超限保留前缀、标记 `text_truncated`/`text_limit_reached` 并使覆盖不确定；`text_budget` 报告上限和实际保留数 | 合成限额回归；未用真实 8Mi 边界原件验收 |
| BIFF8 声明工作表覆盖 | `sheet_coverage`/`read_status` 区分 read/partial/not_read/not_applicable；偏移不匹配、重叠、BOF 无效不再报告完整覆盖 | 合成故障映射回归；未覆盖所有私有 WPS 子流变体 |
| 其他私有ET格式 | unsupported | 不猜测、不自动转换或软件回退 |

后缀不是格式证据。主入口按签名、容器、内部负载分流；存在多个 Workbook/Book 根流时选择首条通过 BIFF8 globals BOF 校验的候选并记 issue，不再要求唯一。

## 兼容策略和来源

不再提供 strict/legacy 读取策略：所有公开入口默认内容优先容错。CFB 头 CLSID 为零、已观察的 Excel WorksheetClass GUID、其他非零 GUID、任意 minor version、非零保留字节或 v4 填充均不阻止读取，改为记录 `nonstandard_header_clsid`、`cfb_nonstandard_minor_version`、`cfb_nonzero_reserved_header`、`cfb_nonzero_header_padding` 等 issue；major/sector、FAT/MiniFAT 分配链及加密检查不变。旧 `--compatibility` 参数仅作为 no-op 接受并附 `compatibility_option_ignored`。历史 strict/legacy 语义只保留在历史兼容说明 (`<skill-root>/references/biff8-readonly.md#observed-legacy-compatibility`) 中，不代表当前行为。

## 预算与输出

统一输入 32MiB，非阻塞普通文件打开，fstat 预算后读取并复核变化。CFB 用户流 32MiB、聚合流 32MiB、目录 4096、深度 32；超预算按已校验前缀截断或拒绝并附 issue，FAT/MiniFAT 链循环、交叉引用与越界仍拒绝。

OOXML 解压前检查目录/成员/命名冲突，部件 32MiB、累计 128MiB、成员 4096；XML 每部件 16MiB、深度 128，并累计元素/属性 200 万、节点 800 万、扩展名分配 128MiB 预算，拒绝 DTD/实体。必须 Expat >= 2.6；所有 ZIP 成员有界长度及 CRC 检查，不解包到磁盘；不读取与单元格无关的部件，只列名并记 issue。

BIFF8 单元格值文字上限 8Mi UTF-16 单位；自定义格式、批注和链接等展示文字另有 2Mi 单位预算。格式文本按 number-format id 去重计费；展示预算超限时省略受影响 `custom_format_raw`/顶层格式文本并附 issue，保存值继续返回。`output_text_budget` 报告两套预算的实际用量、上限与是否受限。OOXML 也用独立展示预算按 `numFmtId` 去重，超限时省略自定义格式展示字段而保留值。

BIFF 记录 100000、单元格 200000、工作表 1024、合并 65536、共享字符串 200000、XF 4096、原始元数据 8MiB/65536 条、结构化 Row 65536、结构化字节 8MiB、诊断 1000 条（超出记 `issues_truncated`）、重复单元格文字及格式文本累计 8Mi UTF-16 单位；公式组 1024、图表 128、Continue 链 256。日期保留原始数值及 date_system/XF/format，另有直接格式白名单 numeric_interpretation；复杂格式 unknown。公式 tokens 及已有缓存分开保留，未重算且可能过时；共享/数组支持与拒绝条件统一见公式组边界 (`<skill-root>/references/formula-groups.md`)，字符串续段见富文本续段 (`<skill-root>/references/biff-rich-continuation.md`)。其他记录范围详见 BIFF 参考 (`<skill-root>/references/biff8-readonly.md`)。

完整 JSON 默认上限 64MiB；`--max-json-bytes` 接受 1MiB 到 256MiB，超预算时先自动精简一次（去重原始字节证据，保存值/类型/缓存/合并/诊断保留）再决定成功或明确失败。`--compact` 主动选择精简输出；`--output <裸文件名>` 将完整 JSON 流式写入任务根目录的新文件，stdout 仅返回 `et_output_manifest_v1`（文件名、字节数、SHA-256、状态），从不输出截断 JSON。读取视图默认 `max-output-chars=65536`（512 到 2097152），`--max-output-bytes` 接受 512 到 64MiB。

## 数字解释与保存可见性

numeric_interpretation (`<skill-root>/references/numeric-interpretation.md`) 为附加解释，不改变 value/type/cache_status；Decimal 金额/百分比、千分/缩放/编号和白名单日期时间均有边界，非完整渲染。公式缓存不重算。

保存可见性 (`<skill-root>/references/saved-visibility.md`) 解释 Row/ColInfo 隐藏标志；未知行状态保留 unknown，隐藏原因不区分手动或筛选。筛选记录保留原字节、条件未解释且未执行，partial。默认全部数据；显式 saved_visible 选择也保留未知行。OOXML 电子表格使用独立 schema 和同样的保守可见性约定；复杂样式继承不猜测。

## 更广OPC容器子集

支持 ISO/IEC 29500 Strict 与 ECMA-376 Transitional 标准电子表格包；部件读取遵循安全预检。

## 统一读取可信度

读取结果按实际支持范围提供置信度评估，见 结果契约 (`<skill-root>/references/reading-assessment.md`)。

## 会计与条件数字格式

支持标准会计格式与条件数字格式的离线识别，详见 会计格式 (`<skill-root>/references/accounting-formats.md`) 与 条件数字格式 (`<skill-root>/references/conditional-number-formats.md`)。

## CFB兼容头与容器检查

CFB 容器解析器 `wps_cfb.py` 校验扇区链、FAT 分配表、MiniFAT 与目录结构，防止死循环和越界内存访问；头部 CLSID/minor/保留字节、未引用扇区、不可达目录项、非法流名及尾缀差异改为诊断，不阻止读取可定位的 Workbook 流。加密包和无法安全建立的基础分配表仍拒绝；流链冲突停止该链并保留已验证前缀和覆盖风险。
