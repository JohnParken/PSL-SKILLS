# tl-pptx 原生 PPTX 能力保留与迁移矩阵（2026-11-17）

## 结论与适用范围

用户最新要求：保留 ppt-master 的全部原生 PPTX 能力；不能以“独立、最小依赖”为由默认裁剪模板、编辑往返、动画、旁白等能力。本表记录当前源码快照、必要依赖和后续迁移路径，不代表用户批准裁剪，也不把尚未迁移写成上游不支持。

本表仅为仓库维护资料，路径以 PSL-SKILLS 仓库根目录为基准，不是 Skill 运行时依赖。此轮只写本维护文档，不改运行时、依赖或现有发布包；后续实现须同步更新 SKILL、公开 CLI、自检、白名单和验收。

基线：
- 上游默认分支 main，SHA `7e0df6705de0cd24345210d31f07fcb5a32b6900`；入口元信息版本 6.9.0。
- 上游 MIT，Copyright (c) 2025-2026 Hugo He；Office preset 数据另保留 Apache-2.0、Open XML SDK MIT 及 NOTICE。声音、字体、品牌资产按各资源许可分别核对，不能从项目 MIT 推导所有素材权利。
- 当前独立实现观察快照：`skills/tl-pptx/SKILL.md` 版本 `20261118.0005.5100`，公开入口只有 `check` 和 `convert`。这一版本的“不提供”清单是阶段事实，已不等于最终目标；不得把它当作用户授权裁剪。
- 研究采用指定 SHA 的 README_CN、SKILL、导出 CLI、相关 workflow/reference 与实现；没有执行上游所有功能，也没有重新查询本表之外的移动分支状态。
- “当前已验”仅指本轮实际完成的 macOS 功能/结构测试；未完成 Linux、Windows PowerPoint 打开/播放、完整模板/往返或真实 DeepAgents 验收。

## 状态定义

- **已接入、部分验收**：公开独立入口可用，已有具体样例；不等于该类别全部边界已验收。
- **代码在包、公开接入不完整**：核心组件存在，但入口参数、资源或实际行为未完整接通。
- **待迁移，继续保留**：能力明确在上游，当前独立版未接通；属于工作缺口，不是裁剪建议。
- **平台/服务条件能力**：保留实现和接口，按运行环境报告可用/受阻，不把一个平台不可用改成永久删除。

## 原生能力矩阵

