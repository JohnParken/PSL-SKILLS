# ET 保存值统计与结果表处理

维护资源说明：文中 `docs/maintenance/et/` 与 `skills/et/scripts/test/` 均以宿主确认的仓库根目录为基准，仅供源码维护，不随运行包分发；运行任务不读取或执行。没有仓库时跳过维护资料，不按 Skill 安装目录拼接。

路径占位符说明：`<skill-root>` 是 ET Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 ET 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 目录

- 入口与范围（`<skill-root>/references/table-analysis.md#入口与范围`）
- 查询示例（`<skill-root>/references/table-analysis.md#查询示例`）
- 表头区域预览（`<skill-root>/references/table-analysis.md#表头区域预览`）
- 类型与缓存（`<skill-root>/references/table-analysis.md#类型与缓存`）
- 表头与合并（`<skill-root>/references/table-analysis.md#表头与合并`）
- 操作及统计顺序（`<skill-root>/references/table-analysis.md#操作及统计顺序`）
- 统计质量报告（`<skill-root>/references/table-analysis.md#统计质量报告`）
- 输出与限制（`<skill-root>/references/table-analysis.md#输出与限制`）
- 可选字节预算与续读（`<skill-root>/references/table-analysis.md#可选字节预算与续读`）
- 验证依据（`<skill-root>/references/table-analysis.md#验证依据`）

## 入口与范围

基础区域统计直接读 `<skill-root>/references/basic-statistics.md`；本页用于复杂查询与组合操作，保留它们所需的完整共同契约。

先遵守主入口的任务目录、输入边界及执行环境约束（`<skill-root>/SKILL.md#执行环境约束与首步自检`）。输入及可选查询 JSON 使用任务目录下相对路径；仅依赖 Python 3.9+ 标准库，不安装包、不联网、不调用办公软件。确认输入可读；仅读 stdout 不创建探针或归档清单。

```python
execute(command="python -I -S -B \"<skill-root>/scripts/analyze_et.py\" --check")
execute(command="python -I -S -B \"<skill-root>/scripts/analyze_et.py\" ./input.ett --sheet \"数据\" --range A1:D100 --header-rows 1")
```

自检要求退出 0 且输出 `environment-ok`；OOXML 还要求 `ooxml-sheet=available`。自检失败停止依赖操作。完成后读取输入的默认 data 结果，不改读取策略，不自动尝试 salvage。后缀不证明内部格式；未知格式和原读取器的结构/安全/资源拒绝仍生效。

工作表和矩形范围必须明确。表头可选；不指定时第一行也是数据。`--header-rows 1:2` 使用已有表头重建规则，但列 ID 始终为物理字母 `A`、`B` 等，重复标题不会覆盖列。不要让标题、说明、小计、总计或无关表格落入统计范围后宣称得到业务总额；执行器不推断这些角色。连续区域内穿插这些行时，可用 `exclude_rows` 列出经任务确认的原始物理行号。

程序入口是 `<skill-root>/scripts/et_table_analysis.py` 的 `analyze(result, query)`。`result` 必须为完整的 `biff8_readonly_v1` 或 `ooxml_spreadsheet_readonly_v1` 成功/partial 对象，也接受由完整 JSON 还原的数字字符串。分页投影、失败字典和其他 schema 拒绝。API 查询另外包含 `sheet`、`range` 和可选 `header_rows`。来源结果和查询均不原地修改。

## 查询示例

查询文件为 UTF-8 JSON，只提供处理选项；CLI 的工作表、范围和表头不重复放入查询文件。下面的字段名与列字母替换为实际任务所需；示例不表示原文件具有这些业务列。

```json
{
  "exclude_rows": [20, 40],
  "filters": [{"column": "C", "op": "ge", "type": "number", "value": "0"}],
  "distinct_by": ["A", "B", "C"],
  "sort": [{"column": "A"}, {"column": "C", "descending": true}],
  "select": ["A", "C"],
  "group_by": ["A"],
  "aggregates": [
    {"op": "rows", "as": "records"},
    {"op": "sum", "column": "C", "as": "amount"},
    {"op": "mean", "column": "C", "as": "average"}
  ],
  "formula_policy": "exclude",
  "merge_policy": "preserve",
  "quality_sample_limit": 5,
  "offset": 0,
  "limit": 100,
  "group_offset": 0,
  "group_limit": 100
}
```

