# WPS 文档声明式生成指南

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 概述

`<skill-root>/scripts/run_wps_task.py` 生成 RTF 流（默认 `.wps` 后缀，显式请求 `.rtf` 时保留），默认公文参数参照项目 DOCX 公文模式；不是金山原生二进制，不承诺跨阅读器完全一致。无运行时 DOCX Skill 依赖。以下输入输出均位于宿主确认的任务工作目录。

## 目录

- 主题与默认值：`<skill-root>/references/generation.md#主题系统与公文规范`
- 支持的块：`<skill-root>/references/generation.md#支持的-block-类型`
- 产物验收：`<skill-root>/references/generation.md#产物验证与交付收尾`
- RTF边界：`<skill-root>/references/generation.md#rtf结构与可验证排版范围`
- 标题与表格：`<skill-root>/references/generation.md#标题制表位和表格增量`
- 表格合并：`<skill-root>/references/generation.md#表格合并和边框`

## 核心命令与调用范式

通过管道传入 JSON 规范：

```console
python -I -S -B "<skill-root>/scripts/run_wps_task.py" create "./通知.wps" --compact-receipt <<'JSON'
{
  "theme": "official",
  "header": "内部公文",
  "footer": "机密",
  "blocks": [
    {"type": "title", "text": "关于推进业务系统升级的通知"},
    {"type": "heading", "level": 1, "text": "一、工作目标"},
    {"type": "paragraph", "text": "确保核心业务链路平稳运行与高效协同。"},
    {"type": "list", "ordered": true, "items": ["基础环境升级", "数据架构迁移"]},
    {"type": "table", "rows": [["阶段", "负责人"], ["第一阶段", "张三"]]}
  ]
}
JSON
```

## 主题系统与公文规范

| 主题 | 页面边距与排版规则 | 适用场景 |
|---|---|---|
| `official`（默认） | 参照项目 DOCX 公文预设：A4，上下25.4mm、左右31.8mm；正文仿宋16pt，西文Times New Roman，左对齐、首行2字、固定28pt、零段前段后；默认不生成页码。 | 通知、工作汇报、通报、纪要 |
| `business` | 严肃商业报告规范：边距 25.4mm（1英寸）；标题微软雅黑/黑体加粗，正文 11 磅，1.25 倍行距。 | 企业商业方案、项目分析、运营总结 |
| `general` | 通用文档：边距 20mm；标题黑体/楷体，正文小四号宋体，1.2 倍行距。 | 日常备忘、说明文档、个人笔记 |

`official` 的主副标题和分级标题默认如下；西文均使用 Times New Roman，行距均为1.15倍，零首行缩进，默认与后文同页。这是实际预设，不等于公文标准认证。

| 块 | 中文字体与字号 | 对齐／加粗 | 段前／段后（pt） |
|---|---|---|---|
| title | 方正小标宋简体22pt | 居中／否 | 0／12 |
| subtitle | 方正小标宋简体14pt | 居中／否 | 0／8 |
| heading 1 | 方正小标宋简体18pt | 居中／否 | 0／0 |
| heading 2 | 黑体16pt | 左／否 | 0／0 |
| heading 3 | 楷体16pt | 左／是 | 0／0 |
| heading 4–9 | 仿宋16pt | 左／否 | 0／0 |

页码使用 WPS 既有字段 `page_numbering`，显式 true/false 优先；business/general 仍默认开启。公文页眉页脚与启用后的页码采用仿宋／Times New Roman、16pt、左对齐、1.15倍行距。PAGE 域中的1仅为占位结果，实际页码由阅读器更新。读取器不提取页眉页脚和执行域，因此这些文档仍可能返回partial，不能抹去诊断。

字体只写入名称，不检测安装、不嵌入字体。默认复合字体分别声明东亚与西文字体；显式 `runs[].font` 继续作为整段run的单一字体，字号等覆盖保留。映射支持方正小标宋简体；其他未知显式字体保留既有宋体回退，但回执新增 `unsupported_font_substituted` 并标记partial。缺少字体时实际替代仍由阅读器决定。

默认不按章节、篇幅或空段猜测新页。用户未要求时，不添加 `page_break`、`page_break_before:true`；标题用 `keep_with_next`，正文由阅读器自然分页。用户明确要求时可在标题或段落设置 `page_break_before:true`，或用独立 `page_break`，同一位置只选一种；不静默删除显式分页。不支持 DOCX 分节和自动目录。

