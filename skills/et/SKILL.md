---
name: et
version: "20261113.2302.5108"
description: 读取金山 WPS 表格（.et/.ett）的保存值与公式缓存，适合查看表头、筛选排序、去重及按指定范围统计汇总。保留文本编号并报告读取缺口与数据质量问题；仅支持部分格式，不重算公式、不修改原表；原生 XLSX/XLSM 使用 xlsx。
---

# WPS 表格（ET/ETT）只读提取与统计

## 执行环境约束与首步自检

**归档展示边界（始终适用）：** 工具回执是内部核验材料，不是最终回复模板；完整或精简回执均不得整段转贴。面向用户的正文、进度、表格、代码块、链接文字及链接目标都不得包含归档路径、目录或任务 ID，也不展示带这些信息的原始警告/错误。不能仅把链接文字改为文件名而仍链接归档副本。

用户归档提示只采用已核对完整状态的 `archive_user_notice` 或等义安全话术，不追加“归档位置”等说明。主文件链接仅使用宿主确认可交付的实际主文件引用；`archive_output`、`files[].archive_output` 及任何归档位置字段不得用作下载地址，也不得据其拼接链接。缺少主文件交付引用时如实说明，不用归档地址代替。

回复前分别核对业务、归档与清理状态，并检查可见文字和链接目标均无内部归档信息。失败、部分成功、跳过、待交接、未知与清理未完成仍须说明实际影响；必要原因用不含路径/ID的自然语言概括，不因隐藏路径省略失败。内部机器字段、完整诊断和原退出码保留供核验，不修改或删除来伪装成功；纯文本和只读任务不新增归档。

**宿主大结果读取：** 宿主返回的 `/large_tool_results/` 引用属于宿主结果路径，仅用宿主提供的 `read_file` 按其真实 schema 读取返回的准确引用，按实际续读能力核对覆盖、结束与截断。不要交给 `execute`、`cat`、Shell 或 Python，也不猜测映射、替代路径或将路径掩码当作真实路径；缺少 `read_file`、读取失败或完整性无法确认时报告具体缺口并停止依赖该结果的操作。这不改变普通业务文件和 Skill 资源原有的读取及路径边界。

脚本由宿主 `execute(command=完整非空命令字符串)` 执行，按实际 schema 提交；包含 stdin 时在同一命令完整提供。替换路径并安全引用参数，不把 `execute(...)` 嵌入命令。执行工具或路径映射不可用时停止并报告；独立调用逐项核对结果，状态未知不盲目重试。

路径占位符说明：`<skill-root>` 是 ET Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 ET 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

宿主路径与执行器：读取工具中的 `file_path` 属于该工具的路径空间；先用它定位和读取 Skill 内部资料。执行脚本前另确认该位置与执行器实际可访问路径的映射，并用执行器路径替换命令中的 `<skill-root>`；只有宿主确认两者一致时才可直接复用。确认执行器的当前目录就是宿主指定的任务目录，且任务输入在该执行器可见；不能确认映射或工作目录时停止并报告，不猜测或切换目录。

**用户文件定位：** 优先附件映射和用户指定文件名。上传文件和待查交付物只定位任务 `./` 顶层，不递归或到子目录查找；无映射仅列顶层候选，歧义询问，无候选或指定位置不在顶层时报告。内部归档、Skill 资源与程序输入安全契约不变。