```python
execute(command="python -I -S -B \"<skill-root>/scripts/analyze_et.py\" ./input.ett --sheet \"数据\" --range A1:D100 --header-rows 1 --query ./query.json")
```

上例的 20、40 仅演示物理行号，不表示它们是输入文件的合计行；实际没有需排除的行时省略 `exclude_rows`。查询重复键、未知字段、错误类型和不支持的操作明确失败。查询不接受 Python 表达式、SQL、任意函数、脚本或公式；文字筛选只是大小写敏感的字符串比较。

## 表头区域预览

只查看候选表头与合并范围时直接读自包含 `<skill-root>/references/table-preview.md`，无需本页复杂查询。预览 schema 不可替代原读取或统计结果。

## 类型与缓存

- 数字按 Decimal 处理，JSON 中以字符串输出，文本 `"001"` 与数字 `1` 不合并；布尔不会当成 0/1。
- `blank` 是已保存空白，`missing` 是矩形内无保存单元格；空文本 `""` 是文本。三者分开统计；缺失公式缓存不是普通空白。
- `formula_policy=exclude` 为默认：保留行里的保存值与缓存状态，但 `analysis_state=formula_excluded` 的值不用于比较、计数或数值汇总，报告 partial。
- `formula_policy=cached` 为明确缓存选择：使用非错误的已保存缓存，并报告 `formula_cache_used`。不计算表达式、不验证新鲜度；`missing_cache`、`error_cache` 和普通 `error` 保留原值与位置但不参加有效值计算。
- `profiles` 同时给出类型数、状态数、公式位置数、已用缓存数、有效值数及数值数，不能只取 sum 而隐去这些分母和缺口。状态计数不会把排除项当零。
- `source` 保留源 status/partial、issues、diagnostics、reading_assessment 和已有覆盖字段；源 partial 不因筛选或统计而升级为完整成功。

`analysis_issues` 的单元格诊断保留整个所选矩形的 `positions`，`scope=selected_rectangle_before_processing`；其中 `matched_data_positions` 是排除行、筛选和去重后的匹配数据位置数，均不受预览分页影响。沿用的 `formula_cache_used` 诊断表示选区中按缓存策略处理的未验证缓存位置，也可能位于表头或之后被排除的行，并包含 blank 缓存；不可将其总数当作数值计算使用的缓存数。实际匹配列的有效非空缓存数看 `profiles.formula_caches_used`，数值分母看 `numeric_count`。排除后诊断数量可以为零，但原选区诊断与源 partial 仍保留。

不应用数字格式或单位，不把数字序列自动转日期、百分比或货币。OOXML 的 ISO 日期在源读取器中作为文本保留，统计继续保持文本；数值日期序列仍是数值。需要日期分组或金额口径时先明确列和值的业务含义，本入口没有日期周期分组或单位换算。

## 表头与合并

表头仅在已声明行内按实际合并锚点构造层级，不向普通空白猜填标题；跨表头边界或被截断的表头合并仍拒绝。不可用缓存形成 `unavailable` 标签，不把它伪装为空字符串；所有操作使用物理列 ID。

数据默认 `merge_policy=preserve`：按原保存位置处理，不复制合并锚点；被区域裁切的合并在 `merges` 中标记。非锚点若本来保存了内容，默认仍保留该内容，不宣称视觉显示等价。

显式 `fill_text` 只把所选范围内普通、非公式的文本锚点复制到同一合并区域内缺失/blank 的数据位置。每个填充项记录 `propagated_from`、`original_type` 和 `original_value`。数字、公式标签、范围外锚点及被 `exclude_rows` 排除的锚点不填充；被排除的目标行也不填充。其余非锚点已有内容（包括空文本）时拒绝覆盖。此操作只影响本次派生结果，不改工作簿。

