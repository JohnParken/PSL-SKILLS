# 公开命令与输入契约

路径占位符说明：从宿主实际读取主入口 `SKILL.md` 的真实 `file_path` 去掉文件名确定 `<skill-root>`；相对读取路径先按本次已确认的基准目录解析。使用前替换占位符并引用完整路径，参考文档目录不是根目录；无法定位时停止并报告，不猜路径。

## 目录

- 公共约束与自检
- convert 与 build
- analyze、import 与 rebuild
- 布尔与文字轮廓
- 离线文本测量与 SVG 预检
- 离线素材查询与选择
- 模板公开诊断入口
- 预算、回执与恢复

## 公共约束与自检

先完成 `<skill-root>/SKILL.md` 自检。以下命令均在宿主已确认的任务工作目录执行并保持不变，所有任务文件为顶层裸文件名。公开入口 `<skill-root>/scripts/tl_pptx.py`，API `1.0`。输出必须不存在，禁止覆盖；输入不得为链接。解释器由部署方提供，不自动创建/安装或回退。

```console
"<skill-root>/scripts/.venv/bin/python" -I -B "<skill-root>/scripts/tl_pptx.py" check
"<skill-root>/scripts/.venv/bin/python" -I -B "<skill-root>/scripts/tl_pptx.py" check --write
```

`optional_capabilities` 只报告当前依赖与功能边界，不验证 Office 许可证或字体可读性，不安装其他组件。全部写产物命令还会进行写入自检。受保护操作验证独立发行完整性及各自部署条件，不直接调用内部脚本绕过。

## convert 与 build

最小页面保存为任务根目录 `page_01.svg`：

```xml
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">
  <rect x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>
  <text x="80" y="120" font-family="Arial" font-size="48" fill="#172554">项目进展</text>
  <text x="80" y="240" font-family="Arial" font-size="28" fill="#334155">填写有来源的事实。</text>
</svg>
```

按最终页序列出互异 SVG；每页相同零原点 viewBox。convert 仅接受文档内 `#id` 或内嵌 PNG/JPEG/SVG 资源，不自动访问外部 URL、本地图片、data-icon 或外置 native payload。build 的显式资源映射例外见下方。原生对象示例见 `<skill-root>/references/native-objects.md`。图片不会自动变为可编辑图表。

```console
"<skill-root>/scripts/.venv/bin/python" -I -B "<skill-root>/scripts/tl_pptx.py" convert "简版.pptx" "page_01.svg"
```

完整 builder 的 `build.json` 最小例子：

```json
{
  "slides": [{"svg": "page_01.svg", "notes": "讲解项目当前进展。"}],
  "options": {"transition": "fade", "transition_duration": 0.7}
}
```

```console
"<skill-root>/scripts/.venv/bin/python" -I -B "<skill-root>/scripts/tl_pptx.py" build "汇报.pptx" "build.json"
```

`slides` 每项为 `svg` 与可选字符串 `notes`。notes 是普通演讲者备注，不是旁白音轨。`audio`、`transition_sound`、旁白时间及动画声音配置不再支持，输入须明确失败，不能静默忽略。`layouts` 为明确列出的布局定义 SVG 名单，不根据外观自动升级 Master/Layout。`options` 接受受控 builder 参数，例如 transition、auto_advance、animation、animation_duration、animation_trigger、animation_config、doc_metadata、image_quality、text_flow、primary_language。结构/动画仍须满足上游契约；未知键、类型错误和 filesystem-valued 参数拒绝，不能传 cache_dir、resource_root、危险兼容开关或任意 source_package。

可选顶层 `spec_lock` 指定任务根目录已有的 UTF-8 `.md` 裸文件名，最多 1 MiB，例如在 `build.json` 添加 `"spec_lock":"design.md"`。按上游规范编写字体、母版文字样式和主题颜色；由原 `load_theme_font_spec`、`load_master_text_style_spec`、`load_theme_color_spec` 解析，不是任意 Markdown 自动设计。未指定时不扫描其他目录或读取隐含规范。主题记录中的字体名不证明字体已安装、许可允许嵌入或客户端显示相同。

