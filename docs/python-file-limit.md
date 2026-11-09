# Python 文件行数硬规范与职责拆分

本页验收记录的历史基线：`926505d9b1ca6ef03fe36ffab7634c8f7045820a` 加当时尚未提交的 DOCX 字体编辑/格式保护及模块拆分变更。验收证据记录于提交前；后续状态以 Git 历史和远端 SHA 为准，历史结果不自动代表当前版本重测。

## 规则与检查

所有 Skill 内 `.py` 文件最多 1000 物理行，1000 行允许，1001 行开始为 `python-file-lines` error。包括注释、空行、测试、资源脚本和内嵌第三方引擎，不设置生成代码或第三方代码豁免。按 Python 源码编码与通用换行读取；LF、CRLF、CR 各作为一行终止，末尾非空但没有换行仍算一行，字符串中的 Unicode 分隔符不额外计数。

规则同时写入 [AGENTS.md](../AGENTS.md) 和 [唯一开发规范](../skills/skill-development-standards/SKILL.md)。默认和严格模式都拒绝超限；严格模式仍会把原有建议作为失败处理，不隐藏既有缓存警告。

```console
python -I -S -B skills/skill-development-standards/scripts/check_skill.py skills/*
python -I -S -B skills/skill-development-standards/scripts/check_skill.py skills/ofd --strict
python -I -S -B -m unittest discover -s skills/skill-development-standards/scripts/test -p 'test_check_skill.py' -v
python -I -S -B tools/run_all_tests.py --report-dir docs/validation/new-run
python -I -S -B tools/check_project_docs.py
```

## 扫描与重构

初始扫描覆盖 10 个 Skill、472 个 Python 文件，14 个超过 1000 行：OFD 1 个、DOCX 1 个、ET 1 个、XLSX 1 个、PPTX 10 个。没有通过删空行、压缩语句、放宽阈值或排除内嵌引擎消除问题。

- OFD：安全输入/共享预算、ZIP/XML 容器、存储结构目录从稳定读取入口拆出；原函数与类 AST 保持一致，状态、partial、签名/附件和目录边界不变。
- DOCX：格式约束、输入/图片规格和 OOXML 生成分工，交付/归档入口保留；兼容现有未提交字体保护工作。
- ET：解码值与稳定异常分离；XLSX：纯通配匹配分离，读取和公式能力不扩大。
- PPTX：DrawingML 和原生图表/表格/公式模块按职责拆开，以显式导入保留旧入口；保留 upstream 来源、许可证及派生映射。
- 运行清单显式列出新增依赖，原 CLI/API 名称保持；单包自包含，不使用仓库公共目录或相邻 Skill。

## 最终扫描结果

全量重扫 10 个 Skill、553 个 Python 文件，超限 0 个，最大 965 行。新增文件用于职责拆分与兼容回归，不减少业务内容或测试覆盖。完整逐文件统计见 [源码检查](validation/python-line-limit-source-20261109.json)，版本与原文件前后计数见 [汇总](validation/python-line-limit-summary-20261109.json)。六个被维护 Skill 的版本同步为 `20261109.1818.2343`；description 已核对，本轮不扩大能力或改写适用范围。

| 原超限入口 | 原行数 | 拆分后入口行数 |
| --- | ---: | ---: |
| `pptx/scripts/_engine/svg_to_pptx/drawingml/elements.py` | 5190 | 241 |
| `pptx/scripts/_engine/svg_to_pptx/drawingml/utils.py` | 3887 | 253 |
| `pptx/scripts/_engine/svg_to_pptx/drawingml/converter.py` | 2608 | 206 |
| `pptx/scripts/_engine/svg_to_pptx/native_objects/formula_parser.py` | 2183 | 505 |
| `pptx/scripts/_engine/svg_to_pptx/native_objects/table.py` | 2059 | 117 |
| `docx/scripts/create_docx.py` | 2039 | 402 |
| `pptx/scripts/_engine/svg_to_pptx/native_objects/chart_xml.py` | 1742 | 117 |
| `pptx/scripts/_engine/svg_to_pptx/native_objects/chart_data.py` | 1712 | 83 |
| `pptx/scripts/_engine/svg_to_pptx/native_objects/chart_style.py` | 1697 | 121 |
| `et/scripts/wps_biff8.py` | 1159 | 958 |
| `ofd/scripts/read_ofd.py` | 1113 | 496 |
| `pptx/scripts/_engine/svg_to_pptx/native_objects/marker_common.py` | 1112 | 120 |
| `pptx/scripts/_engine/svg_to_pptx/native_objects/formula_omml.py` | 1051 | 96 |
| `xlsx/scripts/formula_engine.py` | 1020 | 965 |

