# ET/ETT 读取容量与稳定性

维护资源说明：文中 `docs/maintenance/et/` 与 `skills/et/scripts/test/` 均以宿主确认的仓库根目录为基准，仅供源码维护，不随运行包分发；运行任务不读取或执行。没有仓库时跳过维护资料，不按 Skill 安装目录拼接。

路径占位符说明：`<skill-root>` 是 ET Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 ET 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

先完成 执行环境自检 (`<skill-root>/SKILL.md#执行环境约束与首步自检`)。本轮增强针对内容被识别为 CFB/BIFF8 的 ET/ETT；OOXML 仍走自身读取分支。未知私有结构不猜测，不执行公式、宏或外链，不应用注音显示，不写回输入文件。

## 新增可读范围

- SST 共享字符串可以附带注音扩展。单元格仍只返回原来的主文字，附加 `shared_string_phonetics` 按 SST 索引保留原始 ExtRst 字节与来源；注音结构、读音及显示未解释，结果为 partial。
- 文字、FormatRun 与注音原始字节分别处理跨 Continue 记录的边界；字符续段消费编码字节，原始元数据续段不消费编码字节。固定头不得跨记录，负长度、截断及超预算明确拒绝。公式 String 缓存仍只支持原有纯文字子集。
- 共享字符串独立数量上限为 200000；仍受 32 MiB 输入/流、100000 条记录、32767 码元单字符串、8 Mi UTF-16 单元格输出文字及 64 MiB 完整 JSON（可配置到 256 MiB）等预算限制。达到数量上限不代表任意长度组合都可接受。
- 合并范围总量为每工作簿 65536，保留范围与非锚点数据，不展开单元格。重叠检测使用行边界扫描及 256 位列占用掩码，避免逐对比较；边界相邻允许，共享一个单元格降级为 issue 并保留范围证据。
- 小范围表头读取不会仅因其他行存在超过 10000 个合并范围而失败；实际表头仍受区域、完整合并边界、非锚点内容和单元格预算检查。

相邻 xlsx 技能的 WPS JSON 分析适配器仍限定每表 256 个合并范围；本批未修改该技能。超过该范围的读取结果可使用本包的显式表头入口，但不能假定所有下游分析工具同步支持新容量。

## 失败结果

BIFF 解析异常可附带已记录的工作表、记录 ID 和 Workbook 流偏移；字符串失败还可包含 payload 偏移。统一入口将这些位置保留在 `location`，不复制失败记录中的正文。无可确认的位置时不推测。

资源耗尽优先降级：诊断条目超过 1000 截断并置 `issues_truncated`；结构化行/元数据超预算保留已读部分并附 issue；输入 32 MiB、流 32 MiB、记录负载 8224 字节等硬边界仍明确失败。加密/FILEPASS、无法安全建立基础分配表、ZIP/XML 安全禁止项和输入变化仍返回失败，其余结构问题按 `invalid_or_unsupported` 或 partial 报告；失败不交付未经完整检查的部分工作表，partial 不冒充 ok。

所有公开入口（文件 API、读取 CLI、read/metadata 协议、低层字节解析）统一使用内容优先容错策略；旧 `--compatibility`、`--chart-policy`、`--salvage-unknown-metadata` 仅为 no-op 并附 issue。选择发生在一次解析前，不会在拒绝后重试或重同步；详见 默认容错读取策略 (`<skill-root>/references/biff8-readonly.md#default-tolerant-policy`)。

## 验收边界

新增注音、容量和位置行为以标准库合成样例验证；合并检测另与小范围独立单元格枚举结果比较。四份已有真实 ET/ETT 用于回归及保存值核对，不包含新增注音或极大容量特性的生产证据。不能把容量提高或合成通过写成完整原生 ET 兼容。

规范定位见 ET 格式依据 (`docs/maintenance/et/et-format-evidence.json`)。现有来源不保证覆盖用户的每一种 WPS 版本；具体失败文件仍须核对实际容器、拒绝记录及独立内容预期。

实际运行结果见验证记录 (`docs/maintenance/et/validation.md#历史证据`)及本批证据摘要 (`docs/maintenance/et/reading-enhancement-evidence.json`)。

## 默认容错读取与失败分类

容错策略只扩大已知非值语义与可恢复结构的可读范围，单条坏记录不丢弃整本。样式、打印、窗格、分页及支持的图表保留原始字节与诊断。工作表标准窗口组另提取保存的冻结行列及当前单元格，范围见 `<skill-root>/references/saved-visibility.md#保存的工作表视图`。容器偏差及其他未解释流也导致 partial。

完整分帧的未知记录保留原始字节并标记保存值覆盖不确定；单条值承载记录损坏时跳过该记录/单元格并置 `omissions_affect_cell_values=true`。外链只记录不跟随、宏存储不加载、宏/图表/对话框子表跳过；加密、记录帧无法继续、无法安全建立基础分配表、ZIP/XML 安全禁止项、输入变化和资源耗尽仍失败。缺失的公式 String 缓存仍明确 missing，并将 cell_value_coverage 降为 missing_formula_caches，不作空白或缓存正确性推断。分析端的公式默认排除和 missing 缓存拒绝不变。

四份固定真实 ET/ETT 在容错策略下均返回 partial，独立冻结的 198 个保存值（含 33 个未重算缓存）保持一致；空 ET 不证明有内容 ET 的兼容性。本轮重构后的验证记录见 `docs/maintenance/et/validation.md#内容优先容错重构验证`。

## 纯表格 Unicode 声明与保留位

data 在原有 BIFF8 范围内新增 CodePage 932/936/1252 声明接受，普通 Label、String 缓存与 Format 的 Unicode 头保留位按规范忽略，保留原始标志及位置并报告 partial。不是按代码页猜解码，不扩展 BIFF 版本、SST/Continue 标志或未知记录。完整范围与证据限制见 纯表格兼容 (`<skill-root>/references/biff8-readonly.md#pure-table-unicode-compatibility`)。
