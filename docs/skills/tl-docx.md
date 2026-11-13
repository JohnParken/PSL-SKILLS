# tl-docx

本 Skill 属于允许按需使用第三方组件的 `tl-` 系列，不受“仅 Python 标准库”限制；当前实际依赖仍以下方说明及 `requirements.txt` 为准。本次政策调整未新增组件，不扩大已实现能力，路径、安全与归档清理约束不变。

当前 Skill 版本 `20261113.2302.5108`。项目源码在 [Skill 入口](../../skills/tl-docx/SKILL.md)。Python ≥3.10，唯一直接依赖 `python-docx==1.2.0`。使用其文档对象及 XML 验证，标准库处理包边界、原始 XML 结构、续读、模板替换、发布和归档。`lxml`、`typing_extensions` 由上游组件传递安装。 [python-docx 1.2.0 依赖声明](https://github.com/python-openxml/python-docx/blob/v1.2.0/pyproject.toml)

## 与 docx 的能力对照

| 任务 | tl-docx 实际入口及覆盖 |
| --- | --- |
| 读取报告 | 保留默认完整报告。支持原始 XML、节点树、三个修订视图、精细表头与合并定位、图形偏移、图表点分批、精简表格和重复章节消歧。`-o` 保存 JSON、文本或 XML，归档可显式开启。见 [本轮月报与安装验收](../maintenance/tl-docx/read-write-20261113.md)、[前轮回归](../maintenance/tl-docx/parity-20261113.md) 和 [首版验收](../maintenance/tl-docx/validation.md)。 |
| 创建 | 支持公文、商务、学术样式，新增多级中文和十进制标题、命名列表续号、复杂表格预设与逐边设置、首页和奇偶页眉页脚、分节页码。 |
| 编辑及批注 | `edit` 支持定点文字和 run 格式、段落插入、规则表格行和简单单元格、同类型图片。`replace` 按预期次数批量填充正文、指定页眉页脚的普通文字。`comments` 支持 DOCX/DOTX 的现代四部件批注查询、增加、一级回复、更新及删除，经典简单批注支持查询、更新、删除。格式参考 [Microsoft Word 扩展规范](https://learn.microsoft.com/en-us/openspecs/office_standards/ms-docx/b839fe1f-e1ca-4fa6-8c26-5954d0abbccd)。 |
| 分页、命名及交付 | `validate --strict-pagination` 可将 12 类静态分页警告作为质量门禁。创建与编辑可自动命名，同名递增，发布时记录原清单。`resume` 只恢复该任务的收尾，`archive` 可按已核对的 SHA-256 重试归档。 |

## 剩余读写差距

对照工作树中 `docx` 版本 `20261113.2302.5108` 的入口、参考与实现。两套 CLI 的参数和 JSON 格式按各自契约使用。

| 方向 | 当前 `docx` 已有而 `tl-docx` 尚缺或更窄 |
| --- | --- |
| 结构与报告兼容 | 节点树用自有 schema，原始部件可按字节取出。缺少旧版 `--start-block`、字符预算、旧结构和精简回执字段的兼容，以及现有 `docx` 的跨次报告解析缓存及释放入口。单个大 XML 树只能作为一条记录返回。复杂属性与表格修订保留结构和诊断，实际还原仍有限。 |
| 创建样式与验证 | 尚缺完整 Profile、全局角色样式、原工具的分节尺寸字段和专门排版验证器。表格 `body_format`、`header_format` 的段落及字体控制、独立 `reference` 块仍有差距，当前可用行内 REF/PAGEREF。 |
| 底层编辑 | 缺少受控修订标记写入、run 合并、解包重包和独立字体候选差异保护工具。通用定点编辑仍限 DOCX，DOTX 写入支持批注和模板替换。 |
| 原清单恢复与交付细节 | 支持本工具原清单收尾。`docx` 的历史 worker 清单、历史 retry 事务、可编排的多文件清单与精简兼容回执仍由其原入口处理。 |

两者都依赖阅读器验证实际页码、断页、目录字段与字体效果。图表的工作簿实时值、图片 OCR、复杂线程和任意无损编辑仍需单独能力。`tl-docx` 已提供带 XML 定位的单元格段落、图片 SHA-256 和行内字段构建。

## 构建和使用

从仓库根执行 `python -I -S -B tools/package_skills.py tl-docx` 构建 ZIP。按 [白名单](../../packaging/runtime-manifests/tl-docx.json) 分发，保留必要运行文件，安装后从宿主确认的任务目录使用。预装依赖的解释器以 `-I -B` 执行，不依赖仓库布局或相邻 Skill。合成测试、真实附件和维护验收材料各自独立，附件不纳入运行包。
