---
name: wps
version: "20261031.1546.1688"
description: 用于离线读取 WPS 文字（.wps）的正文和有限表格结构，以及 WPS 表格（.et/.ett）的工作表、单元格保存值与公式缓存；报告部分读取和未支持范围。未知私有格式不猜测，不重算公式，不生成、编辑、转换或还原页面布局。WPS 软件保存的原生 DOCX/XLSX 使用对应技能。
---

# WPS 家族只读提取

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

用于读取 `.wps/.et/.ett` 的文字、单元格和保存的公式缓存；按实际内容识别 MS-DOC、OOXML Word、OOXML 电子表格或 BIFF8 有限子集。原生 DOCX/XLSX 任务使用对应技能；本技能不生成、编辑或转换办公文件，不恢复页面布局，不执行公式、宏、外链或嵌入对象。未知容器和私有格式明确拒绝，有限读取不等于完整 WPS 兼容。

## 执行环境约束与首步自检

**用户文件定位：** 用户上传文件及用户要求读取的交付物，默认以宿主确认的任务工作目录为基准，按 `./文件路径` 查找；文件路径含子目录时使用已确认的相对路径。优先采用宿主提供的附件映射或用户明确指定的路径；映射缺失、文件不可访问或目标不明确时如实说明并按需询问，不猜测其他路径或自行搜索其他目录。此规则不授权扫描或读取未指定的历史归档。

1. 在 harness 确认的任务工作目录运行。优先用本轮上传附件及宿主提供的文件映射确定输入，不要求用户在问题中重复文件名；用户指定文件时尊重其选择，仅提供文件名按 `./文件名`、提供子目录按该相对路径读取。未指定文件名且本轮只有一个可访问的候选附件时，直接处理该附件；多个附件时结合用户指代和任务类型选择，仍有歧义才列出候选名称并询问。没有附件映射时，只列出 `./` 下的候选文档：唯一候选直接处理，多个且无法判定才询问，没有候选则说明未找到可访问文件。不自动递归搜索或到其他目录寻找，不将历史文件优先于本轮附件。CLI 仍须传入解析出的具体文件路径；CLI 的必填参数不构成要求用户手动提供文件名的理由。输入、输出与临时文件使用该目录及子目录的相对路径；不使用绝对数据路径、父目录或切换挂载绕过边界。宿主附件尚未映射到允许的任务目录时先报告访问缺口，不猜路径。若当前目录是 Skill 源码目录，先由 harness 明确任务目录，不直接生成任务文件。
2. 由实际读取本文件的 `file_path` 去掉 `SKILL.md` 确定 `<skill-root>`；内部脚本和资源以它为基准，Markdown 内部引用统一使用以 `<skill-root>` 为根目录的完整路径。执行前替换占位符并正确引用完整路径，不猜测其他会话或掩码路径，不为调用脚本切换到安装目录。
3. 使用宿主确认可用的 Python 3.9+，按 requirements.txt (`<skill-root>/requirements.txt`) 核对仅需标准库；禁止安装包、运行时联网或调用办公软件。保留 `-I -S -B`，首次读取前运行：

```console
python -I -S -B "<skill-root>/scripts/read_wps.py" --check
```

4. 要求退出 0 且输出 `environment-ok`。全部 OOXML 分支另需 Expat >=2.6；自检的 `ooxml-word=available` 表示此条件满足，`unavailable` 时只可读取二进制子集，不尝试 OOXML 解析。自检仅检查基础入口和所导入模块，不代表所有可选分支或文件已验收。
5. 确认输入为可读普通文件。仅读取无需写探针；保存结果时，在任务目录以独占探针实际写入、读回并清理，避免覆盖已有文件。自检失败、缺少成功标记或结果不明时停止依赖操作，环境修复后再验，不降低要求。
6. 自检不代表产物正确或具备下载能力。交付前核对实际结果、目标文件及宿主支持的交付方式；不构造猜测链接。压缩后若指令缺失，重读本文件，保留已确认的任务目录和文件名。

**自检与回传：**`--check` 的成功 stdout 保留 `environment-ok` 和 OOXML 可用性标记，并补充 `check_kind=environment artifact_validation=not_run`；受控依赖检查失败在 stderr 输出同样的范围标记及 `environment-failed`，依赖错误退出1、Python版本不足退出2。入口导入或参数解析阶段失败可能只有异常/usage诊断，必须按非零退出判失败，不要求它伪造已执行自检。正常读取仍是原JSON/纯文本契约，不能混入环境提示。

