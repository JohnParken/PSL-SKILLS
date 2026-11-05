# 输入与能力契约

## 目录

- JSON 配置（`<skill-root>/references/input-contract.md#json-配置`）
- 路径与输出（`<skill-root>/references/input-contract.md#路径与输出`）
- SVG 子集（`<skill-root>/references/input-contract.md#svg-子集`）
- 图片、图表、表格与公式（`<skill-root>/references/input-contract.md#图片图表表格与公式`）
- 内容核对与细节（`<skill-root>/references/input-contract.md#内容核对与细节`）
- 现有 PPTX 的读取边界（`<skill-root>/references/input-contract.md#现有-pptx-的读取边界`）

路径占位符说明：`<skill-root>` 是占位符；从本次主入口 `SKILL.md` 的真实 `file_path` 定位，相对路径先按宿主确认的读取基准解析，再去掉文件名。使用前整体替换为真实目录，将完整路径作为一个参数正确引用；无法定位时停止。
定位依据是主入口，不是当前参考文档目录。相对路径先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名。使用前将占位符整体替换，保留资源路径，将完整路径作为一个参数正确引用并按执行器语法转义。
无法取得主入口真实读取路径时停止依赖操作；不要按当前工作目录、环境变量、其他会话或仓库位置猜测。所有示例数据路径均相对宿主确认的任务工作目录。
输入和输出不得位于安装目录，不通过绝对路径、父目录、符号链接或切换挂载绕过边界；不要切换到安装目录或向其写入。
先执行 主入口自检（`<skill-root>/SKILL.md#执行环境约束与首步自检`），再使用本契约。

## JSON 配置

`create CONFIG OUTPUT` 读取任务目录内的 UTF-8 JSON；`CONFIG` 为 `-` 时读取 stdin。未知字段、重复键、非有限数值、非法 Unicode 和深度超过 64 的 JSON 均拒绝。

| 字段 | 类型与要求 |
| --- | --- |
| `schema` | 必填，固定为 `psl.pptx.v1` |
| `title` | 字符串，默认 `Presentation`，最多 512 字符 |
| `width`、`height` | 数值，默认 1280、720；每边 96–5376 px，不接受布尔值 |
| `text_flow` | `preserve`（默认）、`reflow` 或 `split`；默认保留 SVG 的文本布局意图，不保证字体测量与桌面应用完全一致 |
| `native_objects` | 布尔值，默认 `true`；原生图表和表格要求开启 |
| `slides` | 必填，1–200 页；每页只能包含 `svg`、`svg_path`、`notes` |
| `slides[].svg` / `svg_path` | 必须且只能选择一项：SVG 源字符串或任务相对路径 |
| `slides[].notes` | 纯文本字符串，默认空字符串，最多 200000 字符；不解释 Markdown |

```json
{
  "schema": "psl.pptx.v1",
  "title": "进度汇报",
  "slides": [{
    "svg": "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 1280 720\"><text x=\"80\" y=\"100\" font-size=\"36\">进度汇报</text></svg>",
    "notes": "说明当前进度与下一步安排。"
  }]
}
```

配置最大 32 MiB，单页源 SVG 最大 8 MiB。图片内联、引用展开和原生对象预留共同受全稿 128 MiB 保守预算约束；这不是进程峰值内存承诺。
每页预计展开节点最多 20000，源和预计展开深度最多 128；复用引用在展开前检查，循环、缺失与重复 ID 明确失败。保守估算可能先于字面文件大小限制拒绝输入。
公式先按有界编译后的数学结构、UTF-8 字节和重复样式成本估算，再加 64 KiB 基础预留；块公式与行内公式累计计入预算。SVG 预览及图片另外计费，保守估算可能拒绝实际较小的结果。

## 路径与输出

