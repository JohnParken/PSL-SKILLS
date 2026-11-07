# 格式覆盖与复杂 JSON 示例

路径占位符说明：`<skill-root>` 是本 DOCX Skill 根目录的文档占位符，不是 Shell 变量。取得宿主本次实际读取主入口 `SKILL.md` 的真实 `file_path`；若为相对路径，先按本次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名，所得目录才是根目录，不能使用参考文档所在目录。读取资源或执行命令前，将 `<skill-root>` 整体替换为该真实目录，保留资源后缀，将完整路径作为一个参数正确引用；嵌入 execute 字符串时按其语法转义。无法确认主入口真实路径时停止依赖操作并报告，禁止猜测路径。示例的工作目录为宿主已确认的任务目录 `./`，任务输入和受控临时文件沿用该目录内相对路径规则；交付输出只传裸文件名，由脚本固定写入任务根目录 `./`，不写入 Skill 安装目录。

本页仅供对应任务按需加载。定位 `<skill-root>` 与任务目录、执行限制以主入口 `<skill-root>/SKILL.md` 为准；本页提供完整原始字段契约。

## 显式覆盖示例

保留默认字体，分别覆盖正文、一级标题、单个段落和局部文字。下面的JSON仅展示完整字段结构；实际复杂输入使用Python构造并序列化后传stdin，见复杂JSON模板（`<skill-root>/references/complex-json.md`），不要手写拼接或直接落盘：

```json
{
  "page": {"margin_mm": 20, "margin_top_mm": 25.4},
  "styles": {
    "body": {"size_pt": 14, "line_spacing_pt": 24},
    "heading1": {"size_pt": 20, "align": "left"}
  },
  "blocks": [
    {"type": "heading", "level": 1, "text": "项目报告 / Project report",
      "format": {"size_pt": 18}, "align": "center"},
    {"type": "heading", "level": 2, "text": "一、工作进展"},
    {"type": "heading", "level": 3, "text": "（一）阶段成果"},
    {"type": "heading", "level": 4, "text": "1. 复核情况"},
    {"type": "paragraph", "text": "负责人说：“已完成复核。” Status: reviewed."},
    {"type": "paragraph", "format": {"first_line_chars": 0, "bold": true},
      "runs": [
        {"text": "说明："},
        {"text": "此句取消加粗，西文使用 Arial。", "bold": false, "font_latin": "Arial"}
      ]}
  ]
}
```

核对生成结果：顶部边距 25.4mm、其余三边 20mm；一级标题按块覆盖为 18pt 居中；普通正文为 14pt、固定 24pt；最后一段取消首行缩进，第二个 run 取消加粗并仅覆盖西文字体。二至四级标题继续使用各自默认值。不要把一次调用的覆盖保存成下一份文档的新默认值。

## 中文 JSON 与引号

使用 UTF-8 编码保存输入。保留英文半角结构符号 `"`、`:`、`,`、`{}`、`[]`；中文文字、中文标点和中文引号可以作为字符串内容。不要翻译字段名和 `type` 等枚举值，不要加入注释或尾随逗号。

使用下面的合法段落块；正文中的中文引号无需转义，正文中的英文双引号使用 `\"` 转义：

```json
{"type": "paragraph", "text": "负责人说：“请在周五前完成复核。”"}
```

```json
{"type": "paragraph", "text": "负责人说：\"请在周五前完成复核。\""}
```

将这些段落块放入完整输入的 `blocks` 数组，不要直接把单个段落块作为生成器的顶层输入。

不要使用下列写法；它们是错误示例，不是可运行 JSON：

```text
{“type”: “paragraph”, “text”: “中文内容”}
{"type": "paragraph", "text": "负责人说："请完成复核。""}
```

第一例使用了中文语法引号；第二例没有转义正文中的英文双引号。不要把正文中合法的中文引号全部替换掉。出现 `JSONDecodeError` 时，根据报错的行号、列号检查该位置及其前面的引号、冒号和逗号；仅凭“有中文引号”无法确定原始输入的具体错误。

复杂输入统一使用Python调用模板（`<skill-root>/references/complex-json.md`）：构造dict/list后通过json.dumps(..., ensure_ascii=False, allow_nan=False)序列化，传给标准runner的stdin，不另建document.json。ensure_ascii=False保留可读中文，序列化器负责必要转义，但不会修复已写错的JSON或Python源码，也不代替业务校验。

仅在已确认的兼容/定制流程确需配置文件时，先按任务清单（`<skill-root>/references/task-cleanup.md`）登记尚不存在的任务根目录文件名，再独占创建；成功、失败或停止都由外层在子进程退出后seal/clean并核对清单删除，不能用write_text覆盖既存输入。用户提供的既存JSON属于受保护输入，可位于任务目录及其子目录；本轮新增的临时JSON只能平铺于`./`。新建主文件位于任务根目录，编辑输出也只传裸文件名并写入任务根目录；不得将任务数据写入Skill安装目录。