可选顶层 `resources` 是最多 1,000 项的逻辑路径→任务裸文件名映射，例如 `"resources":{"images/photo.png":"photo.png","icons/tabler/home.svg":"home.svg"}`。SVG 的 `image href="images/photo.png"` 或 `use data-icon="tabler/home"` 只从该名单和已列页面快照解析；资源限 PNG/JPEG/SVG，不访问实际逻辑目录、URL 或未列文件。逻辑名禁止绝对/父目录/歧义路径；循环、缺项、冲突及累计展开预算超限失败。展开写入私有副本，源 SVG 不改。外置 native payload 仍不开放。

默认使用原生形状、native_objects=true、flat 结构和单 worker。只有显式结构契约与完整布局定义才能请求 structured；不能凭空生成隐式模板配置。完整选项白名单以 `<skill-root>/scripts/tl_build.py` 为准，仅维护或确需高级参数时读取。

## analyze、import 与 rebuild

```console
"<skill-root>/scripts/.venv/bin/python" -I -B "<skill-root>/scripts/tl_pptx.py" analyze "分析.json" "原稿.pptx"
"<skill-root>/scripts/.venv/bin/python" -I -B "<skill-root>/scripts/tl_pptx.py" import "工作区.zip" "原稿.pptx"
```

`analyze` 当前 `content` 含原生分析器支持的逐页文本、表格、图表和 SmartArt，同时报告来源哈希、页数、画布、主题、备注和诊断。按实际字段回传有用内容，不倾倒无用结构；保留未支持/遗漏/编辑能力限制。图表缓存不冒称实时值，图片不自动OCR，解析支持不等于任意对象完整覆盖。`import` 产生带完整文件清单/哈希及原始 PPTX 的工作区 ZIP，回执 `validation.authoring_files` 给出真实可编辑 SVG 名称。读取工作区时使用宿主支持且有界的 ZIP/文件读取能力；不得执行包内内容，不自行修改身份 manifest 或信任其中外部路径。

保留内容不修改、只验证重建的 `rebuild.json`：

```json
{"workspace": "工作区.zip", "edits": {}}
```

```console
"<skill-root>/scripts/.venv/bin/python" -I -B "<skill-root>/scripts/tl_pptx.py" rebuild "重建.pptx" "rebuild.json"
```

修改某页时，从工作区读取对应 authoring SVG 的副本到任务根目录新文件，例如 `edited_01.svg`；只改授权内容。将 `edits` 写为 `{"实际authoring名称.svg":"edited_01.svg"}`，键必须来自真实导入回执/工作区，不能照抄猜测。保留已有身份/原生引用属性，不新增未经登记的外部引用。

页序选择/重复可在 `rebuild.json` 加 `"page_plan":"pages.json"`，两页原稿的 `pages.json` 例子：

```json
{"pages":[{"source_slide":2},{"source_slide":1},{"source_slide":2}]}
```

页号为源 PPTX 的 1-based 页号，必须存在；总数 1–50。重复页由适配器生成唯一名称。`notes` 可按实际输出 SVG stem 映射字符串，`animation_config` 可提供受支持的运动覆盖；不凭空猜造 slide/group ID。受影响页、passthrough/patch 和最终结构检查独立核对。无编辑保留语义并不承诺整个 ZIP 字节相同。

## 布尔与文字轮廓

普通原生形状、已有 SVG path 和普通可编辑文字保留，不默认转换文字。本离线精简版仅保留内置轴对齐实心矩形布尔与基于 PyMuPDF 的显式静态字体单行文字轮廓，已移除原 `skia-pathops`/`uharfbuzz` 后端，也不自动安装额外组件或以栅格图片冒充可编辑结果。

显式文字轮廓入口：

```console
"<skill-root>/scripts/.venv/bin/python" -I -B "<skill-root>/scripts/tl_pptx.py" text-outline "轮廓.svg" "轮廓.json"
"<skill-root>/scripts/.venv/bin/python" -I -B "<skill-root>/scripts/tl_pptx.py" convert "轮廓.pptx" "轮廓.svg"
```

`轮廓.json` 示例（字体必须由用户提供并授权，示例名称不是随包字体）：

