# 结构读取与进阶检查

文件位置统一遵循 [文件定位与读取规则](../SKILL.md#文件定位与读取规则)：文档及操作文件参数相对任务目录；后续读取使用本次实际输出路径，不按输入目录推导。
读取本轮明确输入或主输出；不主动扫描或读取 `./output/{questionId}/` 中的文档内容，也不因多轮对话自动使用历史归档。用户明确要求读取指定归档文件时才将其作为输入；必要的归档定点检查与副本验证见 [使用边界](delivery.md#归档目录使用边界)。

交付归档：编辑 CLI 和 [公开交付 API](delivery.md#公开交付-api) 的写操作成功后由代码自动归档本次输出；只读操作不归档。低层 API 或读取保存等其他方式生成的最终交付文件，按 [当轮统一归档](delivery.md) 显式列出本轮文件归档；不把验收中间文件或历史产物加入清单。


不支持读取 WPS 格式（.wps/.et/.ett），请使用 wps skill；本入口明确拒绝，不自动调用或转换。

`.wps`、`.et`、`.ett` 统一交给 wps 技能；本技能文本及 JSON 读取均拒绝这些后缀。伪装为 DOCX/DOTX 的 CFB 内容同样明确引导 wps。

## 结构化 JSON

先用 `read_docx.py --check` 完成只读运行条件自检；仅写 stdout 不需输出目录写入探针。保存 `-o` 结果前另核对实际输出目录的写入能力。从宿主确认的任务工作目录执行；将示例 `<skill_dir>` 替换为主入口定义的真实目录：

```python
execute(command="python -I -S -B \"<skill_dir>/scripts/read_docx.py\" ./input.docx --format json --revisions all --include-related -o ./structure.json")
```

使用 Python API `read_docx_structure(path, revisions="accepted", include_related=False)` 获取字典。默认 CLI 和 `read_docx()` 仍返回纯文本。

JSON 顶层契约为 `schema_version: 1`、`format: "docx-structure"`：

| 字段 | 语义 |
|---|---|
| `revisions` | `accepted`、`original` 或 `all` 文本视图；不改写文档。 |
| `semantics` / `limitations` | 明确显式属性、未计算编号及非页面顺序等限制。 |
| `parts` | 正文及请求的页眉、页脚、脚注、尾注和批注；各项有部件名、类别和根节点。 |
| `definitions` | 存在时读取原始 `styles.xml`、`numbering.xml` 定义；不计算继承关系。 |

每个元素节点包含 `type`、`tag`、`namespace`、`attributes`、`children`。属性保留带前缀名称，命名空间声明保留在原节点；不要剥离声明后把前缀当成全局固定名称。

按节点类型读取附加字段：

- 段落 `style_id`、`numbering.num_id/level`；run `style_id`。未显式声明时为 `null`，不推断默认样式或列表层级。
- 表格子节点保留 `tblGrid/gridCol`，行有 `grid_before/grid_after`，单元格有 `grid_span/vertical_merge`；原始属性通常是字符串。`vertical_merge` 在省略 `val` 的已存在标记上为 `continue`。不重建虚拟单元格、不展开合并布局。
- 文本节点有 `text`，同时保留 XML 文本子节点；使用其中一种表示核对文字，不重复累加两者。
- 超链接、内容控件、嵌套表格和修订保留容器层次。视图排除的修订容器带 `included: false`、`omitted: "revision-view"`，其 `children` 为空；使用 `all` 查看两类修订内容。
- 未知元素以 `opaque` 节点及其子节点保留；根元素内部的 XML 注释、CDATA 和处理指令也保留，不输出 XML 声明或根元素之外的节点。不要将 `opaque` 解释为已验证其功能。

总输出共享最多 200000 个 DOM 节点的预算，仍遵守技能的 ZIP/XML 上限；超限失败，不输出截断的成功结果。不读取未知二进制部件或为结构 JSON 解析图片、字段、公式、图表与绘图的显示结果。

禁止将 JSON 用作无损 DOCX 导入格式或直接交给生成器；两者的输入契约不同。未承诺真实 Word/WPS 打开、字体、布局或完整修订语义。

## 进阶检查范围

```python
execute(command="python -I -S -B \"<skill_dir>/scripts/office/validate.py\" ./input.docx")
```

同一校验入口执行基础规则和以下显式结构约束；错误报告部件、表格/行/单元格或编号 ID/层级位置：

- 检查 `gridSpan`、`gridBefore/gridAfter` 数值及重复属性；按逻辑列区间检查 `vMerge restart/continue` 的前行连续性，相邻嵌套表格独立计算。
- 允许省略 `tblGrid` 及合法扩充网格，不把列宽总和或可由阅读器重建的网格当作通用错误。按格式要求忽略的偏移值会产生警告。
- 检查显式编号层级 `0..8`、重复定义/覆盖、直接段落编号层级引用，以及 `start/startOverride/lvlRestart` 基本数值规则。允许有符号的起始值；格式要求忽略的重启值会产生警告，不模拟实际编号序列。
- 对旧式 `hMerge`、包装/修订行单元格、样式链接或继承编号输出 `unverified` 警告。人工核对或报告未验证，禁止把无错误退出解释为这些复杂语义已验证。

不执行完整 XSD、样式继承、阅读器布局和编号计算，也不自动修复输入。不能将“检查通过”表述为全部合并/编号形式都受支持、整份文档符合国家标准或视觉验收通过。