宿主须将每次调用的 stdout、stderr、真实退出码和调用标识一起返回模型，失败/partial也不丢 stdout，空通道明确为空字符串。未启动、超时、强杀或状态未知时不得伪造退出0；截断须注明通道与长度，不解析截断JSON。缺少工具回传时只能说明无法确认，print或本地捕获不证明模型收到。环境通过不代表任何原件、提取结果或缓存已验收。

## 电子表格：读取 ET/ETT 保存值

完成共同自检后，在任务目录运行；将文件名换成实际输入，保留默认 JSON 和 `data` 策略，不需要先选择兼容参数：

```console
python -I -S -B "<skill-root>/scripts/read_wps.py" ./input.et
```

1. **核对结果。** 退出 0 或 3 均读取完整 JSON；电子表格 `schema_version` 应为 `biff8_readonly_v1` 或 `ooxml_spreadsheet_readonly_v1`。退出 3 保留已读数据并报告 `issues` 与 `reading_assessment` 中的缺口；退出 1 或 2 停止后续分析，不把失败当空表。自动调用不要使用把退出 3 当失败丢弃的 `check=True`，见 标准库调用示例 (`<skill-root>/references/reading-assessment.md#消费电子表格-json`)。
2. **解释保存值。** 按单元格 `type` 解释 `value`，保留数字字符串的精度和文本编号；`numeric_interpretation` 仅补充格式解释，不能替代原值。`formula` 存在时读取的是保存缓存，结合 `cache_status` 区分缺失、错误和未验证缓存，不能保证最新或重算。BIFF 的 `cell_value_coverage`、`omissions_affect_cell_values` 与原始 `issues` 区分缓存缺失和元数据遗漏，不能据此宣称完整格式支持；详细字段见 结果契约 (`<skill-root>/references/reading-assessment.md#结果字段与范围评估`)。
3. **交付或分析。** 提交所需工作表/区域及明确缺口；需要汇总时先确认 schema、选定区域和缓存政策。如另有 xlsx 技能，可消费满足其限制的 JSON，仍保留源 partial 和诊断；本包不依赖相邻技能。BIFF 合并表头重建及适配器每表 256 个合并范围限制见 分析接入 (`<skill-root>/references/biff8-readonly.md#headers-and-output`)。

默认 data 对有证据的非单元格元数据及受支持图表保留原始记录并报告 partial；包括结构校验后的 DXF（`0x088D`）差异格式属性，格式不应用到保存值。未知记录、加密、结构与资源错误仍停止，不在失败后轮换策略。只有任务明确要求更窄子集才加 `--compatibility strict`；单独拒绝图表可加 `--chart-policy reject`。需排查格式时按结果读 BIFF8 边界与高级策略 (`<skill-root>/references/biff8-readonly.md`) 或 OOXML 保存值 (`<skill-root>/references/ooxml-spreadsheet.md`)；编码、容量及失败定位按需读 解析增强边界 (`<skill-root>/references/et-parser-stability.md`)，容器检查另见 CFB 调查 (`<skill-root>/references/et-ett-readonly.md`)。

## 文字：读取 WPS 正文与附加内容

```console
python -I -S -B "<skill-root>/scripts/read_wps.py" ./input.wps
```

同样按退出 0/3 保留结果、1/2 停止处理。仅需正文时可加 `--format text`；电子表格不支持 text。文字不代表页面布局或完整表格还原。按任务加载以下参考，不将文字选项用于电子表格：

默认 data 按精确头 GUID 自动选择已有的旧版 Word/Excel CFB 兼容路径，并接受旧版 minor `0x003B`；仍检查对应负载、版本、扇区链和边界，并报告 partial。显式 strict 保留零头 GUID 与 minor `0x003E` 要求。字段、对象及正文布局等独立限制继续按下表处理，容器可读不等于任意 MS-DOC 都能提取。

| 用户任务 | 操作与按需参考 |
|---|---|
| 正文或字段/对象边界 | 默认读取；具体容器、字段与对象策略见 格式边界 (`<skill-root>/references/format.md`)。 |
| 脚注、尾注、批注或页眉页脚 | 加 `--include-related`，仅 JSON；`related_stories` 与正文分开。按容器读 MS-DOC 存储区域 (`<skill-root>/references/ms-doc-stories.md`) 或 OOXML 引用 (`<skill-root>/references/ooxml-related-stories.md`)，不推断有效页码或页眉继承。 |
| 隐藏文字或修订视图 | 先读 隐藏与修订策略 (`<skill-root>/references/word-visibility.md`)。OOXML 默认 include/accepted；二进制不能过滤隐藏或修订。未知状态保留并说明，不能作为脱敏保证。 |
| 文档表格 | OOXML JSON `tables` 提供逻辑网格和来源，见 结构化表格 (`<skill-root>/references/ooxml-tables.md`)。MS-DOC 仅提供有直接属性证据的一级行/单元格，合并与表格归组未知，见 二进制表格边界 (`<skill-root>/references/binary-tables.md`)。 |

