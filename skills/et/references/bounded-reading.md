# 概况与有界输出

路径占位符说明：`<skill-root>` 是 ET Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 ET 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

大 ET/ETT 只需结构时先概况，逐格任务按需分页。本入口仅处理电子表格，拒绝 WPS 文字内容。以下旧 read_et.py 视图均是**解析后的输出投影**，仍完整解析输入，不降低安全资源上限，不代表减少底层内存或时间。不自动归档、不执行公式、宏或外链。

```python
execute(command="python -I -S -B \"<skill-root>/scripts/read_et.py\" ./input.et --summary")
execute(command="python -I -S -B \"<skill-root>/scripts/read_et.py\" ./input.et --sheet \"Data\" --page-size 100 --max-output-chars 65536")
```

新输出使用 `schema_version=wps_read_view_v1`，原schema另存于 `source_schema_version`。保留原status/partial、issues、diagnostics、reading_assessment及已有coverage字段；源partial仍退出3，源ok退出0，失败退出1，参数错误退出2。视图省略的全局与工作表元数据逐项列于 `metadata_omitted`；未返回不表示不存在。样式、合并、超链接、批注、附加区域等完整证据仍需 `--profile full` 完整读取。不得把投影交给只接受原schema的XLSX分析器；本 Skill 区域统计直接使用 analyze_et.py；外部原 schema 消费时保留完整提取 JSON。

概况只返回表名与存储单元格数，不含单元格值，`coverage=overview_only_no_values_or_text`。单元格模式保留 `items[].sheet/cell/data` 的原始值、类型、数字解释、公式及cache_status，不丢精度或把文本编号转数值；只按表原顺序及行列顺序取已存单元格，不填空白。`--sheet` 按精确名称选表，不提供range参数。

需要按明确矩形检查候选表头、空位与合并锚点时，使用 `<skill-root>/scripts/analyze_et.py` 的 `--preview --sheet "数据" --range A1:G8`；先完成主入口自检，输入仍为任务目录相对路径。它返回独立 `et_table_preview_v1`，不自动识别表头、不填值、不计算统计，也不替代完整读取 schema。参数、分页、覆盖和遗漏详见 `<skill-root>/references/table-analysis.md#表头区域预览`；不要为定位小块表头逐页取完整工作表。

视图默认最多200个存储单元格，字符预算65536；`--page-size` 为1..10000，`--max-output-chars` 为512..2097152，预算含JSON末尾换行。仅 `--summary` 在完整诊断过大时改为 `diagnostic_summary`：保留源 status/partial、值覆盖风险和 assessment 的核心结论，报告源诊断条目数、至多20类代码样本及省略字段；计数是诊断条目，不是受影响单元格或聚合事件数。摘要不提供完整诊断，`details_omitted=true` 时须按需完整读取或提高预算；摘要本身仍放不下则失败。逐格分页不省略诊断，单格或完整诊断超过预算时返回 output_view_error。字符不是token，完整 JSON 预算也不同于视图预算。

分页返回total_items/start/returned_items、truncated、next_cursor。truncated只表示当前视图还有后续，不改变源status；概况truncated=false也不表示数据已完整交付。保持文件、所有解析参数、sheet、page-size不变，将完整next_cursor作为正确引用的 `--cursor` 参数续读；允许调整字符预算。只有next_cursor=null才取完所选范围。源字节SHA256或选项变化、游标损坏会拒绝续读；从头重读，不混拼两版数据。游标不是授权或签名。

当前 CLI 默认 `--profile content`：有界视图的 `source_schema_version=et_content_v1`，原解析 schema 和投影遗漏在 `content_profile_projection` 诊断中说明；诊断概况 fallback 仍明确省略详情。保存值与缓存保留，二进制/版式证据不随默认页回传；默认诊断为代码/次数与有界定位，完整诊断或原格证据加 `--profile full`。摘要和逐格分页不返回图表，图表内容须使用不带 --summary/--page-size 的默认内容读取。游标绑定 profile，不能混用或手改，旧游标重新开始。

## 完整 JSON 的预算、精简与文件输出

完整读取默认上限 64 MiB。需要更大输出时：

