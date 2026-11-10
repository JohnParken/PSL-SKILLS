# 有限数值解释

维护资源说明：文中 `docs/maintenance/et/` 与 `skills/et/scripts/test/` 均以宿主确认的仓库根目录为基准，仅供源码维护，不随运行包分发；运行任务不读取或执行。没有仓库时跳过维护资料，不按 Skill 安装目录拼接。

路径占位符说明：`<skill-root>` 是 ET Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 ET 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

数字解释附在 `numeric_interpretation`。原始 Decimal、XF 和格式 ID、自定义格式、日期系统及公式缓存状态均随单元格保留。

BIFF8 单元格 XF 的 `fAtrNum` 置位时使用其直接格式。未置位时，核对 `ixfParent` 指向的独立 `StyleXF`，并要求父子保存的格式 ID 一致。继承结果以 `number_format_source` 记录父 XF 索引和格式 ID。父引用无效返回 `style_inheritance_unresolved`，格式 ID 冲突返回 `cell_parent_number_format_conflict`。OOXML 的两层格式来源见 `<skill-root>/references/style-inheritance.md`。

支持 General、固定小数、千分位、末尾逗号缩放、百分比、明确货币符号和零占位编号。单段固定数字允许引号包裹或转义的货币符号前缀，以及最多 32 个字符的引号后缀或一个反斜线转义字符。金额与百分比使用 Decimal 和 half-up 量化。货币符号作为显示信息保留，货币代码需要独立来源。

内置 11–13 支持标准科学计数 `0.00E+00` 和两种混合分数 `# ?/?`、`# ??/??`。科学计数支持一位整数的尾数、最多 12 位固定小数和 `E+`、`E-`。分数显示用分母不超过 9 或 99 的最接近分数，省略对齐空格。内置 37–40 支持常见负数括号格式。格式 ID 5–8 读取工作簿提供的格式文本，缺少定义时不猜测当地货币。支持的数字段可用颜色前缀，所选颜色以 `format_color_raw` 保留。

固定小数支持 `0.##`、`0.0##` 等从必需位到可选位的组合，最多 12 个小数占位符。常量条件见 `<skill-root>/references/conditional-number-formats.md`，位置多段见 `<skill-root>/references/accounting-formats.md`。区域指令和复杂格式仍按适用范围返回 `unknown`。显示文本仅用于预览，统计继续读取 `value`。

BIFF8 数字已确定为 `General`，且保存的 Decimal 可精确还原为 binary64 时，`display_text` 提供能往返该二进制值的简短十进制文本。例如保存值是 `0.1` 的精确二进制展开时，显示文本为 `0.1`。精确的 RK 缩放十进制值如果无法还原为 binary64，保留原有表示。`value` 和 `interpreted_value` 保持原精度，显示文本不参与统计。

日期限明确格式和内置 ID 映射。`yyyy"年"m"月"d"日"`、`m\/d\/yyyy` 及支持的补零形式同时附加 `display_text` 和 ISO 式解释值。1900 序列 60 保留 `fictional_date` 与 `valid_gregorian_date=false`，1904 从 1904-01-01 开始。负日期和时间拒绝解释；普通时间与 `[h]` 时长分开，秒按 Decimal 四舍五入并处理进位。未映射的区域内置日期 ID 返回 `unknown`。

格式≤255字符，小数≤12位，缩放逗号≤3。数值精度与指数受解释预算限制，超出只标 `unknown`。除合成验证外，已用固定公开样本复核斜杠日期显示文本。Office 客户端显示未对照。运行时不联网、不调用办公软件或转换。

公开格式来源见numeric-evidence.json (`docs/maintenance/et/numeric-evidence.json`)。日期only忽略小数部分，不因秒四舍五入跨日。

有限2–4分段会计格式另见会计边界 (`<skill-root>/references/accounting-formats.md`)；OOXML数字格式来源解析见两层样式链接 (`<skill-root>/references/style-inheritance.md`)。原值始终不变，不能把解释值代回原数据分析。