| 能力 | 上游实际接口与边界 | 当前阶段事实 | 必需组件、资源或条件 | 可迁移路径与验收 |
| --- | --- | --- | --- | --- |
| 新建原生可编辑 PPTX | `svg_to_pptx.py`；普通 SVG→DrawingML，flat 或 structured，Quick/Default authoring；不是整页截图 | 已接入、部分验收：独立短组包器替代上游完整 builder，仅新建 flat 路径 | python-pptx、lxml、Pillow、XlsxWriter；字体度量 JSON、Office preset registry/XML/semantics | 保持当前安全入口，将完整 builder 的所需能力按职责迁入，而非把短组包器当最终能力上限；逐页内容、对象、关系、包回读与视觉分开验收 |
| SVG 几何、文字、图片、效果 | rect/circle/ellipse/path/polygon/line、变换、组、文字运行/语言、渐变、受支持 shadow/glow/clip 等；187 Office presets；不承诺任意 SVG/CSS | DrawingML 核心已接入；常规与部分复杂合成例通过；未覆盖全 preset/效果矩阵 | 现有核心依赖、preset 数据及两份 font metrics/advances；真实客户端字体 | 建立代表性与边界 golden corpus，逐项确认不能转换时明确拒绝；不能通过整页栅格化伪装保留原生能力 |
| 原生 Chart、Table、OMML 公式 | `--native-charts-and-tables` 替换显式标记；上游默认 chart/table 仍为可编辑图形 fallback；公式 intrinsically native；table 需 semantic-table.v2 | 已接入、部分验收：column chart+embedded XLSX、2×2 table、block formula；保留更多 chart/formula 代码但未全验 | XlsxWriter；formula AST/parser/OMML、chart/table 全链；无 Office 自动化要求即可生成 | 保留完整 upstream native 支持集；公开选择原生数据对象与fallback模式的差别，补全部 chart 类型、合并/样式表格、inline/block公式与跨页 part allocation |
| 文本流、语言与图片优化 | `--reflow-text`、`--no-merge`、默认 preserve；`--primary-language`；image cap/display/scale/quality 与 EXIF geometry | 核心代码在包，CLI未暴露同等选项，默认不能代替完整能力 | Pillow；已有字体与语言模块 | 逐项向兼容 API 暴露受控参数；测 RTL、CJK、tspan、proofing语言、缩放/裁剪，保护原图输入 |
| 原生 Theme/Master/Layout/Placeholder | `--pptx-structure structured`，完整显式结构 metadata；flat 与 structured 是组包模式，不是模板类型 | 待迁移，继续保留；当前 assembler 未携带完整结构构建 | builder、template_structure、template_validation、theme_fonts/theme_colors 与结构契约 | 迁移结构模型和验证器，保留多 Master/Layout、slot/placeholder映射；回读层级+PowerPoint无修复打开分别验收 |
| Brand/Style/Layout/Deck 模板创建 | Create Template 四类分工；素材提取、规格、review PPTX、library 注册；Layout/Deck多Master有包门禁 | 待迁移，继续保留；不能把这四类误等同Theme/Master | create-template workflows、design_spec/schema/spec_lock/scaffolds、所选模板资产；注册 YAML frontmatter需PyYAML | 保留四类创建/选择/安装契约；按任务装必要资产，不复制无关展示材料；模板注册与用户现有library写入须保持授权/冲突边界 |
| 模板工作区应用 | `apply_template.py` + register_template；按种类/segment优先级应用，每个root一次，冲突与回执 | 待迁移，继续保留 | PyYAML（实际含YAML规格时）；相关schema、索引及选择的本地模板文件 | 迁移发现、预检、规划、写入/回执；目标只能任务工作区，不能静默改Skill目录或用户模板库；测重复应用、冲突、混合模板、独立部署 |
| 既有 PPTX 模板导入与预览 | `pptx_template_import.py`、template_import/manifest/native_structure、template_preview_pptx；保留原生母版/版式/主题语义 | 待迁移，继续保留 | PPTX ZIP/XML读取闭包、preset/字体资源；可选预览renderer另检测 | 安全ZIP/关系/外链预检后导入，未知对象保留诊断；模板与输入源隔离、保留主/布局、不得称无损任意编辑 |
| 既有 PPTX 阅读投影与编辑往返 | `pptx_to_svg.py --roundtrip` → immutable source、authoring-svg-flat、analysis；`svg_to_pptx.py --roundtrip` 恢复未改对象语义 | 待迁移，继续保留；当前guard拒绝source_package只是阶段防护，不是最终目标 | pptx_to_svg全转换闭包、authoring_roundtrip、pptx_ooxml、workspace、payload stores、source package；核心Python包即可支撑多数包级功能 | 不能直接解除source_package禁令；须先补各层ZIP/XML/base64/gzip预算、关系允许表、路径/身份保护，再接入源语义恢复和部分编辑。测未改对象、已改对象、原子proxy、丢失动画目标及损坏源 |
| 页选择、排序、重复、省略 | roundtrip `page_plan.json`；未改页按源引用保留；同页复制的覆盖项按输出stem | 待迁移，继续保留 | roundtrip/page roster、clone与关系重映射 | 未改源内容保留承诺按上游粒度验证；内部链接指向被删除或重复目标时上游要求歧义拒绝，不擅自猜目标 |
| SmartArt/复杂效果/嵌入媒体保护 | 编辑路线将不支持直接编辑的对象保留为atomic source proxy | 待迁移，继续保留；不是承诺SmartArt任意内容编辑 | 导入诊断、source proxy、原始OPC部件及安全关系验证 | 测未改proxy保留、试图编辑proxy拒绝、媒体关系与包内容保留；继承上游边界而不虚构新增编辑能力 |
| 对象动画 | entrance/emphasis/path/exit、Start三模式、顺序/时长/stagger、trigger_shape、animations.json；并非完整关键帧时间线 | 待迁移，继续保留；当前仅抽取分组帮助函数，完整动画尚未接入 | pptx_animations、svg_to_pptx/animation_config、`pptx_animation_presets.json`、builder时间树与read-back验证 | 迁移完整canonical registry、配置验证、目标绑定和timing XML；测四种生命周期、触发器、目标消失、源动画overlay及包回读/客户端播放 |
| 页面过渡、Morph、自动推进/kiosk | 48 canonical transition keys；effect options、显式Morph pairs、auto-advance、kiosk；Morph两端是作者创建的静态状态 | 待迁移，继续保留 | pptx_transitions、动画配置/registry、builder；客户端决定最终播放 | 保留全部上游canonical键和选项；Morphed对象类型兼容/身份验证，不靠自动heuristic宣称确定性；Linux可生成OOXML但播放需实际客户端 |
| 演讲者备注 | 默认非Quick嵌入；Quick通过with-notes显式启用；导入/编辑overlay与输出页对应 | 待迁移，继续保留 | notes.py、notes_import、页映射、Markdown→plain text | 建立输入/页映射接口，测notes master/slide/rels、复制页继承与独立覆盖；沿用上游“不直接删除复制页继承备注”边界 |
| 主题字体、配色、嵌入字体保留 | theme_fonts/colors可将匹配样式转scheme字体/颜色；embedded_fonts模块保存/恢复源PPTX嵌字 | theme函数在包但完整公开接入未完成；嵌字往返待迁移 | 原主题契约与字体sidecar；真实字体文件和相应使用/嵌入许可 | 保留字体theme/script槽和配色映射；嵌入字体模块主要是源字体保留，不应宣传自动任意字体嵌入；源许可不明时只对实际需要的字体询问 |
| 原生超链接 | whole-object与inline `<a href>`；外部绝对URI或内部`#slide-N`；禁止file/data/javascript/宏/OLE/任意ppaction | 核心hyperlink代码在包，当前外层guard统一拒绝，属于待恢复公开能力 | 无网络访问即可写URI；hyperlink_contract、converter、assembler关系允许表/页映射 | 将“数据链接”与“加载外部资源”分开；不抓取链接内容；按用户提供明确目标导出受支持关系。roundtrip页删除/复制需歧义拒绝；外链存在不授权访问或执行 |
| 形状布尔合并 | combine/intersect/subtract/union/fragment，按PowerPoint兼容语义物化为原生路径 | 待迁移，继续保留 | skia-pathops>=0.9.2；shape_boolean_svg、shape_boolean、preset helpers | 安装声明为该能力实际必需，不用允许第三方推导其他包；拆分大文件保留算法，测孔洞/相交/曲线/退化、结果仍可编辑freeform |
| 文字轮廓作为布尔operand | OpenType shaping与glyph outlines；用于文字参与形状运算，不等同所有文本必须轮廓化 | 待迁移，继续保留 | uharfbuzz>=0.50.0、text_outline、实际字体文件；字型许可与平台字体存在性 | 显式定位授权字体，替代上游宽泛字体目录搜索为宿主可控边界；缺字/字体不可读明确阻断，保留普通文字可编辑文本路径 |
| 已有音频嵌入与录制计时 | narration-audio-dir可部分匹配；recorded-narration要求完整音频目录；padding/start-floor、use-narration-timings、继承motion | 待迁移，继续保留 | narration.py、音频格式/时长探测、sound/relationship组包；不同格式探测器需逐项自检 | 先保留本地已提供音频路径，不要求先联网TTS；安全读取并绑定页序，测音频部件、记录计时、自动推进与不完整匹配诊断 |
| TTS旁白、逐词字幕与语音同步动画 | notes_to_audio与backend模块；Edge默认无key，另OpenAI/ElevenLabs/Qwen/MiniMax/CosyVoice；按页音频/SRT；narration cue同步 | 待迁移，继续保留；联网服务为条件能力 | edge-tts>=7.2.8（带aiohttp传递依赖）；OpenAI backend直接用aiohttp；其他后端多为urllib/API；相应网络、key、费用和数据授权 | 各后端可单独自检；不自动选收费provider，不把缺key归为永久删除；由用户明确目的地/发送内容/费用界限授权后调用；保留逐词时间、页映射和失败恢复 |
| 过渡/对象声音 | 配置sound字段，sync本地WAV；最终PPTX有cue不保证PowerPoint MP4编码含cue | 待迁移，继续保留 | sound_sync、所选WAV、声音索引/CC0与third-party notices；完整默认音效库是可选素材集合，不是功能本身 | 保留用户自给声音+库选择能力；如不携带全库需明确资源可用性，不能只留空接口；测试cue一次、正确目标、无外部路径泄露 |
| 原生 PowerPoint 视频导出 | powerpoint_video.py调用Windows PowerShell COM CreateVideo；支持Office2016+基线 | 平台条件能力，待迁移；当前Mac/Linux不得宣称可执行原生导出 | Windows+兼容已安装且授权的PowerPoint+PowerShell；无Python pywin32要求 | 保留入口与平台自检；有Windows执行器后单独验收编码进度/超时/输出；无该环境时交付narrated PPTX并如实报告MP4受阻，不替换成截图/HTML第三方视频冒充原生 |
| 视频音效混合、字幕对齐 | video_sound_mix在原生MP4后混音并验证视频流/时间线未改；video_subtitles为最终视频字幕对齐 | 待迁移，继续保留；属于条件后处理 | 混音ffmpeg+ffprobe+numpy；字幕对齐脚本额外stable-ts（import stable_whisper）+ffmpeg，后者不在默认requirements启用清单 | 独立声明按需依赖，不把stable-ts假称已由主requirements提供；检查模型/网络下载/硬件成本后再用。capture分支不得重复混音；验证音频一次、video stream hash与时间线 |
| PNG预览/兼容fallback与视觉QA | media.py优先CairoSVG，再svglib+reportlab；只是SVG预览/兼容渲染，不代表PowerPoint渲染 | 未接入完整预览流程；当前visual_validation=not_run | CairoSVG及平台Cairo，或svglib+reportlab实际renderPM能力；真实PowerPoint视觉独立 | 保留可选预览诊断，不作为native编辑能力的替代；明确预览引擎与PowerPoint视觉之间的证据差别 |

