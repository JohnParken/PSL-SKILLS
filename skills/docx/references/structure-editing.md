# 受控结构编辑输入约定

路径占位符说明：`<skill-root>` 是本 DOCX Skill 根目录的文档占位符，不是 Shell 变量。取得宿主本次实际读取主入口 `SKILL.md` 的真实 `file_path`；若为相对路径，先按本次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名，所得目录才是根目录，不能使用参考文档所在目录。读取资源或执行命令前，将 `<skill-root>` 整体替换为该真实目录，保留资源后缀，将完整路径作为一个参数正确引用；嵌入 execute 字符串时按其语法转义。无法确认主入口真实路径时停止依赖操作并报告，禁止猜测路径。示例的工作目录为宿主已确认的任务目录 `./`，任务输入和受控临时文件沿用该目录内相对路径规则；交付输出只传裸文件名，由脚本固定写入任务根目录 `./`，不写入 Skill 安装目录。

路径定位与任务目录边界统一遵守 `<skill-root>/SKILL.md#执行环境约束与首步自检`；占位符必须用宿主本次已确认的真实目录替换，禁止猜测路径。

文件位置统一遵循 文件定位与读取规则（`<skill-root>/SKILL.md#文件定位与读取规则`）：文档及操作文件参数相对任务目录；后续读取使用本次实际输出路径，不按输入目录推导。
写入前按 交付文件命名（`<skill-root>/references/generation-runtime.md#交付文件命名`） 确定实际名称；以下业务名称仅为示例，须按本次任务替换，用户指定名称优先。

交付归档：编辑 CLI 和 公开交付 API（`<skill-root>/references/delivery.md#公开交付-api`） 的写操作成功后由代码自动归档本次输出；只读操作不归档。低层 API 或读取保存等其他方式生成的最终交付文件，按 当轮统一归档（`<skill-root>/references/delivery.md`） 显式列出本轮文件归档；不把验收中间文件或历史产物加入清单。


先执行技能入口要求的自检，保留输入文件，从宿主确认的任务工作目录执行；将 `<skill-root>` 替换为主入口定义的真实目录：

```python
execute(command="python -I -S -B \"<skill-root>/scripts/edit_structure.py\" ./采购管理办法.docx ./operations.json 采购管理办法_修订版.docx")
execute(command="python -I -S -B \"<skill-root>/scripts/office/validate.py\" ./采购管理办法_修订版.docx")
```

顶层只接受非空 `operations` 列表。各操作按原始 XML 定位，全部验证通过后才应用。`paragraph_index` 从 0 开始，只计算 `w:body` 的直属 `w:p`，不计表格或容器内段落；`run_index` 从 0 开始，计算锚点段落的直属 `w:r`。同时提供完全一致的 `expected_text`，run 格式修改另提供 `expected_run_text`，避免索引漂移时改错内容。

## 目录

- 已有文档字体修改
- 插入段落
- 修改普通 run 格式
- 拒绝边界与验收

## 已有文档字体修改

这是保留现有格式的编辑路径，不是新建排版。先核对原件及全部目标段落/run，使用 `<skill-root>/scripts/run_docx_edit.py` 的 `structure` 模式，只提交用户明确要求改变的字段。不要把读取纯文本、compact 或结构 JSON 转成生成规格后调用 `create`；新建默认主题和样式不能替代原文档格式。

例如原件的第一个直属段落只有一个普通 run，原文为“现有正文”，仅更改中文字体：

```python
execute(command="python -I -S -B \"<skill-root>/scripts/run_docx_edit.py\" structure ./原文档.docx 字体修订版.docx <<'JSON'\n{\"operations\":[{\"type\":\"format_run\",\"paragraph_index\":0,\"expected_text\":\"现有正文\",\"run_index\":0,\"expected_run_text\":\"现有正文\",\"format\":{\"font_east_asia\":\"黑体\"}}]}\nJSON\n")
```

