# XLSX 专项任务路由

路径占位符说明：`<skill-root>` 指本次实际读取 XLSX 主入口 `SKILL.md` 的真实 `file_path` 去掉文件名所得目录；相对路径先按本次已确认的读取基准解析。它不是 Shell 变量，也不是当前参考文档目录。使用前整体替换为真实根目录，并将替换后的完整路径作为一个参数正确引用；无法定位时停止，不猜工作目录、环境变量、其他会话路径或宿主掩码。脚本、参考、模板及依赖清单均从该根目录定位。

普通读取和简单静态创建直接使用主入口，无需本页。只在明确需要下表专项能力时读取本页，再直达该项；不要展开全部参考。

## 按当前任务选择

只读取当前分支，缺字段或需要额外能力时再读；链接不表示递归预读，调用已知 CLI 不先读源码。混合任务取必要参考的并集。同一任务只有路径/版本未变且完整内容仍在上下文时才复用；缺失、变更或截断时重新读。此规则不修改 Deep Agents 的加载、缓存或计费机制，不复用其他任务授权。

| 任务 | 直达参考 |
|---|---|
| CSV/TSV 读取及额外类型/缓存解释（普通 XLSX/XLSM 只需入口） | `<skill-root>/references/read-analyze.md` |
| 大文件选表、概况、区域或分页 | `<skill-root>/references/bounded-reading.md` |
| 超出简单静态创建、公式或样式的创建 | `<skill-root>/references/create.md` + `<skill-root>/references/write-contract.md` + `<skill-root>/references/task-delivery.md` |
| 修改已知单元格；修复公式 | `<skill-root>/references/edit.md`（修复另读 `<skill-root>/references/fix.md`）+ 写入契约 `<skill-root>/references/write-contract.md` + 收尾 `<skill-root>/references/task-delivery.md` |
| 创建/修改公式、显式计算或公式 issues | `<skill-root>/references/formula-calculation.md` + 写入契约 `<skill-root>/references/write-contract.md` + 收尾 `<skill-root>/references/task-delivery.md` |
| 原生 XLSX/XLSM 的 profile/summarize 区域统计 | `<skill-root>/references/data-analysis.md`；新算公式值另读公式参考 |
| 仅识别文件格式 | `<skill-root>/references/read-analyze.md#identify-contents-including-etett-candidates` |
| 消费已有 ET 完整提取 JSON | `<skill-root>/references/et-result-analysis.md` + `<skill-root>/references/data-analysis.md`，保留来源与统计两层契约 |
| 存款到期分布；客户/产品覆盖；存款变动、结构与集中度 | 分别直达 `<skill-root>/references/maturity-distribution.md`；`<skill-root>/references/customer-structure.md`；`<skill-root>/references/deposit-analysis.md` |
| 管理口径/计划/同业比较；两期勾稽；静态看板 | 分别直达 `<skill-root>/references/management-policy.md`；`<skill-root>/references/management-reconciliation.md`；`<skill-root>/references/management-dashboard.md` |
| 管理宽表；机构层级汇总；日均/日流量累计 | 分别读 `<skill-root>/references/management-wide.md`；层级汇总同时读 `<skill-root>/references/institution-hierarchy.md` 与 management-wide；日统计读 `<skill-root>/references/daily-analysis.md` |
| 排名/组内占比；中位数/分位数/方差；月度同比环比 | 分别直达 `<skill-root>/references/rank-share.md`；`<skill-root>/references/advanced-statistics.md`；`<skill-root>/references/period-comparison.md` |
| 类型化键联表；去重排序；候选表头；显式多级表头 | 分别直达 `<skill-root>/references/table-matching.md`；`<skill-root>/references/row-processing.md`；`<skill-root>/references/header-candidates.md`；`<skill-root>/references/multi-level-headers.md` |
| 已有分析 JSON 导出 XLSX | `<skill-root>/references/result-export.md` + `<skill-root>/references/task-delivery.md` |
| CSV/TSV 导入导出 | `<skill-root>/references/tabular-interchange.md` + `<skill-root>/references/task-delivery.md` |
| 新建表外观；用户要求财务格式 | 分别读 `<skill-root>/references/readable-appearance.md`；`<skill-root>/references/format.md`，不用于重建既有表 |
| 专项产物验收；日期/宏/XML/编辑边界 | `<skill-root>/references/validate.md`；`<skill-root>/references/result-boundaries.md` 的对应条目 |
| 复杂 JSON；工具失败/截断；自检异常 | 分别读 `<skill-root>/references/complex-json.md`、`<skill-root>/references/tool-execution.md`、`<skill-root>/references/environment-results.md` |
| 自定义临时文件、归档故障或清理恢复 | `<skill-root>/references/delivery-lifecycle.md` |

上述分析专项与 `<skill-root>/references/analysis-contract.md` 组成必需并集，不经 data-analysis 大导航；仅命中基础统计或 ET JSON 消费才读 data-analysis；ET 另加来源契约。专项明确要求的业务依赖仍须读取；导出或创建配置文件还须按写入/收尾规则管理。锚点只定位文字，不是 read_file 的选段参数；按真实 schema 读取，返回截断时补齐所需约束后再执行。

维护跨技能接口时才读 `<skill-root>/scripts/docs/cli-contract.md`；迁移旧内存调用才读 `<skill-root>/references/workflow-api.md`。不把维护或历史验收资料加载到普通业务任务。