1. **确认任务工作目录。** `./` 为宿主确认的任务工作目录；在该目录运行，保持不变。优先用本轮上传附件及宿主提供的文件映射确定输入，不要求用户在问题中重复文件名；用户指定文件名时按 `./文件名` 定位；若映射或指定路径不在顶层，报告限制。未指定文件名且本轮只有一个可访问的候选附件时，直接处理该附件；多个附件时结合用户指代和任务类型选择，仍有歧义才列出候选名称并询问。没有附件映射时，只列出 `./` 顶层的候选文档：唯一候选直接处理，多个且无法判定才询问，没有候选则说明未找到可访问文件。不自动递归搜索或到其他目录寻找，不将历史文件优先于本轮附件。CLI 仍须传入解析出的具体文件路径；CLI 的必填参数不构成要求用户手动提供文件名的理由。其他程序输入与受控临时文件沿用该目录内相对路径契约。完整读取默认写 stdout；需要把完整 JSON 留作后续处理时可显式使用 `--output <裸文件名>`，由脚本在任务根目录独占创建文件，拒绝目录、绝对路径、父目录和已存在文件，stdout 只返回小清单。不使用绝对数据路径、父目录或切换挂载绕过边界。宿主附件尚未映射到允许的任务目录时先报告访问缺口，不猜路径。若当前目录是 Skill 源码目录，先由 harness 明确任务目录，不直接生成任务文件。
2. **确定 Skill 真实位置。** 由实际读取本文件的 `file_path` 去掉 `SKILL.md` 确定 `<skill-root>`；内部脚本和资源以它为基准，Markdown 内部引用统一使用以 `<skill-root>` 为根目录的完整路径。执行前替换占位符并正确引用完整路径，不猜测其他会话或掩码路径，不为调用脚本切换到安装目录。
3. **核对真实运行条件。** 使用宿主确认可用的 Python 3.9+，按 requirements.txt（`<skill-root>/requirements.txt`）核对仅需标准库；禁止安装包、运行时联网或调用外部办公软件。保留 `-I -S -B`，首次读取前运行环境自检：

```python
execute(command="python -I -S -B \"<skill-root>/scripts/et_cli.py\" run --check")
```

4. **首次操作前完成自检。** 要求退出 0 且输出 `environment-ok`。OOXML 电子表格分支另需 Expat >=2.6；自检的 `ooxml-sheet=available` 表示此条件满足，`unavailable` 时仅可读取二进制 BIFF8 子集，不尝试 OOXML 解析。自检仅检查基础入口和依赖可用性，不代表任务文件已验收。
5. **按任务需要检查读写。** 确认输入为可读普通文件。仅读取无需写探针；使用 `--output` 或保存分析结果时，在任务目录以独占新文件实际写入、读回并清理/核对，避免覆盖已有文件。自检失败、缺少成功标记或结果不明时停止依赖操作，环境修复后再验，不降低要求。
6. **失败停止依赖操作。** 自检失败、输入文件缺失或读取返回失败时，停止依赖该结果的后续流程，如实报告具体失败项与诊断位置。不得原样反复重试相同命令，不通过轮换参数掩盖错误。
7. **区分自检与交付验收。** 环境可用不代表输入文件格式合法或数据正确提取。交付前仍需核对实际提取结果、退出码、覆盖标记与诊断缺口。
8. **压缩后恢复约束。** 会话上下文若被压缩导致指令不全，重新读取真实 `SKILL.md` 后继续，保留已确认的任务目录与文件路径。

**自检与回传：**`--check` 的成功 stdout 保留 `environment-ok` 和 OOXML 可用性标记，并补充 `check_kind=environment artifact_validation=not_run`；依赖检查失败在 stderr 输出 `environment-failed`，依赖错误退出 1、Python 版本不足退出 2。

维护资源说明：文中 `docs/maintenance/et/` 与 `skills/et/scripts/test/` 均以宿主确认的仓库根目录为基准，仅供源码维护，不随运行包分发；运行任务不读取或执行。没有仓库时跳过维护资料，不按 Skill 安装目录拼接。

用于读取 `.et/.ett` 的工作表、单元格保存值与保存的公式缓存；按实际内容识别 BIFF8 有限子集（CFB 容器内 Workbook/Book 流）或 OOXML 电子表格。原生 XLSX 任务使用对应技能；本技能不生成、编辑或转换办公文件，不恢复页面布局，不执行公式重算、宏、外链或嵌入对象。未知容器和私有格式明确拒绝，有限读取不等于完整 WPS 兼容。

还可对已读出的指定区域做类型明确的筛选、排序、去重、列统计与分组汇总，输出派生 JSON；原始文件及读取结果不变。统计不等于完整格式认证、公式重算或原格式写回。

