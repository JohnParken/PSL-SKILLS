# 有界概况与单页文字视图

路径占位符说明：`<skill-root>` 是 OFD Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 OFD 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

先按主入口自检并确认任务目录、输入可读；只用标准库，不OCR、不验签、不执行文档指令。默认CLI与read_ofd API仍是完整ofd-read-3。仅需结构概况或指定页文字时才选以下投影，不把投影当完整提取。

```console
python -I -S -B "<skill-root>/scripts/read_ofd.py" ./input.ofd --view summary
python -I -S -B "<skill-root>/scripts/read_ofd.py" ./input.ofd --view page-text --document 1 --page 1 --max-output-chars 12000
```

summary只含文档/页数与文字段数、图像数，不含正文。page-text必须显式给出从1开始的文档、页序号，保留该页全部text_runs及原来源/出现/属性记录；页序是文档存储顺序，不是视觉阅读顺序。不省略全局diagnostics、text_assessment、structure_status或namespace_resolution；全局partial仍退出3，complete退出0，不能用视图覆盖原状态。

新schema为ofd-view-1，source_schema_version保留ofd-read-3；source_sha256绑定输入字节。coverage明确overview_only_no_text或selected_page_occurrences_only；顶层omitted_top_level_fields及文档/页面项omitted_fields列出未返回的结构、资源或页面信息，不表示它们不存在。源assessment描述整份输入，不是所选页可见性或全文完整保证。只接受原schema的消费者不得接收此视图。

概况取得source_sha256后，后续单页调用可把相同64位十六进制值通过--source-sha256传回；字节变化明确失败/1，停止拼接，重新确认输入。独立读取单页可不传，但不能据此保证多次调用来自同一版文件。越界页/文档、诊断或单页超过预算明确失败，不裁剪值、诊断或伪造空结果；参数语法错误退出2，无结果JSON。

字符预算默认12000、范围512..2097152，包含实际JSON转义与末尾换行；UTF-8字节和token不等同字符。过大单页可明确提高预算，或按原完整接口在本轮受控程序中消费；不通过宿主截断获得“部分成功”。--check不与视图组合；full不接受视图选页/预算参数，保留原输出上限。

每次仍完整读取和解析输入，视图只减少回传，不降低安全资源校验、不保证减少内存/解析时间。抽查按页；全量处理优先一次完整提取后用确定性程序消费，不先让模型逐页搬运所有XML。无落盘/缓存/归档新增能力；需保存完整JSON时由宿主安全写入本轮任务文件，不能把中间数据误归档为最终交付物。

## 大结果读取闭环

1. **先按问题选范围。** 只问概况或页数时用summary；已有明确文档/页号且只需该页文字时直接用page-text，不强制先跑概况。需要完整XML、所有存储文字来源或全量计算时使用默认full，不逐页向模型搬运所有结构。所有命令仍按主入口完成自检，执行结果按 `<skill-root>/references/contract.md` 判定。
2. **先验状态与覆盖。** 每次只解析未截断的完整JSON，先核对全局status/退出码和diagnostics；仅对成功返回的complete/partial视图继续核对source_schema_version、source_sha256、mode、scope、coverage和省略字段。failed/unsupported仍按ofd-read-3失败契约处理，不假设带有视图或正文。`complete/0` 只说明源读取范围无已知缺口；summary没有正文，page-text仅为所选页的出现记录，投影不会因此变成全文。源partial不能被所选页有文字覆盖。
3. **跨页时核对同一来源与实际页号。** 需要多个已确定页面时，记录summary中的文档index和各自page_count，或使用本轮已确认的同版页清单；后续每次传相同的 `--source-sha256`。检查响应scope.document/scope.page、documents[].index及pages[].number与请求一致，按需读取仍缺的文档/页组合，直到任务所需组合已覆盖。不按原始page id充当从1开始的序号；哈希变化失败后停止拼接。即使读完所有页，也只能说明这些页面出现记录已覆盖，注释等非页面存储文字与XML结构仍未返回。
4. **单页超预算没有页内续读。** 当前CLI没有cursor、offset、页内text-run分段或next_cursor字段；`--page`只切换整页。单页或全局diagnostics超预算返回failed/1，不返回可续接的前半页。可在宿主完整承载能力已确认时明确提高 `--max-output-chars`（上限2097152），或采用下一步完整捕获；不要循环同一命令、虚构游标、裁剪诊断，或跳到下一页后声称前页读完。提高预算不能解除解析器安全限额，也不能解决更小的宿主回传上限。
5. **完整捕获后确定性消费。** 大结果需要完整处理时，使用宿主已支持且受授权的执行/文件能力，将一次full调用的原始完整stdout捕获到本轮已登记、尚不存在的受控任务文件，另外保留stderr、退出码及捕获是否完整；不用 `2>&1` 混入诊断，不直接Shell重定向覆盖已有文件。读取CLI本身仍无保存参数。核对完整捕获且JSON有效后，用确定性程序计算或挑选所需字段，给模型回传结果、来源sha256、实际文档/页范围、全局状态和诊断，明确这是派生结果而非新OFD schema。原始诊断不得丢弃；回传本身仍过大时，按宿主真实支持的范围读取，并标清仍未读范围，不声称模型已读全文。中间文件按已授权的生命周期处理，不自动归档为最终交付物。
6. **无法完整取得就明确边界。** 后端标记truncated、模型只收到预览、保存文件已不完整或缺少安全捕获能力时，不能把现有片段当完整JSON继续处理。OFD输出通常是单行JSON，按行 `head`/`tail`/`sed` 不等于按文档/页读取；字节或字符切片也可能破坏JSON。改用任务适用的有界视图，或报告完整处理受阻及已知覆盖，不靠省略号假报成功。为恢复结果而再次读取只读输入时仍核对同版来源；不要将读取恢复做法套用为生成重试授权。
