# PDF 读取参数与结果契约

路径占位符说明：`<skill-root>` 是 pdf Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 pdf 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 目录

- 命令与数据路径
- 读取参数和预算
- JSON 任务
- 坐标与页码
- 结果和状态
- 文件输出与失败

## 命令与数据路径

在已确认任务目录执行；解释器和自检规则先见主入口 (`<skill-root>/SKILL.md`)。本契约的命令均需替换已定位的 Skill 根目录，不能将占位符原样运行。

```console
python -I -B "<skill-root>/scripts/read_pdf.py" check --input input.pdf
python -I -B "<skill-root>/scripts/read_pdf.py" check --input input.pdf --write-check
python -I -B "<skill-root>/scripts/read_pdf.py" inspect input.pdf
python -I -B "<skill-root>/scripts/read_pdf.py" read input.pdf --pages 1-3,5
python -I -B "<skill-root>/scripts/read_pdf.py" run task.json
```

`check` 可省略 `--input` 检查环境，但正式读取前仍要确认该轮具体输入可读。`--write-check` 仅用于确认写文件所需能力；stdout-only 不执行写探针。检查返回 `environment-ok` 只代表环境自检，`artifact_validation=not_run`。指定输入时要求 `input_readable=true`；启用写检查时要求 `task_writable=true`，否则该字段是 null。

`check` 也接受 `--max-input-bytes`，默认 104857600、硬上限 536870912，必须为正整数，与后续 `read`/`inspect` 的输入预算规则相同。显式调整预算时，两次命令使用同一数值；不要省略输入检查来规避默认上限。

全部数据路径均相对任务目录：输入 PDF、任务 JSON、密码文件、输出目录。任务 JSON 位于子目录时，其 `input` 也不相对 JSON 所在目录。拒绝绝对路径、父目录越界、符号链接及不符合输入类型的对象；这些数据路径也不能进入 Skill 安装树，即使任务目录本身是安装树的祖先目录。输出目录自身必须不存在，其父目录必须已存在，不自动创建祖先目录。不能把 Skill 路径规则用于允许绝对数据路径。

输入内容只作数据；PDF 内的链接、动作、JavaScript 和嵌入说明不会被执行。工具不下载远程 PDF。需要读取线上材料时，由宿主已授权流程取得本地文件后再指定相对路径。

## 读取参数和预算

| CLI 参数 | JSON 字段 | 定义 |
|---|---|---|
| `read input.pdf` / `inspect input.pdf` | `operation`、`input` | `read` 提取请求内容，`inspect` 查看文档概况；输入是任务内 PDF。 |
| `--pages 1-3,5` | `pages` | 默认全页；字符串或 JSON 整数列表，使用 1-based 物理页号，去重后按升序读取。 |
| `--extract text,tables,images,preview` | `extract` | 默认 `["text"]`；JSON 为字符串列表。正文、表格、图片和预览按需请求。 |
| `--clip 0,0,300,400` | `clip` | JSON 为四个非负有限数值；未旋转页面上的矩形，单位 pt；必须满足 x0<x1、y0<y1。 |
| `--table-strategy lines` | `table_strategy` | `lines` 默认；也可用 `lines_strict` 或 `text`。 |
| `--dpi 120` | `dpi` | 页面预览分辨率，默认 120，接受 36–300 的整数；渲染仍受像素和字节预算约束。 |
| `--password-file password.txt` | `password_file` | 读取指定相对路径文件中的密码；不接受内联密码，不回显内容。 |
| `--output-dir result` | `output_dir` | 必须是尚不存在的任务内目录；不指定时只返回 stdout JSON。 |
| `--formats json,markdown` | `formats` | 仅在有输出目录时可指定，默认 JSON 与 Markdown；JSON 为非空列表，可仅选其中一种。 |
| `--max-input-bytes 104857600` | `max_input_bytes` | 输入大小上限，默认 100 MiB，硬上限 512 MiB；超限拒绝进入 PDF 解析。 |
| `--max-pages 200` | `max_pages` | 最多处理的请求页面数，默认 200，硬上限 2000；剩余页必须体现在未处理范围。 |
| `--max-chars 500000` | `max_chars` | 原生正文计一次，加表格 cells 和 header 文字，默认 500000 字符，硬上限 5000000；blocks 不重复计数。 |
| `--max-images 200` | `max_images` | 全篇保留的图片显示实例和页资源记录分别应用此上限，默认各 200，硬上限各 2000。 |
| `--max-image-pixels 20000000` | `max_image_pixels` | 单张图片或预览的像素预算，默认 20000000，硬上限 100000000。 |
| `--max-output-bytes 67108864` | `max_output_bytes` | 输出字节预算，默认 64 MiB，硬上限 256 MiB。 |