多份来源计算后填入另附 XLSX 模板时，交由独立 table-workflow 技能编排，ET 仍只读。纯文本或仅 stdout 不归档；文件交接继承提供方已验证的归档状态与用户提示，成功即提示去云空间查看，非成功如实报告，不虚构文件链接或暴露内部归档信息。交接原始完整读取 schema 与 partial/缓存诊断，不能将 et_table_analysis_v1 或预览直接当原工作簿；单表读取与统计继续本入口，不加载跨表流程。

维护CLI或接入跨技能编排时，按需读取 `<skill-root>/scripts/docs/cli-contract.md` 的输入输出、接口版本与兼容策略；普通任务不预读维护契约。回归脚本集中在 `skills/et/scripts/test/`。

## 执行结果判读

每次调用先核对是否执行、真实退出状态和宿主截断说明，再检查完整业务 JSON。工具 `success` 不等于读取成功；Deep Agents 可以合并 stdout/stderr 并附状态文字，不假定两个独立字段或固定分隔符。保留实际可见诊断，不把宿主前后缀当 JSON，不靠截取首尾花括号猜正文。退出码缺失/未知不补成0；未启动、超时或结果不明时停止依赖并核实。

退出3仍读取可靠 JSON 并保留 partial，退出1检查失败诊断，参数错误退出2可能只有 stderr 用法文本，不假造空表。宿主结果引用（如 `/large_tool_results/`）仅用实际可用的 `read_file` 按真实 schema 逐段读取，不交给 Shell 或猜替代路径。区分宿主截断与业务视图尚未取完；如何续读及确认覆盖见 `<skill-root>/references/bounded-reading.md`。完整结果不可取得时明确缺口，不以首屏或概况完成全量分析。

## 电子表格：读取 ET/ETT 保存值

完成共同自检后按任务选择入口，不先输出完整 JSON：

- 表名与规模概况：`<skill-root>/scripts/read_et.py` 加 `--summary`。
- 候选表头与空位：下方 `analyze_et.py --preview` 指定矩形。
- 本 Skill 区域统计：直接使用下方 `analyze_et.py`，不遍历分页或绕经完整 JSON。
- 完整证据或外部原 schema 消费：使用以下默认完整读取。

在任务工作目录运行，将文件名换成实际输入。读取一律使用内容优先的容错策略，不再需要 `--compatibility strict` 等选项；旧参数仅为兼容而接受，会被忽略并记录 `compatibility_option_ignored`。

```python
execute(command="python -I -S -B \"<skill-root>/scripts/et_cli.py\" run ./input.et")
```

只需概况或逐格查看时，读有界输出（`<skill-root>/references/bounded-reading.md`）；其 `wps_read_view_v1` 是投影，不交给原 schema 分析器。分页用于抽查，不先遍历全部页做全量统计；每页仍重读解析输入。需要统计或处理结果表时用下方分析入口；已取得完整 JSON 的程序按电子表格消费（`<skill-root>/references/spreadsheet-consumption.md`）交接，保留源 partial 与诊断。

完整读取的 JSON 默认上限为 64 MiB，可用 `--max-json-bytes`（1 MiB–256 MiB）调整；超过预算时先自动精简一次（省略原始字节证据与重复来源字段，保存值、类型、缓存、合并、诊断不变）再决定成功或明确失败。`--compact` 可主动选择精简输出，`output_projection` 明确原始证据不完整。需要避免把大 JSON 全部回传时，使用 `--output <裸文件名>` 流式写入任务根目录新文件；文件预算默认 256 MiB（包含末尾换行），仍不能绕过上限。stdout 仅返回 `et_output_manifest_v1` 清单（文件名、实际字节数及 SHA-256、状态），不输出截断 JSON。

