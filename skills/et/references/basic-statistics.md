# ET 基础区域统计

路径占位符说明：`<skill-root>` 是 ET Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 ET 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

先完成主入口的任务目录、路径与读取边界核对；使用已确认 Python 3.9+、-I -S -B，仅标准库、离线、不调用办公软件。输入使用已确认任务目录内相对路径，确认可读，不改原件。首次分析执行以下自检，要求退出0且 environment-ok；OOXML 另需 ooxml-sheet=available，自检失败停止依赖操作：

```python
execute(command="python -I -S -B \"<skill-root>/scripts/analyze_et.py\" --check")
```

默认容错解析；旧 `--compatibility` / `--salvage-unknown-metadata` 仅为 no-op。非值元数据与可恢复结构降级为 partial 并附 issue，未知容器、加密、记录帧无法继续、ZIP/XML 安全和资源耗尽仍拒绝。仅读 stdout 无需写探针/清单；确需保存查询/输出时先核对写权限，并按主入口的生命周期规则保护输入及收尾。只读取本页即可处理此分支，不预读复杂 query。

锚点不是 read_file 的选段参数；按真实工具 schema 取得完整必要说明。保留 stdout/stderr/真实退出码，宿主截断 JSON 不等于脚本有界分页。/large_tool_results/ 仅用宿主 read_file，缺失或失败就停止依赖，不用 Shell/Python 绕过。真实资源读取路径不自动证明 execute 可见，只用宿主确认的映射。

## 最小操作与口径

```python
execute(command="python -I -S -B \"<skill-root>/scripts/analyze_et.py\" ./input.et --sheet \"数据\" --range A1:D100 --header-rows 1")
```

工作表、矩形和表头由任务明确；无 --header-rows 时首行也是数据。默认不填充合并、不自动应用隐藏/筛选，不识别小计/总计。先确认金额单位、日期意义、统计期间和分母；不能把明细和合计同时求和。需要非连续 exclude_rows、筛选/分组/去重/排序/fill_text 或 API 时改读 `<skill-root>/references/table-analysis.md`；只查看表头改读 `<skill-root>/references/table-preview.md`。

默认返回列 profiles 与前100行。只要统计时，可在新建任务查询 JSON 中写 {"limit":0}，以 --query ./query.json 调用，省略行预览不缩小统计。明确接受缓存才用 {"formula_policy":"cached","limit":0}，不猜选缓存。查询必须有效 UTF-8 JSON，未知/重复键拒绝，不执行表达式。区域内的表头合并不得裁切或跨越声明边界；数据合并保持原保存位置，不复制数值锚点。

## 类型与缓存

- 数字按 Decimal 处理，JSON 中以字符串输出，文本 `"001"` 与数字 `1` 不合并；布尔不会当成 0/1。
- `blank` 是已保存空白，`missing` 是矩形内无保存单元格；空文本 `""` 是文本。三者分开统计；缺失公式缓存不是普通空白。
- `formula_policy=exclude` 为默认：保留行里的保存值与缓存状态，但 `analysis_state=formula_excluded` 的值不用于比较、计数或数值汇总，报告 partial。
- `formula_policy=cached` 为明确缓存选择：使用非错误的已保存缓存，并报告 `formula_cache_used`。不计算表达式、不验证新鲜度；`missing_cache`、`error_cache` 和普通 `error` 保留原值与位置但不参加有效值计算。
- `profiles` 同时给出类型数、状态数、公式位置数、已用缓存数、有效值数及数值数，不能只取 sum 而隐去这些分母和缺口。状态计数不会把排除项当零。
- `source` 保留源 status/partial、issues、diagnostics、reading_assessment 和已有覆盖字段；源 partial 不因筛选或统计而升级为完整成功。

`analysis_issues` 的单元格诊断保留整个所选矩形的 `positions`，`scope=selected_rectangle_before_processing`；其中 `matched_data_positions` 是排除行、筛选和去重后的匹配数据位置数，均不受预览分页影响。沿用的 `formula_cache_used` 诊断表示选区中按缓存策略处理的未验证缓存位置，也可能位于表头或之后被排除的行，并包含 blank 缓存；不可将其总数当作数值计算使用的缓存数。实际匹配列的有效非空缓存数看 `profiles.formula_caches_used`，数值分母看 `numeric_count`。排除后诊断数量可以为零，但原选区诊断与源 partial 仍保留。

不应用数字格式或单位，不把数字序列自动转日期、百分比或货币。OOXML 的 ISO 日期在源读取器中作为文本保留，统计继续保持文本；数值日期序列仍是数值。需要日期分组或金额口径时先明确列和值的业务含义，本入口没有日期周期分组或单位换算。

## 统计质量报告

分析结果的 `quality_report` 使用 `et_table_quality_v1`，覆盖显式排除行、值筛选与去重后的全部匹配数据，不受行/组预览分页或 select 影响；选区内已被排除的缺口仍在外层 `analysis_issues` 与 source 中，不能只看质量报告就隐藏它们。

- 每列 `numeric_coverage` 给出 numerator/denominator；partition 将全部匹配位置恰好分成 number_included、unavailable、blank、missing、non_numeric_usable 五类，不把缺失或缓存错误当零。零行时分母为 0，不编造比例或总额。
- `findings` 给出类型/缓存缺口、空文本、非数值、合并非锚点和文本传播的数量与物理位置样本。`quality_sample_limit` 默认每列每类 5 个，允许 0..20；样本按原始行号升序，`samples_truncated` 明确剩余未展示位置。此设置只控制诊断样本，不裁剪计数或统计。不同 findings 可重叠，不能把其数量相加作为列分母。
- `non_numeric_value`、`mixed_usable_types` 是事实提示，文本编号、分类列与合法空值可能符合业务设计，不自动判错、转换类型、删除记录或把 ok 改为 partial。unverified_cache 包含已采用缓存策略的 blank/空文本位置；数值分母与有效非空缓存数仍分别看 numeric_coverage 和 profiles。
- 表头报告 declared/inferred、空标签、不可用标签及重复标签所在物理列；不猜补标签或改列名。分组覆盖列出 grouped_rows 和不可用组键排除数，不能用少量组预览冒充全量分组范围。`numeric_mean_inexact` 提示均值已按既定精度舍入。