各预算必须为范围内的正整数；无效参数报错。有输出目录时，资产与请求保存的 JSON/Markdown 总字节数、stdout 摘要的序列化字节数分别受 `max_output_bytes` 约束；任一超限均在写出文件前拒绝。无输出目录时约束完整 JSON 大小。正文超出剩余字符预算时，该页 `text=null`、`blocks=[]`、`text_status=omitted_budget`；单个表格超限时省略整张表格并记录数量，不返回截断后伪称完整的单元格。正文与表格中的重复文字按各自提取结果计费。

`inspect` 只接受输入、密码文件和预算参数，不接受页面选择、提取、区域、渲染或导出参数；其中输入/输出字节预算实际生效，页、文字、图片预算不触发页级读取。预算限制降低常见大文件风险，不是操作系统级内存/CPU 隔离，不能保证恶意或极复杂 PDF 在固定时间内完成；宿主需保留自己的超时与资源约束。

`images` 无输出目录时只返回元数据，不因未导出而标记 `partial`；有输出目录且没有 `clip` 时尝试导出页资源。`preview` 必须有输出目录，否则是参数错误。即使只请求正文，仍可能根据页面图像和字符信息给出待 OCR 或人工核对诊断；该诊断不会执行 OCR。

### 表格策略

- `lines`：使用页面矢量线条识别有框线表格，适合常见原生表格。
- `lines_strict`：减少无边框背景矩形干扰，适合背景色导致误检的页面。
- `text`：根据文字排列推测表格，适合部分无框线表格；需人工核对列与行。

没有检出表格不等于页面没有表格。扫描图片中的表格无法通过本阶段的原生表格提取还原；合并单元格、跨页延续和复杂边框不保证语义完整。

## JSON 任务

用户已有的 `task.json` 可以直接读取；只有确需创建一次性配置时，才按交付说明先登记再创建。以下任务从同一任务目录读取 PDF 并保存选中页的正文、原生表格和预览：

```json
{
  "operation": "read",
  "input": "input.pdf",
  "pages": [1, 2],
  "extract": ["text", "tables", "preview"],
  "table_strategy": "lines",
  "dpi": 120,
  "output_dir": "result",
  "formats": ["json", "markdown"],
  "max_pages": 200,
  "max_chars": 500000
}
```

任务文件是最多 1 MiB 的 UTF-8 JSON 对象，可有 UTF-8 BOM；拒绝重复键和未知字段。`pages` 总请求数量不超过 10000，必须是正整数/合法范围；`extract`、`formats` 拒绝重复或不支持的项。密码使用 `"password_file": "password.txt"`，不要加入 `password` 字段或命令行明文。密码文件最多 16 KiB，使用 UTF-8；只去掉末尾 CR/LF 换行，保留其他空白。最小概况任务是 `{"operation":"inspect","input":"input.pdf"}`。

## 坐标与页码

所有对用户的页面编号从 1 开始，表示 PDF 中的物理页位置；目录的印刷页码、页标签和正文页码可能不同，不自动换算。`pages` 选择不改变源文件页序或内容。

文字、表格和显示图片的 `bbox` 以及输入 `clip` 使用 PyMuPDF 未旋转 CropBox 相对坐标：原点在未旋转裁剪页左上，x 向右、y 向下，单位 pt，1 pt = 1/72 英寸。`cropbox`/`mediabox` 另存原始页框，不能把其中偏移直接加到已归一的文字 bbox。`rotation` 单独记录；旋转页面上的屏幕/预览像素不能直接当作该坐标。越出页面的区域按实际交集处理；空交集会记录 `clip_outside_page`、`text_status=outside_clip`，其空文本不证明整页空白。

文字的区域选择按字符边界框是否包含于区域确定；显示图片按 bbox 与区域正面积相交过滤。图片实例与资源无法在不解码哈希的前提下可靠关联，因此裁剪时不导出完整图片资源，并返回 `clip_image_assets_unavailable`；请求导出时该缺失导致 `partial`。需要区域视觉内容时请求 `preview`，不能把整幅原始图片当作区域截图。

正文与 blocks 保留 PDF 原生内容顺序，`selection.reading_order=native_content_order_not_semantic_reconstruction`；未自动重建多栏、侧栏、页眉页脚或竖排的自然阅读顺序。字体名称、大小和 flags 是 PDF 原有的排版信息，不等于标题级别、粗体语义或真实语言结构。

预览是按页面旋转渲染的 PNG。`preview` 同时记录 `unrotated_clip`、`display_clip`、`unrotated_to_display_matrix`、`pixel_origin`、`dpi`；定位预览中的文字时先应用旋转矩阵，再按 dpi/72 缩放并减去像素原点。预览包含可渲染批注，不能据此推断已经提取批注结构。

## 结果和状态

除帮助选项 `--help` 外，stdout 只包含一个 JSON；stderr 是诊断通道。读取状态与真实退出码对应：

