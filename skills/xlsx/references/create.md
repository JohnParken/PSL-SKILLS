# Create a spreadsheet

路径占位符说明：`<skill-root>` 是 XLSX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 XLSX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

Follow the path and first-operation rules (`<skill-root>/SKILL.md#执行环境约束与首步自检`)
before using this reference; internal resources and task data have different path bases.

Read Standard-library calculation and file contract (`<skill-root>/references/python-calculation.md`) for the JSON schema and CLI.
Plan sheet names, input values, calculations and formatting first. Use `create` with
an explicit JSON cell specification. The tool builds from the bundled XML template.
The source assets use `content_types.xml` and `_rels/root.rels` to satisfy filename
rules. Creation maps them in memory to the OOXML ZIP members `[Content_Types].xml`
and `_rels/.rels`; it never renames or writes files in the installed skill directory.
Do not manually rename template files before execution. Existing XLSX/XLSM inputs
continue to use the standard ZIP member names.

Choose the output behavior:
- Editable model: use supported formulas; the engine writes formulas and calculated caches.
- Static report: calculate business results in Python and use `value`, or use
  `create --values-only` to evaluate supported formulas and replace them with values.

Do not invent data or formula caches. Label assumptions and units. Preserve required
column/sheet names. For financial styling consult Financial Formatting & Output Standards (`<skill-root>/references/format.md`); select template
style indices deliberately. For new-workbook pale-blue headers, row/category banding,
header merges, explicit widths and freezing, use Readable appearance for newly created workbooks (`<skill-root>/references/readable-appearance.md`).
Charts, auto-fit and arbitrary layout editing remain unsupported.

Check the calculation report, reopen the output with `read`, verify expected sheet names,
input values and independently known results. Explain static outputs and unresolved
formulas. See Validate a standard-library output (`<skill-root>/references/validate.md`). No Office runtime or third-party packages are needed.
