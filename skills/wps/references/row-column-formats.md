# 有限行列数字格式来源

OOXML 原始 cell XF、行 `s/customFormat` 和列 `style` 始终保留。无显式单元格 `s` 时，仅一个有效来源（启用 customFormat 的行，或一段列）且目标 cell XF 明确 applyNumberFormat=true，或与有效父样式的保存数字格式一致并通过[两层来源校验](style-inheritance.md)，才用已有数字格式白名单解释。解释附带 style_source，原始单元格索引、数字及格式字段不改写。

同时存在行列来源、无效/缺失样式、未明确启用的行格式或父样式缺失/冲突时继续 unknown/partial，不猜优先级。复杂格式仍 unknown；不计算条件格式、主题、完整样式继承或页面显示。已有五份真实样本没有此 OOXML 样式来源的独立显示对照；新增测试仅合成。

公开依据为 Microsoft Open XML Row/Column/Cell 类所列 ISO/IEC 29500 属性语义，链接见 row-column-formats-evidence.json；只供开发核对，运行时不联网。
