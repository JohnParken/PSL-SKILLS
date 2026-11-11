---
name: et
version: "20261111.1428.1770"
description: 读取金山 WPS 表格（.et/.ett）的保存值与公式缓存，适合查看表头、筛选排序、去重及按指定范围统计汇总。保留文本编号并报告读取缺口与数据质量问题；仅支持部分格式，不重算公式、不修改原表；原生 XLSX/XLSM 使用 xlsx。
---

# WPS 表格（ET/ETT）只读提取与统计

## 执行环境约束与首步自检

脚本由宿主 `execute(command=完整非空命令字符串)` 执行，按实际 schema 提交；包含 stdin 时在同一命令完整提供。替换路径并安全引用参数，不把 `execute(...)` 嵌入命令。执行工具或路径映射不可用时停止并报告；独立调用逐项核对结果，状态未知不盲目重试。

路径占位符说明：`<skill-root>` 是 ET Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 ET 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

宿主路径与执行器：读取工具中的 `file_path` 属于该工具的路径空间；先用它定位和读取 Skill 内部资料。执行脚本前另确认该位置与执行器实际可访问路径的映射，并用执行器路径替换命令中的 `<skill-root>`；只有宿主确认两者一致时才可直接复用。确认执行器的当前目录就是宿主指定的任务目录，且任务输入在该执行器可见；不能确认映射或工作目录时停止并报告，不猜测或切换目录。

**用户文件定位：** 优先附件映射和用户指定文件名。上传文件和待查交付物只定位任务 `./` 顶层，不递归或到子目录查找；无映射仅列顶层候选，歧义询问，无候选或指定位置不在顶层时报告。内部归档、Skill 资源与程序输入安全契约不变。

1. **确认任务工作目录。** `./` 为宿主确认的任务工作目录；在该目录运行，保持不变。优先用本轮上传附件及宿主提供的文件映射确定输入，不要求用户在问题中重复文件名；用户指定文件名时按 `./文件名` 定位；若映射或指定路径不在顶层，报告限制。未指定文件名且本轮只有一个可访问的候选附件时，直接处理该附件；多个附件时结合用户指代和任务类型选择，仍有歧义才列出候选名称并询问。没有附件映射时，只列出 `./` 顶层的候选文档：唯一候选直接处理，多个且无法判定才询问，没有候选则说明未找到可访问文件。不自动递归搜索或到其他目录寻找，不将历史文件优先于本轮附件。CLI 仍须传入解析出的具体文件路径；CLI 的必填参数不构成要求用户手动提供文件名的理由。其他程序输入与受控临时文件沿用该目录内相对路径契约；本技能只输出 stdout，不新增文件输出参数。确需另存交付 JSON 时仅指定裸文件名并写到任务根目录 `./`，不得接受用户输出目录；不使用绝对数据路径、父目录或切换挂载绕过边界。宿主附件尚未映射到允许的任务目录时先报告访问缺口，不猜路径。若当前目录是 Skill 源码目录，先由 harness 明确任务目录，不直接生成任务文件。
2. **确定 Skill 真实位置。** 由实际读取本文件的 `file_path` 去掉 `SKILL.md` 确定 `<skill-root>`；内部脚本和资源以它为基准，Markdown 内部引用统一使用以 `<skill-root>` 为根目录的完整路径。执行前替换占位符并正确引用完整路径，不猜测其他会话或掩码路径，不为调用脚本切换到安装目录。
3. **核对真实运行条件。** 使用宿主确认可用的 Python 3.9+，按 requirements.txt（`<skill-root>/requirements.txt`）核对仅需标准库；禁止安装包、运行时联网或调用外部办公软件。保留 `-I -S -B`，首次读取前运行环境自检：

```python
execute(command="python -I -S -B \"<skill-root>/scripts/et_cli.py\" run --check")
```