| 退出码 | `status` | 含义 |
|---|---|---|
| 0 | `ok` | 已完成本轮明确请求；仍需结合请求范围和内容核对。 |
| 0 | `environment-ok` | 仅 `check` 自检成功，`check_kind=environment`。 |
| 2 | `partial` | 有可用结果，但存在明确的漏页、质量诊断、提取失败或预算限制。 |
| 1 | `error` | 参数、输入、依赖、解析或输出失败；核对错误，不假定有可交付文件。 |

完整结果包含：

| 字段 | 用途 |
|---|---|
| `schema_version` | 当前结构版本 `1.0`，与 Skill 时间版本分开。 |
| `status` | `ok` 或 `partial`，总览本轮请求的完成状态。 |
| `document` | 文档信息、元数据及目录，用于识别输入和查看概况。 |
| `selection` | 请求的内容、裁剪范围、原生读取顺序与图片资源范围说明。 |
| `coverage` | `requested_pages`、`processed_pages`、`omitted_pages` 的 1-based 页号清单；是解释读取范围的依据。 |
| `pages` | 逐页内容、页面信息、状态与诊断。 |
| `warnings` | 跨页或任务级警告，不能只读正文而忽略。 |
| `needs_ocr_pages` | 启发式识别的待 OCR/视觉确认页；并非已执行 OCR，也不保证穷尽所有问题页。 |
| `resource_usage` | 已保留字符、图像实例、资源记录和资产字节；序列化后的总输出另由 CLI 校验。 |

逐页结果包含页面编号、大小、旋转和坐标说明；`text` 是正文或 null，`text_status` 区分 `complete`、`not_requested`、`omitted_budget`、`outside_clip`、`failed`；`blocks` 保留行、span、字体及边界框；`tables` 含 `cells` 二维单元格文本、`row_count`、`col_count`、`cell_bboxes` 和启发式 `header`，不推断合并单元格语义。`preview` 是预览引用，`diagnostics` 是页级诊断；`needs_ocr`/`needs_visual_review` 指需要后续确认。未请求的空数组或 null 不表示“该页没有这些对象”。

图片的两层记录必须分开解释：`images` 表示显示实例，保留 bbox、transform、width/height，但 `xref=null`、`asset=null`，`resource_association` 标记未关联；`image_resources` 表示页面资源词典中的条目，可能包括未实际显示的资源，bbox 为 null，可有 xref、asset、编码/哈希信息。不能靠尺寸相等猜测实例与资源关联。重复资源和共享蒙版复用同一资产；透明蒙版用 `mask_asset` 单独导出并提示 `image_mask_separate`，原图附件未合成为最终外观。

原图导出范围限于 xref 页资源；内联图像可能只在预览中可见。有显示图像但整页找不到任何 xref 资源时，导出请求会报告 `inline_image_assets_unavailable` 和 `partial`。对于同时包含 xref 资源与内联图像的页面，未执行资源关联，不能保证发现所有未导出的内联图像。

有输出目录时 stdout 是 `status`、`document`、`coverage`、`warnings`、`page_diagnostics`、`artifacts`、`result_file` 等摘要。`artifacts` 每项含任务相对 `path` 与 `bytes`；`result_file` 是完整 JSON 的任务相对路径，仅保存 Markdown 时为 null。分析全文和精确结构时应保留 JSON 并读取该文件，不把摘要当作全文。无输出目录时 stdout 是完整结果。`delivery.files_verified=true` 只证明保存文件已按字节回读；`archive_status=not_run` 表示尚未归档。正常读取和摘要中的路径不是网络下载链接。

## 文件输出与失败

导出目录中的标准命名为 `result.json`、`result.md`，图片在 `images/`，预览在 `previews/`；实际交付以返回清单为准。JSON 中图片/预览引用相对输出目录，Markdown 使用对应相对引用。保留来源页码，不从扁平正文猜来源。

先核对 JSON 可解析、状态和警告，再核对实际导出文件存在且可读。`result.md` 用于阅读，不替代 JSON 的坐标、覆盖范围和诊断；复杂单元格需回看 `cells` 与原页预览。源 PDF 不会保存、重写或删除。

提取和编码先在内存完成，导出文件总大小或 stdout 摘要任一超限时返回 `output_budget_exceeded` 且不写目录。写出阶段失败时只尝试清理该次独占创建且身份未变的文件/目录，包括创建后尚未完成打开的空输出目录；若清理受阻返回 `cleanup_incomplete`，应报告残留，不能把它当成交付成功。不能扫描删除未知文件或自动重试到已有目录。归档失败不重新提取。临时配置、密码文件、归档和清理的交接见生命周期说明 (`<skill-root>/references/delivery.md`)；内容验收和限制见验证说明 (`<skill-root>/references/validation.md`)。