## 依赖分层与许可/平台决策

### 继续迁移，不需要先向用户请求删功能

1. 包级原生能力：完整 DrawingML、native对象、主题/备注、结构化模板、动画/过渡、超链接、导入/往返。依赖审查按真实import闭包，不因上游有Web/AI服务就引入全部服务依赖。
2. 布尔与轮廓：为实际使用的能力声明 skia-pathops、uharfbuzz 与部署字体；不得将它们删掉来满足“最小”字面数字。
3. 模板注册：PyYAML只在解析YAML frontmatter实际路径必需；如果保留全部模板能力就保留这一条件，而非要求用户改掉合法规格。
4. 旁白本地封装与联网生成分层：已有音频无需TTS服务；TTS各provider按选择检测；缺provider不能阻断无需旁白的新建/编辑。
5. 原生视频保留接口与严格平台自检。当前Mac/Linux无法运行Windows COM是实际平台限制，不是批准裁剪。

### 仅在出现真实未决条件时请用户决定

- 选择收费TTS/API服务、发送的备注内容、目标provider与费用上限；服务条款/凭据依现行权限流程处理。
- 需要Windows PowerPoint环境/许可证而无可用执行器时，说明PPTX可完成、原生MP4受阻，由用户提供该能力或明确选择其他交付；不能擅自替换渲染链。
- 特定自定义字体、商业声音或品牌模板资产的使用/嵌入许可不明确时，针对实际素材处理；不能笼统以许可风险删除全部模板/声音。
- PDF来源转换的PyMuPDF属于上游非PPTX核心输入适配，AGPL/商业许可需单独审查；它不是保留原生PPTX能力的必装依赖。类似Web、OCR、AI图像/编辑器能力另列外围来源与创作能力，不凭本矩阵默认删除，也不混入所有原生操作的硬依赖。

