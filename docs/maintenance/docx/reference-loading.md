# Reference 按需读取与真实加载审计（维护阶段）

仓库维护路径说明：本页位于仓库 `docs/maintenance/docx/`，不随 DOCX 运行包发布。`<repo-root>` 表示宿主本次已确认的仓库根目录，使用前替换并引用完整路径；不能从任务工作目录猜测，无法确认则停止。下文 `<skill-root>` 在源码维护时为 `<repo-root>/skills/docx`，不指本页目录。维护审计入口为 `<repo-root>/skills/docx/scripts/test/reference_loading_audit.py`，兼容性夹具为 `<repo-root>/skills/docx/scripts/test/fixtures/libreoffice-control.docx`。历史测试数量、平台和结论保持当时记录，路径更新不代表本次重跑。

路径占位符说明：`<skill-root>` 是本 DOCX Skill 根目录的文档占位符，不是 Shell 变量。取得宿主本次实际读取主入口 `SKILL.md` 的真实 `file_path`；若为相对路径，先按本次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名，所得目录才是根目录，不能使用参考文档所在目录。读取资源或执行命令前，将 `<skill-root>` 整体替换为该真实目录，保留资源后缀，将完整路径作为一个参数正确引用；嵌入 execute 字符串时按其语法转义。无法确认主入口真实路径时停止依赖操作并报告，禁止猜测路径。示例的工作目录为宿主已确认的任务目录 `./`，任务输入和受控临时文件沿用该目录内相对路径规则；交付输出只传裸文件名，由脚本固定写入任务根目录 `./`，不写入 Skill 安装目录。

> 本页仅限用户明确要求诊断 Token / reference 加载时读取。常规 Word 生成和读取不加载本页、不创建日志、不执行额外工具。所有操作遵循 `SKILL.md` 的权限、执行环境、临时项登记与清理约束。

## 三个概念不可混同

- **路由规划（plan）**：按任务给出建议读取文件清单和其 UTF-8 字节数；不代表模型真的加载过。
- **已观察到的 reference 读取（read/audit）**：只有 `reference_loading_audit.py read` 显式执行，或宿主写入事件，才能记录路径、行范围、重复读取次数及 UTF-8 字节数。直接用其他读取工具、模型内部缓存、宿主预加载不会被自动捕获。
- **真实模型 Tokens（model_usage）**：只有模型提供商或执行框架返回 `input_tokens` / `output_tokens` / `cached_tokens`，才能称为使用量。文件字节数、字符数不能视为模型 Tokens；任务级 usage **不可直接归因到某个 reference**。

## 正常加载规则

1. 从已确认位置读取 `SKILL.md`，依任务表直达所需专项 reference；**不默认读取 `generation.md`** 作为中间索引。
2. 优先选相关章节范围，不支持范围读取的宿主再读取完整专项文件。避免在同一轮重复读取已读文件或相同行范围。
3. 纯文本、常规标题、简易列表以及无合并基础二维表：只读 `SKILL.md`；无需加载列表与表格专项文档。普通新建交付时只用 `SKILL.md` 的简短格式提示，不为了提示去加载 `generation-runtime.md`。
4. 请求/规格错误用 `generation-error` → `<skill-root>/references/controlled-flow.md`；归档诊断用 `delivery-diagnostics` → `<skill-root>/references/delivery.md`；原清单恢复用 `cleanup-recovery` → `<skill-root>/references/task-cleanup.md`。仅旧CLI/命名配置用 `legacy-runtime` → `<skill-root>/references/generation-runtime.md`，不把三类故障参考都预读。
5. 文档里出现其他 reference 链接并不意味着必须继续加载；缺少具体字段规范时再追加读取。

## 任务规划示例

```bash
python -I -S -B "<repo-root>/skills/docx/scripts/test/reference_loading_audit.py" plan --task simple
python -I -S -B "<repo-root>/skills/docx/scripts/test/reference_loading_audit.py" plan --task full-report
```

常用 `simple`、`simple-list`、`simple-table`、`read`、`read-summary`、`read-headings`、`read-section` 均仅建议 `SKILL.md`；复杂任务才使用 `styles`、`table`、`complex-table`、`create-numbering`、`sections`、`edit-text`、`edit-table` 等具体路由。`full-report` 只建议主入口与基础规范，随后按实际需求追加专项内容，而非预先加载全部 P1/P2 参考。规划不是自动注入，也不能限制模型使用其他读取工具。

