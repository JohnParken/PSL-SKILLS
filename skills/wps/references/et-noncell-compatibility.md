# BIFF8 数据读取：明确的非单元格记录兼容与拒绝矩阵

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 目标

针对 `.et/.ett` 的 **BIFF8 保存值读取**，把非单元格元数据和单元格值记录区别处理。仅在 MS-XLS 明确规定记录结构、所属 substream、顺序、大小、预算和后续记录边界时开放受限兼容；保留原始字节、来源位置与 `partial` 诊断。**不进行未知记录通用跳过、回退扫描、容器修复、加密绕过、公式执行或完整格式/视觉推断**。本工作线只修改 `wps_biff8.py` 和新增 `wps_biff8_noncell.py`，`.wps` MS-DOC 文字读取器保持原样。

## 已实施的两类明确记录（仅 compatibility=data）

| 记录 | 允许位置与有限验证 | 保存值影响与结果 |
|---|---|---|
| `WriteProtect (0x0086)` | 仅 Globals BOF 之后第一个记录；负载必须为 0 字节；重复或其他位置拒绝。 | 表示有写保护，原样记录 `write_protected_raw=true`；不会解除写保护、修改或写文件。保留 raw 和范围诊断，`partial`。未实现 FileSharing/password 检验与全部保护规则。 |
| `Uncalced (0x005E)` | 仅 Worksheet BOF 之后第一个记录；负载恰好 2 字节零值；其他位置、重复或 reserved 非零拒绝。 | 说明保存时有公式等待重算，保留 `saved_formula_recalculation_pending` 与 `cache_freshness=unverified`。不更改数值、字符串、布尔、错误或缺失缓存，增加明确 `partial` 诊断。 |

这两种记录原先没有被读取器列入白名单，即使其出现不一定改变单元格保存值也会阻止整簿提取。现在仅在精准验证成功时保留为 `uninterpreted_metadata`，结构见各记录的 `structure` 子段。低层严格 `strict` 和显式旧版 `legacy_excel_clsid` 的原有拒绝边界不变；不在任何失败后自动重试 `data`。

## 仍应拒绝、暂不放宽的记录与结构

| 情况 | 保持拒绝的原因 |
|---|---|
| 未知 Global/Worksheet ID、`Continue (0x003C)` | 无法证明记录不是某段的实际数据或分段正文；不对任意 ID 采用 `continue` |
| `FILEPASS`、宏与 VBA、未知外链、非法 `SupBook` | 可能涉及加密、安全操作与外部来源；不能通过只读默认策略绕过 |
| `BOF/EOF`、`BoundSheet8`、工作表偏移、`WsBool`、`SST/LabelSst` | 属于记录流结构或单元格寻址依赖；即使保存值部分可见，也无法安全证明完整区间 |
| `Date1904` 缺失、CodePage 超出证据范围 | 必须先进一步证明缺省与文本、日期的独立关系；本轮不猜年份系统或字符编码 |
| `Formula/String` 缓存、`ShrFmla/Array`、`PtgExp`、非标准空隙 | 与单元格计算结果/引用关系相关；不能按元信息静默跳过 |
| 绘图/对象/图表和复杂表格记录 | 对象记录可能要求后续 `Continue`、`Obj` 等跨记录语义；仅可独立验证的已有受限 opaque 路径保留 |
| CFB FAT/扇区交叉引用/长度/CRC/资源预算、无效字符串 | 安全和结构边界，**不降低** |

这不是一个标准记录全集或格式白名单的泛化版本。其他候选必须逐项获得 MS-XLS 结构依据、真实样本/可独立复验的预期，才可能追加。

## 反向安全测试与真实样本

新测试 `<skill-root>/scripts/test_wps_biff8_noncell.py` 覆盖精准通过、严格/legacy 保持拒绝、重复/错序、非法长度及 reserved 非零、未知记录、加密、损坏的 Number 和 `Continue` 仍拒绝，并把原始单元格保存值与未增加该元信息前的结果逐一比较。**测试样本是标准库合成记录，不代表真实 WPS 软件录入验证。**

**实际自动化验收已完成**：GitHub Actions Linux Python 3.11 和 3.13 各运行全部 WPS 回归 **445/445**，包含新增 6 项精准非单元格记录测试；`--check` 环境自检和 `check_skill.py skills/wps --strict` 均通过（运行 `41187871280`）。另一个维护作业 `41187871278` 在固定哈希核验后，对 4 份公开 `.et/.ett` 原件重新对照既有 **198 项冻结保存值（含 33 项未验证时效性的公式缓存）**，全部一致。原件未修改、分发或转格式，用户任务运行时仍不联网。机器证据见本 Skill 的记录（`<skill-root>/references/et-noncell-compatibility-evidence.json`）。这些真实原件未证明包含本次新增的两类记录，因此两类新格式分支本身仅具有合成测试依据；不能据此声称已修复用户的某个真实拒绝样本。

## 规范依据

- MS-XLS **Globals Substream**：`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/ca4c1748-8729-4a93-abb9-4602b3a01fb1`
- MS-XLS **Worksheet Substream**：`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/f41c06f2-9057-49a1-8c3f-a4a4d211fc56`
- MS-XLS **WriteProtect**：`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/8d791d7b-82f2-4259-9230-ea58afe5fd07`
- MS-XLS **Uncalced**：`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/2bcabcfa-1054-4ae2-ab3c-63a021ae0a14`
