# Strict Word 限定读取

按根 officeDocument 关系选定 Strict 或 Transitional 家族，逐个已选 Word XML 部件及关系验证不得混用两家族。Strict WordprocessingML 和 officeDocument relationships 的展开名称，仅在验证后于内存树规范化以复用正文、修订、关联故事、表格及隐藏策略；不修改原文件或转换文件。OPC package relationships/content-types 命名空间及公开 Word MIME 类型保持不变。

主部件仍限定 word/document.xml；不支持任意主部件布局或完整 Strict 规范认证。被遗漏的 foreign/MC/绘图内容继续明确 partial，不将命名空间支持称为全特性支持。结果 ooxml_conformance 记录 strict/transitional。拒绝混合 Word 元素/属性命名空间、混合关系 Type 家族及既有外部目标/无效引用。

test_wps_strict_word.py 为标准库合成样本：正文/表格/隐藏/修订、自定义关联部件及混合家族拒绝；无真实 WPS 来源或独立显示验收。公开名称依据 ISO/IEC 29500 Strict 的 purl.oclc.org/ooxml/wordprocessingml/main 与 purl.oclc.org/ooxml/officeDocument/relationships。运行时不联网。