报告不提供质量分数、不认证业务总额、单位、日期含义或公式新鲜度；结合 `source`、`analysis_issues`、原始预览和任务口径解释结果。缺失类型约束或真实业务预期时明确说明，不能把完整计数当作业务验收。

## 输出与限制

返回独立 schema `et_table_analysis_v1`。`rows` 和 `groups` 为预览；`row_page`、`group_page` 给出 total/offset/returned/truncated。`profiles`、`totals` 与质量报告总是覆盖排除行、筛选和去重后的全部匹配行，分组内措施覆盖该组全部行。设 limit=0 可省略行预览，统计仍完整执行；需要后续页必须保留同一源文件和查询口径。

状态 ok/0 表示本次声明的保存值计算完成；partial/3 保留源缺口或不可用值/缓存诊断；参数用法错误为 stderr/2，无结果 JSON；读取/查询/资源失败为完整失败 JSON/1。stdout 不混入进度提示，不截断 JSON 后冒充完整输出。

| 上限 | 数值 |
|---|---|
| 选择矩形 | 200000 个位置、256 列；受源格式网格边界限制 |
| 查询文件 | 64 KiB，重复键拒绝；显式排除行最多 10000 个 |
| 筛选/去重键/分组键/措施 | 16 / 16 / 8 / 32 |
| 分组 | 10000 组 |
| 行与组预览 | 分别默认 100，最多 10000；可设 0 |
| 统计质量位置样本 | 每列每类默认 5，最多 20；可设 0，计数仍完整 |
| 独立矩形预览 | 默认 10 行，最多 50 行/10000 位置；不计算统计 |
| 数值分析 | 系数最多 1100 位、指数绝对值最多 2200、adjusted 指数绝对值最多 1100；覆盖本次验证的读值范围，不扩展源读取器 |
| 输出 JSON | 完整读取与分析结果默认 64 MiB（`--max-json-bytes` 1–256 MiB，完整读取可用 `--output` 写文件）；超出时输出失败，收窄预览/选择范围或改用文件输出后重做，不能截取输出继续分析 |

原容器、单元格、文字及元数据预算不变。此入口不计算新公式、不联表、不重建透视表、不推断小计，不编辑、导出或写回办公文件；需要这些能力时另行明确交接及验证。

## 可选字节预算与续读

在原命令添加 `--max-output-bytes 24000`，可对 `--preview` 或分析结果按 UTF-8 字节预算分页；512..67108864，包含真实紧凑 JSON 的全部字段、转义与末尾换行。仅传游标时分析分页预算默认12000；完整读取视图默认字符预算65536，完整 JSON 默认64MiB（`--max-json-bytes` 1–256 MiB）。预算不是token数。

新模式保留原 schema、partial、源诊断、统计质量报告和分母，另加 `output_page_revision=1`、`has_more`、`coverage` 与 `next_cursor`。`row_page`/`group_page` 另加 `has_more`、`next_offset`；旧 total/offset/returned/truncated 含义保留。`coverage` 对每条结果流声明是否请求、初始 offset 和覆盖范围；偏移之前的行不属于本次输出覆盖，不能据末页声称已经返回整个结果表。`limit=0` 或 `group_limit=0` 表示不请求对应结果流，仍完整计算统计，不产生该流的续读；独立预览仍要求limit为1..50。

原查询的 offset/group_offset 是本次输出起点，limit/group_limit 是**每页上限**。将返回的完整 `next_cursor` 正确引用为 `--cursor` 参数，输入、sheet、range、header-rows、preview模式及查询文件内容保持不变；可以调整字节预算。游标绑定源字节SHA256、完整查询与模式、分页修订号，源或语义参数变化、损坏游标均拒绝；游标不是授权或签名。新模式拒绝所请求结果流超过total的起始offset；等于total可为空页。旧非游标模式的offset行为保持不变。

每次续读仍完整解析源文件、处理整个指定区域，并在分页前完成排除行、筛选、排序、去重、分组与统计。分页只限制返回的 rows/groups，不缩小 profiles、totals、quality_report、缓存纳入/排除分母；不通过重设range来凑预算。仍遵守原200000位置、256列、10000组以及候选预览行数上限，不创建无界全量结果列表。行结果先占预算，组结果可在后续页续读；只有顶层 `has_more=false` 才结束本次请求的全部结果流。

整行/整组是最小输出单元，不切开长单元格。header、query、merges、源诊断、quality_report及统计等必要元数据不分页、不裁剪；放不下时明确返回 `output_view_error`/退出1。没有任何行/组能前进时同样失败，不返回带不前进游标的空成功页。有效预算也约束失败JSON；提高预算、明确减少展示列/每页行数，或使用旧完整输出。不要为减少输出而改变统计口径。预览页的合并anchor_in_page和returned_positions按实际返回行重算，合并定义仍覆盖整个选区。

数值统计只用可用 number，不把文本数字或布尔纳入；空数值集的 sum/mean/min/max 为 null。sum 使用有界 Decimal 精确运算，mean 为34位有效数字、ROUND_HALF_EVEN，并保留 mean_inexact；不消除源 BIFF 浮点尾数，不证明 Excel 等价或业务正确。先抽核源行、有效数量及合计，再解释结果。
