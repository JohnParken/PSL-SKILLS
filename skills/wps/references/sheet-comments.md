# 工作表传统批注的有限读取

新增读取通过工作表 comments 关系连接的独立部件，验证关系家族、内容类型、根和子元素。输出每条批注的单元格、作者、纯文字、关系 ID 和部件路径。批注不并入单元格值，不执行或跟随任何内容；布局未解码时明确 partial。

支持 comments/authors/commentList/comment/text 与普通及富格式文字串。作者索引、单元格界限、重复批注位置、数量及累计文字预算验证后才保留。未知扩展、线程批注、批注关系、外部目标、其他绘图关系仍拒绝；不声称视觉气泡、显示坐标或完整批注格式支持。传统Transitional Note可按[有限VML锚点](vml-note-anchors.md)验证单元格与原始矩形；未知形状、Strict VML和活动内容仍拒绝，不删除部件来取得成功。

每部件最多4096作者/批注、1Mi字符，另受既有XML/总文字与最终JSON预算约束。空批注集显式返回空列表。新测试仅标准库合成包，未提供真实WPS批注独立内容对照。

官方开发依据见[sheet-comments-evidence.json](sheet-comments-evidence.json)，运行时不访问网络。