本次默认值变更影响新建以及显式选择official的内容重排副本，不改写原件，不改变原文件的读取规则。需要旧外观时可显式给定page、paragraph参数及runs字体字号；页码仍可显式开启。DOCX的styles/profile/format不是WPS接口，不能直接照搬；未知配置继续报告spec_warnings。

## 支持的 Block 类型

- `title`、`subtitle`: 主副标题，按主题取默认值。
- `heading`: 1–9级标题，写入大纲级别；公文前四级对应DOCX，5–9级沿用四级外观。
- `paragraph`: 正文段落，可包含 `text` 或富文本 `runs` 数组（支持字体、字号、加粗、斜体、下划线）。
- `list`: 有序/无序列表项，带规整悬挂缩进。
- `table`: 二维表格，自动分配列宽与单元格边框，支持表头背景色。
- `callout`: 引用/批注强调框（两端内缩、楷体倾斜）。
- `page_break`: 强制分页符。

## 产物验证与交付收尾

`run_wps_task.py` 内置原子写入机制（先写 `.tmp_wps_` 临时文件，再原子替换）和底层只读回读验证（调用 `read_wps.py` 进行魔数与正文回读）。`--compact-receipt` 确保在成功时返回精简 JSON 凭据，避免大模型上下文膨胀。

## RTF结构与可验证排版范围

生成内容为标准库RTF流，.wps只是允许的后缀，不是金山原生WPS二进制。Unicode按UTF-16有符号码元输出，非BMP字符使用代理对；固定行距为负twips，倍数行距另用slmult1。默认A4；可用page对象指定width_pt、height_pt和margin_top_pt/margin_bottom_pt/margin_left_pt/margin_right_pt，数值必须有限且留出正文区域。

paragraph支持align、space_before_pt、space_after_pt、indent_chars、line_spacing_exact_pt、line_spacing_mult、keep_with_next、keep_together、page_break_before、widow_control。显式倍数行距覆盖主题默认固定值；两者均显式指定时固定值优先，可用null清除相应行距。table必须非空矩形，col_widths以磅计、长度等于列数且总宽不超过正文区域；未指定时均分，不再强制最小列宽导致宽表溢出。不会按内容猜换页或渲染测量。

title/subtitle/heading也接受上述段落参数和left_indent_pt/right_indent_pt/first_line_indent_pt/tabs_pt，显式值覆盖主题默认值。公文正文字符缩进同时写入cufi和等效fi回退，默认2字／32pt；显式first_line_indent_pt只使用磅数，不被字符缩进覆盖。基础公文不强制widow_control，保留阅读器默认；用户可显式设置。keep_together与keep_with_next是阅读器排版请求，不保证超长内容不分页。

公文表格使用仿宋／Times New Roman16pt、1.15倍行距、无首行缩进、零段前段后；表头居中加粗、无默认灰底，数据行默认左对齐，默认请求行内不拆分，可由row_keep_together覆盖。列表使用16pt、1.15倍行距、零段前段后与悬挂缩进，但仍是字面编号；自动续号、列表细节、表格内边距及callout不宣称与DOCX等价。RTF生成成功和回读成功不证明字体可用或视觉分页一致。

编辑为content_reflow_not_lossless：保留可获得的段落/表格顺序，重新排版，原字体、页眉页脚、字段、对象、修订和页面布局不保证保留。必须输出新文件；默认对partial输入或缺少表格顺序拒绝自动重建。用户明确接受潜在损失时，可加--allow-lossy-reflow（API: allow_lossy_reflow=True），只发布到未占用的新文件：保留已读内容、原始issues/diagnostics/omitted_regions及assessment，未知顺序明确采用正文段落后接表格，不声称恢复原位置。副本正文增加部分内容重排提示，回执partial=true、losses列明损失，CLI退出3；不据此跳过危险格式或资源检查，原件保持不变。创建/编辑先验证临时候选再发布；候选失败不覆盖目标。回执partial/源诊断与layout_verification=not_run分别核对，success不等于实机视觉验收。

## 标题、制表位和表格增量