4. **首次操作前完成自检。** 要求退出 0 且输出 `environment-ok`。OOXML 电子表格分支另需 Expat >=2.6；自检的 `ooxml-sheet=available` 表示此条件满足，`unavailable` 时仅可读取二进制 BIFF8 子集，不尝试 OOXML 解析。自检仅检查基础入口和依赖可用性，不代表任务文件已验收。
5. **按任务需要检查读写。** 确认输入为可读普通文件。仅读取无需写探针；保存结果时，在任务目录以独占探针实际写入、读回并清理，避免覆盖已有文件。自检失败、缺少成功标记或结果不明时停止依赖操作，环境修复后再验，不降低要求。
6. **失败停止依赖操作。** 自检失败、输入文件缺失或解析报错时，停止依赖该结果的后续流程，如实报告具体失败项。不得原样反复重试相同命令，不通过轮换参数掩盖错误。
7. **区分自检与交付验收。** 环境可用不代表输入文件格式合法或数据正确提取。交付前仍需核对实际提取结果、退出码与诊断缺口。
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

在任务工作目录运行，将文件名换成实际输入。省略兼容参数时由程序统一使用 `data`，读取前一次性确定。显式指定 `--compatibility strict` 时按严格模式读取。

```python
execute(command="python -I -S -B \"<skill-root>/scripts/et_cli.py\" run ./input.et")
```

只需概况或逐格查看时，读有界输出（`<skill-root>/references/bounded-reading.md`）；其 `wps_read_view_v1` 是投影，不交给原 schema 分析器。分页用于抽查，不先遍历全部页做全量统计；每页仍重读解析输入。需要统计或处理结果表时用下方分析入口；已取得完整 JSON 的程序按电子表格消费（`<skill-root>/references/spreadsheet-consumption.md`）交接，保留源 partial 与诊断。

1. **核对结果。** 退出 0 或 3 均读取完整 JSON；默认完整电子表格 `schema_version` 应为 `biff8_readonly_v1` 或 `ooxml_spreadsheet_readonly_v1`。退出 3 保留已读数据并报告 `issues` 与 `reading_assessment` 中的缺口；退出 1 或 2 停止后续分析，不把失败当空表。
2. **解释保存值。** 按单元格 `type` 解释 `value`，保留数字字符串的精度和文本编号；`numeric_interpretation` 仅补充格式解释，不能替代原值。`formula` 存在时读取的是保存缓存，结合 `cache_status` 区分缺失、错误和未验证缓存，不能保证最新或重算。程序消费和保存值解释见电子表格消费（`<skill-root>/references/spreadsheet-consumption.md`）。
3. **交付或分析。** 提交所需工作表/区域及明确缺口；需要汇总时选定区域和缓存政策，使用下面的只读分析入口。如另有 xlsx 技能，可消费满足其限制的原读取 JSON，仍保留源 partial 和诊断；不要把派生分析 schema 冒充原工作簿。

查询保存行高/列宽、冻结窗格和当前单元格时读 `<skill-root>/references/saved-visibility.md`；解释数字显示时读 `<skill-root>/references/numeric-interpretation.md`。需要富文本、简单公式文字或相关字段概况时读 `<skill-root>/references/advanced-spreadsheet-routing.md#显示与保存视图`；统计始终使用保存值。

默认 data 优先提取已校验的保存值。对边界完整的未知全局或工作表记录，以及无法解释的部分名称和调色板，保留原始记录并返回 partial。未知记录可能携带未识别的值，标记 `omissions_affect_cell_values=true`，交付时说明覆盖不确定。默认 data 对已完整定界的未知 OfficeArt 容器也验证嵌套边界并保留原始证据；受支持的批注分片链中同样适用。内容未解码，覆盖不确定，返回 partial，不能称为完整解析。核心单元格记录、已识别记录组、加密、结构及资源错误仍校验；关联不明或截断时停止。独立 salvage 选项另可容忍部分缺失或未知的声明。

## 统计与结果表处理

在已确认任务目录执行；分析入口先自检，要求退出 0 且 `environment-ok`，OOXML 仍需可用标记。工作表、矩形区域和表头行由任务确定，不猜测标题或合计行：

