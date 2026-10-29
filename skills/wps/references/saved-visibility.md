# 保存的可见状态与筛选元数据

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

BIFF Row与ColInfo只解释保存的隐藏位；hidden_cause为unknown，不推断手动隐藏或筛选原因。列号256是默认列格式哨兵，default_column_format_included标明它，actual_last_zero_based不把它当实际新列。

FilterMode、AutoFilterInfo和AutoFilter保留范围内的原始记录，条件未解释、不重新执行筛选，明确partial。AutoFilterInfo必须1至256列。保存行状态不证明筛选条件仍与数据一致。

默认输出所有单元格。若显式选择保存可见行，在已确认的任务目录使用包内模块（仅Python标准库）：

```python
from wps_visibility import select_rows
records = select_rows(result['sheets'][0], mode='saved_visible')
# 每项(address, cell, saved_state)；仅排除确定hidden行，unknown保留。
```

将已确认skill目录的scripts加入模块路径后导入；没有新增CLI选项。原始数值、缓存及来源诊断随cell保留，选择不转换文件。尚未实现列可见性过滤、筛选条件语义或下游自动可见记录统计。

长度、范围、重复/冲突与原有metadata/record预算检查保留。合成单测包含保存隐藏位、未知保留、256哨兵、非法筛选列数、畸形记录和实际BIFF路径；真实样本由总验证记录说明。开发依据见来源 (`<skill-root>/references/visibility-evidence.json`)，运行时不联网。