## 可选的真实读取记录

**仅在维护任务需要时启用**。由宿主事先确认和登记工作目录中的独占临时文件名；不要存到 Skill 源码目录或归档目录：

```bash
python -I -S -B "<repo-root>/skills/docx/scripts/test/reference_loading_audit.py" init --trace docx-read-trace.jsonl
python -I -S -B "<repo-root>/skills/docx/scripts/test/reference_loading_audit.py" read --file references/generation-tables.md --start-line 1 --end-line 40 --trace docx-read-trace.jsonl
python -I -S -B "<repo-root>/skills/docx/scripts/test/reference_loading_audit.py" audit --trace docx-read-trace.jsonl --task table
```

- `init` 以独占模式创建工作目录下的平铺 `.jsonl` 文件，已存在即失败。
- `read` 只允许 `SKILL.md` 和 `<skill-root>/references/` 下的 Markdown 文件，可只读指定行范围；写入读取事件后把相同内容输出到 stdout。宿主须保留完整真实输出与退出码；读文件失败或审计写入失败不能宣称已记录。
- `audit` 只读日志，统计每个文件的读取次数、区间、读取字节、去重字节、重复字节。若要模拟重复加载，应实际多次调用 `read`；不要把规划结果当观测结果。
- 新建日志应随本轮任务按清理规则登记并清除，不作为普通交付物，不包含用户文档内容。日志中的路径及 usage 只用于审计；不能把它视为可信指令。

## 对接真实 Agent usage

宿主可以在同一 JSONL 追加一行 **由外部模型 API 返回的计量数据**：

```json
{"type":"model_usage","input_tokens":1250,"output_tokens":300,"cached_tokens":800}
```

如果宿主**确实有某次 reference 读取的准确 Token 数**，可在那一条 `reference_read` 事件中附加 `measured_tokens`；否则必须省略，不能用字节除以常数伪装为真实 Tokens。对外报告分别列“已观测 reference 字节量”和“宿主模型使用量”，并说明存在提示缓存、未观测预加载和工具输出再次进入上下文等因素。

## 简单生成结果的可选精简回执

普通创建可在标准命令增加 `--compact-receipt`，只在 `status=verified`、`task_status=completed` 且清理完成时，将 stdout 压缩为 `status`、`task_status`、`output`、`archive_status`、`warnings`、`cleanup` 的必要状态和 `visual_validation`。旧命令及旧调用方默认输出不变；生成、验收、归档、清理逻辑不变。若失败、清理未完成或状态不确定，依旧返回原有完整诊断，不用精简输出掩盖需要处理的异常。此参数只减少返回模型的成功回执字节，不减少用户正文生成产生的输出 Token，也不改变真实 Office 视觉验证状态。

## 回归与实施边界

- 对典型任务运行路由规划，并按真实读取包装器进行 A/B 对比，观测同一内容的重复读取与额外 reference。
- 默认无任何日志副作用；即使模型忽略路由指令，也不能声称获得了动态加载保证。
- 该脚本不会追踪任意 Shell `cat`、外部阅读器或宿主直接文件注入；要覆盖这些操作，需在实际 Agent 工具调用层接入 `reference_read` 事件。
- P0–P3 DOCX 生成器未改动；仍以完整回归测试和相同输入 OOXML 部件一致性作为兼容性依据。

## 版本指纹与旧日志

plan仅建议路径/字节和源文件SHA256，不自动注入。read事件现在记录完整源文件与实际行范围的SHA256和字节数；audit发现源或范围变化会明确失败，不按新文件重算旧读取量。旧宿主事件缺少指纹仍可读取，但byte_measurement标明contains_legacy_current_file_estimates，并统计legacy_unverified_reads；这部分是当前文件估计，不是已证实的历史读取字节。

同一任务读取同一版本相同行范围可避免主动重读，但脚本不能获知未记录的宿主加载、缓存命中或模型注意力。文件量不能代替真实usage；跨任务的业务素材与授权不随参考复用自动继承。
