# ET 表头与矩形预览

先完成主入口的任务目录、路径与读取边界核对；使用已确认 Python 3.9+、-I -S -B，仅标准库、离线、不调用办公软件。输入使用默认任务目录内相对路径，确认可读，不改原件。首次分析执行以下自检，要求退出0且 environment-ok；OOXML 另需 ooxml-sheet=available，自检失败停止依赖操作：

```python
execute(command="\"<skill-root>/scripts/.venv/bin/python\" -I -S -B \"<skill-root>/scripts/analyze_et.py\" --check")
```

默认容错解析；旧 `--compatibility` / `--salvage-unknown-metadata` 仅为 no-op。非值元数据与可恢复结构降级为 partial 并附 issue，未知容器、加密、记录帧无法继续、ZIP/XML 安全和资源耗尽仍拒绝。仅读 stdout 无需写探针/清单；确需保存查询/输出时先核对写权限，并按主入口的生命周期规则保护输入及收尾。只读取本页即可处理此分支，不预读复杂 query。

锚点不是 read_file 的选段参数；按真实工具 schema 取得完整必要说明。保留 stdout/stderr/真实退出码，宿主截断 JSON 不等于脚本有界分页。/large_tool_results/ 仅用宿主 read_file，缺失或失败就停止依赖，不用 Shell/Python 绕过。真实资源读取路径不自动证明 execute 可见，只用宿主确认的映射。

## 预览与覆盖

先选择较小的明确矩形，查看原始位置和合并关系后再确定表头；`--preview` 不需要声明表头，不自动寻找标题或合计行：

```python
execute(command="\"<skill-root>/scripts/.venv/bin/python\" -I -S -B \"<skill-root>/scripts/analyze_et.py\" ./input.ett --sheet \"数据\" --range A1:G8 --preview")
```

返回 `et_table_preview_v1`，按物理行列列出矩形内保存值、类型、公式存在标记与缓存状态。`value` 保持原精度，blank 与 missing 保持不同。若已有可用的数字显示文本，预览附带 `display_text`。`formula_text` 来自 OOXML 的非空 `<f>` 或受支持的 BIFF8 独立表达式转写。没有可读公式文本的单元格仍展示自身缓存。`merges` 包含整个所选矩形相交的合并范围、锚点、是否被选区裁切及 `anchor_in_page`；锚点不在当前页时不读取或复制其值。预览可说明被裁切的合并，而后续正式表头重建仍拒绝裁切或跨越声明表头边界的合并。

默认最多返回 10 行，`--query` 在预览模式下仅接受 `offset`、`limit`，例如 `{"offset": 0, "limit": 8}`；limit 为 1..50，单页最多 10000 个位置，矩形仍受 200000 个位置/256 列限制。其他分析参数、`--header-rows` 不与预览混用，明确失败而非忽略。按 `row_page.next_offset` 续读；每页仍完整解析源文件，没有快照缓存；本段为旧offset模式，下节字节分页另提供绑定游标。拼接前确认每页 `source_sha256` 相同，工作表、范围、解析条件及 limit 不变，只调整 offset；API 调用方自行固定同一 source 对象。

`row_page.total` 是所选矩形物理行数，包含没有保存单元格的行；`truncated` 表示当前页不覆盖整个选择，末页 offset 非零时仍为 true，`next_offset=null` 仅表示无下一页。`metadata_omitted` 列出预览省略的 BIFF 公式 token、公式细节、完整格式解释、样式、可见性、源筛选及其他对象。源诊断完整保留。状态和退出码沿用源读取结果，缓存显示不证明缓存最新；该视图不做统计，也不交给只接受原 schema 的分析器。非字节分页的JSON输出受本分析入口固定64 MiB字节上限约束，不提供max-json-bytes/output参数，不能截断后消费。

## 来源与状态

所选合并重叠时预览仍展示物理保存值，返回 `preview_issues` 和 partial；范围保留，但 `anchor_mapping=not_applied_due_to_overlap`，单元格不附歧义锚点。不把预览成功当作表头重建或合并填充已可执行。

预览只呈现保存值，不计算统计、不重算公式、不认证缓存新鲜度、单位或日期。保留源 status/partial、issues、diagnostics、reading_assessment 和已有 cell_value_coverage / omissions_affect_cell_values；源覆盖不等于当前页覆盖。状态 ok/退出0仅表示声明范围完成，partial/3保留缺口，读取失败1，用法错误2；失败或缺失结果不当空表。数字以 Decimal 字符串和类型保留，文本编号/布尔/空文本/blank/missing 不互换，缺失缓存不当零。核对源诊断及 metadata_omitted，不将此 schema 交给原 schema 消费方。

## 可选字节预算与续读

CLI字节分页另返回 `continuation_entrypoint=analyze_et.py` 和 `next_arguments` 数组，末页数组为null。数组含range/sheet、原query文件参数、header-rows或preview模式、预算与游标，不含输入路径或可执行Shell字符串。将其逐参数安全传给已定位的同一脚本和同一原始输入；保持query内容和source_sha256不变。预算可提高，但不要省略原已提高预算而回落默认12000。缺参数可按仍完整的原页数组补齐续读；scope/数组已丢失则重新读取必要结果，不猜游标。默认内容页、旧单元格视图与本入口游标不能混用，preview与analysis模式也不能交换。宿主截断须先取回完整JSON再续读；partial/退出3保留可靠结果并按缺口继续，不用 `&&` 或 `check=True` 无条件丢弃。每页仍完整解析原文件，无持久化快照。

在预览命令添加 --max-output-bytes 24000，按 UTF-8 字节分页，允许512..67108864；含紧凑 JSON 全部字段、转义及末尾换行，不是token预算。仅传 --cursor 时预算默认12000；两者均不传时保持原offset/limit预览与固定64 MiB字节上限，不是字符预算。

新模式保留 et_table_preview_v1、源诊断及partial，增加 output_page_revision=1、has_more、coverage、next_cursor；row_page 增加 has_more/next_offset，原 total/offset/returned/truncated 含义不变。coverage 明确初始 offset 与实际返回覆盖；末页仍不证明返回过偏移之前的行。预览查询只允许 offset/limit，limit为1..50且为每页上限，不能传0。

续读把返回的完整 next_cursor 正确引用为 --cursor 参数；输入、sheet、range、preview模式及原查询内容不变，字节预算可调。游标绑定源字节SHA256、完整查询、模式和修订号，不是授权或签名；源/参数变化或损坏均拒绝。初始offset大于total拒绝，等于total允许空页；不改旧非游标模式的offset规则。

每页仍完整解析输入，不缩小矩形或跳过安全检查。完整行是最小输出单位，不能拆长单元格；源诊断、合并定义等必要元数据不分页、不裁剪。元数据或首个完整行放不下时 output_view_error/退出1，不返回不前进的空成功页；有效预算同样约束失败JSON。可提高预算或使用旧完整输出，不截断JSON冒充成功。每页 anchor_in_page 与 returned_positions 按实际返回行重算，合并定义仍覆盖整个选区。只有 has_more=false 才结束本次请求的输出，源覆盖和遗漏仍分别判断。
