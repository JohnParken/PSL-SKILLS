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
