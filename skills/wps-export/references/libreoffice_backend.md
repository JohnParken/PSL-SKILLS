# LibreOffice 后端导出规范

路径占位符说明：`<skill-root>` 是本 Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 导出原理与机制

LibreOffice 将金山 WPS 文字的基础二进制格式定位为建立在 Microsoft Word 97–2003（DOC）复合文件（CFB）之上的兼容二进制结构。在 LibreOffice 的过滤器定义中：
- 过滤器标识：`MS Word 97`
- 对应输出语法：`wps:MS Word 97`

执行导出时，调用格式如下：

```console
soffice --headless --convert-to "wps:MS Word 97" --outdir <output-dir> <input-file.docx>
```

该命令调用 LibreOffice 二进制导出器，将 OOXML 结构（.docx）转换为包含 WordDocument 流与 FIB（File Information Block）的 OLE/CFB 二进制文件，并将文件扩展名命名为 `.wps`。

## 依赖探测与执行环境要求

1. **可执行程序探测**：优先检查环境变量 `SOFFICE_PATH` 或 `LIBREOFFICE_PATH`，其次在系统 `PATH` 及常见安装路径（如 `/usr/bin/soffice`、`/usr/lib/libreoffice/program/soffice`、`/Applications/LibreOffice.app/Contents/MacOS/soffice`）中寻找 `soffice`。
2. **版本自检**：执行 `soffice --version` 验证程序可正常启动。
3. **输出限制与隔离**：
   - 使用独立临时或目标目录接收转换结果，避免覆盖已有文件。
   - 显式指定 `--headless` 参数，禁止拉起图形界面。
   - 捕获退出码及 stderr 输出，非 0 退出码视为转换失败并保留诊断日志。

## 格式能力与已知兼容边界

1. **结构保留**：正文段落、常用多级标题、列表编号、表格单元格文本及基础字符格式（粗体、斜体、字号、颜色）能够完整转换。
2. **公文与板式降级风险**：
   - 复杂嵌套表格与特殊单元格边距在转换为二进制流时可能产生微调。
   - 复杂分页符与奇偶页眉页脚需经实机核验。
   - 浮动图形锚定与艺术字可能降级为内联对象或静态位图。
3. **专有扩展**：金山 WPS 私有的公文域代码、印章及私有对象不在 DOC 二进制标准导出中，属于兼容格式交付。
