# ET 表头与矩形预览

路径占位符说明：`<skill-root>` 是 ET Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 ET 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

先完成主入口的任务目录、路径与读取边界核对；使用已确认 Python 3.9+、-I -S -B，仅标准库、离线、不调用办公软件。输入使用已确认任务目录内相对路径，确认可读，不改原件。首次分析执行以下自检，要求退出0且 environment-ok；OOXML 另需 ooxml-sheet=available，自检失败停止依赖操作：

```console
python -I -S -B "<skill-root>/scripts/analyze_et.py" --check
```

默认 data 解析，不自动 salvage；未知格式、结构、安全和资源错误仍拒绝。仅读 stdout 无需写探针/清单；确需保存查询/输出时先核对写权限，并按主入口的生命周期规则保护输入及收尾。只读取本页即可处理此分支，不预读复杂 query。

锚点不是 read_file 的选段参数；按真实工具 schema 取得完整必要说明。保留 stdout/stderr/真实退出码，宿主截断 JSON 不等于脚本有界分页。/large_tool_results/ 仅用宿主 read_file，缺失或失败就停止依赖，不用 Shell/Python 绕过。真实资源读取路径不自动证明 execute 可见，只用宿主确认的映射。

## 预览与覆盖

先选择较小的明确矩形，查看原始位置和合并关系后再确定表头；`--preview` 不需要声明表头，不自动寻找标题或合计行：

```console
python -I -S -B "<skill-root>/scripts/analyze_et.py" ./input.ett --sheet "数据" --range A1:G8 --preview
```

返回 `et_table_preview_v1`，按物理行列列出矩形内保存值、类型、公式存在标记与缓存状态；数字不经过 float，blank 与 missing 保持不同，不填充或计算。`merges` 包含整个所选矩形相交的合并范围、锚点、是否被选区裁切及 `anchor_in_page`；锚点不在当前页时不读取或复制其值。预览可说明被裁切的合并，而后续正式表头重建仍拒绝裁切或跨越声明表头边界的合并。

默认最多返回 10 行，`--query` 在预览模式下仅接受 `offset`、`limit`，例如 `{"offset": 0, "limit": 8}`；limit 为 1..50，单页最多 10000 个位置，矩形仍受 200000 个位置/256 列限制。其他分析参数、`--header-rows` 不与预览混用，明确失败而非忽略。按 `row_page.next_offset` 续读；每页仍完整解析源文件，没有快照缓存或绑定游标。拼接前确认每页 `source_sha256` 相同，工作表、范围、解析条件及 limit 不变，只调整 offset；API 调用方自行固定同一 source 对象。

`row_page.total` 是所选矩形物理行数，包含没有保存单元格的行；`truncated` 表示当前页不覆盖整个选择，末页 offset 非零时仍为 true，`next_offset=null` 仅表示无下一页。公式表达式、格式解释、样式、可见性、源筛选和其他对象未在网格中还原，逐项见 `metadata_omitted`；源诊断完整保留。状态和退出码沿用源读取结果，缓存显示不证明缓存最新；该视图不做统计，也不交给只接受原 schema 的分析器。JSON 输出仍受 16 MiB 上限约束，不能截断后消费。

## 来源与状态

预览只呈现保存值，不计算统计、不重算公式、不认证缓存新鲜度、单位或日期。保留源 status/partial、issues、diagnostics、reading_assessment 和已有 cell_value_coverage / omissions_affect_cell_values；源覆盖不等于当前页覆盖。状态 ok/退出0仅表示声明范围完成，partial/3保留缺口，读取失败1，用法错误2；失败或缺失结果不当空表。数字以 Decimal 字符串和类型保留，文本编号/布尔/空文本/blank/missing 不互换，缺失缓存不当零。核对源诊断及 metadata_omitted，不将此 schema 交给原 schema 消费方。

## 可选字节预算与续读

在预览命令添加 --max-output-bytes 24000，按 UTF-8 字节分页，允许512..16777216；含紧凑 JSON 全部字段、转义及末尾换行，不是token预算。仅传 --cursor 时预算默认12000；两者均不传时保持旧输出与16MiB序列化上限。

新模式保留 et_table_preview_v1、源诊断及partial，增加 output_page_revision=1、has_more、coverage、next_cursor；row_page 增加 has_more/next_offset，原 total/offset/returned/truncated 含义不变。coverage 明确初始 offset 与实际返回覆盖；末页仍不证明返回过偏移之前的行。预览查询只允许 offset/limit，limit为1..50且为每页上限，不能传0。

续读把返回的完整 next_cursor 正确引用为 --cursor 参数；输入、sheet、range、preview模式及原查询内容不变，字节预算可调。游标绑定源字节SHA256、完整查询、模式和修订号，不是授权或签名；源/参数变化或损坏均拒绝。初始offset大于total拒绝，等于total允许空页；不改旧非游标模式的offset规则。

每页仍完整解析输入，不缩小矩形或跳过安全检查。完整行是最小输出单位，不能拆长单元格；源诊断、合并定义等必要元数据不分页、不裁剪。元数据或首个完整行放不下时 output_view_error/退出1，不返回不前进的空成功页；有效预算同样约束失败JSON。可提高预算或使用旧完整输出，不截断JSON冒充成功。每页 anchor_in_page 与 returned_positions 按实际返回行重算，合并定义仍覆盖整个选区。只有 has_more=false 才结束本次请求的输出，源覆盖和遗漏仍分别判断。