1. **核对结果。** 退出 0 或 3 均读取完整 JSON；默认完整电子表格 `schema_version` 应为 `biff8_readonly_v1` 或 `ooxml_spreadsheet_readonly_v1`。退出 3 保留已读数据并报告 `issues` 与 `reading_assessment` 中的缺口，不等于失败或必须停止；退出 1 或 2 才停止后续分析，不把失败当空表。
2. **解释保存值。** 按单元格 `type` 解释 `value`，保留数字字符串的精度和文本编号；`numeric_interpretation` 仅补充格式解释，不能替代原值。`formula` 存在时读取的是保存缓存，结合 `cache_status` 区分缺失、错误和未验证缓存，不能保证最新或重算。程序消费和保存值解释见电子表格消费（`<skill-root>/references/spreadsheet-consumption.md`）。
3. **交付或分析。** 提交所需工作表/区域及明确缺口；需要汇总时选定区域和缓存政策，使用下面的只读分析入口。如另有 xlsx 技能，可消费满足其限制的原读取 JSON，仍保留源 partial 和诊断；不要把派生分析 schema 冒充原工作簿。

查询保存行高/列宽、冻结窗格和当前单元格时读 `<skill-root>/references/saved-visibility.md`；解释数字显示时读 `<skill-root>/references/numeric-interpretation.md`。需要富文本、简单公式文字或相关字段概况时读 `<skill-root>/references/advanced-spreadsheet-routing.md#显示与保存视图`；统计始终使用保存值。

### 内容优先的容错读取

读取器以“尽量交付已解析的单元格和内容”为先：容器、记录分帧、非单元格元数据、扩展关系或输出预算的差异，按可恢复程度降级为 `partial` + `issues[]` + 原始证据，而不是丢弃整本结果。安全硬边界仍然保留：不执行宏/公式、不联网、不跟随外链、不解密；无法安全建立基础分配表、加密、超大记录载荷、ZIP 逃逸、DTD/实体和硬资源上限仍拒绝。流级分配链越界、循环或交叉占用会停止该链，不读取问题扇区；若已有可验证前缀则保留并报告覆盖风险，不等于整本数据完整。

- 未知/未实现的全局或工作表记录保留原始字节并报告 coverage 不确定；紧邻的 Continue 一并保留。
- Row/ColInfo/筛选/视图/合并/公式组/DXF/表样式/条件格式/数据验证/图表/绘图/批注/链接/名称等**非单元格元数据**校验失败时，保留原始证据与问题，不中止单元格读取。
- 单条单元格记录损坏时跳过该格并标记 `omissions_affect_cell_values=true`；其它单元格继续交付，不把不确定覆盖说成完整。
- 宏存储、图表/宏/对话框工作表、缺失 Date1904/WsBool、未知 CodePage 等以诊断保留，不执行、不重算、不猜日期系统。
- SST/单元格索引越界等局部值错误跳过受影响值并标记覆盖不确定；加密、无法安全建立解析基础、ZIP/XML 安全禁止项与硬资源上限仍返回失败，保留位置与原因；可恢复的短记录尾部或流链问题保留已验证部分及覆盖风险。
- 图表缓存不作为工作表值。损坏图表只在已分帧的明确恢复点继续；图表/工作表 EOF 归属不明时保留有界证据并标记覆盖不确定。重复 OOXML `sheetData`/行继续读取不同坐标，冲突保留首个可读值并报告，不把恢复结果说成完整。
- BIFF8 已确认的 Note 批注返回在 `sheets[].comments`（单元格/作者/纯文字），HLink/HLinkTooltip 返回在 `sheets[].hyperlinks`（目标只记录不解析、不跟随、不联网）；二者不并入单元格值，也不改变保存值。原始 note 记录、`note_drawings` 诊断和无法解析的链接记录仍作为有界证据保留。BIFF8 `sheet_coverage`/`read_status` 明确未读出的声明工作表；偏移不匹配、重叠或 BOF 无效时不能把 `cell_value_coverage` 报成完整。
- OOXML 保存文本的 `_xHHHH_` 转义只解码一次，`_x005F_` 字面保护不误解为控制字符；共享/内联/富文本与 `t="str"` 的保留文字共用 Unicode 代码点预算，超限返回截断值并标记 `text_truncated`/`text_budget`，不能当完整原值。原始编码证据见 `text_encoding`/`shared_string_encoding`。
- BIFF8 共享公式 `ShrFmla` 的 `cUse=0` 在真实 WPS 原件中按“未指定”处理：仅当范围、锚点、token、重叠和成员标志等其余检查通过时保留组定义，标 `use_count_unspecified` 和 metadata-only 诊断；不把它当正常使用次数，也不取消其他校验。保存缓存和单元格值不受影响，但组表达式语义仍不认证。
- 已知非值元数据（合并、公式组、条件格式/验证、HLink、绘图外 Note 等）损坏只保留 issue 和 `partial`，不单独把 `cell_value_coverage` 降为不确定；未知记录、OfficeArt/对象、值承载记录、跳表、缺 EOF 和文字截断仍按值覆盖保守处理。不能把 `complete` 理解为元数据语义已验证。
- BIFF8 单元格值文字和展示文字使用独立预算：值上限 8Mi UTF-16 单位，自定义格式/批注/链接展示上限 2Mi，格式文本按格式 ID 去重；OOXML 自定义格式也使用独立展示预算并按 numFmtId 去重。展示超限只省略展示字段并附诊断，继续返回全部保存值；`output_text_budget`/`text_budget` 报告两套预算。BIFF8 `RString (0x00D6)` 返回有界纯文字和可选 run 证据，格式 run 异常不回退掉可确定文字。