所有数据路径均以实际任务工作目录为基准：配置即使位于 `specs/presentation.json`，其中的 `slides/one.svg` 仍从任务目录解析。
SVG 文件中的图片 `images/photo.png` 也相对任务目录，不相对 SVG 所在目录。拒绝绝对路径、`..`、反斜杠、盘符和符号链接；输入必须是普通文件。
输出必须是尚不存在的 `.pptx`，父目录必须已存在；无覆盖、自动改名或创建父目录行为。生成、校验在内存处理，暂存与收据在输出同目录预先登记后独占创建。
暂存完整写入、同步和逐字节读回后，通过同目录硬链接独占发布；不支持该文件系统能力时失败，不改为直接写最终名称。
发布前强杀时最终名称不存在；发布后强杀时最终文件已完整写入。这里保证进程中断时的名称发布完整性，不声明已验证断电、磁盘硬件故障或恶意同用户目录写入者。
正常情况下暂存与收据均清除。发布与清理分别回报；`publication.cleanup_complete: false` 时，`retained` 列出本次尚未安全收尾的登记项，身份不明时不据此认领或删除。
stderr 的创建前登记事件含 `output_parent`（相对任务目录）、`journal`、`staging`、`final`（均为文件名）、`parent_identity`、`expected_bytes`、`sha256`。
有效收据还记录创建身份；强杀后只按原登记核查，不自动遍历目录寻找“看起来像”暂存的文件。主文件与内部暂存的处理分开，归档失败仍保留主文件。
读取静态 四页示例（`<skill-root>/assets/example.json`） 后可将其内容直接作为 stdin；不要将安装目录路径作为业务配置数据路径传入 CLI。

## SVG 子集

根元素使用标准 SVG 命名空间，`viewBox` 必须严格匹配 `0 0 width height`。使用显式布局和受支持的属性；本入口不是完整浏览器 SVG 渲染器。
支持的元素包括 `g`、`defs`、基本形状、`path`、`polyline`、`polygon`、`text`、`tspan`、`a`、`image`、`use`、`symbol`、渐变、`stop`、`clipPath`、`marker` 及说明性元素。
元素、可视属性及内联样式采用封闭允许列表；未知属性会失败，不静默忽略。拒绝 CSS 类、`style` 元素、`filter`、`mask`、事件、动画和外部图标库 `data-icon`。
`use`、渐变及样式中的引用只允许同文档 `#id`；`href` 与 `xlink:href` 同时提供时必须一致。`use` 不得克隆原生对象标记或公式。
允许的图片来源是任务相对路径或 PNG/JPEG 的 base64 data URI。普通链接仍受包关系安全校验，不允许借此引入外部数据、脚本或嵌入对象。
不要要求字体塑形、字体轮廓化、布尔路径运算或浏览器 CSS 完整保真；该版本不含外部字体、渲染或路径运算依赖。
文字采用随包的字宽与字体度量数据计算宽度和首行基线；不查询系统字体或联网下载字体。字体文件不随包分发，桌面应用的字体替代与显示仍需实际验收。

## 图片、图表、表格与公式

- 图片仅支持 PNG/JPEG，保留原始字节并生成原生图片对象；`meet`/`slice` 使用 DrawingML 尺寸或裁剪信息，不执行像素重编码、优化或像素裁剪。
  拒绝 APNG、GIF、WebP、SVG/EMF 图片及 EXIF orientation 非 1 的 JPEG；有旋转方向标记的源图须在本技能外先归一化。
  每图最多 32 MiB，单边最多 32768 px、最多 6400 万像素，PNG 展开扫描线最多 128 MiB。PNG 校验块、CRC 与展开长度；JPEG 校验标记与帧头，不执行完整像素解码。
- 原生对象以 `data-pptx-replace-with` 标记 `chart`、`table` 或 `formula`；不得嵌套原生标记。每个标记必须且只能有一个 JSON 载体，载体最大 1 MiB。
  JSON 可通过 `data-pptx-json`、`data-pptx-data` 或直接子级 `metadata type="application/json"` 提供；不要同时使用多种载体。具体结构以四页示例为依据。
