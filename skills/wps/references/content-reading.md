# 内容投影与高级读取解释

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

仅在需要详细字段、对象、修订或兼容解释时读取；普通 content 读取不需要本参考。先按主入口完成自检，仍在任务 `./` 执行，不改变授权、安全与交付边界。

## 内容与保存视图

CLI 默认 `--profile content` 返回 `wps_content_v1`：正文、保留分组/合并的精简表格、可确认父子单元格的 `nested_content`、附加区域、图表保存值与归并遗漏提示，不倾倒关系ID、XML路径、二进制记录及密集诊断。OOXML 含表格时用 `body` 顺序引用 `tables`，表格文字不再重复进入正文 `text`；嵌套单元格的 `content` 保留段落/子表顺序，同文但独立段落不会删除。`--profile full` 返回原详细结构；显式 standard/maximum 未指定 profile 时保持原详细输出。API `read()`、`read_bytes()` schema 不变。分页对所选 profile 的完整结果无损分片，游标不能跨 profile 复用。

CLI、`read()`、`read_bytes()` 默认 `reading_mode=content`，不先跑摘要、渲染、转换或图片提取。正文、表格、文本框与附加区域分别按既有结构返回，保留原始文字、来源、可确认分组/合并及缺口，不把 `text` 一栏当作全文。`drawing_objects` 保留支持的 OfficeArt 描述/艺术字，`related_stories` 保留文本框、页眉页脚、脚尾注等支持区域；不猜页面、阅读顺序或未知对象内容。二进制 `binary_table_structure` 保留单元格文字、表格分组和可确认合并，嵌套内容按 `nested_row_evidence` 和 `nested_tables` 读取，缺父范围或边界不猜归属。

二进制 content 默认有界读取浮动/内联图表保存缓存，不解析 PNG，也不为纯段落读取 PAPX 布局；含表格时仍验证必要属性、结构和共享范围，只省略非必要布局明细。`content_reading.not_requested` 和 `binary_optional_content_not_requested` 明示未检查项目；图片/内嵌图表中独有的文字或数值可能遗漏，不能称全格式完整。PNG或布局证据需显式 maximum，图片专有内容仍不做OCR；`binary_images` 为有界 PNG 元数据，原始字节再加 `--include-image-bytes`；`binary_charts.charts[].presentation` 为可用保存值、格式及缺值范围，不补零、不反算、不访问外部工作簿。两模式都不做 OCR、渲染或版式复原。CFB/ZIP/XML、加密、重叠、大小和资源门禁不降低；没有新增依赖。

OOXML 的 content/maximum 同时返回 `ooxml_charts` 中原生图表的标题、系列名、类别与数值保存缓存，保留原始字符串、格式代码、点索引和缺值诊断；不刷新、反算、打开外链或内嵌工作簿，不把缓存等同已验证可见值。图片图表不做 OCR。MC AlternateContent 按 Requires 在真实 XML 作用域解析的命名空间选择受支持 Choice，否则 Fallback；未能选择时明确 partial 与分支来源，不称已确认当前视图，不按相同文字删除独立对象。

两种全文模式均保留隐藏文字与域缓存但不执行。OOXML/RTF 正文用受支持 accepted 当前视图，不同的 all 存储视图在 full/API 另放 `revision_content`，内容投影仅提示另有存储修订，不混入当前正文；显式 all 标 `all_stored_revisions_not_current`，不可当当前内容。MS-DOC 与无法可靠过滤的已分类 RTF 修订返回存储文字，但 `current_view_available=false` 且 partial。关联区域缺失/损坏保留可靠正文并明确缺口；未知块、公式、图片与对象未解码仍报告，不以空数据伪装完整。完整边界与诊断见 `<skill-root>/references/reading-assessment.md#最大化读取模式`，仅需要细节时加载。

概况不含正文；段落范围只投影主段落，核对 `selection_status/source_status/projection_scope`。全文结果必须保留 JSON 与诊断；宿主截断不是完整 JSON。显式 standard 的 `--format text` 仅在确实不需结构时用，partial 诊断仍在 stderr；RTF 纯文本按块顺序包含表格，JSON `text` 仅段落，表格及顺序在 `tables/blocks`。standard 的附加区域需 `--include-related`，正文投影不得冒充全文。


## 专项直达

仅为当前问题加载对应资料，不递归预读：容器字段 `<skill-root>/references/format.md`；对象文字/PNG `<skill-root>/references/binary-shapes.md`；修订和隐藏文字 `<skill-root>/references/word-visibility.md`；表格结构 `<skill-root>/references/ooxml-tables.md` 或 `<skill-root>/references/binary-tables.md`；附加区域 `<skill-root>/references/ms-doc-stories.md` 或 `<skill-root>/references/ooxml-related-stories.md`。
