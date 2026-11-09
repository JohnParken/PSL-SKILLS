# BIFF8 Name (0x0018)：静态区域与宏/外链的只读边界

维护资源说明：文中 `docs/maintenance/et/` 与 `skills/et/scripts/test/` 均以宿主确认的仓库根目录为基准，仅供源码维护，不随运行包分发；运行任务不读取或执行。没有仓库时跳过维护资料，不按 Skill 安装目录拼接。

路径占位符说明：`<skill-root>` 是 ET Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 ET 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 目标与允许范围

这是独立 ET Skill 的 **BIFF8 `.et/.ett` 只读 Name 兼容子集**。依据 MS-XLS Lbl、XLUnicodeStringNoCch、NameParsedFormula、PtgArea3d、RgceAreaRel 和 XtiIndex 规范，明确区分非宏的普通静态区域、内置打印区域、宏以及外部引用。新增能力在默认 `compatibility=data` 下可用，不需要启用临时 `--salvage-unknown-metadata`；显式 strict/legacy 仍保留原 Name 接受范围，BIFF8 核心安全校验不变。只消费 Workbook 内部保存的字节，不计算名称/公式、不打开外部链接、不执行宏。

区域分支支持 **普通非宏命名区域的单个内部绝对 `PtgArea3d`，或同一内部工作表最多两个绝对 `PtgArea3d` 由唯一 `PtgUnion (0x10)` 连接**：先验证 14 字节 Lbl 头、`fHidden/fFunc/fOB/fProc/fCalcExp/fBuiltin` 等标志为 0、键盘快捷键为 0、保留字段为 0、名称为受限的纯 Unicode 字符串（1–255 字符、合法首字符和安全的字母/数字/下划线/句点后缀）；仅允许 11 字节单个 `PtgArea3d`（`0x3B/0x5B/0x7B`），内部 `ExternSheet` 的 `ixti` 必须指向已被前置检查证实的 self SupBook 之单个工作表，矩形行列与绝对标志合法。名称实际内容只在内存中校验；输出只给名称哈希、作用域、原始 token 和有限区域信息；原始负载仍按已存在的 `uninterpreted_metadata.payload_hex` 机制保存。

**两阶段交叉校验**：Name 可以在 BoundSheet8 列表之前出现，因此读取记录时只验证名称、公式 token 与已出现的内部 ExternSheet 证据，并将 `itab` /区域所属工作表暂存；Globals EOF 时再与完整 BoundSheet8 数量核对，防止提前拒绝真实文档或接受越界索引。每簿最多 256 个此类名称；相同工作簿/工作表作用域下同名（忽略大小写）拒绝。所有实际记录仍受 BIFF 8204/8224 记录长度、1 MiB 原始元数据和总输出预算约束。

保留旧有 **built-in Print_Area (0x06)** 的确切兼容规则，不把任意 built-in name 视为普通名称。新增有限的 **Print_Titles (0x07)** 和 **_FilterDatabase (0x0D)**（见下节），其余内置名称仍拒绝。宏名称、函数名称、`fOB/fProc`、快捷键、未在受控子集中允许的标志、`PtgFunc`、`PtgNameX`、外部 SupBook、跨多个工作表的 XTI、相对单元格范围、复杂公式/附加字节、未支持的名称字符及非法保留字段均**明确拒绝**。此模式不对未知 Name 执行静默 continue 或将其归为普通的 `unknown_biff_record_salvaged`；即使用户启用了临时 salvage 也保持拒绝。

## 扩展：单个内部绝对单元格（仅 data 模式）

普通名称还允许恰好 7 字节的 `PtgRef3d (0x3A/0x5A/0x7A)`：2 字节 XTI + 2 字节行 + 2 字节列，仅接受绝对坐标和 BIFF8 列 0–255。复用名称头、非宏、内部链接、作用域最终核对、重复名称及预算检查；相对坐标、跨表范围、外链、多个单元格 token/联合、额外公式字节仍拒绝，内置名称接受范围不扩大。

返回 `classification=ordinary_static_internal_cell_only`、原始 token 与 `[row,row,col,col]` 坐标；原有区域名称结构不改名，不执行名称求值。真实 Amortization ETT 中 8 个单元格名称及 2 组条件格式在默认 data 下可受限保留；260 个已知单元格记录数独立匹配，35 个保存值（含 1 个未重算数值缓存）独立匹配。其余文本缓存/错误缓存、显示样式与公式正确性未获独立验证，不能由 35 项推为全部值验收。完整证据及复现入口见 `docs/maintenance/et/et-real-validation.md#template-compatibility-iteration-2026-11-03`。

规范依据：MS-XLS PtgRef3d（`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/1ca817be-8df3-4b80-8d35-46b5eb753577`）和 NameParsedFormula 中使用的 RgceLocRel（`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/2db37ba7-32f3-4395-88fe-6646034a5358`）；运行时不联网。

## 扩展：打印标题、筛选区域与双区域联合（仅 data 模式）

