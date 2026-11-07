# DOCX 生成参考：按需加载索引

路径占位符说明：`<skill-root>` 是本 DOCX Skill 根目录的文档占位符，不是 Shell 变量。取得宿主本次实际读取主入口 `SKILL.md` 的真实 `file_path`；若为相对路径，先按本次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名，所得目录才是根目录，不能使用参考文档所在目录。读取资源或执行命令前，将 `<skill-root>` 整体替换为该真实目录，保留资源后缀，将完整路径作为一个参数正确引用；嵌入 execute 字符串时按其语法转义。无法确认主入口真实路径时停止依赖操作并报告，禁止猜测路径。示例的工作目录为宿主已确认的任务目录 `./`，任务输入和受控临时文件沿用该目录内相对路径规则；交付输出只传裸文件名，由脚本固定写入任务根目录 `./`，不写入 Skill 安装目录。

只需最短 JSON 时优先使用 `SKILL.md`；字段概览查 `generation-core.md`。**常见任务应按 `SKILL.md` 直接进入专项文件，无需先读取本索引。不要把此索引当作需要继续读取全部文件的任务清单。** 本文件保留旧版 `generation.md#章节` 导航锚点，详细契约已无损迁移到下列专项文件。执行和路径安全要求以 `<skill-root>/SKILL.md` 为准。

| 任务意图 | 加载文件 |
|---|---|
| 常用 `theme` / `blocks` / 样式概览 | `<skill-root>/references/generation-core.md` |
| 主题、字体、字号、行距、边距、样式覆盖 | `<skill-root>/references/generation-styles.md` |
| `paragraph`、`runs`、`list`、图片、目录、分节与字段列表 | `<skill-root>/references/generation-blocks.md` |
| 表格配置、边框、单元格、复杂表头 | `<skill-root>/references/generation-tables.md` |
| 格式覆盖示例、中文 JSON 引号 | `<skill-root>/references/generation-examples.md` |
| 预检、命名、归档、兼容 CLI、配置诊断、交付提示 | `<skill-root>/references/generation-runtime.md` |
| P1 标题编号/合并单元格/目录细节 | `<skill-root>/references/p1-layout.md` |
| P2 多节/页眉页脚/交叉引用细节 | `<skill-root>/references/p2-layout.md` |
| 分页诊断 | `<skill-root>/references/pagination-governance.md` |

## 目录

上方任务路由表即为当前目录；原版目录内容已映射到专项参考。

## 历史锚点兼容导航

## 路径解析示例

详见 `<skill-root>/references/generation-runtime.md#路径解析示例`，仅在任务涉及本节时加载。

## 预检与集成验收

详见 `<skill-root>/references/generation-runtime.md#预检与集成验收`，仅在任务涉及本节时加载。

## 交付文件命名

详见 `<skill-root>/references/generation-runtime.md#交付文件命名`，仅在任务涉及本节时加载。

## 输出目录与文件名

详见 `<skill-root>/references/generation-runtime.md#输出目录与文件名`，仅在任务涉及本节时加载。

## 配置调试

详见 `<skill-root>/references/generation-runtime.md#配置调试`，仅在任务涉及本节时加载。

## 主题与文档标题

详见 `<skill-root>/references/generation-styles.md#主题与文档标题`，仅在任务涉及本节时加载。

## 顶层字段

详见 `<skill-root>/references/generation-blocks.md#顶层字段`，仅在任务涉及本节时加载。

## 默认格式与页面

详见 `<skill-root>/references/generation-styles.md#默认格式与页面`，仅在任务涉及本节时加载。

## 样式字段与覆盖顺序

详见 `<skill-root>/references/generation-styles.md#样式字段与覆盖顺序`，仅在任务涉及本节时加载。

## 内容块

详见 `<skill-root>/references/generation-blocks.md#内容块`，仅在任务涉及本节时加载。

## 表格配置与示例

详见 `<skill-root>/references/generation-tables.md#表格配置与示例`，仅在任务涉及本节时加载。

## 显式覆盖示例

详见 `<skill-root>/references/generation-examples.md#显式覆盖示例`，仅在任务涉及本节时加载。

## 中文 JSON 与引号

详见 `<skill-root>/references/generation-examples.md#中文-json-与引号`，仅在任务涉及本节时加载。

## 生成后格式引导

普通新建成功交付直接使用 `SKILL.md` 的「验收与收尾」短规则，不读取运行说明；旧格式引导详见 `<skill-root>/references/generation-runtime.md#生成后格式引导`（仅维护历史兼容）。

## P1 原生排版扩展（2026-10-31）

详见 `<skill-root>/references/generation-blocks.md#p1-原生排版扩展2026-10-31`，仅在任务涉及本节时加载。

## P2 原生高级版式扩展（2026-10-31）

详见 `<skill-root>/references/generation-blocks.md#p2-原生高级版式扩展2026-10-31`，仅在任务涉及本节时加载。