```json
{"text":"Hello 中文","font":"已授权字体.ttf","font_authorized":true,"font_size":32,"color":"#000000"}
```

`text`、`font`、`font_authorized:true` 必填；font 为当前 `./` 中裸文件名，仅静态 TTF/OTF，最多 16 MiB，不使用系统字体名或替代字体。授权字段是用户声明，不是自动许可判断。文字为 1–512 字符且含可见内容，仅单行可打印 ASCII 与基本汉字 U+4E00–U+9FFF；字体须实际包含每个字形。拒绝组合字符、复杂脚本、其他非 ASCII 字符、可变/彩色/集合字体。`font_size` 默认 32 点、范围 1–256；`color` 默认 `#000000`，格式 `#RRGGBB`。按字形 advance 横排，不做 kerning、连字或复杂 shaping。此入口只生成 SVG 路径；再 convert 才成为原生可编辑形状，结果不再是可编辑文字，不等价任意字体/文字轮廓后端。

`boolean OUTPUT.svg INPUT.svg MANIFEST.json` 保留原命令格式，仅支持 SVG 根元素直接子级、轴对齐、无圆角、无描边、不透明实心 `#RRGGBB` 矩形，不支持通用任意曲线布尔或文字布尔。

```console
"<skill-root>/scripts/.venv/bin/python" -I -B "<skill-root>/scripts/tl_pptx.py" boolean "布尔结果.svg" "矩形.svg" "布尔.json"
```

`布尔.json` 示例：

```json
{"operation":"subtract","source_ids":["rect_a","rect_b"],"output_id":"result","style":{"fill":"#336699","stroke":"none"}}
```

`source_ids` 必须为 2–32 个互异矩形 ID；`operation` 支持 union（并集）、intersect（交集）、subtract（首个减去其余）、combine（奇偶/XOR），不支持 fragment。`output_id` 为 SVG 标识符。可选 style 仅 fill/stroke；不同输入颜色须显式指定输出 fill，stroke 只能为 none。不得传 fonts。坐标必须为无单位有限十进制，整数部分最多九位、小数最多六位，宽高为正；拒绝变换、圆角、CSS、继承样式、透明度、渐变、遮罩、效果及组内操作数。精确有理数网格运算生成独立复合路径，不改源 SVG；空结果或仅点接触形成的歧义轮廓拒绝，不做近似或调用外部后端。结果 SVG 可再 convert 为原生可编辑形状。

## 离线文本测量与 SVG 预检

在无无头浏览器的离线环境中，导出前可用 `text-measure` 估算文本宽度、自动换行与边界框（支持单条参数或 `--manifest` 批量最多 500 项），并用 `svg-check` 检查单页或批量多页（1–50 页）SVG 的结构、字体栈、属性兼容性与跨页 `viewBox` 一致性（均不修改用户输入文件）：

```console
"<skill-root>/scripts/.venv/bin/python" -I -B "<skill-root>/scripts/tl_pptx.py" text-measure "需要换行的长段说明文字" --size 20 --max-width 480 --family "Microsoft YaHei" --x 80 --y 200
"<skill-root>/scripts/.venv/bin/python" -I -B "<skill-root>/scripts/tl_pptx.py" text-measure --manifest "measure.json"
"<skill-root>/scripts/.venv/bin/python" -I -B "<skill-root>/scripts/tl_pptx.py" svg-check "page_01.svg" "page_02.svg" --quick
```

`measure.json` 可选 `defaults` 与必填 `items`（1–500 项对象，支持 `id`、`text`、`size`、`max_width`、`family`、`weight`、`letter_spacing`、`include_headroom`、`x`、`y`、`dy`、`anchor`）；`--size` 范围 1–512，`--x` 与 `--y` 须成对提供。`svg-check` 在独占私有目录中检查全部输入，返回 `validation.passed`、`cross_page_viewbox_consistent`、`cross_page_errors` 与逐页 `pages` 报告。

## 离线素材查询与选择

