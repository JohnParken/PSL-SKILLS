# Word OOXML 逻辑表格

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

JSON 增加 `table_schema=word_logical_tables_v1` 与平铺 `tables`。仅支持已识别的 Transitional Word OOXML；二进制 MS-DOC 仍只提供表格文字，不恢复网格。此结构不表示页面坐标、列宽或样式效果。

每表有唯一 id、parent_table_id、parent_cell_id、深度及部件/XML 路径；行 index、单元格 row/column 从零计数。grid_columns 是显式 tblGrid 的列数，grid_span 表示占据列数。gridBefore/gridAfter 是省略位置，不制造空单元格。缺少可用 grid 时 column 为 null，文字仍保留并诊断 partial，不推断列数。

v_merge 的 restart 以当前 cell id 为锚；continue 必须与前行同起列、同跨度的活动合并连续。输出 merge_anchor_id，不复制锚点内容。空 tc 保留为空文字和空段落集合。单元格 text/paragraphs 仅含直接段落，嵌套表使用独立 table id；blocks 保留段落与子表的原顺序，避免重复计入父单元格 text。runs 为所选修订视图中的存储 run 文字与来源，不是格式还原。

支持已知 sdt/customXml/修订包装的行和单元格，沿当前 revision view 遍历。旧 text/paragraphs 算法保持兼容；其无法显示的包装内容保存在结构结果并标 partial。未解码的绘图、字段等继续沿用全局诊断。

跨度整数、网格界限、每行覆盖与省略位置、重复属性、纵向合并连续性均校验。缺 grid 不算损坏；非法属性/不连续合并拒绝；合法跨度扩充网格或行范围不齐时标 partial/grid_complete=false，不自动补网格或空格。最多1024表、深度16、100000单元格、4096列、8Mi UTF16结构文字，诊断1000；不分配稠密网格。最终JSON仍受统一输出限额。

新增行为使用标准库合成矩阵测试，未有真实WPS OOXML表格的独立内容对照。公开结构依据为 ECMA-376/ISO29500 WordprocessingML 的 tbl/tblGrid/tr/tc、gridSpan、vMerge、gridBefore/gridAfter；不能将有限子集称为完整规范认证。

公开结构依据及采用的有限规则见表格依据 (`<skill-root>/references/ooxml-tables-evidence.json`)。运行时不联网。
