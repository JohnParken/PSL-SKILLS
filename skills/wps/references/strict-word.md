# Strict Word 限定读取

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

按根 officeDocument 关系选定 Strict 或 Transitional 家族，逐个已选 Word XML 部件及关系验证不得混用两家族。Strict WordprocessingML 和 officeDocument relationships 的展开名称，仅在验证后于内存树规范化以复用正文、修订、关联故事、表格及隐藏策略；不修改原文件或转换文件。OPC package relationships/content-types 命名空间及公开 Word MIME 类型保持不变。

主部件仍限定 word/document.xml；不支持任意主部件布局或完整 Strict 规范认证。被遗漏的 foreign/MC/绘图内容继续明确 partial，不将命名空间支持称为全特性支持。结果 ooxml_conformance 记录 strict/transitional。拒绝混合 Word 元素/属性命名空间、混合关系 Type 家族及既有外部目标/无效引用。

test_wps_strict_word.py 为标准库合成样本：正文/表格/隐藏/修订、自定义关联部件及混合家族拒绝；无真实 WPS 来源或独立显示验收。公开名称依据 ISO/IEC 29500 Strict 的 purl.oclc.org/ooxml/wordprocessingml/main 与 purl.oclc.org/ooxml/officeDocument/relationships。运行时不联网。
