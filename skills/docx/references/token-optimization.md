# Token 分层与按需读取（P3）

主入口为 `SKILL.md`；新建普通文档无需读取所有 references。按需读取：`generation-core.md` 为常用字段；`generation.md` 为旧锚点兼容路由；`generation-styles.md` 为格式；`generation-blocks.md` 为内容块；`generation-tables.md` 为表格；`generation-runtime.md` 为归档/兼容入口；`generation-examples.md` 为复杂 JSON。读取/编辑/分页校验的其他文档仍由 `SKILL.md` 按任务分流。

所有安全、文件路径、受控执行、清理及交付要求以 `SKILL.md` 为必需前置约束，路由不会放宽已有 Python 代码校验。完整计量对比和局限性见本次单独交付的优化报告（不作为普通文档任务的必读参考）。

## 2026-11-01：加载治理增量

- 正常新建成功后的格式引导已直接写入 `SKILL.md`，不再为了这句话读取约 19 KB 的 `generation-runtime.md`；后者保留历史锚点供维护与错误诊断。
- 常见任务从 `SKILL.md` 的路由表直接进入专项文件，不必先读 `generation.md` 索引；能按行范围读取则只读相关区间，不自动递归引用。
- 新增可选 `scripts/reference_loading_audit.py` 和 `references/reference-loading.md`。`plan` 只给出静态建议；`read` 加 `--trace` 才会记录实际经过该脚本的读取；`audit` 区分已读取字节、去重字节、重复读取及宿主提供的真实模型 usage。无法拦截宿主的其他读文件工具。
- 模拟结果：此前 `SKILL.md + generation-runtime.md` 交付风险路径约 33.1 KB；避免多余读取后仅 `SKILL.md` 约 13.9 KB，降幅约 57.9%。对已遵循最小按需读取的任务，额外节省接近零；**这不是模型 Token 实测**。
- 本轮未修改原有 Python 生成、读取和编辑代码及业务 JSON 协议；回归测试与静态说明量是本阶段验收依据。真实 Tokens 必须在 Agent/模型接口收集 usage。


## P3 简单生成快速路径（2026-11-01）

新主入口自含 `title`、`subtitle`、`heading`、`paragraph`、`list` 与基础 `table` 的最小 JSON 规范；普通任务仅需读取 `SKILL.md`，不再因为简单列表/二维表格加载 `generation-core.md`、`generation-blocks.md` 或 `generation-tables.md`。复杂合并、特殊样式和分节仍需按任务直达专项文档。`reference_loading_audit.py plan --task simple-list` 和 `--task simple-table` 仅作确定性规划，不等于实际模型加载。完整代码和生成器接口均不改变。
