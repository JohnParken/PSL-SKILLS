# 保存的可见状态与筛选元数据

维护资源说明：文中 `docs/maintenance/et/` 与 `skills/et/scripts/test/` 均以宿主确认的仓库根目录为基准，仅供源码维护，不随运行包分发；运行任务不读取或执行。没有仓库时跳过维护资料，不按 Skill 安装目录拼接。

路径占位符说明：`<skill-root>` 是 ET Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 ET 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

BIFF Row 与 ColInfo 保留保存的隐藏位和尺寸。`hidden_cause` 为 `unknown`，不推断手动隐藏或筛选原因。列号 256 是默认列格式哨兵，`default_column_format_included` 标明它，`actual_last_zero_based` 不把它当实际新列。

完整读取的 `sheets[].visibility.rows` 和 `columns` 分别附带行高 `height_pt`（点）和列宽 `width_chars`（保存的字符宽度单位），数值用字符串表示。BIFF8 保留 `height_twips_raw` 与 `width_256th_char_raw`。OOXML 读取行 `ht` 和列 `width` 的已保存值。字段缺失时不补默认值，无法解释的字段用对应的 `height_pt_status` 或 `width_chars_status` 标为 `uninterpreted`，单元格覆盖按原契约判断。隐藏记录仍可保留其保存尺寸，实际屏幕尺寸取决于字体和渲染环境。

BIFF8 的 AutoFilterInfo 记录号为 `0x009D`，AutoFilter 为 `0x009E`。FilterMode、AutoFilterInfo和AutoFilter保留范围内的原始记录，条件未解释、不重新执行筛选，明确partial。AutoFilterInfo必须1至256列。保存行状态不证明筛选条件仍与数据一致。

默认输出所有单元格。若显式选择保存可见行，在已确认的任务目录使用包内模块（仅Python标准库）：

```python
from wps_visibility import select_rows
records = select_rows(result['sheets'][0], mode='saved_visible')
# 每项(address, cell, saved_state)；仅排除确定hidden行，unknown保留。
```

将已确认skill目录的scripts加入模块路径后导入；没有新增CLI选项。原始数值、缓存及来源诊断随cell保留，选择不转换文件。尚未实现列可见性过滤、筛选条件语义或下游自动可见记录统计。

长度、范围、重复/冲突与原有metadata/record预算检查保留。合成单测包含保存隐藏位、未知保留、256哨兵、非法筛选列数、畸形记录和实际BIFF路径；真实样本由总验证记录说明。开发依据见来源 (`docs/maintenance/et/visibility-evidence.json`)，运行时不联网。

## 保存的工作表视图

完整读取在 `sheets[].views` 中分别返回每个保存的窗口视图。无视图记录时返回 `[]`。`freeze_row` 和 `freeze_col` 分别是冻结的行数和列数。已知无冻结时为 `0`，无法确认时为 `null`。普通分割的距离和滚动位置不参与计算。

`active_pane` 标识活动窗格。`selected_cell` 是该窗格明确保存的 A1 地址，即使该位置没有保存单元格值。没有明确活动单元格或选区记录相互冲突时返回 `null`。选区范围、其他窗格的光标和滚动位置不用于补推当前单元格。BIFF8 视图附 `record_offset`，只从同一个 `Window2` 记录组取 `Pane` 与 `Selection`。OOXML 视图附 `workbook_view_id`，同一 ID 的重复定义使用最后一项。

有界视图字段异常时保留可确认的字段，并附 `saved_sheet_view_partially_read` 诊断。BIFF8 的 `Pane` 仍须通过原有记录长度及字段边界检查。预览与分析按显式范围处理，冻结和选区信息不参与表头推断。
