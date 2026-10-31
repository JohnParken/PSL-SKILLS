# 验证方法、样本证据与未验证范围

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 目录

- 当前验证范围 (`<skill-root>/references/validation.md#当前验证范围`)
- 真实失败样本的受限修复 (`<skill-root>/references/validation.md#真实失败样本的受限修复`)
- 分支增量：MS-DOC 段落来源范围 (`<skill-root>/references/validation.md#分支增量ms-doc-段落来源范围`)
- 分支增量：CHPX/PAPX 观测来源关联 (`<skill-root>/references/validation.md#分支增量chpxpapx-观测来源关联`)
- 农业银行官网公开 DOC/PDF 实测 (`<skill-root>/references/validation.md#农业银行官网公开-docpdf-实测`)
- 中行、建行、工行官网公开文档实测 (`<skill-root>/references/validation.md#中行建行工行官网公开文档实测`)
- 可复现的本地检查 (`<skill-root>/references/validation.md#可复现的本地检查`)
- 样本与未验证边界 (`<skill-root>/references/validation.md#样本与未验证边界`)
- 历史证据 (`<skill-root>/references/validation.md#历史证据`)

## 分支增量：MS-DOC 段落来源范围

本次分支针对二进制 MS-DOC 的原始段落 CP 范围增加**默认关闭**的可选来源信息：`--binary-paragraph-sources`。默认 JSON/退出码和原有文本处理不变；对经过字段过滤、占位符或标记替换的故事明确拒绝输出虚假精确来源。未新增 WPS 格式兼容模式，不修改 CFB/CLX 安全边界。

本轮已在 Linux/Python3.13 标准库环境，对独立 CP 范围算法运行 **5 项隔离单元测试，全部通过**。合成 CP 计算测试的结果不能代替完整解析器验收；可选字段模式无实际文本转换时保留来源，实际字段/占位替换时明确放弃精确对齐。仓库分支新增 11 项 MS-DOC/CFB 合成集成测试。**GitHub Actions Linux** 两个独立环境（Python 3.11、3.13）均完整执行 WPS **435 项测试且全部通过**，环境自检通过、`check_skill.py skills/wps --strict` 通过（运行 `41178515332`，基于提交 `859106446b74f34e3d3b50dc287a2b42dbcd198d`）。这不是目标业务容器的真实宿主集成验证，也不是原生 WPS 文件的语义验收。没有真实 WPS 原生全文及独立段落位置预期，本次不宣称实际生产兼容性提升已被验证。分支审查完成前保持候选状态。

## 政府官网真实 CFB 附件（内容级有限核对）

政府官站页面中的 `.wps` 下载链接已由维护 CI 依真实 HTTPS 地址检查：10 份候选中 7 份成功下载且均为 CFB/OLE；6 份进入受控 MS-DOC 解析（结果均为 `partial`）且与独立 MS-DOC FIB/CLX 解码器的**全文和段落数组**一致；1 份含未支持的字段/对象/C0 控制字符，按设计明确 `unsupported`。另 3 份深圳站附件发生 TLS `BAD_ECPOINT`，属于网络受阻而非格式不兼容。固定附件 SHA、官网链接、段落计数及详细受限结论见政府文件实测 (`<skill-root>/references/wps-government-validation.md`) 和机器证据 (`<skill-root>/references/wps-government-evidence.json`)。

此证据证明官方**公开发布与实存 CFB 内容**，不是 WPS Office 实际生成软件/版本认证；另一套解码器仍共用 CFB 容器基础设施，未获取由 WPS Office 独立导出的全文、人工作业表格或可视化验收。项目严格 `verify_wps_sample.py` 的原生生产来源验收仍**未完成**，不能称为完整 WPS 支持。实测来源为 GitHub Actions Linux / Python 3.13，维护流程 `41181030181`；运行时 Skill 不下载附件。 

## 分支增量：CHPX/PAPX 观测来源关联

在已有 `--binary-paragraph-sources` 的精确存储 CP 坐标上，新增加法关联 `paragraph_evidence`：仅对 `--hidden-text annotate` 已解析的 CHPX run 区间给出 CP 交集；对 PAPX 直接支持的一级表格行结束标记，只给出结束符属于哪个段落的证据。若 CHPX 观察区间重叠/不合法，不声明精确链接；字符最终格式、可见性、修订和网格始终为 unknown。没有新增读取器、写入器或私有记录猜测。

**实际验证**：GitHub Actions Ubuntu Linux / Python 3.11 和 3.13 独立运行，WPS Skill 各 **443/443 项单元测试通过**、无失败或跳过，入口 `--check` 输出 `environment-ok`，严格 `check_skill.py skills/wps --strict` 输出 `OK skills/wps`。来源：workflow run `41178785301`，测试提交 `1e5fd08c70d30b33b1b3e415eebbdac75f51a4ab`（3.11 job `124471737217`，3.13 job `124471737020`）。这验证了仓库代码及合成测试，不是生产业务容器或 Word/WPS 真实原件全文语义验证。当前尚未取得可核实来源的原生 .wps 和独立段落/格式预期，未运行真实软件布局测试。保留 Draft PR，不合并主分支。