仅改中文字体不附带西文字体、字号、颜色、加粗或段落属性；仅改字号只传 `size_pt`。每个目标 run 单独声明操作，所有索引基于原件；读取视图的 1-based 段落序号不能直接当作编辑器的 0-based 索引。全篇请求先核对覆盖范围，不把支持的正文子集声称为全篇完成。

`format_run` 保留原 `w:pPr`、`w:pStyle`、`w:rStyle`、未指定的 run 属性，以及 `styles.xml`、`numbering.xml` 等非目标部件；不展开或重建样式继承。首行/左右/悬挂缩进、行距、段前段后和制表位不是这项操作的修改目标。字符缩进与字体度量、自动行距及换行位置仍可能影响阅读器显示，属性保留不等于视觉完全相同。

格式阶段由 `<skill-root>/scripts/format_guard.py` 独立核对，不能只凭结构检查或修改后 XML 的序列化回读判断保留成功。检查基于修改前快照，只放行规格中的目标属性；失败发生在发布和归档之前。字体/格式与插段混合批次仍兼容：先核验格式阶段，再执行已验证的插段计划；这不等于对含插段的最终结果承诺“仅字体变化”。

已有手工 XML 候选须通过 `<skill-root>/references/ooxml-editing.md#字体修改与候选文件保护` 的 `format-checked` 模式后交付；该模式不接受插段或拆/合 run。

## 插入段落

例如输入的第一个直属段落原文为“现有正文”：

```json
{
  "operations": [
    {
      "type": "insert_paragraph",
      "paragraph_index": 0,
      "expected_text": "现有正文",
      "position": "after",
      "text": "补充说明：“中文与 English 均可保留。”",
      "style": "BodyText"
    }
  ]
}
```

`position` 只接受 `before` 或 `after`；`text` 为非空单行 XML 文字。`style` 可省略；指定时必须为目标文档中真实存在且 `w:type="paragraph"` 的段落样式 ID，不接受字符、表格或编号样式。省略时使用文档默认样式，不复制原段落的编号、格式、书签或节属性，不把样式显示名称当成样式 ID。

## 修改普通 run 格式

例如第一个直属段落只有一个普通 run，原文为“现有正文”：

```json
{
  "operations": [
    {
      "type": "format_run",
      "paragraph_index": 0,
      "expected_text": "现有正文",
      "run_index": 0,
      "expected_run_text": "现有正文",
      "format": {
        "font_east_asia": "仿宋",
        "font_latin": "Times New Roman"
      }
    }
  ]
}
```

`format` 必须非空，仅支持 `font_east_asia`、`font_latin`、`size_pt`、`bold`、`italic`、`color`；未请求的字段不传。字号为 1–200pt 的半点增量，颜色为六位 RGB 十六进制。显式 `false` 会关闭相应属性；指定颜色或字体时清除对应主题覆盖，未指定的属性保留。不安装或嵌入字体，不承诺字体可用或视觉显示结果。

## 拒绝边界与验收

禁止操作空段落、表格内段落、书签、复杂 run、字段、批注/移动范围、修订/节属性或未知容器锚点。相同 run 或相同段落插入位置重复操作时停止，不猜测顺序；多个不同锚点的索引均以原文为准。

遇到不支持的目标时，指出具体段落/run 或范围及拒绝原因，停止该请求。禁止删除复杂结构、静默跳过、仅编辑可支持部分后声称完成，或回退到读取后重建；用户明确缩小范围后才能按新范围编辑。

本入口不增删表格行、不替换图片、不改页眉页脚或整个段落样式；不得通过删除复杂结构让操作通过。使用独立输出且保持 DOCX/DOTX 扩展名不变；输入/输出不能经符号链接或硬链接指向同一文件。

实际输出前执行受限解析、已实现结构规则和 XML 节点/属性值保真核对，失败时保留原文件及已有输出。成功后对照操作核对文字和实际 XML 格式值，核对未修改 ZIP 部件内容字节；仍不等同于 XSD、国家标准、真实阅读器或视觉验收。