- **Print_Titles（`0x07`）**：只接受 `Lbl.fBuiltin=1`、非宏、局部作用域、`cch=1`，且名称字节恰为内置标识 `0x07`；保留原生 `fHidden`（0 或 1）。`NameParsedFormula` 仅允许一个绝对的自引用 `PtgArea3d`，范围必须是完整行（列 0–255）或完整列（行 0–65535）；或两条同一内部工作表区域引用加一个尾部 `PtgUnion (0x10)`，分别对应完整行、完整列。整张表、两个完整行或两个完整列的组合不在本阶段接受范围。
- **_FilterDatabase（`0x0D`）**：同样要求非宏 `Lbl`、局部作用域、合法单个内部 `PtgArea3d`，至少跨两行以区分标题行和数据行；不接受任何联合 token、复杂函数或相对区域。只保存原始区域信息，**不执行筛选、隐藏行或覆盖用户的 `cell.value`**。
- **普通双区域 Name**：仅允许两条同一 `ixti` 和同一内部工作表的完整绝对 `PtgArea3d`，末尾唯一 `PtgUnion`，`cce=23`；不接受第三条区域或其它公式运算符。原有单区域结构字段 `bounds_zero_based` 保持不变；双区域的详细来源在 `regions_zero_based`，并把单矩形字段标成 `null`，防止错误缩并为单个区域。
- **属性级完整验证**：仍核验 `SupBook/ExternSheet` 是已识别的自工作簿内部引用，作用域 `itab` 与最终 BoundSheet8 一致，列高 6 位保留字段与相对引用标志合法；名称、区域和公式 token 数量都受资源预算限制。记录字节原样进入现有 `uninterpreted_metadata.payload_hex`。新增结构标明 `print_settings_applied=false`、`filter_applied=false`、`formula_executed=false`、`external_link_followed=false`；**不能据此宣称已恢复打印页面、实际筛选视图或公式计算结果**。
- **不可放宽**：其他内置 ID（如 Auto_Open/Auto_Close）、宏/函数标志、`PtgNameX` 和外链、任意多 token 公式、未知可选负载、非法布局/重复内置名称继续拒绝；显式 `strict/legacy` 模式与历史 `Print_Area` 的处理不变，显式 salvage 也不绕过这些拒绝。

这三类新行为现阶段主要通过 **14 项合成测试** 验证。现有四份固定 ET/ETT 以及六份可读取的政府 ET 重新审计，均未出现 `Print_Titles`、`_FilterDatabase` 或普通双区域 `PtgUnion` 记录。它们对原有保存值和此前恢复的静态 Name 原件可提供**无回归**证据，但不能作为新增三类名称的真实文件兼容率。新的实际样本应固定原件哈希与独立完整预期，再追加应用级验收。本轮实际 Linux CI 已完成：Python 3.11/3.13 各 **500/500** WPS 回归、严格 Skill 自检通过；四份固定 ET/ETT 的 198 项保存值及 33 个未验证公式缓存保持一致，六份政府原件此前恢复的 126 单元格 Name 个案仍通过。规范子集、具体 CI 编号、零条真实新增 Name 记录的覆盖缺口见机器证据（`docs/maintenance/et/biff8-builtins-union-evidence.json`）。

MS-XLS 规范依据（只用于维护研究，运行时不联网）：`Lbl`（见下文链接）、<https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/665bd564-4677-4ea7-bf6b-89f27edb0788>（AutoFilter / _FilterDatabase）和 <https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/90d54742-4776-4271-a08c-39bd73b7b8fc>（PtgUnion）。

## 真实 BIFF8 生产者示例：Print_Titles 的 PtgMemFunc 包裹

在既有纯 `PtgArea3d + PtgUnion` 子集之外，新增一个有证据支持的**打印标题专用、非执行** token 形态：`PtgMemFunc(0x29/0x49/0x69)` 的两个字节 `cce` 必须恰为 **23**，内层严格为 **两条各 11 字节 `PtgArea3d` + 唯一 1 字节 `PtgUnion(0x10)`**；其后可以没有其他字节，也可恰有一个**不计入 cce** 的显示用 `PtgParen(0x15)`。任何不匹配 `cce`、截断、嵌套、非 Union 运算符、第二个尾随 token 均拒绝；`_FilterDatabase` 和普通名称仍**不接受**此新包裹，也不会启动公式或打印引擎。

独立参考是 ExaDev documents.js 所提供的独立 LibreOffice BIFF8 `.xls` 原始 Print_Titles Lbl 测试向量（`https://github.com/ExaDev/documents.js/blob/main/packages/xls-codec/src/workbook/print-names.test.ts`）。该仓库公开了一条 **47 字节**的真实生产者 `Lbl(0x0018)` 记录（43 字节负载），源文件中的打印标题是 **重复 A 列、重复第 1–2 行**；尾端精确为 `PtgMemFunc cce=23`、两区域 `PtgUnion` 和可选 `PtgParen`。我们的维护测试以这条**独立公开的原始记录字节**构建测试向量，核对绝对区域 `[0,65535,0,0]` 与 `[0,1,0,255]`，并增加 **8 项**恶意长度/尾部/外链/宏语义边界测试。这里只证明 **LibreOffice 所保存 BIFF8 的一类真实记录布局**，**不是**原生 WPS Office `.et/.ett` 文件，也没有整份 LibreOffice 原件的独立完整内容验收。

