# 工作表批注的有限读取（OOXML 传统批注与 BIFF8 Note）

维护资源说明：文中 `docs/maintenance/et/` 与 `skills/et/scripts/test/` 均以宿主确认的仓库根目录为基准，仅供源码维护，不随运行包分发；运行任务不读取或执行。没有仓库时跳过维护资料，不按 Skill 安装目录拼接。

路径占位符说明：`<skill-root>` 是 ET Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 ET 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

新增读取通过工作表 comments 关系连接的独立部件，验证关系家族、内容类型、根和子元素。输出每条批注的单元格、作者、纯文字、关系 ID 和部件路径。批注不并入单元格值，不执行或跟随任何内容；布局未解码时明确 partial。

支持 comments/authors/commentList/comment/text 与普通及富格式文字串。作者索引、单元格界限、重复批注位置、数量及累计文字预算验证后才保留；单条批注元数据异常时跳过该条并附 issue，单元格仍照常读取。未知扩展、线程批注、外部目标、其他绘图关系只记录关系名并附 issue，不读取其部件；外部目标从不跟随或联网。不声称视觉气泡、显示坐标或完整批注格式支持。传统Transitional Note可按有限VML锚点 (`<skill-root>/references/vml-note-anchors.md`)验证单元格与原始矩形；未知形状、Strict VML和活动内容保留原始证据并附 issue，不删除部件来取得成功。

每部件最多4096作者/批注、1Mi字符，另受既有XML/总文字与最终JSON预算约束。空批注集显式返回空列表。新测试仅标准库合成包，未提供真实WPS批注独立内容对照。

## BIFF8 Note 批注

BIFF8 工作表的已确认 note 对象链（MsoDrawing/Obj/TxO/Note）在原有边界校验通过后，除继续保留原始记录与 `note_drawings` 证据外，还会把可解码的批注放入 `sheets[].comments`。每条为 `cell`、`author`、`text`、`object_id`、`kind='biff8_note'`、`source`（Note/TxO 记录偏移与物理行列）及 `layout_decoded=false`、`display_applied=false`、`target_followed=false`。批注文字和作者计数进入既有输出文字预算；不写入单元格值，不渲染气泡，不执行或跟随任何内容。

多分片 TxO 文字按声明长度与编码标志拼接；空文本、编码切换、代理对、Note 作者尾字节变体均在既有长度与 Unicode 校验下处理。无效对象链、作者、文字、对象/单元格链接或超出预算时保留原始记录并附 issue，不中止其它单元格。`note_drawings` 报告仍提供 drawing SHA-256、片段/对象计数、`text_decoded_for_output=true` 与 `comment_count`，但正文只在 `sheets[].comments` 返回一次，避免重复放大。未知 OfficeArt 原子仍列在 `unknown_officeart_records`，其覆盖保持不确定。

该能力由合成 BIFF8 note 链回归覆盖；尚无真实 WPS 批注原件独立内容对照。原始记录仍受原始元数据字节/条目预算限制。

官方开发依据见sheet-comments-evidence.json (`docs/maintenance/et/sheet-comments-evidence.json`)，运行时不访问网络。