## 验证结果与边界

平台为 macOS 27.2 arm64、Python 3.12.12、Expat 2.6.3；全部命令保留 `-I -S -B`。

- [最终全量回归](validation/python-line-limit-tests-final-20261109/summary.json)：2417 项全部通过，0 失败、0 跳过。包括 DOCX444、ET482、OFD154、PPTX106、XLSX403、WPS447、生命周期17、规范39、仓库322及PPTX适配器3。
- 全量中包含十包独立安装11项、DOCX专项包26项和通用打包安全11项；只读安装、空 PATH、包/源码指纹、跨包依赖及全部运行包严格检查通过。测试临时构建新 ZIP，不改写既有发布包。
- 默认源码检查十个 Skill 全部通过，所有 `python-file-lines` error 为0。严格源码检查8个通过；ET70条、WPS56条原有缓存警告仍按严格模式失败，未删除归属不明缓存或豁免规则。运行包不包含这些缓存，十包严格检查通过。
- 新增8项规范回归覆盖999/1000/1001、注释/空行/文档字符串、LF/CRLF/CR、无末尾换行、Unicode分隔符、源编码、嵌套/资源/测试目录及默认/严格CLI退出码。
- [完整性记录](validation/python-line-limit-integrity-20261109.json)：OFD21个函数/类AST保持一致；PPTX141条来源记录哈希匹配，67个新增引擎模块逐一在新解释器中导入成功；新增Python模块未发现超200字符行或单行多语句。
- [首轮结果](validation/python-line-limit-tests-20261109/summary.json) 如实保留。首轮发现XLSX测试将SHA中的“001”误当单元格内容，现改为验证解析后的工作表概况，生产摘要逻辑未改；另两个失败为当前加载/选择快照过期，人工复核后更新当前证据并保留旧副本。最终完整重跑通过。
- README、docs、运行清单、来源记录及版本已同步；文档链接/版本检查和 `git diff --check` 通过。PPTX许可证、OFD字体原件及既有DOCX格式保护工作保留。

Linux/Windows未重跑，真实客户端视觉、完整格式认证及宿主端到端触发未验证。Pylint未安装，轻量AST/行长扫描不是全量lint；ET原主体既有6条超200字符行及57个多语句行单独保留，不把本轮文件长度修复称为所有风格问题清零。结构检查、AST比较和合成回归不能替代真实阅读器验收。后续提交与推送不改变上述验证范围。

## 提交前复核

分批暂存覆盖新增文件后，发现30个新模块末尾存在多余空行；已仅规范末尾换行并逐文件确认AST不变，PPTX来源哈希与扫描计数同步。[推送前完整回归](validation/python-line-limit-prepush-tests-20261109/summary.json) 2417项全部通过，0失败、0跳过；保留此前各轮证据，不用格式等价代替实际重跑。

用户后续要求已补入 AGENTS：Python 默认使用 Pylint 做合规检查，修复 Error/Fatal，不以禁用规则掩盖问题。本机 PATH 无 pylint，已核实 Python3.12/3.13 均未安装；没有擅自安装，也没有执行或宣称 Pylint Error 已清零。该工具状态与2417项行为回归通过分别报告。首轮失败日志仅规范一处行尾显示空格，原诊断行保留在完整性记录中。
