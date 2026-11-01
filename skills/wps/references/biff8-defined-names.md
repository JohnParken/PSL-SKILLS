# BIFF8 Name (0x0018)：静态区域与宏/外链的只读边界

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 目标与允许范围

这是现有 WPS 文字 Skill 下独立的 **BIFF8 `.et/.ett` 只读 Name 兼容子集**。依据 MS-XLS Lbl、XLUnicodeStringNoCch、NameParsedFormula、PtgArea3d、RgceAreaRel 和 XtiIndex 规范，明确区分非宏的普通静态区域、内置打印区域、宏以及外部引用。新增能力在默认 `compatibility=data` 下可用，不需要启用临时 `--salvage-unknown-metadata`；显式 strict/legacy 仍保留原 Name 接受范围，BIFF8 核心安全校验不变。只消费 Workbook 内部保存的字节，不计算名称/公式、不打开外部链接、不执行宏。

当前明确支持 **普通非宏命名区域的单个内部绝对 `PtgArea3d`**：先验证 14 字节 Lbl 头、`fHidden/fFunc/fOB/fProc/fCalcExp/fBuiltin` 等标志为 0、键盘快捷键为 0、保留字段为 0、名称为受限的纯 Unicode 字符串（1–255 字符、合法首字符和安全的字母/数字/下划线/句点后缀）；仅允许 11 字节单个 `PtgArea3d`（`0x3B/0x5B/0x7B`），内部 `ExternSheet` 的 `ixti` 必须指向已被前置检查证实的 self SupBook 之单个工作表，矩形行列与绝对标志合法。名称实际内容只在内存中校验；输出只给名称哈希、作用域、原始 token 和有限区域信息；原始负载仍按已存在的 `uninterpreted_metadata.payload_hex` 机制保存。

**两阶段交叉校验**：Name 可以在 BoundSheet8 列表之前出现，因此读取记录时只验证名称、公式 token 与已出现的内部 ExternSheet 证据，并将 `itab` /区域所属工作表暂存；Globals EOF 时再与完整 BoundSheet8 数量核对，防止提前拒绝真实文档或接受越界索引。每簿最多 256 个此类名称；相同工作簿/工作表作用域下同名（忽略大小写）拒绝。所有实际记录仍受 BIFF 8204/8224 记录长度、1 MiB 原始元数据和总输出预算约束。

保留旧有 **built-in Print_Area (0x06)** 的确切兼容规则，不把任意 built-in name 视为普通名称。`Print_Titles`、`_FilterDatabase` 或其他内置命名区域在完成独立规则验证之前依然不自动扩大。宏名称、函数名称、`fOB/fProc`、快捷键、非零/未知标志、`PtgFunc`、`PtgNameX`、外部 SupBook、跨多个工作表的 XTI、相对单元格范围、复杂公式/附加字节、未支持的名称字符及非法保留字段均**明确拒绝**。此模式不对未知 Name 执行静默 continue 或将其归为普通的 `unknown_biff_record_salvaged`；即使用户启用了临时 salvage 也保持拒绝。

## 真实官方 ET 验收

维护专用探测使用中山市发展和改革局官网下载的原始 `.et`（`https://www.zs.gov.cn/zsfgj/attachment/0/572/572365/2632406.et`），固定 SHA-256 `2ccc6cca516b5936a3330d634536ed3fab71cf762616cf018b5b42ff84161cad`、大小 36864 字节。此前版本在 Workbook record `0x18`、偏移 `13817` 拒绝；该记录负载 50 字节，名称 12 字符（UTF-16）、宏标志全 0、局部表序号 1、`ixti=0`、表达式为 11 字节单个 `0x3B PtgArea3d`，其绝对引用标志有效。源记录负载 SHA-256 `40161c6c8cc4728f9ae56568190a83b2420973f02d997371acdb208981927167`。

新方案在**默认 BIFF8 data 模式**下返回 `partial`，提取 2 个工作表、126 个已识别单元格；独立于目标 BIFF 解析器的维护级记录帧计数器，确认 2 张表分别有 66 和 60 个**已识别单元格记录**，合计 126 个。普通与显式 salvage 结果相同，真实新增接受行为并不依赖跳过未知记录。这仍不构成全量未知记录或 WPS Office 应用级格式语义认证，也不证明名称的最终 Excel 显示或任何公式计算。

验收入口是仓库根目录下的维护程序 `tests/wps_biff8_salvage_government_probe.py`（真实官方 HTTPS 下载、严格域名白名单、16MiB 上限、固定源哈希；**生产 Skill 运行时不联网**），独立记录数检查 `tests/wps_biff8_name_oracle.py`；单测为 `<skill-root>/scripts/test_wps_biff8_names.py`。维护报告只输出原件哈希、数量、来源、是否安全分类及诊断，**不打印名称正文、表格单元格原值或原始名称公式内容**。

## 使用与限制

仍按主入口 `<skill-root>/SKILL.md#执行环境约束与首步自检` 确认 Linux/Python 标准库、真实目录、输入来源和 `environment-ok`。用户只读 ET 业务任务执行 `python -I -S -B "<skill-root>/scripts/read_wps.py" ./input.et`，允许返回 `partial` /退出码 3，并保留 `issues` 与 `reading_assessment`。完整文档、样式、命名公式求值、复杂外链和宏仍不支持。失败停止依赖操作，禁止把未知名称忽略后声称单元格完整。

## 格式依据

- Microsoft MS-XLS Lbl：`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/d148e898-4504-4841-a793-ee85f3ea9eef`
- XLUnicodeStringNoCch：`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/e64abeee-2f3a-4004-b9e3-3d67e29d6066`
- NameParsedFormula：`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/9afd1c6c-c486-4d9c-b644-3708428d2ffb`
- PtgArea3d：`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/869033ad-6304-4b9a-b2ba-1e7794ae345a`
- RgceAreaRel：`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/75afd109-b1ce-4511-b56f-2d63116f6647`
- XtiIndex：`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/9ded1bd9-4881-4b77-b32f-c77b93df03fd`