同时从中山市、汕头市、吉林省财政厅和杭州海事局等官网公告新增采样 **9 份 ET 原件**：每份均获取到实际 CFB/OLE、校验唯一 SHA-256；独立遍历 BIFF8 Globals 后发现这 **9 份全部没有 Name (0x0018)**，所以新增的三类 Name 实际 ET 生产者验证仍待补齐。维护测试只读取公开原件、在 GitHub Actions 中留存原件身份与 Name 固定字段证据，不保留原件、不打印单元格值，生产运行时不联网。来源网址、固定哈希、已观察记录和真实/非真实验收的分界见机器证据（`docs/maintenance/et/biff8-print-title-real-evidence.json`）。

格式依据：MS-XLS PtgMemFunc（`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/e4f8e3b4-0bb7-4100-99ee-1ac53acc9161`）、MS-XLS Rgce（`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/6cdf7d38-d08c-4e56-bd2f-6c82b8da752e`）。

## 真实官方 ET 验收

维护专用探测使用中山市发展和改革局官网下载的原始 `.et`（`https://www.zs.gov.cn/zsfgj/attachment/0/572/572365/2632406.et`），固定 SHA-256 `2ccc6cca516b5936a3330d634536ed3fab71cf762616cf018b5b42ff84161cad`、大小 36864 字节。此前版本在 Workbook record `0x18`、偏移 `13817` 拒绝；该记录负载 50 字节，名称 12 字符（UTF-16）、宏标志全 0、局部表序号 1、`ixti=0`、表达式为 11 字节单个 `0x3B PtgArea3d`，其绝对引用标志有效。源记录负载 SHA-256 `40161c6c8cc4728f9ae56568190a83b2420973f02d997371acdb208981927167`。

新方案在**默认 BIFF8 data 模式**下返回 `partial`，提取 2 个工作表、126 个已识别单元格；独立于目标 BIFF 解析器的维护级记录帧计数器，确认 2 张表分别有 66 和 60 个**已识别单元格记录**，合计 126 个。普通与显式 salvage 结果相同，真实新增接受行为并不依赖跳过未知记录。这仍不构成全量未知记录或 WPS Office 应用级格式语义认证，也不证明名称的最终 Excel 显示或任何公式计算。

GitHub Actions Linux Python 3.11 和 3.13 均已完成 **486/486** WPS 全量回归及严格 Skill 检查；固定来源的四份历史 ET/ETT 样本仍与独立冻结的 **198 项**保存值（其中 33 项未验证时效性的公式缓存）一致。该新增 Name 子集由 **12 项**合成单测覆盖，包括宏、外链和非法名称拒绝；真实政府来源的受限接受证据、记录 SHA、独立 126 单元格记录统计及各项 CI 见机器验证报告（`docs/maintenance/et/biff8-defined-names-evidence.json`）。新代码未修改 `.wps/MS-DOC` 文字读取器，仍不执行公式或外链。

验收入口是仓库根目录下的维护程序 `tests/wps_biff8_salvage_government_probe.py`（真实官方 HTTPS 下载、严格域名白名单、16MiB 上限、固定源哈希；**生产 Skill 运行时不联网**），独立记录数检查 `tests/wps_biff8_name_oracle.py`；单测为 `skills/et/scripts/test/test_wps_biff8_names.py`。维护报告只输出原件哈希、数量、来源、是否安全分类及诊断，**不打印名称正文、表格单元格原值或原始名称公式内容**。

## 使用与限制

仍按主入口 `<skill-root>/SKILL.md#执行环境约束与首步自检` 确认 Linux/Python 标准库、真实目录、输入来源和 `environment-ok`。用户只读 ET 业务任务执行 `python -I -S -B "<skill-root>/scripts/read_et.py" ./input.et`，允许返回 `partial` /退出码 3，并保留 `issues` 与 `reading_assessment`。完整文档、样式、命名公式求值、复杂外链和宏仍不支持。失败停止依赖操作，禁止把未知名称忽略后声称单元格完整。

## 格式依据

- Microsoft MS-XLS Lbl：`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/d148e898-4504-4841-a793-ee85f3ea9eef`
- XLUnicodeStringNoCch：`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/e64abeee-2f3a-4004-b9e3-3d67e29d6066`
- NameParsedFormula：`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/9afd1c6c-c486-4d9c-b644-3708428d2ffb`
- PtgArea3d：`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/869033ad-6304-4b9a-b2ba-1e7794ae345a`
- RgceAreaRel：`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/75afd109-b1ce-4511-b56f-2d63116f6647`
- XtiIndex：`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/9ded1bd9-4881-4b77-b32f-c77b93df03fd`