## 迁移顺序与安全保持

1. 先修正目标描述与能力清单：阶段缺口必须标为待迁移/未验收；不得继续以当前SKILL的永久排除语气宣布交付完成。
2. 独立包保留完整接口语义，内部按职责拆分每Python文件≤1000行；不得通过压缩、exec文本或全局关闭检查保留大文件。
3. 优先接通备注/主题/超链接、动画/过渡；同步必要JSON preset、音效契约和严格输入通道。
4. 然后迁移结构化模板及roundtrip闭包。外置payload/source_package恢复前，先补压缩后/解压后预算、内部包遍历/关系允许表、源身份验证和跨页映射；不能删除当前拒绝门禁后直接调用上游解包。
5. 接通Boolean/text outline，然后本地音频、录制计时、TTS与视频条件入口；每种可选能力自检，失败只停止依赖它的操作。
6. 所有公开操作沿用独立解释器、DeepAgents真实非空command、stdout/stderr/exit/truncation回传、任务目录数据边界、新裸文件名输出、用户源保护、归档/清理与不覆盖要求。模板/roundtrip内部多文件工作区需设计明确受控接口，不能让上游project_path通配替代任务边界。
7. 每一组新增代码、依赖/资源/许可、公共CLI、docs、runtime-manifest同时更新。源码回归、独立包、Pylint、Linux目标验收、Office视觉/播放、真实宿主轨迹各自记录，不能互相替代。

