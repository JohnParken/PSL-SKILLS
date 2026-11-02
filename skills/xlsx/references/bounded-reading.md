# 有界读取视图

路径占位符说明：`<skill-root>` 是 XLSX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 XLSX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

默认 `read` 的完整输出保持不变。只需了解结构时先用概况；逐格核对才按表、区域分页。新选项仅支持原生 XLSX/XLSM，CSV/TSV 保持原完整读取接口。不计算公式、不写文件、不归档；仍完整解析输入，不承诺降低底层内存或解析时间。

```console
python3 -I -S -B "<skill-root>/scripts/stdlib_xlsx.py" read ./input.xlsx --summary
python3 -I -S -B "<skill-root>/scripts/stdlib_xlsx.py" read ./input.xlsx --sheet "Data" --range A1:D1000 --page-size 100 --max-output-chars 12000
```

输出为独立 `schema_version=xlsx_read_view_v1`，不是旧完整读取 schema。概况不含值，`coverage=overview_only_no_cell_values`；单元格模式返回 `items[].sheet/cell/data`，data 保留原 type/value、公式、格式索引及缓存字段。数字字符串不转 float、文本编号不转数字，缓存仍是未验证保存值。日期系统单独保留；不推断空白单元格或可见格式。

`--sheet` 按精确名称选择；`--range` 需配合 sheet，使用大写 A1 或 A1:D1000。按工作表原顺序、行号、列号返回已存单元格；稀疏空白不填充。`sheets[].stored_cells` 是该表总存储数，`total_items` 是本次选区数。无筛选的显式分页可跨所有表。

任何视图默认每页最多200个单元格、12000个Unicode字符（含JSON末尾换行）；`--page-size` 为1..10000，`--max-output-chars` 为512..2097152。字符不是token，不能推断宿主预算。`returned_items/start/total_items` 描述当前覆盖；`truncated=true` 是**视图尚未取完**，不是源解析失败。概况的truncated=false也不表示已返回所有值。

续读：保持文件、sheet、range、page-size不变，把返回的完整 `next_cursor` 作为一个正确引用的 `--cursor` 参数传回；可调整字符预算。游标绑定本轮解析的同一不可变字节快照SHA256与选择条件，不是访问授权或签名。输入字节或选项改变、非法游标均失败；从头重新读取，不能把新旧页拼接。只有next_cursor=null才取完所选存储单元格。并发修改读取期间检测失败也停止，不重试拼接。

单个单元格或元信息超过预算时，返回完整失败JSON和退出1，不截断值、不伪造空页；提高字符预算或缩小选区。语法参数错误仍退出2。默认CLI与API结果不变，新视图不能直接替代旧完整JSON作为其他分析器的输入；统计优先用现有profile/summarize和明确区域。

完整结果若确需落文件，使用宿主已确认的安全写入方式保存到尚不存在的任务结果文件；不得猜输出参数或覆盖已有文件。结果文件只是中间数据，不自动当作最终交付物归档，后续按本轮所有权与生命周期处理。宿主如何读取文件、截断结果或保留上下文仍需真实集成验证。