## 统计与结果表处理

在已确认任务目录执行；分析入口先自检，要求退出 0 且 `environment-ok`，OOXML 仍需可用标记。工作表、矩形区域和表头行由任务确定，不猜测标题或合计行：

```python
execute(command="python -I -S -B \"<skill-root>/scripts/analyze_et.py\" --check")
execute(command="python -I -S -B \"<skill-root>/scripts/analyze_et.py\" ./input.et --sheet \"数据\" --range A1:D8 --preview")
execute(command="python -I -S -B \"<skill-root>/scripts/analyze_et.py\" ./input.et --sheet \"数据\" --range A1:D100 --header-rows 1")
```

尚不确定表头时，先用 `--preview` 查看明确矩形内的物理位置、空白/缺失、保存缓存与合并锚点，再确定表头和数据范围。可用时，预览另附 `display_text` 和 `formula_text`，公式文字来自 OOXML 保存文本或可完整转写的 BIFF8 公式。预览不推断表头、不计算统计、不填充内容；不得将预览 schema 当完整读取结果消费。分页、覆盖和遗漏说明见 `<skill-root>/references/table-preview.md`。

默认返回列统计与前 100 行；统计覆盖整个指定区域的数据行，预览分页不缩小汇总范围。数字用 Decimal，文本编号、布尔、空文本、空白与缺失分别处理；公式默认仅展示保存值并从计算排除，计入缺口。只有明确采用缓存时，才在查询中设置 `formula_policy=cached`；可消费的已保存缓存包括普通未验证值和空文本 `unverified_empty_string`，缓存未证明最新，错误、缺失或未知缓存编码仍排除并报告，不当零。

所选合并范围重叠时，普通统计与预览继续使用物理保存值，省略歧义锚点映射并返回 partial；合并文本填充和受影响的表头重建仍因歧义停止。概况诊断过大时仅 `--summary` 可改为显式 `diagnostic_summary`，保留源风险与省略计数；它不交付完整诊断或单元格。

分析同时返回 `quality_report`：列出数值纳入/排除分母、缺失与缓存状态、有界位置样本、表头标签和分组覆盖。它是事实诊断，不是业务正确性评分；样本截断不缩小统计范围。解释总额前核对质量报告和源 partial，详见 `<skill-root>/references/basic-statistics.md`。

筛选、分组、排序、去重、选列与文本合并标签填充使用 `--query ./query.json`；格式及边界见 `<skill-root>/references/table-analysis.md`。默认不填充合并区域、不自动隐藏或筛掉源行、不识别小计/总计。区域内穿插合计或重复表头时，可用 `exclude_rows` 明确列出原始物理行号；先核对范围和排除项，不凭标签猜测业务口径。XLSX 可传递覆盖不确定的结果与诊断；跨表计算仍保守拒绝，不能伪装成完整数据。结果 `et_table_analysis_v1` 保留源 partial/诊断及分析缺口，分别报告选区诊断和最终匹配数据数量；输出仅 stdout，成功 0、部分结果 3、失败 1、用法错误 2，不写回 ET。

