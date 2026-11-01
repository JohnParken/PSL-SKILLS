# BIFF8 临时最大兼容：仅保留可确定来源的保存值

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 临时范围与警告

用户明确要求临时放宽 ET/ETT 的 BIFF8 读取拒绝条件。本功能**不是标准 BIFF8 解析，也不证明每个保存值完整**。它是由维护人员/用户明确选择的 best-effort 读取路径，在有界 BIFF 记录已经完整分帧、CFB 容器和强制安全检查通过的前提下，尝试保留能明确解析的单元格保存值。所有跳过的未知记录原样保留于诊断，状态**只能是 `partial`**，并设置 `cell_value_coverage=unverified_due_to_best_effort_salvage` 和 `omissions_affect_cell_values=true`，避免用户把它误当作可用于对账的完整数据。

**不能把宽松结果直接用于财务结算、自动对账、交易决策或任何依赖完整单元格范围的程序。** 只做人工初步阅读；重要值应与独立 WPS/Excel 软件或可信原始台账对比。未知记录可能存储本模块尚不认识的单元格值、计算结果或关系，因此即使未报错也可能遗漏数据。

## 执行入口

先从实际读取目标 `SKILL.md` 的 `file_path` 确认并替换 `<skill-root>`，从 harness 已确认的任务工作目录读取用户指定输入。完成 Skill 的 `--check` 自检后，**显式选择**仅用于 BIFF8 的独立低层 CLI：

```console
python -I -S -B "<skill-root>/scripts/read_wps.py" --check
python -I -S -B "<skill-root>/scripts/wps_biff8.py" ./input.et --compatibility data --salvage-unknown-metadata
```

这不是默认策略；普通 `read_wps.py ./input.et` 仍按原来的安全与准确性边界拒绝。**此选项对 .wps/MS-DOC 和 OOXML 无效**，不自动切换其他格式，也不在一次失败后悄悄重试。Python 标准库运行，不使用办公软件、外部 API、联网、写文件或第三方组件。只读 `.ett` 也可采用相同显式命令。

## 在宽松模式下会发生什么

- 对未识别、但外层 BIFF 记录长度、偏移和累计配额已经校验成功的 Global/Worksheet Record，保留 `offset`、`scope`、`record_id`、`payload_hex`。按记录种类和子流分组诊断 `unknown_biff_record_salvaged`，记录原始条数。此记录内容**可能**代表未解析的单元格，因此将保存值覆盖范围标成未知，并非安全忽略。
- 原本拒绝的**缺少必需 Date1904**：临时允许继续读取保存的原始数值，`date_system=unknown`，禁止 `numeric_interpretation` 日期推断；`date_system_missing_salvage` 会明确保留规范缺口。这不是宣布此缺失符合 MS-XLS。
- 原本拒绝的**缺少 Worksheet WsBool**：仅在该表 BOF/EOF 和其他记录结构可完整解析时返回 `partial`，附加 `wsbool_missing_salvage`，不保证工作表实际类型与保存视图。
- 普通 Formula 的数字、布尔、错误和文本缓存按已有有界逻辑原样读取，**不重算、不推断是否最新**；未知记录可能改变其业务语义，不能把缓存当作经过验真的实际计算结果。
- 所有原生数据结构、SST/Continue 字符边界、重复或越界单元格、公式组索引和资源配额仍然有效；这类失败不能因为“宽松”被忽略。
- 读到部分数据时退出码 3 和 stdout JSON 保留，退出 1 表示还存在不可恢复的格式/安全/结构拒绝；不把退出 3 作为完整成功处理。

## 必须保留的不可恢复边界

CFB 文件头、FAT/MiniFAT 扇区链交叉引用/循环、记录截断和超长、FILEPASS 加密、宏/VBA、超出预算、非法核心记录 BOF/EOF、公式缓存 String 不一致、SST/LabelSst 越界、数字/日期结果溢出、无法确认数据来源的重复单元格和不合法共享/数组公式关系，均不自动放宽。外部链接、可执行对象也不会执行或下载。

如果未知记录被保留而非解析，其实就是选择了**不确定覆盖率的部分读取**，不是“全部文件均能读完整”。不能将 `omissions_affect_cell_values=false`、`status=ok` 或默认数字解释宣称为安全，亦不得使用返回的部分表格汇总去证明总额准确。

## 合成验收与真实原件

新回归代码 `<skill-root>/scripts/test_wps_biff8_salvage.py` 覆盖全局及表内未知记录、同 ID 多次出现、缺少 Date1904、缺少 WsBool、错误/加密/核心结构仍拒绝、元数据预算、低层显式 CLI + 原件字节不变。**新行为目前仅有合成 BIFF8 规范测试；真实四份 ET/ETT 原件仅能验证旧保存值不回归**。只有拿到本来被拒绝的真实 ET/ETT，并由独立来源验证其内容与期望，才能扩大通过范围。
