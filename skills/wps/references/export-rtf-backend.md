# RTF 规范流纯 Python 导出机制

路径占位符说明：`<skill-root>` 是本 Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 核心机制与兼容性原理

纯 Python 后端将受支持的 DOCX 内容映射为 RTF，输出以 `{\rtf1` 开头，默认使用 `.wps` 后缀；用户显式请求 `.rtf` 时保留；不是金山原生二进制。实际 WPS/Word 版本如何识别后缀、是否提示以及保存为何种格式，须实机核对，不保证无弹窗或自动升级格式。

**导出与新建分开。** `<skill-root>/scripts/docx_to_wps_rtf.py` 读取document.xml及受支持的直接格式，未解析styles.xml的完整样式继承。它不会自动套用声明式创建的official预设，也不能保证保留DOCX公文样式、页眉页脚或分页；不能用“先生成DOCX再导出”替代排版一致性验收。新建参数见 `<skill-root>/references/generation.md#主题系统与公文规范`。任务输入输出以宿主已确认的任务工作目录为基准。

## 中文字体与排版映射

RTF 构建器写入字体名称和排版控制，不安装、嵌入或检查字体，不承诺精确复现跨阅读器外观：
- **字体映射表（`\fonttbl`）**：
  - `\f0`: 宋体（SimSun，`\fcharset134`）
  - `\f1`: 黑体（SimHei，`\fcharset134`）
  - `\f2`: 仿宋（FangSong，`\fcharset134`）
  - `\f3`: 楷体（KaiTi，`\fcharset134`）
  - `\f7`: 微软雅黑（Microsoft YaHei，`\fcharset134`）
  - `\f8`: 仿宋_GB2312（FangSong_GB2312，`\fcharset134`）
  - `\f9`: 方正小标宋简体（Unicode名称，`\fcharset134`）
  - `\f5`: Times New Roman（西文字体）
- **Unicode 转义规范**：
  - 声明 `\uc1`（每个 Unicode 字符后跟随 1 字节 ANSI 回退占位符 `?`）。
  - Unicode按有符号16位码元输出，非BMP字符使用代理对；正文回读另行核验，不以编码规则保证所有阅读器效果。
- **页面几何尺寸（A4 标准）**：
  - 纸张宽度：`\paperw11906`（210mm，以 1 磅 = 20 缇为基准）
  - 纸张高度：`\paperh16838`（297mm）
  - 声明式official创建默认上／下1440缇（25.4mm）、左／右1803缇（31.8mm取整）；该默认值不用于声称DOCX导出已保留其样式。
- **表格与跨页控制**：
  - 表头行重复：通过 `\trhdr` 声明表头行，跨页自动在新页顶端重现表头。
  - 行不截断控制：`\trkeep` 请求行内不拆分，超长行及实际分页仍由阅读器处理。
  - 单元格合并：支持横向合并（`\clmgf` 首格 / `\clmrg` 后续格）与纵向跨行合并（`\clvmgf` / `\clvmrg`）。

## 依赖与验证边界

纯 Python 路径仅依赖标准库，不需要LibreOffice或第三方Python包。性能取决于输入和宿主，未给出普遍时间保证。字体缺失、阅读器兼容及分页仍须独立验收；原始输入受保护，转换成功、正文回读和视觉验收是不同结果。

新建公文复合字体与字符缩进控制依据Microsoft RTF 1.9.1的Composite Fonts及Paragraph Formatting章节：cufi写字符单位并保留等效fi回退。这些控制的写入测试不替代实机渲染。规范来源（外部资料，不是Skill资源路径，无需运行时联网）：`https://officeprotocoldoc.z19.web.core.windows.net/files/Archive_References/%5BMSFT-RTF%5D.pdf`。

写入 `.rtf/.wps`，拒绝 `.doc/.docx`，包括旧 convert_docx_to_rtf_wps API 和 RtfDocument.save。后缀约束只针对写入；输入按 OOXML 内容识别，不依靠扩展名。来源或输出回读为 partial 时保留诊断；复杂合并网格无法证实时可返回可靠物理单元格文字并标记 partial，截断和文字恢复不得作为合格输出。RTF 的 Unicode 与正文通过真实 read_wps 解析，不再用正则剥离控制词。

省略导出 `--output` 时使用输入同目录、同主名的 `.wps`，例如 `./材料.docx` 导出到 `./材料.wps`；显式输出路径保持不变。RTF 内容任务明确使用 `--backend rtf`。内部候选可使用 `.rtf`，最终交付名称以回执 output_file 为准，不重命名历史文件。