- heading.level支持1–9，写入对应0–8的RTF大纲级别；这是大纲语义，不是命名样式表、自动目录或读取器的格式识别保证。公文5–9级沿用四级外观，其他主题三级以上沿用三级外观。
- paragraph增加left_indent_pt、right_indent_pt及first_line_indent_pt；后者覆盖indent_chars，可用负值表达悬挂缩进。tabs_pt为严格递增的左对齐制表位磅数列表，例如[72,144]，正文用真实TAB字符。暂不支持右/居中/小数点制表位、引导符或多级编号。
- list.start可指定字面有序列表的起始数字，默认1；旧项目文本和项目符号行为保留，不生成自动编号listtable，不能保证客户端自动续号。
- table.col_alignments与列数一致，支持left/center/right/justify，作用于数据行，首行仍使用原表头样式。repeat_header默认true；只有header=true的首行可重复，false仅关闭重复，不取消表头样式。row_keep_together控制行内不拆分；与后行同页、重复表头、行内不拆分是不同控制，实际分页须客户端验收。
- 未实现的styles/style等选项不会被当作已应用：为保持旧输入可用，回执记录spec_warnings、spec_warning_count并置partial；最多保留100条路径诊断，不复制原始参数值。非法参数数值/类型、不能表达的原生多级列表等明确报错，不生成假功能。

示例：paragraph中设置"left_indent_pt":36、"first_line_indent_pt":-18、"tabs_pt":[72,144]；表格设置"col_widths":[72,144]、"col_alignments":["left","right"]、"repeat_header":true。列宽仍以磅计，必须适合可打印区域；字体、渲染、换页和表头实际跨页重复未在办公客户端验证。

维护语义依据：微软RTF 1.9.1第78–83页与第96页的段落/制表位/表格控制说明，来源 `https://officeprotocoldoc.z19.web.core.windows.net/files/Archive_References/[MSFT-RTF].pdf`。仅借鉴规范和接口设计，不复制DOCX实现或规范示例代码。

## 表格合并和边框

merges为零基、闭区间的[r0,c0,r1,c1]列表，例如横向[0,0,0,1]、纵向[1,0,2,0]或矩形[1,0,2,1]。最多256个范围、100000个覆盖格；范围须在原始矩形rows内、至少两个格、互不重叠。内容仅放左上锚点，其他覆盖格必须为明确空字符串，否则拒绝，绝不拼接或吞掉原值。支持同时跨行跨列的矩形合并，按每个物理格的横向/纵向状态写出控制标记；不接受反向范围或布尔坐标。纵向跨首行重复表头边界时拒绝；确需该结构可显式repeat_header=false，不声称跨页显示已验收。

borders支持all/none/outer，也接受true/false分别规范化为all/none；border_width_pt默认0.5，范围0.05..12磅，必须有限。仅支持单实线。每个物理格明确写出四边实线或brdrnil，避免边框状态继承；outer仅给物理表格外沿边框，不宣称复杂合并后的最终显示已验证。col_widths仍为各物理列的磅数，跨行保持一致边界，不以合并后单元格数重新分配列宽。

生成仍为RTF。读取器保留每个物理格的文字、空格位及表格顺序；对行原点为0、各行相同且严格递增的cellx边界、数量匹配且续接标记闭合的显式网格，返回merges、cell_boundaries_twips和merge_geometry_status=explicit_grid_recovered。支持横向、纵向及矩形组合，不拼接或删除覆盖格文字，也不声称视觉布局已还原。

边界/格数不一致、非零行原点、孤立或冲突标记、超过100000物理格或256范围时，保留已读文字并以rtf_merge_geometry_not_reconstructed说明partial；覆盖格有文字则另外报告rtf_merge_covered_content_retained，阻止静默丢弃。几何诊断最多保留100条，geometry_issues_truncated记录其余数量。不把几何恢复失败升级成全文读取失败，也不放松原有安全限制。

正常重排编辑对已确认且无覆盖文字冲突的网格保留合并范围和物理列宽，输出新文件；这是受限结构往返，不是原格式无损编辑。partial输入仍走显式降级流程，保持原件并列明潜在结构损失。任意变宽行、嵌套表、复杂行偏移和跨页合并显示不在认证范围；候选partial仍须交付时说明。

语义依据为微软RTF 1.9.1表格控制：保留每行cellx与cell数量对应，横向与纵向合并分别用独立标记；只实现上述可验证子集。未增加外部渲染依赖，未进行WPS/Word实机或跨平台分页验收。

创建 CLI、公开 create_document API 与 RtfDocument.save 均只接受 `.wps/.rtf` 输出，拒绝将 RTF 写成 `.doc/.docx`。回执 `output_format=rtf`、`binary_doc=false`；需要二进制 DOC 时使用有条件的导出路径，见 `<skill-root>/references/export-libreoffice-backend.md`。