## 农业银行官网公开 DOC/PDF 实测

2026-10-31，维护 CI 针对中国农业银行官网正式公开下载附件，按实际内容而非文件后缀识别格式：共 8 份全部下载；3 份名为 `.doc` 的广东省分行文件实为 ZIP/OOXML，分别 `ok` 读出 16/9/12 个段落；广西两份 `.doc` 实为 CFB，其中桂林分行 76 个段落以 `partial` 返回并记录对象缺口，南宁分行因容器严格结构门禁拒绝；另三份总行附件为 PDF，不属于本 Skill 可用输入。所有 SHA、来源链接和原始错误见农业银行验证报告（`<skill-root>/references/wps-abc-official-validation.md`）及机器证据（`<skill-root>/references/wps-abc-official-evidence.json`）。这不是原生 `.wps` 的生产版本或全文视觉认证，不能绕过 CFB 检查提升“成功率”；测试通过说明分支路由与声明的有限读取范围。

## 中行、建行、工行官网公开文档实测

维护 CI 在三家银行官方公告中的 10 个办公附件直链上分别执行**受限真实下载与文件内容签名识别**：中行 2 份 .docx 均下载、识别 OOXML 且以 ok 提取 16/14 个段落；工行 2 份下载，其中 1 份 .docx 为 OOXML/partial（1 段，报价标题锚点缺失），1 份 .doc 为 CFB/MS-DOC/partial（1 段，另一个独立 FIB/CLX 算法的全文和段落数组一致）；建行两处官网的 6 份 .doc/.docx 均因 `UNSAFE_LEGACY_RENEGOTIATION_DISABLED` 下载失败，无文件字节可解析，**不计入解析成功或失败**。CI 运行 `41183896639`，精确 URL/SHA-256、锚点及受限结论见三行实测报告（`<skill-root>/references/wps-three-banks-official-validation.md`）和机器证据（`<skill-root>/references/wps-three-banks-official-evidence.json`）。不禁用 TLS 证书验证或降低协议安全条件，不从第三方副本替代原件。

四份已读取文件均非已验证的原生 .wps，包含 3 份 OOXML 和 1 份 CFB/MS-DOC；不能以 4/4 推论 WPS 专有格式兼容，也不能将算法一致当成 Office 应用人工逐段验证。生产读取器、安全边界及公共 `partial` 契约未因该维护测试修改。

## 真实失败样本的受限修复

本次基于中山市政府实际公开的“佐证材料.wps”（原件 SHA-256 `3ccedb9e26d89410796508531912169559c0a0258303a085075e69829762375c`），确认 WordDocument 偏移 2546、CP249 的 `U+0008` 对应主故事 `PlcfSpa` 形状锚点。实现只读且显式的 `object_policy=placeholder`：保守替换该绘图标记后，真实文件返回 **partial、36 个段落**，保留图形资源、sprmCFSpec 生效状态及表格结构未知；默认 reject 仍返回 unsupported。真实对照通过本次维护 CI 只读原件哈希确认，不代表 WPS Office 独立图形语义验收；详见 绘图对象锚点（`<skill-root>/references/binary-shapes.md`）。

中国农业银行广西南宁分行 .doc 文件已明确具备两个异常证据：总长度对 512 字节扇区余 51 字节且尾部非零、扇区对齐前缀 FAT 仍宣称物理范围之外的分配。本轮补充 `cfb_unaligned_physical_length` 和 `cfb_fat_unbacked_allocation` 定位，真实原件**继续被安全拒绝**。禁止自动裁剪、补零或忽略 FAT 约束；只有获得可靠原件生产和独立打开/内容预期证据后才可研究兼容读取。

GitHub Actions Linux Python3.11/3.13 全量 WPS **451 项测试均通过**、入口自检及严格 Skill 检查通过（本次开发分支的中间运行 `41185919368`）；真实失败源专项验证运行 `41185919275`。新逻辑为只读且基于证据的增加，不扩展原生格式兼容承诺，不启动 WPS/LibreOffice 或联网执行用户任务。

## 当前验证范围

历史基线 `20261029.1850.4782` 新增全局 DXF（0x088D）的结构读取与原始属性保留；data 自动选择已有精确 Word 头兼容路径，data/legacy 接受已观察的 CFB minor 0x003B 并诊断。strict 仍要求 minor 003E 与零头 GUID。未知私有记录不跳过，格式属性不应用于保存值，不宣称完整 WPS 私有格式支持。共用文件有界读取、加密/歧义流及资源门禁，不在失败后扫描或轮换策略。