## 操作及统计顺序

顺序为选择范围/表头、显式排除物理行、可选文本填充、值筛选、去重、稳定排序、统计/分组，最后做预览分页。所选矩形包含隐藏和已筛选行；不自动应用源筛选条件或推断隐藏原因。行排除不跳过源解析、所选单元格结构检查或矩形资源限制。

| 选项 | 规则 |
|---|---|
| exclude_rows | 最多 10000 个不重复的整数物理行号；必须位于所选数据行内，不能指向表头、表头前的说明行或范围外；不接受字符串、布尔或行范围表达式；允许排除全部数据行 |
| filters | 多条条件 AND；`eq/ne/lt/le/gt/ge` 要求同一显式 type，类型不匹配或不可用时不匹配（包括 ne）；`contains` 仅文本 |
| 无值条件 | `is_blank`、`is_missing`、`is_unavailable` 只接受 column/op；不会把缺失缓存匹配成普通 blank |
| distinct_by | 按指定列的类型和值保留原次序第一行；不同精度写法但相等的数值同组，文本编号不合并；不可用键行不去重，另计数 |
| sort | 最多八键稳定排序；可用类型升序为 number/text/boolean，降序反转可用项顺序；文本按 Unicode 比较；blank/missing/不可用始终在末尾 |
| select | 仅控制返回行的列与顺序；不会移除其他列的 profiles 或改变筛选/分组/统计范围 |
| group_by | 类型和值组成键；blank 与 missing 分组不同；不可用键行排除出分组并计数，仍可参与其他列的总体统计；组按处理后行首次出现排序 |
| aggregates | 支持 rows、count、count_distinct、sum、mean、median、min、max；别名 as 必须唯一；rows 无 column，其余指定物理列 |

`count/count_distinct` 统计可用且非 blank/missing 的类型化值，包含空文本；数值统计只使用可用 number，文本数字和布尔不参加。没有数值时 sum/mean/median/min/max 为 null，count 为 0。每个聚合附带有效值数、数值数和类型/状态分布。

`scope.excluded_source_rows` 返回排序后的原始行号。`row_counts.input_rows` 是去掉声明表头及前导行后的原数据行数；它等于 `explicitly_excluded_rows + filtered_out_rows + duplicate_rows_removed + matched_rows`。每一步单独计数，排除合计行不会算作值筛选失败；源行号不重新编号。

sum 和 median 在有界 Decimal 精度下精确计算，不经过 float；mean 使用 34 位有效数字、ROUND_HALF_EVEN，并提供 `inexact`/`mean_inexact`。这不是 Excel 二进制运算的逐位复现。源 BIFF 浮点值已由读取器转换为精确 Decimal，统计不主动消除原浮点尾数。

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
| 输出 JSON | 16 MiB；超出时输出失败，收窄预览/选择范围后重做，不能截取输出继续分析 |

原容器、单元格、文字及元数据预算不变。此入口不计算新公式、不联表、不重建透视表、不推断小计，不编辑、导出或写回办公文件；需要这些能力时另行明确交接及验证。

## 可选字节预算与续读

在原命令添加 `--max-output-bytes 24000`，可对 `--preview` 或分析结果按 UTF-8 字节预算分页；512..16777216，包含真实紧凑 JSON 的全部字段、转义与末尾换行。不传此参数和 `--cursor` 时默认 CLI/API、原字段及16MiB序列化上限不变。仅传游标时预算默认12000；预算不是token数。

新模式保留原 schema、partial、源诊断、统计质量报告和分母，另加 `output_page_revision=1`、`has_more`、`coverage` 与 `next_cursor`。`row_page`/`group_page` 另加 `has_more`、`next_offset`；旧 total/offset/returned/truncated 含义保留。`coverage` 对每条结果流声明是否请求、初始 offset 和覆盖范围；偏移之前的行不属于本次输出覆盖，不能据末页声称已经返回整个结果表。`limit=0` 或 `group_limit=0` 表示不请求对应结果流，仍完整计算统计，不产生该流的续读；独立预览仍要求limit为1..50。