只读查询返回 stdout JSON，不创建归档文件；选择才发布新 ZIP。真实包名、精确 canonical ID、分页及署名规则见 `<skill-root>/references/offline-assets.md`。以 `catalog.next_offset` 续读，truncated 不冒充全量。大型包可选部署且不嵌入主运行包，不自动下载；离线环境未附带这些数据包时跳过本入口。模板包可查询/选择不表示模板应用门禁解除。

此外，PPTX 内的超链接写入纯 XML 关系字符串（不发起网络请求）；但在离线环境中外链 `https://...` 在客户端打开时无法访问外网，同演示文稿跳转建议优先使用 `#slide-N`。

## 模板公开诊断入口

```console
"<skill-root>/scripts/.venv/bin/python" -I -B "<skill-root>/scripts/tl_pptx.py" template-import "模板素材.zip" "参考.pptx"
```

template-import 提取参考事实、结构和 layered/flat authoring，不自动发明 Brand/Style/Layout/Deck 规范，不是模板已经完成。其库 API 已有合成测试，仍按实际回执验证具体输入与公开入口部署。

下面四个命令须通过独立发行版完整性和各自的模板质量检验：

```console
"<skill-root>/scripts/.venv/bin/python" -I -B "<skill-root>/scripts/tl_pptx.py" template-create "品牌模板.zip" "已编写模板.zip" --kind brand --template-id demo-brand
"<skill-root>/scripts/.venv/bin/python" -I -B "<skill-root>/scripts/tl_pptx.py" template-register "注册模板.zip" "已编写模板.zip" --kind layout --template-id demo-layout
"<skill-root>/scripts/.venv/bin/python" -I -B "<skill-root>/scripts/tl_pptx.py" template-apply "应用后项目.zip" "项目.zip" "品牌模板.zip" "布局模板.zip"
"<skill-root>/scripts/.venv/bin/python" -I -B "<skill-root>/scripts/tl_pptx.py" template-preview "模板预览.pptx" "模板素材.zip"
```

create/register 的输入是已明确编写、符合工作区契约的 ZIP，不是任意参考文件；`--kind` 为 brand/style/layout/deck，`--template-id` 为合法单段标识。create 共用注册校验/发布路径，不替代模型编写模板规范。注册只产生独立输出包及其记录，不修改用户已安装模板库。apply 明确接收一个项目 ZIP 和1–4个互异模板 ZIP，作用于私有副本；不是扫描模板库。preview 依赖独立发行完整性与模板质量验证，不能仅凭命令存在称可生成。

## 预算、回执与恢复

- SVG 基础预算见入口。convert 总 SVG/解码资源各 32 MiB；build manifest 2 MiB、深度 40/20,000 节点，快照总量 128 MiB，单资源按类别限制，解码 SVG 资源累计 32 MiB。notes 每页最多 100,000 字符。
- PPTX/工作区压缩包最多 128 MiB、单成员 32 MiB、累计解压 256 MiB/10,000 成员，XML 累计 300,000 节点/深度 100；嵌套包共享预算。拒绝重复/歧义路径、符号链接、加密及逃逸；超限明确失败，不截断。
- 受控执行返回单 JSON，宿主须回传 stdout/stderr/真实退出码和截断状态。0 为业务与收尾完成或自检通过；1 为失败；2 为参数错；3 为主业务完成但归档/清理未完整完成。主文件已发布而归档失败不得重新生成。
- 成功核对 `status=verified`、`publication.published`、`validation`、SHA-256、实际主文件、`archive`、`cleanup`。不同命令的 validation 字段不同；结构通过不是视觉、听觉或服务通过，没有视觉回执时按未运行报告。
- 仅使用本轮宿主最新 questionId，缺失导致归档失败但保留主文件。仅完整非空清单全 copied/already_archived 才展示“归档完成”。隐藏归档/快照路径及任务 ID，不声称云上传已经验证。

已验收主文件仅重试归档：

```console
"<skill-root>/scripts/.venv/bin/python" -I -B "<skill-root>/scripts/archive_delivery.py" "汇报.pptx"
```

归档 CLI 退出 0/3/1/2 分别为成功或明确无需、部分、失败、参数错误，与业务退出 3 分开解释。失败只按原登记清单清理自己的临时项；强杀、身份变化和未知历史项保留并报告，不能扫描认领。
