# 原生图表、表格与公式

## 使用条件

先按 `<skill-root>/SKILL.md` 完成自检，在默认任务目录 `./`准备 SVG，不在安装目录生成文件。以下三个最小例子在阶段1快照已通过原生转换与完整 CLI 组包回读，分别验证柱形图数值 `[10,20]`、表格 `[["Item","Value"],["A","10"]]` 和公式 `m:oMath` 存在。它们不代表所有图表类型、任意公式、复杂合并表格或 Office 客户端视觉均已验收。

每个对象使用一个 `<g>`、明确 `data-pptx-replace-with`、一个 JSON `<metadata>`，并显式填写 `x`、`y`、`width`、`height`，坐标使用页面 SVG 坐标系。Chart/table 使用 `data-pptx-native-authority="json"` 表明这些新建对象由 JSON 定义；formula 不设置该属性。这些新建 SVG 例子不要添加旧 PPTX 的 opaque `source_package` 或任意外置 payload 引用；既有 PPTX 的导入/往返须走受控 import/rebuild，从验证的原始包再建可信基线，不能把旧包路径直接塞进新建元数据。

## 柱形图

将此结构作为任务目录中的 `chart.svg`，替换用户已确认的分类、系列名和数值，保持数组对应关系：

```xml
<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
  <g id="chart" data-pptx-replace-with="chart" data-pptx-native-authority="json">
    <metadata type="application/json">{"type":"column","categories":["A","B"],"series":[{"name":"Revenue","values":[10,20]}],"x":50,"y":50,"width":500,"height":300}</metadata>
  </g>
</svg>
```

输出包含原生 Chart 部件和嵌入工作簿，不是把画好的柱形图截图贴进幻灯片。数值必须来自用户数据或明确说明的计算，不能凭图形高度猜造；缺值和其他图表类型需按转换器实际契约处理，不由这个例子推断全面支持。

## 表格

作为独立的 `table.svg`：

```xml
<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
  <g id="table" data-pptx-replace-with="table" data-pptx-native-authority="json">
    <metadata type="application/json">{"schema":"ppt-master.semantic-table.v2","rows":[["Item","Value"],["A","10"]],"x":50,"y":50,"width":500,"height":300}</metadata>
  </g>
</svg>
```

输出为原生 `a:tbl`。本例的单元格是字符串，`"10"` 不被改写成计算值；保留前导零等原文。这个简单二维数组不说明合并、复杂单元格样式或其他 schema 可直接照搬，遇到未核实字段不要猜测。

## 公式

作为独立的 `formula.svg`：

```xml
<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
  <g id="formula" data-pptx-replace-with="formula">
    <metadata type="application/json">{"latex":"x^2+\\frac{a}{b}","x":50,"y":50,"width":500,"height":300}</metadata>
  </g>
</svg>
```

JSON 字符串中的 `\\` 表示 LaTeX 的单个反斜杠；不要再次按 Shell 转义或将反斜杠丢失。本例是 `x²+a/b` 的原生数学对象，`m:oMath` 存在只证明原生数学结构，不能当作 Office 排版截图验收。

## 生成与验证

三页按显式参数顺序输出：

```console
"<skill-root>/scripts/.venv/bin/python" -I -B "<skill-root>/scripts/tl_pptx.py" convert "原生对象示例.pptx" "chart.svg" "table.svg" "formula.svg"
```

用户文本嵌入 JSON/XML 时用正规序列化，正确处理引号、反斜杠及 `&`/`<`，不能把材料拼成 Shell 命令。原生 JSON 还有独立限制：最多 2 MiB 编码字节、20,000 节点、深度 40、字符串合计 1 Mi 字符，拒绝非有限数值和 `source_package`。其他安全和整体预算继续适用，不能通过拆分元数据规避。

按主入口分别核对生成、结构验收、发布、归档与清理，检查实际图表数值、表格文字与公式结构；客户端不可用时明确视觉未验证。更多来源及有意差异见 `<skill-root>/references/upstream.json`；它是维护追溯数据，不需要每次生成前全文读取。
