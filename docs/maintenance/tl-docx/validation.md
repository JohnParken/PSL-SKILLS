# tl-docx 首版验收

Skill 版本 `20261113.1636.3970`。对标当前工作树的 `docx` 版本 `20261113.1526.2905`。验收环境为 macOS 27.2.1 arm64、Python 3.14.10、python-docx 1.2.0、lxml 6.1.3、typing_extensions 4.16.0。命令从已确认的仓库根执行，读真实附件和写文件回归各使用独立任务目录。工作区已经存在的其他 Skill 维护内容保留原样。

## 真实附件

用户提供的 2026 年 10 月运营月报作为只读输入。使用标准库从 ZIP XML 独立取得正文、单元格、文本框和图表的预期，分别比对以下结果。附件、正文、图表数值及外链内容均未加入源码或安装包。

| 项目 | 实际结果 |
| --- | --- |
| 直属正文 | 83 段逐项相符。标题视图返回 18 项，8 个直接单级十进制编号有标签。 |
| 表格 | 14 张、197 行、855 个物理单元格，单元格文本按顺序逐项相符。11 个纵向合并起点，18 个续行关联。 |
| 文本框与图片 | 27 个文本框逐框相符。11 处图片引用对应 6 个包内资源，每个资源摘要与 ZIP 中原字节相符。 |
| 图表 | 2 张原生图、5 个系列，12 对带数值的点按部件、索引、类别和原始字符串逐项相符。3 个系列的数值缓存缺失；两个外部工作簿只识别引用。读取结果为 `partial`。 |
| 分批与源保护 | 默认结果 1,224 条、405,580 UTF-8 字节。12,000 字节预算返回 42 批，逐项拼回与默认结果相同。所有批次退出 0、stderr 为空。原件 SHA-256 前后相同。记录数含表格行、单元格段落与来源信息，不等同于对标 Skill 的块数。 |
| 静态布局 | 退出 0，存储关系及 XML 验证通过。报告 1 个分节、1 个显式分页和首表全部数据行设置重复表头的提示。 |

另从包含 17 个运行文件的独立 ZIP 解包，以只读权限、空 PATH 和禁止读取仓库的 Python 审计钩子复跑真实附件。stdout 与源码运行逐字节一致，stderr 为空，安装文件的摘要均未改变。审计钩子是 Python 层面的验收措施。

读取增强包括单元格内段落与 XML 定位、图片资源 SHA-256、字段指令与保存结果、批注和修订文字等。适用场景经人工检查包括指定用 python-docx 读取复杂月报、生成 DOCX 公文和定点修改字体。旧 DOC、RTF/WPS、仅做图片 OCR 的请求应使用对应能力。未执行真实宿主自动选用测试。

## 自动验证

| 检查 | 结果 |
| --- | --- |
| `python -I -B -m unittest discover -s skills/tl-docx/scripts/test -p 'test_*.py' -q` | 20 项通过，无跳过。覆盖创建、读取与分批、DOTX、结构验证、编辑保护、表格行、经典批注、图片替换、路径拒绝及归档重试。 |
| `python -I -S -B -m unittest discover -s tests -p 'test_skill_runtime_packages.py' -q` | 14 项通过，无跳过。新包从外部只读安装、空 PATH 运行；分别检查依赖缺失和有依赖的读、写、编辑及归档。 |
| `python -I -S -B -m unittest discover -s tests -p 'test_runtime_packager.py' -q` | 11 项通过，无跳过。 |
| `python -I -S -B -m unittest discover -s tests -p 'test_artifact_lifecycle_parity.py' -q` | 3 项通过，无跳过。公共交付副本内容与来源清单摘要一致。 |
| `python -I -S -B skills/skill-development-standards/scripts/check_skill.py skills/tl-docx --strict` 与 `git diff --check` | 退出 0。 |
| `python -I -S -B tools/check_project_docs.py` | 全仓仍退出 1。ET、OFD、table-workflow、XLSX 在本轮前已存在版本不同步；新 Skill 的版本与链接无新增报错。 |
| Pylint 4.1.3 | 用 `--persistent=no --max-line-length=200` 检查新 Skill 的运行和测试脚本、两份更新的仓库测试，退出 28，剩余复杂度、文档字符串和低层 OOXML 接口访问等提示。相同范围 `--errors-only` 退出 0。无 C0301/C0321。 |

现有 DOCX 的全部兼容接口、现代线程批注、自动命名、原清单恢复和完整分页治理未迁移。嵌入或外部图表工作簿、图像 OCR、Word/WPS 视觉呈现、目标 Linux 容器、Windows、真实宿主回传与云空间同步未验证。合成测试不用于推断这些条件下的验收结果。