- 图表与表格必须声明 `data-pptx-native-authority="json"`。图表仅使用 `type` 字段，支持 `column`、`bar`、`line`、`area`、`pie`、`doughnut`、`scatter`；不接受 `chart_type` 别名。
  每图最多 50 个系列，每系列最多 2000 点，总计最多 10000 点；散点系列使用 `x`、`y` 字段。内嵌工作簿保存图表数据，不接入外部数据源。
  图表部件在整份演示文稿内统一分配名字，跨页复用编号不会覆盖前页图表或工作簿；不开放已有 PPTX 图表的原位编辑。
- 表格 schema 为 `ppt-master.semantic-table.v2`，最多 100 行、50 列、5000 个解析后单元格。使用示例中的语义表格结构，不将任意 HTML 表格视为受支持输入。
  合并区域被覆盖的单元格可提供填色、边框、内边距和对齐，仍须留空正文；独立文字或段落会失败，不隐藏或丢弃内容。
  在合并区域左上单元格设置正整数 `row_span` / `col_span`，省略时均为 1；被覆盖的位置仍保留空字符串，或使用 `{"merge_continuation": true}` 并附加所需格式。
  `columns` 是可选表头行，`rows` 是正文行；返回的物理行号包括表头。下例是一个 3 行 × 3 列的表格载荷，第一条正文的前两格合并。

```json
{
  "schema": "ppt-master.semantic-table.v2",
  "x": 80, "y": 100, "width": 1120, "height": 300,
  "columns": ["项目", "负责人", "状态"],
  "rows": [
    [{"text": "联合事项", "col_span": 2}, "", "完成"],
    ["单项", "小张", "处理中"]
  ]
}
```

将该载荷置于表格标记的唯一 JSON 载体中，仍声明 `data-pptx-native-authority="json"`，具体包裹方式沿用四页示例。

- 公式转换为原生 Office 数学对象；LaTeX 输入最多 8192 字符，支持转换器的有限语法。使用已支持的表达式并核对内容，未知命令应报告而非擅自改写。
  块公式提供 SVG 预览子节点时，另生成原生形状 `mc:Fallback`；行内公式以已有预览文字生成备用文本框。预览仍须符合相同 SVG、图片与链接边界，不自动绘制缺失的公式预览。
  行内公式在 `text` 内的 `tspan` 上声明，例如 `<tspan data-pptx-inline-formula="x^2">x²</tspan>`；属性提供 LaTeX，元素文字提供备用显示内容。

## 内容核对与细节

`create` 在发布前自动执行结构验证和输入核对：页数、画布尺寸、标题、备注段落、原生对象数量及图表数据、表格单元格与合并结构、公式结构。
成功返回 `content_validation: "passed"`、`source_comparison: "passed"` 和实际检查范围 `content_checks`、`source_checks`。
普通 SVG 文字、形状、图片的版式与全部属性不在本轮逐字段输入比对内；仍须结合原始材料和实际阅读软件验收。

`inspect INPUT --details` 与 `validate INPUT --content` 会执行同一套原生内容检查。前者返回细节，后者仅返回摘要；两者没有原始 JSON，`source_comparison` 均为 `not_run`。
仅修改图表缓存而未同步工作簿会失败；若缓存与工作簿被同时改成相同错误值，已有文件检查无法获知源数据。创建时的输入比对会独立核对源系列。
`content_object_count` 是实际检查的原生对象总数；为 0 时不能宣称检查过图表、表格或公式内容。

详细结果位于 `slides[].native_objects`；每项有 `kind`、同类对象从 1 起的 `number`、可定位的 `object_id` / `object_name` 和 `data`。

| `kind` | `data` 的含义与边界 |
| --- | --- |
| `chart` | 支持类型的系列、分类或散点坐标、缓存与内嵌工作簿一致性；按点索引读取，缺失点保留为空；不执行工作表公式或外部引用 |
| `table` | `row_count`、`column_count`、EMU 列宽与行高、二维 `rows`；单元格包含 `paragraphs`、`text`、`merge_anchor`；`merges` 中行列均从 1 起 |
| `formula` | `representation: "omml"` 和 `structure` 数学树，保留分子/分母、上下标等顺序与数学属性；字体颜色等显示样式不参与比对 |