| 按需场景 | 读取参考 |
|---|---|
| 只看表头/矩形保存值与合并 | `<skill-root>/references/table-preview.md`，不预读复杂查询 |
| 基础区域列统计与质量分母 | `<skill-root>/references/basic-statistics.md` |
| 区域筛选、排序、去重、分组汇总及合并文本标签处理 | `<skill-root>/references/table-analysis.md` |
| CodePage、DXF、条件格式、数据验证、批注绘图、分页、表样式、静态名称或 Print_Titles 的读取边界 | 高级电子表格路由（`<skill-root>/references/advanced-spreadsheet-routing.md`） |
| 输出预算、精简投影、完整 JSON 文件输出 | 有界输出（`<skill-root>/references/bounded-reading.md`）与 `<skill-root>/scripts/docs/cli-contract.md` |
| 默认读取已满足任务 | 继续保存值、缓存和缺口核对，不加载高级说明 |

## 结果与按需验收

- 按实际容器和失败阶段说明范围。CFB 是容器，本包能读取其中受支持的 BIFF8 子集；不能把某个 CFB 文件被拒绝概括为“只支持 OOXML”。同样，`.et/.ett` 后缀不证明内部结构或生成软件。
- 仅在解释容器恢复、名称、DXF、条件格式、绘图或验证组诊断时，读取 `<skill-root>/references/advanced-spreadsheet-routing.md#默认读取诊断与容器恢复`；常规保存值任务不预读。
- `status=ok`、退出 0：声明范围内成功，允许空工作表，不等于完整格式认证。
- `status=partial`、退出 3：保留提取结果并说明已知缺口。若 `cell_value_coverage=complete_within_declared_sheets` 且 `omissions_affect_cell_values=false`，继续普通读取、筛选、排序和统计，只需简短说明“保存值已读完，部分格式/元数据未解释”，不因 partial 单独要求用户确认或停止；若 omissions=true、存在未读声明表或缺缓存，才限制依赖完整数据的结论。
- 读取失败退出 1；参数错误退出 2。
- 维护验收与真实样例验证详见 真实 ET/ETT 记录（`docs/maintenance/et/et-real-validation.md`）与 格式说明（`<skill-root>/references/biff8-readonly.md`）。

## 最终交付与读取中间结果

纯读取 stdout、模型分析用 JSON 与调试结果不自动归档。只有用户明确要求交付的文件才列入最终清单；保留输入原件，验收后由本包统一归档入口处理。每次使用宿主当次最新 questionId，缺失报告失败并保留主文件；归档目录及子目录不放临时文件。成功须完整非空清单全部核验通过，才提示“🎉 文件归档成功，请到云空间查看。 ☁️✨”。不复用历史回执，不声称已验证云端上传。

验收用户明确要求的结果文件后，用宿主 execute 调用本包 `"<skill-root>/scripts/archive_delivery.py" 读取结果.json --protect-input 用户原件.et`，参数应替换为本轮实际文件名。批次状态 success/partial/failed/not_required，归档 CLI 分别退出 0/3/1/0，参数错误退出 2；单项 copied/already_archived/failed。仅分析用 JSON 不执行归档。

显式最终导出优先直接调用 `execute(command="python -I -S -B \"<skill-root>/scripts/et_cli.py\" run ./input.et --output 读取结果.json --delivery")`（归档需 Python 3.10+）。`--delivery` 必须与 `--output` 同用，不接受概况/分页或自检；写后核对文件字节数和 SHA-256，再保护原件并归档。stdout 清单包含 `final_deliverables`、`output_written` 和 `delivery`；读取 partial/退出 3 保留，归档状态独立检查。缺失 questionId 或归档失败仍保留已写 JSON，不重跑读取覆盖文件，改用显式归档入口重试。未加 `--delivery` 的 `--output` 仅为内部交接文件，不能把它交给用户后省略归档。分析器 stdout 另存最终文件也须验收后显式归档。
