# Word OOXML 存储文字可见性

`--hidden-text include|exclude|annotate` 默认 include 保持文本兼容。可见性不是授权或隐私控制。exclude 仅排除可靠判断为隐藏的 run；未知状态保留并 partial。正文、关联故事、表格共用预处理，诊断仅来源/字符数量，不附带被排除全文。

直接 vanish/webHidden 支持显式布尔值与缺省 true；vanish true 可确定一般隐藏；webHidden 仅影响 Web 视图，本读取器不选择页面视图，因此仅 webHidden true 返回 unknown、保留文字并 partial，不把它当作确定隐藏。样式从正文内部 styles 关系读取并验证内容类型/root，支持有界 basedOn 链及默认段落/字符样式。继承中的 true toggle 尚未完整解释，保守 unknown；没有恢复页面显示或条件表样式。循环、重复及超限明确失败。未知不表示可见。

已有 accepted/original/all 继续处理 insert/delete/move；属性变更只诊断 partial，不应用历史属性。字段指令不执行，存储结果也不能证明显示结果新鲜。二进制默认include不解析额外结构；annotate可读有界[BTE/CHPX原始属性证据](ms-doc-charprops.md)，但样式/PAPX/PRM未应用，有效状态仍unknown，不过滤文字；exclude仍拒绝。

当前验证主要为合成测试，无真实隐藏文字独立显示对照。边界以代码和验证报告为准。
