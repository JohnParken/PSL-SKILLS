# 有限数值解释

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

原始Decimal数值、XF/格式ID、自定义格式与日期系统始终保留；每个数字单元格新增numeric_interpretation，公式缓存额外注明原cache_status，绝不重算。该字段不是Excel完整显示渲染。BIFF分支仅在cell XF的fAtrNum明确置位且非style XF时依据直接XF格式；其余返回style_inheritance_unresolved，尚不解析BIFF父样式。OOXML分支可按有界两层来源 (`<skill-root>/references/style-inheritance.md`)解释确定的数字格式；复杂格式仍返回unknown及原因，不把unknown误称数据丢失。

支持General、固定小数、千分位、末尾逗号缩放、单一百分号倍率、明确货币符号和零占位编号。金额与百分比使用Decimal和明确half_up量化，保留解释前后倍率；不由符号猜货币代码，不将数字编号转成真实文本类型。有界常量条件另见规则 (`<skill-root>/references/conditional-number-formats.md`)，位置多段见会计格式；区域前缀仅元数据unknown，颜色、分数、指数与可选小数等未实现语法返回unknown。

日期限明确的格式白名单和内置ID映射；1900序列60返回fictional_date及valid_gregorian_date=false，绝不伪造合法ISO日期。1904从1904-01-01开始。负日期/时间拒绝解释；时间与[h]时长分开，普通时间保留whole_days与time_wraps_days提示。秒用Decimal四舍五入并处理进位。日期标准化为ISO式解释值，不声称保留本地显示格式。未映射的区域化内置日期ID返回unknown。

格式≤255字符，小数≤12位，缩放逗号≤3；数值精度与指数受解释预算限制，超出只标unknown、不影响原始读取。当前只有标准库合成预期验证，尚无与Office显示或同源独立预览逐项对照。运行时不联网、不调用办公软件或转换。

公开格式来源见numeric-evidence.json (`<skill-root>/references/numeric-evidence.json`)。日期only忽略小数部分，不因秒四舍五入跨日。

有限2–4分段会计格式另见会计边界 (`<skill-root>/references/accounting-formats.md`)；OOXML数字格式来源解析见两层样式链接 (`<skill-root>/references/style-inheritance.md`)。原值始终不变，不能把解释值代回原数据分析。
