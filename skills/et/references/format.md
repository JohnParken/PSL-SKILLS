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
| BIFF8 plain SST/String Continue | typed cells/长字符串/日期元数据/缓存 | 四个既有真实ET/ETT及合成；不重算缓存 |
| BIFF8 plain Label及Continue | 内联单元格文字，保留来源与类型 | 新增布局仅合成；data普通Unicode兼容 (`<skill-root>/references/biff8-readonly.md#pure-table-unicode-compatibility`)忽略保留位并报告partial；不支持RString或实际富格式/注音尾布局，不按GBK猜解码 |
| BIFF8 与 OOXML 保存视图 | 窗口视图中的冻结行列与活动窗格的单元格 | 支持两种格式的合成样本，不参与表头推断 |
| BIFF8 调色板、独立窗格及分页元数据 | data 默认或显式 legacy_excel_clsid 保留 Palette、独立 Pane 及横纵分页原始字节 | 校验记录边界，不应用布局；显式 strict 仍拒绝这些独立记录 |
| BIFF8 DXF（0x088D） | FrtHeader及XFProps/属性边界校验、原始属性保留，partial | data/legacy支持；不应用差异格式；新增仅合成，无反馈原件验收 |
| BIFF8 rich SST及Continue | 文字+原始格式run信息，partial | 仅合成；字符编码与run续段分开，不应用字体布局；phonetic ExtRst 另保留原始字节且未解码 |
| BIFF8共享/数组公式 | 各单元格保存的缓存+组范围/锚点，partial | 仅合成；不恢复表达式、不重算，不将锚点缓存广播 |
| OOXML电子表格及ISO日期类型d | Strict/Transitional保存值、缓存、合并、原始日期字符串 | 仅合成；日期不转serial，复杂样式unknown，关联部件仍有限 |
| 其他私有ET格式 | unsupported | 不猜测、不自动转换或软件回退 |

后缀不是格式证据。主入口按签名、容器、内部负载分流；必须存在且仅存在唯一的 Workbook/Book 载荷。

## 兼容策略和来源

显式 strict 要求 CFB 零头 CLSID 和 minor 0x003E；data 及显式 legacy 额外接受已观察的 minor 0x003B 并报告 partial，其他 minor 仍拒绝。MS-CFB 2.2 对 003E 使用 SHOULD；该兼容不改变 major/sector 及分配验证。legacy_excel_clsid 用于 BIFF 工作簿，允许 v3 精确头字节 `2008020000000000c000000000000046` 及对应受限元数据，具体范围见 BIFF 兼容 (`<skill-root>/references/biff8-readonly.md#observed-legacy-compatibility`)。

## 预算与输出

统一输入 32MiB，非阻塞普通文件打开，fstat 预算后读取并复核变化。CFB 流 8MiB、总 32MiB、目录 4096、深度 32。
OOXML 解压前检查目录/成员/命名冲突，部件 32MiB、累计 128MiB、成员 4096；XML 每部件 16MiB、深度 128 并累计节点/属性/名字预算，拒绝 DTD/实体。必须 Expat >= 2.6；所有 ZIP 成员有界长度及 CRC 检查，不解包到磁盘。

BIFF 流/记录/单元格/字符串预算保持；重复单元格文字及格式文本累计 8Mi UTF-16 单位，JSON 在完整输出前以 iterencode 限制 16MiB。日期保留原始数值及 date_system/XF/format，另有直接格式白名单 numeric_interpretation；复杂格式 unknown。公式 tokens 及已有缓存分开保留，未重算且可能过时；共享/数组支持与拒绝条件统一见公式组边界 (`<skill-root>/references/formula-groups.md`)，字符串续段见富文本续段 (`<skill-root>/references/biff-rich-continuation.md`)。其他记录范围详见 BIFF 参考 (`<skill-root>/references/biff8-readonly.md`)。

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

CFB 容器解析器 `wps_cfb.py` 严格校验扇区链、FAT 分配表、MiniFAT 与目录结构，防止死循环和越界内存访问。