原查询的 offset/group_offset 是本次输出起点，limit/group_limit 是**每页上限**。将返回的完整 `next_cursor` 正确引用为 `--cursor` 参数，输入、sheet、range、header-rows、preview模式及查询文件内容保持不变；可以调整字节预算。游标绑定源字节SHA256、完整查询与模式、分页修订号，源或语义参数变化、损坏游标均拒绝；游标不是授权或签名。新模式拒绝所请求结果流超过total的起始offset；等于total可为空页。旧非游标模式的offset行为保持不变。

每次续读仍完整解析源文件、处理整个指定区域，并在分页前完成排除行、筛选、排序、去重、分组与统计。分页只限制返回的 rows/groups，不缩小 profiles、totals、quality_report、缓存纳入/排除分母；不通过重设range来凑预算。仍遵守原200000位置、256列、10000组以及候选预览行数上限，不创建无界全量结果列表。行结果先占预算，组结果可在后续页续读；只有顶层 `has_more=false` 才结束本次请求的全部结果流。

整行/整组是最小输出单元，不切开长单元格。header、query、merges、源诊断、quality_report及统计等必要元数据不分页、不裁剪；放不下时明确返回 `output_view_error`/退出1。没有任何行/组能前进时同样失败，不返回带不前进游标的空成功页。有效预算也约束失败JSON；提高预算、明确减少展示列/每页行数，或使用旧完整输出。不要为减少输出而改变统计口径。预览页的合并anchor_in_page和returned_positions按实际返回行重算，合并定义仍覆盖整个选区。

新增回归入口 `skills/et/scripts/test/test_et_output_budget.py` 验证真实stdout字节、emoji/转义、超长元数据/单行、源与查询变化、末页移除游标、完整续读、partial/质量分母、默认兼容和独立安装。仅本地macOS/Python3.12验证，不宣称Linux/Windows或真实宿主token测量已完成。

## 验证依据

测试入口为 `skills/et/scripts/test/test_et_table_analysis.py` 和 `skills/et/scripts/test/test_et_table_row_selection.py`，覆盖手工指定类型/缓存数据、独立 Fraction 精度对照、合并标签、稳定筛选去重排序、分页、错误输入、BIFF/OOXML 实际解析链和 CLI 输出契约。后者另用含明细、小计、总计、重复表头和缺失值的合成报表核对排除行后的总额、均值、中位数及分组结果。真实原件的维护统计核对及平台结果见 `docs/maintenance/et/table-analysis-evidence.json`。真实源记录一致性不证明公式最新、视觉布局或业务总额正确。

`skills/et/scripts/test/test_et_table_quality_preview.py` 另验证质量分母与位置样本、重复/空/不可用表头、分组覆盖、跨页/裁切合并预览、blank/空文本缓存及 BIFF/OOXML 实际预览链。Schedule 原件核对 48 个非零保存数值（含 12 个缓存）、12 个分组和 10 个预览保存值；Fraction 独立精确统计及整数商余数实现的 34 位 half-even 均值预期在调用目标分析前冻结。其数值包括序号、日期序列与时长，未把它们冒充已填报金额；未验证源公式新鲜度或业务总额。

已按证据中的来源与 SHA-256 单独取得原件后，先自检，再在任务目录运行离线维护核对；文件名可替换成实际相对路径，不因名称相同就接受，核对器必须匹配原始哈希：

```python
execute(command="python -I -S -B \"skills/et/scripts/test/verify_et_analysis.py\" ./Balance_Sheet.ett ./Schedule.ett \"./Start-up Expenses.ett\"")
```

退出 0 且 `status=passed` 只表示本次提供的固定原件与独立冻结统计一致。Start-up Expenses 原件另核对显式排除五行后 23 个数值的位置和统计、合并表头来源及数值锚点不重复。脚本不会下载、保存或重分发原件；未提供的原件不计为通过。这里的选列仅验证保存值运算，不把包含模板合计行的整列相加解释为业务总额；该模板明细列的零值不能代替多样金额或真实填报业务的验收。
