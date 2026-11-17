# PPTX 结构化内容读取

路径占位符说明：`<skill-root>` 是占位符；从本次主入口 `SKILL.md` 的真实 `file_path` 定位，相对路径先按宿主确认的读取基准解析，再去掉文件名。使用前整体替换为真实目录，将完整路径作为一个参数正确引用；无法定位时停止。该根目录不是当前参考文件目录。
所有示例在默认任务工作目录 `./`执行，输入为安全任务相对路径；先完成主入口自检。此文解释默认 inspect 内容，不需要额外的表格/图表开关。

## 默认内容与兼容字段

默认 `inspect` 在原 `title`、`slides[].number/text/notes/objects` 外，按实际存在内容返回 `tables`、`charts`、`notes_tables`、`notes_charts`。空集合不添加空字段。表格单元格文字仍存在于旧 text/notes 段落中，不重复计数或拼入正文。对象列表和段落数组不是统一视觉阅读顺序，不能从它们推断坐标或页面显示。

`order` 为该正文或备注范围内原生表格/图表的共同出现序号，1 起算，不是全稿唯一 ID。`source.part` 是所属包部件，图表另有 `source.chart_part`；这些是内容出处，不是可下载的文件路径。不倾倒原始 XML、关系图或内部布局诊断给用户。

仅选择受支持的 MCE 语义分支，避免备用显示重复；全包及所有分支仍接受结构/关系安全检查。正文和备注分别提取，备注继续排除页码、日期、页眉页脚、幻灯片图像占位符。

## 表格

每张表有 `row_count`、`column_count`、`rows`。每行包含 1 起算的 `row` 和 `cells`；每格包含 1 起算 `column`、存储文字 `text`、`row_span`、`column_span`、`h_merge`、`v_merge`。格内段落以换行连接，显式空单元格及合并覆盖格保留，不复制合并起始格文字来填空。

行列数和合并标记来自保存的 DrawingML，不保证原稿网格一致。网格缺失、行宽不符、无效/越界/孤立合并标记通过 content_assessment 报告，不凭空补格或宣称完整矩形。字段缺失/不完整须结合诊断判断，不能以空字符串或对象数量推断内容完整。

## 图表保存值

`charts` 每项带 `data_policy=saved_xml_only_not_recalculated`、按源顺序的 `chart_types`、`series`，有依据时另有 `title`、`axes`。只读取经典 DrawingML 图表 XML 中保存的缓存/字面值，不打开内嵌工作簿取数、不执行公式、不联网或自动 OCR。读取支持不扩大创建可接受的图表类型。

支持缓存提取的经典类型包括 area/area3D、bar/bar3D、bubble、doughnut、line/line3D、ofPie、pie/pie3D、radar、scatter、stock、surface/surface3D；组合图按保存顺序保留类型和系列。ChartEx 及未解释扩展明确报告 unsupported，不将标记/数量伪装成已读取图表内容。

- 系列带 `order`、`chart_type`，有保存依据时含 `name`、`categories`、`values`、`x_values`、`y_values`、`bubble_sizes`。直存名称可为 `{source:stored_literal,value:...}`；其他数据集见下表。
- 标题为 `{source:stored_rich_text,text:...}` 或数据集；缺失标题内容可报告 source=missing。轴可含 type、title、number_format、display_units，单位保存原始 builtInUnit/custUnit，不将原值自行按单位缩放。

| 数据集字段 | 解释 |
| --- | --- |
| `source` | saved_cache 或 stored_literal；不支持/缺失时可为 missing_or_unsupported |
| `value_type` | number 或 string，数字仍按原始字符串返回，不转 float、不补零、不损失前导零/精度 |
| `formula` | 有时存在的存储引用/公式，仅作来源，不代表已执行或已重算 |
| `status` | available、missing、ambiguous；available 只代表缓存可读，不证明无缺点或是最新值 |
| `declared_count` | 保存的点数声明，无法解释时为 null；不按巨大的声明分配数组 |
| `points` | 按 XML 顺序的 `{index,value}`，index 为原 0 起算索引，缺失/异常可为 null；不排序补齐稀疏点 |
| `levels` | 多级分类使用各层 points 列表，保留层次而不扁平猜合并关系 |
| `number_format` | 有时存在的保存数字格式，不保证恢复客户端格式化显示 |

缺缓存、重复缓存、无效/重复索引、缺值、稀疏数据、缺必需系列字段、未知类型或扩展均保留内容诊断。不得将缺失缓存称作空图、将保存值称作最新数据，或仅凭类别/外观猜造数值。未取得足够依据时说明具体可用范围。

## 内容缺口、资源与投影

有缺口时，页级 `content_assessment` 为 `status=partial`、`scope=slide_and_notes_before_projection`，diagnostics 含 code、scope、order 及必要定位/限额信息。无此字段不证明所有可见内容都支持，也不等于完成 OCR/渲染。数学限制仍遵循主入口的 text_assessment；图表/表格提取遇到数学文字也可附 math_text_flattened，不恢复数学树。

内容诊断和 `structural_validation`、退出码独立：结构通过可仍退出 0，但消费方必须报告内容 partial；显式 PNG 容错仍是结构 partial/退出 3。安全/结构拒绝仍失败，不因内容读取可局部恢复而绕过。

增量结构化内容使用全稿共享的 100000 项及 4×1024×1024 Unicode 字符预算；预算不足时保留已取内容并报告 content_limit_exceeded，不把缺字段当作不存在。单次正文/备注提取的诊断上限128条，超过时追加 content_diagnostics_truncated 与遗漏数。预算不替代既有 ZIP/XML/媒体上限，也不保证进程峰值内存。

`--summary` 不返回这些内容列表，只保留原对象/段落计数与 text_assessment/content_assessment。`--notes omit` 删除 notes、notes_tables、notes_charts，但保留投影前正文及备注的所有诊断。选页、整页分页、显式页内续读同样保留当前返回页诊断；未返回页的诊断不能从当前页推断。续读和 JSON 预算见 `<skill-root>/references/bounded-reading.md`；预算放不下完整必要诊断时失败，不丢弃诊断换取更小输出。