**MS-DOC 段落来源定位（可选）**：需要核对二进制 `.wps` 正文中的段落来源范围时，显式添加 `--binary-paragraph-sources`。仅对实际内容识别为 CFB + MS-DOC 的分支生效；返回各存储段落的 UTF-16 码元 CP 半开区间，不是 WordDocument 物理字节偏移或页面位置。字段结果过滤、对象占位或注释替换导致输出与存储文本无法可靠对齐时，不返回伪精确的段落来源，明确标记 partial 与诊断。同时显式指定 `--hidden-text annotate` 时，可将已有 CHPX 片段与段落 CP 做非生效属性的来源关联；若二进制表格解析已观察到 PAPX 一级行结束标记，也可仅关联该标记所属段落。来源关联不等于完整段落格式或表格布局恢复。默认读取不新增来源字段；选项、范围与错误契约详见 结果契约 (`<skill-root>/references/reading-assessment.md#结果字段与范围评估`)。无真实 WPS 全文来源对照，不把该元数据当作生产兼容认证。

公开候选 `.wps` 文件已获得一次固定哈希与独立算法的全文/段落对照，但来源声明不能证明创建工具或版本，且尚缺 WPS 软件导出的独立完整预期；具体见真实验收 (`<skill-root>/references/wps-real-validation.md`)。 政府官网公开附件中已有 7 份 CFB 文件下载并核实签名，其中 6 份正文在受控 MS-DOC 子集内返回 partial 并与另一套算法逐段一致；仍不能据此证明 WPS Office 制作版本或完整显示兼容。详情见政府文件实测 (`<skill-root>/references/wps-government-validation.md`)。不得将其视为已完成原生格式兼容认证。

中国农业银行官网公开附件的只读实测中，五份 `.doc` 有三份为 ZIP/OOXML、一份为受限可读 MS-DOC CFB（partial）、一份 CFB 被严格容器校验拒绝；另三份总行附件实际为 PDF。它们并非已验证的原生 `.wps`，不得用其成功数宣称 WPS 专有格式兼容。详细结果见农业银行官方附件验收（`<skill-root>/references/wps-abc-official-validation.md`）。

## 结果与按需验收

- 按实际容器和失败阶段说明范围。CFB 是容器，本包能读取其中受支持的 MS-DOC/BIFF8 子集；不能把某个 CFB 文件被拒绝概括为“只支持 OOXML”。同样，`.wps`、`.et/.ett` 后缀不证明内部结构或生成软件。
- `0x088D` 按 MS-XLS 标准 DXF 结构读取并保留属性，不认定为 WPS 私有记录。未支持的其他 BIFF 记录仍停止读取，不按记录 ID 猜测其语义，不跳过未知记录后宣称成功。某个 ET 文件读成功，仅证明该文件走到了支持范围内，不能证明已解析其他文件中的私有扩展；结合 `status`、`issues` 和范围摘要区分成功、部分读取和失败。
- 用户报告的失败与本地结果不一致时，核对实际使用的 Skill 版本、原文件、完整调用参数、stdout/stderr 和退出码；缺少证据时明确待复现，不声称已修复该真实样本，也不反复换兼容参数掩盖拒绝。
- `status=ok`、退出 0：声明范围内成功，允许空正文或空工作表，不等于完整格式认证。
- `status=partial`、退出 3：保留提取结果并说明已知缺口。MS-DOC 默认省略非空附加区域或遇表格边界也可返回 partial；纯文本诊断另写 stderr。
- 读取失败退出 1；参数错误退出 2。输出通道、Python API 异常与可运行示例见 结果与失败契约 (`<skill-root>/references/reading-assessment.md`)。

需要支持范围或拒绝原因时读 格式矩阵 (`<skill-root>/references/format.md`)。仅做维护验收时读 验证方法与限制 (`<skill-root>/references/validation.md`)、真实 ET/ETT 记录 (`<skill-root>/references/et-real-validation.md`) 或 真实文档验收 (`<skill-root>/references/wps-real-validation.md`)。真实 `.wps` 验收须固定原件哈希、来源声明和独立完整预期；来源声明本身不是生产来源证明。合成测试、容器识别和真实语义验收分别报告，不据测试数量推断兼容率。