```python
execute(command="python -I -S -B \"<skill-root>/scripts/analyze_et.py\" --check")
execute(command="python -I -S -B \"<skill-root>/scripts/analyze_et.py\" ./input.et --sheet \"数据\" --range A1:D8 --preview")
execute(command="python -I -S -B \"<skill-root>/scripts/analyze_et.py\" ./input.et --sheet \"数据\" --range A1:D100 --header-rows 1")
```

尚不确定表头时，先用 `--preview` 查看明确矩形内的物理位置、空白/缺失、保存缓存与合并锚点，再确定表头和数据范围。可用时，预览另附 `display_text` 和 `formula_text`，公式文字来自 OOXML 保存文本或可完整转写的 BIFF8 公式。预览不推断表头、不计算统计、不填充内容；不得将预览 schema 当完整读取结果消费。分页、覆盖和遗漏说明见 `<skill-root>/references/table-preview.md`。

默认返回列统计与前 100 行；统计覆盖整个指定区域的数据行，预览分页不缩小汇总范围。数字用 Decimal，文本编号、布尔、空文本、空白与缺失分别处理；公式默认仅展示保存值并从计算排除，计入缺口。只有明确采用缓存时，才在查询中设置 `formula_policy=cached`；缓存未证明最新，错误或缺失缓存仍排除并报告，不当零。

分析同时返回 `quality_report`：列出数值纳入/排除分母、缺失与缓存状态、有界位置样本、表头标签和分组覆盖。它是事实诊断，不是业务正确性评分；样本截断不缩小统计范围。解释总额前核对质量报告和源 partial，详见 `<skill-root>/references/basic-statistics.md`。

筛选、分组、排序、去重、选列与文本合并标签填充使用 `--query ./query.json`；格式及边界见 `<skill-root>/references/table-analysis.md`。默认不填充合并区域、不自动隐藏或筛掉源行、不识别小计/总计。区域内穿插合计或重复表头时，可用 `exclude_rows` 明确列出原始物理行号；先核对范围和排除项，不凭标签猜测业务口径。XLSX 可传递覆盖不确定的结果与诊断；跨表计算仍保守拒绝，不能伪装成完整数据。结果 `et_table_analysis_v1` 保留源 partial/诊断及分析缺口，分别报告选区诊断和最终匹配数据数量；输出仅 stdout，成功 0、部分结果 3、失败 1、用法错误 2，不写回 ET。

| 按需场景 | 读取参考 |
|---|---|
| 只看表头/矩形保存值与合并 | `<skill-root>/references/table-preview.md`，不预读复杂查询 |
| 基础区域列统计与质量分母 | `<skill-root>/references/basic-statistics.md` |
| 区域筛选、排序、去重、分组汇总及合并文本标签处理 | `<skill-root>/references/table-analysis.md` |
| CodePage、DXF、条件格式、数据验证、批注绘图、分页、表样式、静态名称或 Print_Titles 的读取边界 | 高级电子表格路由（`<skill-root>/references/advanced-spreadsheet-routing.md`） |
| 显式最大兼容 salvage、富字符串/缓存/XF 审计 | 显式部分读取（`<skill-root>/references/advanced-spreadsheet-routing.md#biff8-临时最大兼容仅显式低层入口`） |
| 默认读取已满足任务 | 继续保存值、缓存和缺口核对，不加载高级说明 |

## 结果与按需验收

- 按实际容器和失败阶段说明范围。CFB 是容器，本包能读取其中受支持的 BIFF8 子集；不能把某个 CFB 文件被拒绝概括为“只支持 OOXML”。同样，`.et/.ett` 后缀不证明内部结构或生成软件。
- 仅在解释容器恢复、名称、DXF、条件格式、绘图或验证组诊断时，读取 `<skill-root>/references/advanced-spreadsheet-routing.md#默认-data-诊断与容器恢复`；常规保存值任务不预读。
- `status=ok`、退出 0：声明范围内成功，允许空工作表，不等于完整格式认证。
- `status=partial`、退出 3：保留提取结果并说明已知缺口。
- 读取失败退出 1；参数错误退出 2。
- 维护验收与真实样例验证详见 真实 ET/ETT 记录（`docs/maintenance/et/et-real-validation.md`）与 格式说明（`<skill-root>/references/biff8-readonly.md`）。