## 本阶段已有证据与缺口

- 原始下载的83份Python已按Git blob算法重算并与指定上游tree SHA逐一核对，全部相等；这只验证源字节，不证明迁移完整性。
- native core已有7项测试；公式7类例与未修改上游parser返回AST相等。
- 三个native示例实测：column chart生成chart XML+embedded XLSX；semantic-table.v2 2×2表格生成a:tbl；block公式生成m:oMath。不是所有chart种类或所有语法覆盖。
- 当前完整模板/导入/roundtrip/动画/旁白/视频尚未实现验收；此表提供保留路径，不声称这些已可用。
- 本表研究没有安装任何新增组件、发送备注到服务或执行Office自动化。

## 主要依据（固定SHA）

所有下列路径均来自 `https://github.com/hugohe3/ppt-master/blob/7e0df6705de0cd24345210d31f07fcb5a32b6900/`，可逐路径复核：
- [README_CN](https://github.com/hugohe3/ppt-master/blob/7e0df6705de0cd24345210d31f07fcb5a32b6900/README_CN.md)、[Skill入口](https://github.com/hugohe3/ppt-master/blob/7e0df6705de0cd24345210d31f07fcb5a32b6900/skills/ppt-master/SKILL.md)、[完整依赖](https://github.com/hugohe3/ppt-master/blob/7e0df6705de0cd24345210d31f07fcb5a32b6900/skills/ppt-master/requirements.txt)。
- [导出CLI](https://github.com/hugohe3/ppt-master/blob/7e0df6705de0cd24345210d31f07fcb5a32b6900/skills/ppt-master/scripts/svg_to_pptx/pptx_package/cli.py)、[编辑往返工作流](https://github.com/hugohe3/ppt-master/blob/7e0df6705de0cd24345210d31f07fcb5a32b6900/skills/ppt-master/workflows/edit-native-pptx.md)、[创建模板](https://github.com/hugohe3/ppt-master/blob/7e0df6705de0cd24345210d31f07fcb5a32b6900/skills/ppt-master/workflows/create-template.md)。
- [动画与过渡](https://github.com/hugohe3/ppt-master/blob/7e0df6705de0cd24345210d31f07fcb5a32b6900/skills/ppt-master/references/animations.md)、[超链接](https://github.com/hugohe3/ppt-master/blob/7e0df6705de0cd24345210d31f07fcb5a32b6900/skills/ppt-master/references/native-hyperlinks.md)、[视频边界](https://github.com/hugohe3/ppt-master/blob/7e0df6705de0cd24345210d31f07fcb5a32b6900/skills/ppt-master/references/video-design.md)。
- 实现核查：scripts/shape_boolean_svg.py、svg_to_pptx/shape_boolean.py、svg_to_pptx/text_outline.py、notes_to_audio.py、tts_backends/*、powerpoint_video.py、video_sound_mix.py、video_subtitles.py、pptx_embedded_fonts.py、apply_template.py、register_template.py、pptx_template_import.py、pptx_to_svg.py、svg_to_pptx/pptx_package/media.py。

## 完整用户工作流补充：不是只有转换脚本

以下按固定上游 `workflows/routing.md` 的三个顶层生命周期分类。Quick、Image to PPTX、Beautify属于Generate子配置，不是互相竞争的新路由。实现存在、库测试通过、宿主端到端验收是三个不同状态。本节不批准删除任何工作流。

| 用户工作流 | 必须保留的步骤与约束 | 依赖/运行条件与缺失行为 | 端到端验收要求/当前状态 |
|---|---|---|---|
| 普通生成 Generate Default | 来源转换/有界研究、项目初始化、模板候选、两阶段策略确认、设计spec与lock、资源准备、SVG创作、最终质量门禁、PPTX导出；备注/动画/旁白按激活条件执行 | 宿主能读来源与执行本地工具；模板可选但指定时须真实安装；各来源适配按类型加载。聊天确认可承接宿主不具备的UI，不能省略语义确认 | 需从主题或真实来源走完计划、资源、SVG、检查、交付并保存真实command/stdout/stderr/exit。当前仅底层闭包证据，未宣告整个Default验收 |
| 快速生成 Quick | 明确快速或直接SVG导出意图；不运行Strategist/Confirm UI/spec/lock，但仍来源准备、资源、手工SVG、lockless检查和导出。准确指定的Brand/Style/Layout/Deck工作区每类至多一个 | 无需策略交互不等于忽略校验；flat与structured由实际结构契约决定，不凭模板存在猜测。缺少必要资源/工具仍停止相关步骤 | 需自由设计与指定结构化模板各一条真实链路；当前普通SVG新建测试不能替代Quick完整路由验收 |
| 图片转可编辑PPTX | 必须Quick。规范化有序页面框；逐页看图；原生文字、去字底图、独立对象/图片层；OCR文字层起稿、人工复核、重组对照；一框一页、比例守恒 | 必须具备视觉宿主。纯源像素/原生文字形状无需生成器；需要生成的去字/重建层必须有支持参考图的宿主编辑或OpenAI/Gemini后端。缺失时标记Needs-Manual并阻止导出，不能退化成整页截图 | 需OCR覆盖、独立可编辑对象、去字残留和像素重组比较。当前OCR/图像适配及宿主视觉全链路尚未验收 |
| PPTX美化 Beautify | 单一语义源PPTX，冻结措辞、页数和顺序；构建逐页beautify_inventory，文字/表格/图表值/图示关系/图片追踪；重排视觉而非原样roundtrip。默认Default，明确快速才Quick | 需要PPTX来源抽取、分析与inventory工具。SmartArt按文字关系重画普通编辑形状，不伪称原生SmartArt重建；无损映射失败停止该项，不偷改事实 | 需逐页比对内容账本、图表数据、表格值及所有图片去向，不能只验证能打开文件。此端到端尚未验收 |
| 创建可复用模板 Create Template | 从PPTX/SVG/图片/PDF/文字/品牌混合来源创建新portable workspace；Brand/Style/Layout/Deck分类；模板、资产、spec/manifest与可选预览；参考源绝不原地修改 | 来源适配按需；布局/母版保留需要结构化PPTX库及模板验证；注册或项目范围安装必须指向实际授权位置。不可只复制外观图片冒充结构模板 | 需创建→校验→独立搬移→应用新内容→导出回读；template_import/analyzer由并行闭包迁移，尚须整体工作流证据 |
| 编辑原生PPTX Edit Native | `roundtrip`导入，未改对象/页恢复源OOXML，编辑对象局部重建；page_plan负责选择/排序/重复；备注、动画、旁白可独立覆盖而不重画可见页 | 完整PPTX读取/authoring workspace/物化/保留式builder；source_package必须经过受限预算/关系/源身份门禁，不得直接解除当前拒绝。所有输出为新文件 | 已新增真实矩形文字+备注的读取/工作区/物化测试2项；与固定上游物化SVG SHA相等。尚不等于原生编辑最终PPTX、恶意包边界或Office视觉验收 |

### 来源适配与PyMuPDF许可隔离

- ppt-master自身MIT不覆盖所有第三方依赖。上游requirements明确PyMuPDF仅由`source_to_md/pdf_to_md.py`使用；PyMuPDF/MuPDF提供AGPL与商业许可，不是MIT。官方依据：[PyMuPDF许可说明](https://pymupdf.readthedocs.io/en/latest/about.html)、[商业许可入口](https://pymupdf.io/licensing)。
- 保留PDF来源能力的迁移方案是**独立可选依赖组+按需导入+明确检测**，不是删除功能，也不是把PyMuPDF混入所有原生PPTX操作的硬依赖。发行清单、第三方许可、依赖版本应分别记录；打包或再分发含该组件时须核实实际使用/分发方式适用的许可，不把“商业使用”简单等同必须收费。
- 当前文档研究不购买许可、不安装该依赖、不把许可判断冒充用户批准。若实际分发目标无法满足AGPL且无商业许可，标记PDF适配尚不可在该分发模式启用，给出具体阻塞；不要擅自替换解析器或宣告所有输入功能裁剪完成。
- PDF依赖缺失时，仅PDF来源适配报可操作缺失诊断；已有PPTX导入、SVG生成、模板与无PDF来源流程应能正常导入运行。同理OCR、参考图编辑、TTS、Office视频各有独立运行条件。
- 完整工作流迁移还需关注`source_to_md`、`project_manager`、`beautify_inventory`、`image_ocr`、`slice_images`、图像生成/编辑适配、模板应用/注册、spec/lock与质量门禁文档契约。以上是保留待办清单，不是默认将它们作为同一条命令的必装依赖。

### 本轮读取闭包验证补记

- 新增完整`pptx_to_svg`读取库、`authoring_roundtrip`、`svg_authoring_view`、`extract_svg_assets`与`native_objects/chart_patch`，显式facade按职责拆分，489个函数算法AST与固定上游等价；生成分块最大675行。
- UTF8 compatibility仅保留被调用的stream API，不执行上游官方身份校验或自动录制；这是独立派生身份/宿主约束适配，不假冒官方发行包。归属与MIT许可仍由本包独立清单保留。
- 相关迁移模块Pylint E/F为0；`test_import_roundtrip.py` 2项通过。真实fixture物化SVG在上游与目标均为SHA256 `2842c69c514cda940db2b01ca4c660a296934727c88243ecef91e3c56ed3372f`。
- 此轮没有解除source_package拒绝，没有为不受限上游CLI新增公开入口；公开任务目录安全适配与最终PPTX保留验收由完整集成门禁继续完成。
