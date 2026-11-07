# 概况与有界输出

路径占位符说明：`<skill-root>` 是 ET Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 ET 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

旧CLI/API默认仍输出完整读取结果。大ET/ETT只需结构时先概况，逐格任务按需分页。本入口仅处理电子表格，拒绝 WPS 文字内容。以下均是**解析后的输出投影**，仍完整解析输入，不降低安全资源上限，不代表减少底层内存或时间。不写结果文件、不自动归档、不执行公式、宏或外链。

```console
python -I -S -B "<skill-root>/scripts/read_et.py" ./input.et --summary
python -I -S -B "<skill-root>/scripts/read_et.py" ./input.et --sheet "Data" --page-size 100 --max-output-chars 12000
```

新输出使用 `schema_version=wps_read_view_v1`，原schema另存于 `source_schema_version`。保留原status/partial、issues、diagnostics、reading_assessment及已有coverage字段；源partial仍退出3，源ok退出0，失败退出1，参数错误退出2。视图省略的全局与工作表元数据逐项列于 `metadata_omitted`；未返回不表示不存在。样式、合并、超链接、批注、附加区域等完整证据仍需旧完整读取。不得把投影交给只接受原schema的XLSX分析器；本 Skill 区域统计直接使用 analyze_et.py；外部原 schema 消费时保留完整提取 JSON。

概况只返回表名与存储单元格数，不含单元格值，`coverage=overview_only_no_values_or_text`。单元格模式保留 `items[].sheet/cell/data` 的原始值、类型、数字解释、公式及cache_status，不丢精度或把文本编号转数值；只按表原顺序及行列顺序取已存单元格，不填空白。`--sheet` 按精确名称选表，不提供range参数。

需要按明确矩形检查候选表头、空位与合并锚点时，使用 `<skill-root>/scripts/analyze_et.py` 的 `--preview --sheet "数据" --range A1:G8`；先完成主入口自检，输入仍为任务目录相对路径。它返回独立 `et_table_preview_v1`，不自动识别表头、不填值、不计算统计，也不替代完整读取 schema。参数、分页、覆盖和遗漏详见 `<skill-root>/references/table-analysis.md#表头区域预览`；不要为定位小块表头逐页取完整工作表。

视图默认最多200个存储单元格，字符预算12000；`--page-size` 为1..10000，`--max-output-chars` 为512..2097152，预算含JSON末尾换行。诊断不静默裁剪；单格或完整诊断超过预算时明确退出1并返回output_view_error，提高预算或改用完整读取。字符不是token。原完整JSON的16MiB序列化上限不变。

分页返回total_items/start/returned_items、truncated、next_cursor。truncated只表示当前视图还有后续，不改变源status；概况truncated=false也不表示数据已完整交付。保持文件、所有解析参数、sheet、page-size不变，将完整next_cursor作为正确引用的 `--cursor` 参数续读；允许调整字符预算。只有next_cursor=null才取完所选范围。源字节SHA256或选项变化、游标损坏会拒绝续读；从头重读，不混拼两版数据。游标不是授权或签名。

如需保存完整JSON，使用宿主已确认的安全写入能力，明确任务目录内尚不存在的结果文件；不猜 `--output` 参数、不覆盖已有文件。中间结果不自动作为最终交付物归档；创建前按实际任务所有权登记，后续按生命周期清理。不以摘要代替完整核对、不将落文件视为模型已经读取，宿主结果回传和token加载仍需独立验证。

## 任务分流与累计成本

概况用于确定所需表或内容，分页用于抽查或精确局部核对；页越小，工具调用、游标和共同诊断的累计回传可能越多。若必须取完全部内容，不据首屏较小就宣称整个任务节省token。每次CLI续读仍读取并解析完整输入，再生成输出视图；没有跨调用快照缓存，也没有降低解析资源或安全校验。

本 Skill 指定区域统计直接调用 `<skill-root>/scripts/analyze_et.py`，无需先输出完整 JSON 或拼接分页。仅外部分析器需要原 schema 时，按电子表格消费（`<skill-root>/references/spreadsheet-consumption.md`）取得同一个原完整提取JSON，交给实际可用的分析器；不拼造原schema、不省略partial/源诊断，不推测相邻技能目录。按需落盘的原结果仍只是本轮受控中间数据，不自动当最终交付物归档。单独概况仅统计输出层单元格数量，不再排序，但格式验证和诊断仍来自完整解析。

## 游标投影版本

视图保留原schema和字段，并增加 `projection_revision=2`。新游标同时绑定此投影修订号、输入SHA256及选择/解析参数。缺少修订号的旧游标、不同修订号、非法游标或源/参数变化均明确拒绝续读；不能手改游标或混拼不同版本页，须从头重新读取。旧默认完整读取CLI/API不变，仅已开始的旧视图分页需要重启；以后改变投影顺序/解释时也应升级修订号。

字符预算按实际JSON编码（含转义和末尾换行）核对；多字节UTF-8字节数可能更多，字符不是token。单元格逐项编码累计后仍作最终完整JSON校验，诊断不裁剪；输出超预算保持原明确失败，不以空页或部分值假报成功。

## 可选 UTF-8 字节预算

为匹配按字节限制的宿主，可添加 `--max-output-bytes 12000`，范围为 512..16777216。它与原字符预算同时生效，以**真实序列化 JSON 加末尾换行的 UTF-8 字节**计量；emoji、中文、转义和游标都计入。两者不是 token 数。不传新参数时保留旧完整读取与字符预算契约；仅指定字节预算也会启用有界视图，默认字符预算仍为12000，可显式提高。

分页新增 `has_more`，与本页 `next_cursor != null` 一致；旧 `truncated`、`coverage` 和所有来源诊断保留。概况的 `has_more=false` 只表示概况无后续页，不表示已经返回值或全文。续读可调整字符/字节预算，文件、选择和解析参数仍必须保持不变。末页移除游标后可能比前一候选页更小，实现会单独核对整个不超 page-size 的尾页。

单格或不可省略的诊断/元数据放不下时返回 `output_view_error`，不裁剪值或诊断；有效字节预算同时约束失败 JSON。提高预算或改用原完整读取；不要把错误当空结果。参数用法错误仍使用 stderr/退出2，不提供结果 JSON。来源追踪见 `<skill-root>/references/read-view-runtime.json`，它是维护信息，普通读取无需预读。
