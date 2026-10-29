# Word OOXML 存储文字可见性

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

`--hidden-text include|exclude|annotate` 默认 include 保持文本兼容。可见性不是授权或隐私控制。exclude 仅排除可靠判断为隐藏的 run；未知状态保留并 partial。正文、关联故事、表格共用预处理，诊断仅来源/字符数量，不附带被排除全文。

直接 vanish/webHidden 支持显式布尔值与缺省 true；vanish true 可确定一般隐藏；webHidden 仅影响 Web 视图，本读取器不选择页面视图，因此仅 webHidden true 返回 unknown、保留文字并 partial，不把它当作确定隐藏。样式从正文内部 styles 关系读取并验证内容类型/root，支持有界 basedOn 链及默认段落/字符样式。继承中的 true toggle 尚未完整解释，保守 unknown；没有恢复页面显示或条件表样式。循环、重复及超限明确失败。未知不表示可见。

已有 accepted/original/all 继续处理 insert/delete/move；属性变更只诊断 partial，不应用历史属性。字段指令不执行，存储结果也不能证明显示结果新鲜。二进制默认include不解析额外结构；annotate可读有界BTE/CHPX原始属性证据 (`<skill-root>/references/ms-doc-charprops.md`)，但样式/PAPX/PRM未应用，有效状态仍unknown，不过滤文字；exclude仍拒绝。

当前验证主要为合成测试，无真实隐藏文字独立显示对照。边界以代码和验证报告为准。