- `--max-json-bytes <字节>`：接受 1048576..268435456（1–256 MiB），显式调整完整 JSON 预算。
- 超过预算时先自动精简一次：去重原始字节证据并按记录种类汇总未解释元数据，保存值、类型、缓存、合并、可见性、诊断保持不变；结果附 `output_auto_compacted` 并转 partial。不会输出截断 JSON。
- `--compact`：主动选择精简输出（同上投影），适合只消费保存值、类型、缓存和诊断的场景；`output_projection` 列出省略类别、`raw_evidence_complete=false`，不能称原始字节证据完整。
- `--output <裸文件名>`：把完整 JSON 有界流式写入任务根目录的**新文件**（独占创建，不覆盖），stdout 只返回 `et_output_manifest_v1`：`status`、`partial`、`source_schema_version`、`cell_value_coverage`、`omissions_affect_cell_values`、`compacted`、`output.{file,bytes,sha256}` 与说明。参数只接受裸文件名，拒绝目录、绝对路径、父目录和已存在文件；`--output` 与有界视图不能同时使用。写入失败会删除本次未完成文件并返回失败。
- 单格或不可省略的诊断/元数据放不下时返回 `output_view_error`，不裁剪值或诊断；有效字节预算同时约束失败 JSON。提高预算或改用 `--output`；不要把错误当空结果。参数用法错误仍使用 stderr/退出2，不提供结果 JSON。

如需把完整 JSON 留作后续处理，优先使用 `--output`；使用宿主写文件能力时，同样明确任务目录内尚不存在的结果文件、不覆盖已有文件。中间结果不自动作为最终交付物归档；创建前按实际任务所有权登记，后续按生命周期清理。不以摘要代替完整核对、不将落文件视为模型已经读取，宿主结果回传和token加载仍需独立验证。

文件输出默认上限 256 MiB，可用 `--max-json-bytes` 指定更低预算，包含末尾换行；清单的 bytes/SHA-256 对应实际完整文件字节。流式写入避免在 stdout 输出大正文，但不能绕过256 MiB或输入解析资源上限。

## 宿主大结果与业务分页分开处理

- 先判断是**预览外置**还是**源输出已截断**。若宿主给出完整结果引用，使用实际可用的 `read_file` 和它的真实 schema 读取；需要分页时按其返回的范围、总量或续读提示推进，不猜参数、行号基准或固定页长。`/large_tool_results/` 是宿主引用，不作为 Shell/Python 可见文件。引用不可读时报告缺口并停止依赖，不改用 Shell 绕过。
- 记录已读取的范围，避免缺段或重复计数；只取与当前问题相关的范围。需要完整 JSON 或完整证据时，必须补齐所需全部内容后才解析/验收。宿主状态头、行号或提示不属于业务 JSON；不能把任意分段直接解析、补括号或从首尾花括号猜出“完整 JSON”。若单行过长且宿主没有可用续读能力，报告限制，不原样重复读取同一截断行。
- 捕获上限已丢弃的输出，或明确标记不完整的外置文件，不能靠读到文件末尾恢复。只读任务可按下方已支持的选区/预算重新读取；先检查来源是否变化，不能把不同版本拼接。写操作不为补回执重新执行，应先核对原任务、已有文件与发布/清理状态。
- 业务视图的 `truncated`/`next_cursor` 与宿主截断是两层状态。只有当前页完整可解析时，才按本页真实游标续读；同时核对各页来源、选择条件及 `start/returned_items/total_items` 的连续覆盖。`next_cursor=null` 只表示该投影所选范围取完，不补回 `metadata_omitted`、源 partial 或未请求区域，不据此宣称全文/全工作簿完整。

## 任务分流与累计成本

概况用于确定所需表或内容，分页用于抽查或精确局部核对；页越小，工具调用、游标和共同诊断的累计回传可能越多。若必须取完全部内容，不据首屏较小就宣称整个任务节省token。每次CLI续读仍读取并解析完整输入，再生成输出视图；没有跨调用快照缓存，也没有降低解析资源或安全校验。

本 Skill 指定区域统计直接调用 `<skill-root>/scripts/analyze_et.py`，无需先输出完整 JSON 或拼接分页。仅外部分析器需要原 schema 时，按电子表格消费（`<skill-root>/references/spreadsheet-consumption.md`）取得同一个原完整提取JSON，交给实际可用的分析器；不拼造原schema、不省略partial/源诊断，不推测相邻技能目录。按需落盘的原结果仍只是本轮受控中间数据，不自动当最终交付物归档。单独概况仅统计输出层单元格数量，不再排序，但格式验证和诊断仍来自完整解析。

## 游标投影版本

视图保留原schema和字段，并增加 `projection_revision=2`。新游标同时绑定此投影修订号、输入SHA256及选择/解析参数。缺少修订号的旧游标、不同修订号、非法游标或源/参数变化均明确拒绝续读；不能手改游标或混拼不同版本页，须从头重新读取。Python API 保留完整默认；CLI 当前默认 content，旧完整 CLI 改用 --profile full，已开始的旧视图分页需要重启；以后改变投影顺序/解释时也应升级修订号。