公式树用于检查数学结构传递，不计算表达式，也不证明数学等价；创建时复用有限 LaTeX 编译器获得预期树，不能据此独立证明编译器自身全部正确。
图表数值使用 `numeric_encoding: "decimal_string"` 的精确十进制字符串，缺失数字使用 JSON `null`，避免读取时先转成二进制浮点数而损失精度。
图表详查限定一个受支持的绘图区；`numRef` / `strRef` 必须能解析到已验证的内嵌工作簿，仅接受带工作表名的单行或单列正向 A1 范围。
命名范围、三维引用、公式单元格、外部数据、无法解释的数字转文字格式会明确失败；字面量来源没有工作簿核对对象时标为 `not_applicable`，不伪称已核对引用。
创建端保留现有 Python JSON 浮点语义；整数和数字字符串若经转换器浮点传输后改变数值则拒绝发布。原始 JSON 小数词法不保留；精度敏感的值可用数字字符串表达，无法无损传输时会报错。
工作簿数字格式以 ID / 格式码提供元数据，不实际执行格式渲染；日期分类仍按存储的数值报告，不自动转换日历日期。
简要文字中的 `1215` 不能证明公式为 `12/15`，应查看细节中 `f` 下的 `num`、`den` 结构。
详查最多累计 10000 个原生对象、8 MiB 原生细节 JSON；表格仍限 100 行、50 列、5000 格。不支持的详查结构或超限会明确失败，不输出伪完整结果。

## 现有 PPTX 的读取边界

只处理有界的 Transitional PPTX 子集。拒绝宏、ActiveX、OLE、非 XLSX 嵌入对象、不支持的媒体、危险外部关系和损坏或无法解析的包引用。
ZIP 最大 128 MiB，成员最大 64 MiB；外层与内嵌包共用最多 256 MiB 展开预算及 5000 个成员；单个 XML 最大 16 MiB、深度 128、节点 100000。
`inspect` 返回 `title`、`slides`，逐页包含 `number`、文本段落数组 `text`、备注段落数组 `notes` 和 `objects` 中的形状、图片、表格、图表计数。
默认摘要不包含图表数值、表格行列维度或数学树；需要这些字段时使用 `--details`。不要用文字摘要证明公式结构，也不要用对象计数代替数据核对。
读取 MCE `AlternateContent` 时按声明位置解析 `Requires` 前缀，选择第一个支持的 `Choice`，否则选 `Fallback`；没有可读分支时明确失败。
当前选择覆盖实现已识别的 PresentationML、DrawingML、Office Math、a14 数学扩展与图表命名空间，不是完整 MCE 处理器。文本、备注和计数只读所选分支，关系与结构安全检查继续覆盖所有分支。
`Ignorable` 和 `ProcessContent` 按声明处的命名空间与祖先作用域处理：未知可忽略包装默认整段跳过，明确列入 `ProcessContent` 的包装只展开其内容；前缀重绑定不改写祖先已经展开的名称。
`MustUnderstand` 只在实际处理的内容上要求支持；未选中的备用分支和已丢弃的子树不会引入必须理解的要求。原始各分支仍检查 XML、关系和禁止的包内容。
绘图 ID 的格式和范围仍检查所有原始分支；唯一性只检查实际保留的绘图，互斥分支或被丢弃子树可复用 ID。同时保留的两个对象复用 ID 仍会失败。
未知 MCE 控制属性和元素明确失败；本版不实现 `PreserveElements` / `PreserveAttributes`。`ProcessContent` 包装若带 `xml:base`、`xml:lang` 或 `xml:space` 会拒绝，避免丢失无法安全展开的 XML 语义。
两种读取命令均返回 `slide_count`、`parts`、`slide_size`、`sha256`、`structural_validation: "passed"` 与 `visual_validation: "not_run"`；后者不因结构通过而改变。
结构检查覆盖实现约定的 XML、ZIP、内容类型与关系，不是完整 OOXML schema 认证，也不证明 PowerPoint/WPS 的实际显示效果。验证实际最终文件后再交付。
