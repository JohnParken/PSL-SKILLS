# WPS 文档选择性读取与高阶检索指南

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 概述

在处理大篇幅或历史复杂的金山 WPS（`.wps/.rtf`）文档时，直接全量读取容易造成 Token 消耗过大或模型上下文溢出。`read_wps.py` 提供了选择性投影读取能力，允许精准按大纲层级、段落范围或指定章节提取内容。

## 选择性读取模式

### 1. 仅提取大纲目录（`--heading-only`）

快速抓取文档中的各级标题，掌握文档结构与目录骨架：

```console
# 输出结构化 JSON
python -I -S -B "<skill-root>/scripts/read_wps.py" ./长篇汇报.wps --heading-only

# 输出精简纯文本大纲
python -I -S -B "<skill-root>/scripts/read_wps.py" ./长篇汇报.wps --heading-only --format text
```

### 2. 按段落范围切片（`--paragraph-range START:END`）

按 1-based 包含两端的段落序号进行范围截取，便于分批审阅长文档：

```console
# 仅读取正文第 1 到第 30 段
python -I -S -B "<skill-root>/scripts/read_wps.py" ./长篇汇报.wps --paragraph-range 1:30 --format text
```

### 3. 按章节标题定向提取（`--section EXACT_HEADING`）

从匹配的章节标题开始读取，直至遇到同级或更高级别的标题为止，返回该章节的完整上下文：

```console
# 定向读取“总体要求”章节
python -I -S -B "<skill-root>/scripts/read_wps.py" ./通知.wps --section "总体要求" --format text
```

## 适用场景与建议

- **长文档审阅**：先执行 `--heading-only` 确认章节名称，再针对关键章节执行 `--section`。返回量取决于文档和所选范围；没有宿主 usage 测量时，不宣称具体 token 节省比例。底层仍完整解析输入，不降低解析内存或资源校验。
- **分页抽查**：使用 `--paragraph-range` 快速定位特定段落，核验修改结果或局部数据。

## 定位与部分结果契约

概况/有界分页与大纲/段落/章节选择是不同输出模式，不叠加；每次只选一个选择性模式，冲突参数在读取前退出2，不在分页片段上伪装全篇定位。

可获取时优先使用 RTF outlinelevel 或 OOXML 段落样式继承后的 outlineLvl；没有可靠大纲时才按标题文本启发式识别，`heading_basis` 标明来源。目录不是视觉分页结果。重复标题不任意选第一个：JSON 返回 `selection_status=ambiguous` 及候选段落序号，再传 `--section "标题" --section-index 12`（12 为候选实际段落序号）消歧。无匹配返回 `selection_status=not_found`。

投影保留源 `partial`、issues、omitted_regions、reading_assessment 和可用诊断，`source_status` 是源读取状态；`projection_scope` 说明只选取主段落。`reading_assessment` 描述源文件，不代表投影覆盖全文。完整源上的歧义/未找到退出1；源本身partial时保留退出3和诊断，仍须先核对selection_status再消费选中内容。需要诊断时使用JSON，纯文本不能替代结果契约。