字符预算按实际JSON编码（含转义和末尾换行）核对；多字节UTF-8字节数可能更多，字符不是token。单元格逐项编码累计后仍作最终完整JSON校验，诊断不裁剪；输出超预算保持原明确失败，不以空页或部分值假报成功。

## 可选 UTF-8 字节预算

为匹配按字节限制的宿主，可添加 `--max-output-bytes 12000`，范围为 512..67108864（64 MiB）。它与原字符预算同时生效，以**真实序列化 JSON 加末尾换行的 UTF-8 字节**计量；emoji、中文、转义和游标都计入。两者不是 token 数。仅指定字节预算也会启用有界视图，默认字符预算为65536，可显式提高。

分页新增 `has_more`，与本页 `next_cursor != null` 一致；旧 `truncated`、`coverage` 和所有来源诊断保留。概况的 `has_more=false` 只表示概况无后续页，不表示已经返回值或全文。续读可调整字符/字节预算，文件、选择和解析参数仍必须保持不变。末页移除游标后可能比前一候选页更小，实现会单独核对整个不超 page-size 的尾页。

来源追踪见 `<skill-root>/references/read-view-runtime.json`，它是维护信息，普通读取无需预读。

## 可选 OOXML 解析期分页（独立接口）

对 OOXML 容器的 ET/ETT，可使用 `<skill-root>/scripts/et_parse_page.py`。先执行主入口环境自检并确认 `ooxml-sheet=available`，再在已确认任务目录运行：

```python
execute(command="python -I -S -B \"<skill-root>/scripts/et_parse_page.py\" ./input.et --page-size 100 --max-output-bytes 1048576")
```

本入口提供 `--capabilities`，当前接口 `et_parse_page_v1`、`revision=1`，不替换原完整读取、分析或 `wps_read_view_v1`。只支持 OOXML 表格；BIFF5/7、BIFF8、文本容器使用原入口，不伪装为空页。

- 每次仍完整扫描并按原解析器校验/解码所有已声明工作表和单元格，保持 ZIP/XML、全局文字、单元格/行数等原限制及诊断；只保留当前页的单元格结果对象，以轻量坐标集合保留重复格“首个可读值”语义。输入字节、共享字符串、行元数据、坐标索引仍占内存，不是恒定内存或读取时间优化，不放宽原限制。
- 检测到任意已解析行/列继承样式时，自动释放窗口状态并重新使用完整读取路径，返回 `parse_mode=full_parse_fallback` 与原因。此时**未降低全量 cells 留存**；不能称优化成功。普通窗口为 `parse_time_cell_window`；显式单元格样式仍可窗口读取。无磁盘缓存，不写安装目录、不改原件。
- 顺序为声明工作表顺序及各表首个可读单元格的**存储顺序**，不是旧视图的行列排序。可用 `--sheet` 按解析后的精确表名选择；未选表仍扫描并贡献源诊断。
- `items[].sheet/cell/data` 保留原格值、类型、精度、保存缓存和格式解释；`issues`、`issues_truncated`、`text_budget` 与源值覆盖字段来自完整扫描，不从当前页推导全表正确性。原解析器的损坏恢复/截断限制仍适用。源覆盖仅表示已声明表的解析覆盖，不表示全部内容已交付。
- 结果明确列出 `metadata_omitted`，不交付完整样式、合并、可见性、批注/超链接或全局共享字符串证据；需要这些字段或 `reading_assessment` 时使用完整读取。不得把新 schema 交给只接受原 schema 的分析器。
- 核对 `source_sha256`、`start`、`returned_items`、`total_items` 和 `next_cursor`，逐页连续拼接；`source_cell_count` 是全工作簿已读保存格数。完整 `next_cursor` 传入 `--cursor`，保持输入、sheet、page-size 不变，仅输出字节预算可变。游标绑定源字节、独立接口修订与选项，不兼容旧视图游标；损坏/变更/越界明确失败，重新开始。游标不是签名或授权。
- `page-size` 为1..10000；UTF-8 字节预算为512..67108864，默认1 MiB，计入完整 JSON 和末尾换行。整页及全部源诊断放不下就失败，不悄悄少返回格或截断字段；提高预算，或从头使用更小 page-size。只有 `next_cursor=null` 才取完所选已读格，源遗漏不会因此消失。
- stdout 为单条 JSON，成功/partial/失败/参数错误分别退出0/3/1/2。每页重扫可能增加累计耗时，不建议全量统计遍历分页。平台与真实格式验收仍须单独核对。