机器可读证据 (`<skill-root>/references/reading-enhancement-evidence.json`) 的 `current` 为该开发分支当前摘要，已更新为上述 GitHub Actions 成功运行证据；`history` 只说明对应旧版，不作为当前版本、测试数量或包状态。历史基线在 Darwin arm64、Python3.12.14/Expat2.8.3 下 WPS 424 项与仓库表格分析接入 2 项通过，无失败或跳过。新增测试包括原文/保存值/缓存保留、CLI 退出 3 与完整 JSON、原件不变、DXF 错误边界/作用域/属性预算及 JSON 可序列化。本轮不重跑真实样本、不生成发布包；未运行 DOCX/OFD 全量测试或模型触发评测。Linux 目标容器不可用（docker/podman 未安装），Windows 未执行；本机结果不替代目标验收。结构检查、合成回归和真实文件语义验收分别报告。

## 可复现的本地检查

先完成 入口自检 (`<skill-root>/SKILL.md#执行环境约束与首步自检`)，替换 `<skill-root>`，在宿主确认的任务目录运行：

```console
python -I -S -B "<skill-root>/scripts/read_wps.py" --check
python -I -S -B -m unittest discover -s "<skill-root>/scripts" -p "test_*.py"
```

自检要求退出 0 和 `environment-ok`，并单独确认 OOXML 所需 Expat 可用；测试要求退出 0，区分通过与跳过。运行 契约示例 (`<skill-root>/references/reading-assessment.md#可运行的契约示例`) 要求退出 0 和 `contract-examples-ok`。独立包验证检查 ZIP CRC、包内路径、每个文件与源码的 SHA-256、隔离运行及执行前后安装文件不变。

仓库严格检查是维护工具，不是独立运行包的依赖。维护者从仓库根目录使用 Python 3.10+ 运行 `python -I -S -B skills/skill-development-standards/scripts/check_skill.py skills/wps --strict`；普通使用无需获取相邻技能或仓库检查器。


## 样本与未验证边界

- 允许的四份原始ET/ETT有固定来源和SHA；198个保存值与独立冻结观察对照，其中33个公式缓存未重算、不能证明最新。空ET不证明有内容ET兼容；具体范围见真实ET/ETT验收 (`<skill-root>/references/et-real-validation.md`)及固定预期 (`<skill-root>/references/real-value-expectations.json`)。
- 新增CodePage声明、普通Unicode头保留位兼容目前主要是合成验证；已有样本没有这些新情形，不据其回归宣称新增能力已获生产验收。真实用户仍失败的无图片ET/ETT与独立预期尚缺。
- 本批 CFB 兼容路径及 DXF 仅有合成验证；缺少反馈原件，不能报告原失败已复现或真实修复验收。DXF 校验 FrtHeader、作用域标志、XFProps 数量、属性类型及长度边界、填充/渐变互斥和内部边框标志；属性负载仍是原始十六进制，不验证其内部值语义或完整格式效果。每记录最多 1024 属性、全局合计 4096，另受原有 8224 字节记录及元数据预算限制。
- 真实`.wps`正文/二进制表格生产来源验收未完成；合成二进制一级行单元格证据不是完整网格、合并或布局还原。真实验收方法见文档验收 (`<skill-root>/references/wps-real-validation.md`)。
- 不验证页面视觉、完整原生WPS格式、私有流等价、公式重算、宏/外链执行或显示顺序；原值、格式解释、缓存与缺口按结果契约 (`<skill-root>/references/reading-assessment.md`)分别消费。
- 独立模型选择和本地前向任务不等于真实宿主自动发现/触发；专用命名linter未安装，语法/人工命名审阅及结构检查不能冒充其结果。

## 历史证据

旧版环境、计数、描述字符数和默认策略仅保留在证据history (`<skill-root>/references/reading-enhancement-evidence.json`)及仓库维护记录，避免与当前契约混用。历史记录中的依赖工具或验证方法不授权当前标准库流程调用外部软件。业务读取只按当前入口和实际结果处理，不必先阅读维护历史。

## 公开 .wps 候选样本（非生产来源认证）

候选公开文件的来源、固定 sha256、上游 5 个原始测试断言、另一套标准库 MS-DOC 正文解码与 24 段全文一致性已记录于 `<skill-root>/references/wps-public-candidate-evidence.json`。维护 CI 是独立 job，只在运行时按固定提交取样本，保持生产 WPS Skill 不联网、零第三方依赖。它提高了公开文件的**文本算法交叉验证**证据，但没有证明原始创建软件版本或 WPS 可视化语义，不能改写原有“真实原生 WPS 验收未完成”的结论。样本含对象标记时 CP 与最终转换文本无法可靠一一对齐，保留 `unavailable_after_text_transform` 和 `partial`，不伪造格式证据。
