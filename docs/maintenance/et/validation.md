# 验证方法、样本证据与未验证范围

维护路径说明：本页以宿主确认的 PSL-SKILLS 仓库根目录为基准；`skills/et/` 是源码目录，`docs/maintenance/et/` 是维护证据目录。先确认仓库位置；不能从独立运行包推断仓库。原记录的测试数量、平台、版本与结论保留历史含义，不代表当前重跑。测试仅在源码树执行，不随精简运行包分发。

## 目录

- 验证范围与方法 (`docs/maintenance/et/validation.md#验证范围与方法`)
- CFB 文件头 FAT 数量为零 (`docs/maintenance/et/validation.md#cfb-文件头-fat-数量为零`)
- BIFF8 定义名称头部兼容 (`docs/maintenance/et/validation.md#biff8-定义名称头部兼容`)
- BIFF8 默认保存值提取 (`docs/maintenance/et/validation.md#biff8-默认保存值提取`)
- BIFF8 有界外链、元数据组与 CFB 补白 (`docs/maintenance/et/validation.md#biff8-有界外链元数据组与-cfb-补白`)
- ET 文件读取的统一代码默认值 (`docs/maintenance/et/validation.md#et-文件读取的统一代码默认值`)
- DXF 增量与可读性验证 (`docs/maintenance/et/validation.md#dxf-增量与可读性验证`)
- 真实 ET 样例验证 (`docs/maintenance/et/validation.md#真实-et-样例验证`)
- 内容优先容错重构验证 (`docs/maintenance/et/validation.md#内容优先容错重构验证`)
- BIFF8 批注与超链接读取 (`docs/maintenance/et/validation.md#biff8-批注与超链接读取)
- 公式字面转义、XF 槽位与覆盖状态 (`docs/maintenance/et/validation.md#公式字面转义xf-槽位与覆盖状态)
- OOXML 文本解码、跳表覆盖与缓存契约 (`docs/maintenance/et/validation.md#ooxml-文本解码跳表覆盖与缓存契约)
- ShrFmla cUse=0 兼容修复 (`docs/maintenance/et/validation.md#shrfmla-cuse0-兼容修复)
- 元数据损坏与值覆盖分离 (`docs/maintenance/et/validation.md#元数据损坏与值覆盖分离)
- 展示预算拆分与 RString 读取 (`docs/maintenance/et/validation.md#展示预算拆分与-rstring-读取)
- 可复现的本地检查 (`docs/maintenance/et/validation.md#可复现的本地检查`)
- 样本与未验证边界 (`docs/maintenance/et/validation.md#样本与未验证边界`)
- Pylint 合规维护 (`docs/maintenance/et/validation.md#pylint-合规维护`)
- 历史证据 (`docs/maintenance/et/validation.md#历史证据`)

## 验证范围与方法

本技能面向金山 WPS 兼容电子表格（.et/.ett）及其底层 BIFF8 二进制与 OOXML 电子表格子集，构建了包含格式解析、DXF 样式、单元格精度、公式缓存、合并单元格与分页视图的完备测试矩阵。

## CFB 文件头 FAT 数量为零

ET 版本 `20261110.1448.1878`。用独立生成的 CFB v3、v4 合成工作簿将文件头 FAT 数量置零，与未修改样本比较工作表结果，并核对 A1 的已知保存值。默认入口返回 partial、退出 3，保留原始及推导数量。覆盖扩展 DIFAT、缺失或异常索引、错误 FAT 标记、目录链循环、不支持的工作簿流，以及显式 strict 和 legacy 拒绝。

macOS arm64 / Python 3.12.12 上环境自检、485 项 ET 回归、11 项独立运行包验收、项目文档检查及 `git diff --check` 通过。源码严格检查因既有缓存目录和文件告警失败，独立运行包严格检查通过。当前解释器无 Pylint。未验收用户原件、云端 Linux、Windows 或宿主模型触发。

## BIFF8 定义名称头部兼容

ET 版本 `20261110.1535.1379`。对照 xlrd 的保存值读取和 MS-XLS 的 Lbl 字段定义，仅在默认 data 模式放宽已支持静态名称的隐藏位和保留字段。合成 CFB/BIFF8 工作簿按字段变异，与未修改样本逐表比较单元格及缓存。检查 partial、原始记录和偏移诊断，并验证带隐藏位的打印区域、显式模式拒绝、宏标志、快捷键、非法引用、作用域、重复名称和截断的行为。未引入 xlrd 运行依赖。

macOS arm64 / Python 3.12.12 上环境自检、487 项 ET 回归、11 项独立运行包验收、项目文档检查及 `git diff --check` 通过。源码严格检查仍有 139 条既有缓存告警，独立运行包严格检查通过。当前解释器缺少 Pylint。未验收用户原件、云端 Linux、Windows 或宿主模型触发。

## BIFF8 默认保存值提取

ET 版本 `20261110.1548.5435`。默认文件入口在完整分帧后保留有界未知记录、无法解释的名称及调色板，并提取已校验单元格。合成样本核对全局与工作表记录偏移、宏标志名称及非法打印区域、保存值和缓存、退出 3 及覆盖不确定标记。显式严格模式、非法核心单元格、加密、续段及资源限额仍按各自契约拒绝。低层字节解析的默认验证行为保留。

macOS arm64 / Python 3.12.12 上环境自检、488 项 ET 回归、11 项独立运行包验收、13 项 XLSX 交接回归、177 项跨表工作流回归、文档检查及 `git diff --check` 通过。源码严格检查仍有 139 条既有缓存告警，独立运行包严格检查通过。Pylint 缺失，未验收用户原件、云端 Linux、Windows 或真实宿主触发。

## BIFF8 有界外链、元数据组与 CFB 补白

ET 版本 `20261110.1557.1932`。合成混合链接记录核对 SupBook/XTI 原位置、续段的精确长度、内部名称绑定及公式保存缓存，覆盖外部缓存与单元格缓存值不同、缓存缺失、索引越界和续段缺损。嵌入图表、条件格式和数据验证分别覆盖可隔离的载荷异常、整组缺损及后续核心数据失败。CFB v3/v4 的全零短尾缀在完整分配链和流声明长度校验后忽略，非零尾缀、扇区截断、循环及声明不符继续拒绝。结构通过的只读兼容结果仍使用既有 partial 和覆盖枚举。

macOS arm64 / Python 3.12.12 上环境自检、494 项 ET 回归、11 项通用打包安全测试、12 项独立运行包验收、13 项 XLSX 交接、177 项跨表工作流回归及项目文档检查通过。组合样本用只读 ET 安装包从独立任务目录实际运行，退出 3，返回数字保存值和三类诊断，源文件字节一致。源码严格检查仍有 139 条既有缓存告警，独立运行包严格检查通过。Pylint 缺失，未验收用户原件、云端 Linux、Windows 或真实宿主触发。

## ET 文件读取的统一代码默认值

ET 版本 `20261110.1659.3016`。文件 API、读取 CLI 和 read/metadata 协议共用代码默认值 `data`，分析入口继承文件 API 的默认行为。用偏移 15875 的合成样本验证，省略模式或显式 data 都返回 partial（退出 3）及 7 个单元格，显式 strict 在该位置返回失败（退出 1）。macOS arm64 / Python 3.12.12 上环境自检、495 项 ET 回归、12 项独立运行包验收、源码严格检查及项目文档检查通过。Pylint 缺失，云端 Linux 和真实 WPS 样本未验收。

## 保存值显示、行列尺寸与预览公式文本

ET 版本 `20261110.1720.5461`。合成 BIFF8 样本经 CFB 文件入口读取，验证 `0.1` 的精确保存值和简短显示文本可同时返回，RK 缩放值 `0.01` 保持精确。行列尺寸测试覆盖 BIFF8 的原始单位、隐藏标记和无法解释的行高，以及 OOXML 显式尺寸、缺失尺寸、异常尺寸与单元格覆盖。OOXML 共享公式样本的起始单元格同时返回保存公式文本和缓存，从属单元格保留自身缓存。

macOS arm64 / Python 3.12.12 / Expat 2.6.3 上环境自检、497 项 ET 回归、12 项独立运行包验收、源码严格检查、项目文档检查及 `git diff --check` 通过。Pylint 模块缺失。云端 Linux 和本次新增字段的真实 WPS/Excel 样本未验收。

## 保存的冻结窗格与当前单元格

ET 版本 `20261110.1728.1354`。9 项新回归使用合成 BIFF8 CFB 和 OOXML Strict/Transitional 包，经完整读取 API 验证冻结行列计数、普通分割、活动窗格选区、多窗口、缺失及异常视图。预览测试核对原始单元格仍可读取且不推断表头。BIFF8 另验证可隔离的选区异常保留值覆盖，异常 Pane 长度和核心记录仍失败。

macOS arm64 / Python 3.12.12 / Expat 2.6.3 上环境自检、506 项 ET 回归、12 项独立运行包验收、11 项打包安全测试、3 项仓库 ET 流程测试、源码严格检查、项目文档检查及 `git diff --check` 通过。Pylint 模块缺失。云端 Linux 和真实 WPS/Excel 的冻结窗格与选区样本未验收。

## BIFF8 数字格式继承与常见显示格式

ET 版本 `20261110.1734.1851`。6 项新增回归覆盖继承日期、直接覆盖、父 XF 无效或与保存格式冲突、保存公式缓存、工作簿声明的人民币负数格式，以及科学计数进位、有界分数、位置段颜色和字面量单位。OOXML 样本同时验证内置科学计数与原 Decimal。继承证据参照 Microsoft 的 XF、CellXF 与 StyleXF 记录定义，并仅在父子保存格式一致时报告已解析。

macOS arm64 / Python 3.12.12 / Expat 2.6.3 上环境自检、512 项 ET 回归、12 项独立运行包验收、3 项仓库 ET 流程测试、源码严格检查、项目文档检查及 `git diff --check` 通过。Pylint 模块缺失。云端 Linux、真实 WPS/Excel 文件和客户端实际显示未验收。

## 实际样本日期、富文本、公式文本与条件格式

ET 版本 `20261110.1748.2190`。8 项新增回归覆盖可选小数、位置数字段、显式日期、BIFF8/OOXML 富文本、BIFF8 独立公式及常量比较和单独填充。合成公式的保存缓存特意与表达式不同，核对输出继续使用缓存值。

经 GitHub 按固定提交取得 `docs/maintenance/et/et-real-samples.json` 的四份公开原件，核对长度和 SHA-256 后使用默认读取 API 与 JSON 序列化校验。四份均返回 `partial`，共读取 5036 个单元格。`real-value-expectations.json` 中 198 个预先固定的保存值（含 33 个公式缓存）全部匹配。Schedule.ett 的 24 个 `m\/d\/yyyy` 日期显示文本逐格匹配按保存序列值和明确年月日计算的独立预期。两份有公式的原件共返回 33 个可读表达式；逐格独立对照覆盖缓存值。四份原件均没有富 SST 条目，富文本及填充解释使用合成测试。

macOS arm64 / Python 3.12.12 / Expat 2.6.3 上环境自检、520 项 ET 回归、12 项独立运行包验收、11 项打包安全测试、3 项仓库 ET 流程测试、源码严格检查、项目文档检查及 `git diff --check` 通过。Pylint 模块缺失。云端 Linux、已确认由 Excel 保存的原件及 Office/WPS 客户端显示未验收。两份中山政府原件的下载域名在本次执行环境不可解析，历史证据保留。

## DXF 增量与可读性验证

以合成记录验证所选属性的长度、枚举、UTF-16、分布对齐依赖、原始保留、partial 与分层错误。7 类有界格式问题均保留 18 个单元格和 5 个公式缓存，逐项数据/样式表/合并范围比较通过，退出 3/partial。属性长度问题仅在外层和 XFProp 记录边界完整时降级，不读取越界字节；真正记录损坏、未知 BIFF 数据、加密和资源限制仍拒绝。

## 真实 ET 样例验证

针对真实公开的 ET/ETT 样本，测试已验证保存值、公式缓存和工作表结构，详见 真实 ET/ETT 记录（`docs/maintenance/et/et-real-validation.md`）与 政府公开 ET 验证（`docs/maintenance/et/biff8-gov-real-et-validation.md`）。

## 内容优先容错重构验证

ET 版本 `20261111.1759.5400`。本轮把文件 API、读取 CLI、read/metadata 协议和 BIFF8/OOXML 分支统一为内容优先的容错读取：容器头差异与尾缀、记录分帧尾、未知/未实现记录、非单元格元数据、扩展关系、单条坏单元格和输出预算问题降级为 `partial` + `issues[]` + 原始证据；加密、记录帧无法继续（负载 > 8224 字节）、FAT/MiniFAT 链冲突与越界、ZIP/XML 安全禁止项、输入变化和资源耗尽仍拒绝。旧 `--compatibility`、`--salvage-unknown-metadata`、`--chart-policy` 仅为不改变行为的兼容参数，结果附 `compatibility_option_ignored` 或 `salvage_option_ignored`。

- **CFB**：头 CLSID、minor、保留字节、v4 填充、尾缀、未引用分配、不可达目录项、非法流名降级为 issue；用户流上限由 8 MiB 提高到 32 MiB；多 Workbook/Book 根流选首条通过 BIFF8 globals BOF 校验的候选并记 issue。
- **BIFF8**：未知记录与有界 Continue 保留原始证据；非单元格元数据 fatal 改为 partial；Row/ColInfo 使用独立结构化预算；单条坏单元格跳格并置 `omissions_affect_cell_values=true`；XF 索引无效回退样式 0；宏存储只读不加载；宏/图表/对话框子表跳过；缺 Date1904/WsBool 与未知 CodePage 容错；`0x00EC` 等绘图分片按声明长度拼接；诊断超限截断并置 `issues_truncated`。实现拆分为 `wps_biff8_globals.py`、`wps_biff8_sheet.py`、`wps_biff8_cells.py`、`wps_biff8_result.py`、`wps_biff8_drawing.py`、`wps_biff8_retain.py`。
- **OOXML**：允许 `mc/xr/x14/x14ac` 外来属性与元素；未知工作簿/工作表关系只记录不读取；外部目标不跟随；未知工作表元素 issue 后继续；诊断按 code 聚合；SST 提高到 200000、合并 65536、XML 共享节点/元素预算提升；模板主类型接受；单格容错。值承载解析拆分为 `wps_ooxml_worksheet.py`。
- **输出**：完整 JSON 默认 64 MiB，`--max-json-bytes` 接受 1–256 MiB；超预算先自动精简一次并附 `output_auto_compacted`；`--compact` 主动精简；`--output <裸文件名>` 独占创建任务根目录新文件并只返回 `et_output_manifest_v1`，从不输出截断 JSON；`metadata` 不再全量序列化；读取视图默认 `max-output-chars=65536`。

以下结果由本轮重构维护会话报告（实际执行日志未随文档交接，平台与解释器未逐项记录，因此不作为已独立复跑的验收）：

- ET 全量 `skills/et/scripts/test/` 下 `test_*.py` **570 项通过**。
- 四份固定真实 ET/ETT 的 **198 个冻结保存值**一致；Schedule.ett 现在默认返回 partial，**343 个单元格 / 12 个公式缓存 / 1 个图表**，不再需要 chart 或 compatibility 参数。
- **2 份真实 Excel-97 零尾 BIFF8 原件**由拒绝变为 partial 可读（历史记录中它们此前无法读取）。
- 仓库内 **5 份真实 xlsx 资产**全部 partial 可读：420 / 26546 / 401035 / 68495 / 1399 个单元格；真实 WPS 原生 OOXML `.et` 仍未验证。
- **3 份真实 ET** 默认 `--summary` 可用。

未验证或未执行：云端 Linux 容器、真实 WPS 原生 OOXML `.et` 的语义对照、Pylint、Windows/macOS 客户端视觉验收；本轮未重新运行仓库打包测试与独立运行包安装验收。本轮验证命令与历史记录分开保留，不改变上文各历史节的平台/数量含义。

## 可复现的本地检查

先完成 入口自检 (`skills/et/SKILL.md#执行环境约束与首步自检`)，替换 `skills/et`，在宿主确认的任务目录运行：

```console
python -I -S -B "skills/et/scripts/read_et.py" --check
python -I -S -B -m unittest discover -s "skills/et/scripts/test" -p "test_*.py"
```

自检要求退出 0 和 `environment-ok`，并单独确认 OOXML 所需 Expat 可用；测试要求全部通过。

## 样本与未验证边界

本技能支持的是明确声明的 BIFF8 与 OOXML 电子表格子集，未知私有宏、VBA 脚本、私有加密格式明确拒绝，不提供公式重算引擎，不将提取等同于完整专有格式认证。

## Pylint 合规维护

2026-11-09 在 macOS arm64、Python 3.12.12 和 Expat 2.6.3 上维护源码。Pylint 4.1.3 和 Astroid 4.3.4 仅在独占维护目录使用。从仓库根运行 `python -B -m pylint --max-line-length=200 --output-format=json2 --reports=n`，将 `rg --files skills/et -g '*.py'` 列出的运行、测试和维护脚本作为文件参数；检查环境可导入 `skills/et/scripts` 和 `skills/et/scripts/test`。未禁用默认诊断。

删除了 ET 无调用方的 Word 文字解析分支和对 WPS 文字模块的导入，保留 OOXML 表格使用的 ZIP/XML 校验。BIFF8 记录分帧和白名单迁入 `wps_biff8_records.py`，表头适配归入 `wps_biff8_headers.py`。原 `records`、`bof`、读取和表头入口继续可用，记录数和单元格上限仍由读取器传入。运行清单明确纳入新模块。其余排版逐文件核对去除位置后的 AST，测试方法改名保持原断言。人工核对 ET 读取、汇总、禁止写回和公式缓存的路由，description 无需调整。

- 扫描范围由 94 个源码文件增至 95 个。Error/Fatal 由 3/0 降至 0/0，C0301 由 49 降至 0，C0321 由 454 降至 0，C0103 由 2 降至 0，W1514 始终为 0。全量复扫退出 28，剩余 1334 条 Convention、149 条 Warning 和 124 条 Refactor，主要涉及文档字符串、隔离启动的导入位置、测试导入、子进程写法和函数复杂度。补跑 `--disable=all --enable=E,F,C0103,C0301,C0321,W1514 --reports=n --score=n` 定点检查，退出 0。
- 隔离环境自检退出 0，返回 `environment-ok` 及 `ooxml-sheet=available`。`python -I -S -B -m unittest discover -s skills/et/scripts/test -p 'test_*.py'` 通过 483 项。仓库级 ET 维护探针 5 项、通用打包安全 11 项、独立运行包 11 项、table-workflow 177 项均通过。另在只读 ET 临时安装包内运行 BIFF8 CLI，核对文本编号、表头和输入字节，退出 0。
- 源码树严格检查因既有 `__pycache__` 告警退出 1。排除缓存的源码副本及独立包通过严格检查。项目文档检查和 `git diff --check` 通过。历史发布 ZIP 保留，临时打包仅用于验收。Linux、Windows、真实业务文件、WPS/Excel 客户端和宿主选路未复验。

## 历史证据

机器可读证据见 增强证据清单 (`docs/maintenance/et/reading-enhancement-evidence.json`)。

## BIFF8 批注与超链接读取

ET 版本 `20261112.1338.2600`。合成 note 链（TxO 分片、编码切换、代理对、作者长度/尾字节、对象与单元格链接）与 HLink/HLinkTooltip（internal、external string moniker、opaque OLE moniker、tooltip 匹配/孤儿、截断和非法 Ref8U）经完整读取与低层 API 验证：`sheets[].comments` 返回单元格/作者/纯文字，`sheets[].hyperlinks` 返回 ref/display/target/location/tooltip 且 `target_resolved=false`、`followed=false`、`link_executed=false`；坏记录保留 raw 与 issue，后续单元格继续读取，保存值不变。HLink 目标从不解析或跟随。

macOS 27.2.1 arm64 / Python 3.14.10 / Expat 2.7.4 上 ET 603 项回归通过；check_skill 严格检查、项目文档检查、runtime packager、独立运行包、ET 维护探针、执行示例和跨表 pipeline 回归通过；4 份固定 ET/ETT 的 198 个冻结保存值通过；两份真实 Excel-97 零尾 BIFF8 保持 partial。Pylint 未执行（解释器无 pylint，未安装）。无真实 WPS 批注/超链接原件，客户端显示、目标解析、Linux/Windows 和真实宿主未验收。详见 [维护记录](comments-hyperlinks-20261112.md)。

## 公式字面转义、XF 槽位与覆盖状态

本轮修复三个已复现问题：BIFF8 PtgStr 字符串字面量此前不返回 `formula_text` 且没有内部双引号转义；malformed XF 记录不保留索引槽，导致后续 XF 引用前移并可能静默取错数字格式；BIFF/OOXML 的覆盖状态从可被截断的诊断列表反推，诊断达到上限时会误报 `complete_within_declared_sheets`。现在公式字符串以 Excel 字面量语法加引号并双写内部引号；坏 XF 保留 `valid=false` 占位槽、坏槽引用保留值并标记 `xf_slot_invalid`；缺失公式缓存与覆盖状态使用显式解析状态，`issues_truncated=true` 时仍保持 `missing_formula_caches`/omissions。

合成回归使用 `MAX_ISSUES=1`（BIFF）和 `MAX_ISSUE_CODES=1`（OOXML）复现截断耦合，并用 malformed XF + 后续可区分数字格式复现静默错配。macOS 27.2.1 / Python 3.14.10 / Expat 2.7.4 上 ET 607 项回归通过；check_skill 严格检查、项目文档检查、runtime packager、独立运行包、维护探针、执行示例和跨表 pipeline 通过；4 份固定 ET/ETT 的 198 个冻结保存值、2 份零尾 BIFF8 与 5 份真实 xlsx 保持既有结果。无真实 WPS 公式字符串/坏 XF 原件对照；Pylint 未执行（解释器无 pylint）。详见 [维护记录](coverage-escaping-xf-20261112.md)。

## OOXML 文本解码、跳表覆盖与缓存契约

外部复核报告的三类正确性问题均先复现后修复：OOXML `_xHHHH_` 保存文本此前未解码，正常 XlsxWriter 3.2.9 文件中的回车/控制字符和字面保护序列被读错；BIFF8 BoundSheet8 偏移不匹配或工作表 BOF 无效时仍可能报告完整覆盖；空文本公式缓存 `unverified_empty_string` 在统计 `formula_policy=cached` 下被误判为未知状态。同时确认内联字符串未计费、共享字符串分支忽略预算返回值。

修复后 OOXML 共享/内联/富文本/`t="str"` 统一单次解码，`_x005F_` 字面保护、代理对、只解码一次均有回归；单元格 `text_encoding` 和工作簿 `shared_string_encoding` 保留原始编码证据；保留文字按 Unicode 代码点统一预算，超限返回前缀并标记 `text_truncated`/`text_budget`。BIFF8 增加 `read_status`/`sheet_coverage`，跳表路径显式设置 omissions。统计与预览接受 `unverified_empty_string`，仍区分缺失/错误/未知缓存。

macOS 27.2.1 / Python 3.14.10 / Expat 2.7.4 上 ET 621 项回归通过；独立 XlsxWriter 3.2.9 样本复现修复前后差异；实际 8 Mi 边界样本（260 个 32767 字符内联单元格）保留恰为 8,388,608 个字符并标记限制/覆盖不确定；4 份固定 ET/ETT 的 198 个冻结保存值、2 份零尾 BIFF8、5 份真实 xlsx、check_skill 严格检查、项目文档检查、运行包/维护探针/跨表 pipeline 均通过。复核环境为 Python 3.12.14/Expat 2.8.4，与本机不同；固定 GitHub 原始来源审计 4 份/198 值/33 缓存通过；多政府站点扩展语料下载受阻，未新增真实来源；GitHub Actions 工作流已扩展但本机无 Linux 执行能力，未声称运行通过；无真实 WPS 原生 OOXML 语义对照，性能建议未测量，Pylint 未执行（解释器无 pylint）。详见 [维护记录](reading-correctness-20261112.md)。

## ShrFmla cUse=0 兼容修复

Edge 扩样中的宁夏资金分配表公开 ET 原件存在两条 `ShrFmla` 声明 `cUse=0`，但范围内有 6/32 个共享公式成员。修复前组定义被当作无效 raw salvage，产生 38 条成员 mismatch，`cell_value_coverage` 为不确定。修复后仅在范围、锚点、token 长度、重叠和成员标志等其余结构检查通过时保留组定义，标 `use_count_unspecified=true`，按范围与共享标志关联成员，以 metadata-only `formula_group_use_count_unspecified` 报告声明次数未指定；不把 0 当正常值。

该真实原件（SHA-256 `738c9cdf844424e97328e8bc5957ee41c827c38fb1fb3153c0f635e17d8db1ff`）修复后仍为 447 个保存单元格、211 个非空值、79 个公式缓存，`cell_value_coverage=complete_within_declared_sheets`、`omissions_affect_cell_values=false`；保存值/缓存未改变。组表达式、使用次数语义、缓存新鲜度和 MS-XLS 合规性仍未认证。

macOS 27.2.1 / Python 3.14.10 / Expat 2.7.4 上 ET 624 项回归通过（含 3 项 cUse=0/真实定义改编回归）；check_skill、项目文档、运行包和真实样本检查最终复跑结果见维护记录。无其他 WPS 版本/生产者 cUse=0 证据，未执行 Linux、客户端视觉或 Pylint。详见 [维护记录](shared-formula-zero-use-20261112.md)。

## 元数据损坏与值覆盖分离

已知非值元数据损坏不再单独把 `cell_value_coverage` 降为不确定：合并、共享/数组公式组定义、条件格式/验证组、孤立规则、绘图外 Note、HLink/HLinkTooltip，以及 OOXML 合并数量上限，使用 metadata-only salvage 或不再设置 uncertain。保存单元格未被跳过时值覆盖可保持完整，状态仍因 issue 为 partial。

未知 BIFF 记录、未知 OfficeArt/对象、损坏图表、嵌套子流、SST/单元格/公式缓存等值承载记录、跳表、缺 EOF 和文字截断仍保守标记值覆盖不确定。新增 `test_et_metadata_value_coverage.py` 8 项回归覆盖 BIFF8 公式组/合并/条件格式/Note/HLink、OOXML 合并上限及未知记录不静默放宽。

macOS 27.2.1 / Python 3.14.10 / Expat 2.7.4 上 ET 632 项回归通过；Edge 8 份公开 ET 重读保存格数全部匹配且 8/8 值覆盖完整、omissions=false；固定 4 份 ET/ETT 的 198 冻结值/33 缓存未变。无专门包含这些元数据损坏的真实 WPS 原件对照，未执行 Linux、客户端视觉或 Pylint。详见 [维护记录](metadata-value-coverage-20261112.md)。

## 展示预算拆分与 RString 读取

复现 32,897 个 Number 单元格共用 255 字符自定义格式时，旧实现把每格格式文本计入 8Mi 值文字预算，抛出 `resource_limit` 并无提取结果。修复后值文字和展示文字使用独立预算：值 8Mi UTF-16 单位，展示 2Mi，格式按 number-format id 去重；展示超限只省略展示字段和格式文本并附诊断，保存值全部返回。合成相同规模实测 32,897 格全部返回、`display_units=255`、无失败。

同时补 BIFF8 `RString (0x00D6)` 纯文字和有界 rich-run 读取：run 异常保留已确定文字并标 metadata-only，文字本体损坏才值遗漏。新增 11 项回归；macOS 27.2.1 / Python 3.14.10 / Expat 2.7.4 上 ET 643 项通过。BIFF8 展示预算/ RString 与 OOXML 自定义格式展示预算均已用合成回归验证；没有真实 WPS RString 原件或真实超大格式文件，真正按表/范围按需读取和旧 BIFF 版本仍待后续。详见 [维护记录](display-budget-rstring-20261112.md)。
