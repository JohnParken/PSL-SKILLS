# 优先项 4 / 5 / 1：描述、按需加载与 PPTX 数学诊断

核对日期：2026-11-10 UTC。源码基线 `65bff45ffb9375949d6fe547c4e4837c96213bd7` 加本轮工作区变更；本轮没有提交、推送或重新发布历史 ZIP。这里只记录仓库维护，不是业务运行参考，不随 Skill 运行包分发。

## 范围与依据

用户授权优先实施：ET 入口与 XLSX 基础分析按需加载；适用 Skill 的 description 与选择边界；PPTX 公式读取提示、诊断和回归。本轮维护 `bank-writing`、`et`、`ofd`、`pptx`、`table-workflow`、`wps`、`xlsx` 七个 Skill，版本均为 `20261110.2126.1082`。采样 America/New_York 时间为 `2026-11-10T09:26:10.824179-05:00`，将日历时间整体加 12 小时并截断到百分之一秒。未修改其余三个 Skill。

审查参考 [DeepAgents Skills 文档](https://docs.langchain.com/oss/python/deepagents/skills) 的元信息发现、激活后读取入口、资源按需加载建议，以及 [DeepAgents 0.7.35 源码](https://github.com/langchain-ai/deepagents/blob/deepagents==0.7.35/libs/deepagents/deepagents/middleware/skills.py)；发布信息见 [0.7.35 release](https://github.com/langchain-ai/deepagents/releases/tag/deepagents==0.7.35)。文档为滚动版本，本次按 2026-11-10 审查所核对内容实施，不推断目标宿主已经部署此版本。

只改 Skill、运行包清单和仓库维护资料，不修改 DeepAgents 加载器或执行机制。保留任务目录顶层文件定位、真实 Skill 路径、自检、输入保护、独立部署、安全拒绝、partial 和归档清理要求。已有 OFD 用户验收目录未读取、修改或删除；其他历史证据和发布包保留。

## 实施结果

1. ET 主入口保留最小完整读取闭环，把 CFB/NAME/DXF 等具体兼容诊断和显示、保存视图细节移到既有高级参考，并按任务给出直达条件。没有修改 ET 解析器、默认模式或退出码。
2. XLSX 的 data-analysis 保留 profile/summarize、完整区域、类型、缓存与公式策略、统计质量和金额核对规则。格式识别移入 read-analyze；ET JSON 来源规则单列 et-result-analysis，消费 ET 时与基础统计契约并读；删除重复专项目录，保留旧锚点的条件转向。新参考显式加入 XLSX 运行包白名单。没有改 XLSX 业务代码或实现新的 ET 无文件交接。
3. 七项 description 均经实际能力核对，长度为 100–150 个 Unicode 字符：PPTX 以文字材料制作汇报起笔；ET 强调保存值与缓存；XLSX 区分有限编辑、原生格式和模板填报；WPS 说明原生 DOCX 优先路由；bank-writing 补全摘要、仿写；OFD 排除页面预览；table-workflow 明确已有目标模板。名称未改。人工期望不等于模型选路实测。
4. PPTX inspect 保留原文字拼接和结构/退出码契约；检测所选语义分支中的 OMML 标记时，逐页附加 `text_assessment.status=math_structure_not_preserved`、`diagnostics=["math_text_flattened"]`，并分别记录正文/备注数学段落数。`scope=slide_text_and_notes_before_projection` 明确计数在投影之前；概况、选页、分页和省略备注仍保留返回页诊断。分数 12/15 可读成 1215，上下标也可丢失；入口必须提示不能据此精确转录或计算。结构通过仍为退出 0，PNG 容错 partial 仍为退出 3；没有诊断也不证明所有数学表达形式准确。

README、docs 当前索引及七篇 Skill 导读同步更新；历史日期化验收记录未改写。当前加载快照为 58 个计划场景，选择快照为 41 条人工期望；原 57/31 项快照分别逐字保存在 [加载旧快照](../../../tests/runtime_loading_profiles_before_review_451_20261110.json) 与 [选择旧快照](../../../tests/skill_selection_current_cases_before_review_451_20261110.json)。它们的 SHA-256 分别为 `d1f8d7c5c0733f1ccf9371915492f8f18353a17a9a57d4023c281c0c2f7daf48`、`90dd6977e10a609935c28af7f1a78ddadac76a132709048ac7e0cef4d5d53e03`，已与基线 Git 字节比较。

## 可复现的字符量比较

下表使用上述 65bff45 基线与当前 UTF-8 解码后的 Unicode 字符数，按整文件读取，包含元信息；不换算 token。当前常规 benchmark 仍沿用其原固定基线 4b5a61f，两者不可混用。

| 场景/文件 | 修改前 | 修改后 | 变化 |
| --- | ---: | ---: | ---: |
| ET 主入口 | 9,190 | 7,922 | −13.80% |
| XLSX data-analysis 参考 | 17,126 | 9,698 | −43.37% |
| 原生 XLSX 区域分析：入口 + data-analysis | 25,779 | 18,605 | −27.83% |
| 已有 ET JSON 区域分析：入口 + 基础统计 + ET 来源契约 | 25,779 | 22,088 | −14.32% |

复杂、混合、故障任务继续读取必要参考并集；新增的 PPTX 诊断说明会增加入口长度，不以缩短提示为由省略正确性约束。这些是计划读取代理，不是实际宿主 trace、缓存效果或计费节省。

## 实际验证

平台为 macOS 27.2 arm64，Python 3.12.12，Expat 2.6.3；所有业务测试使用 `-I -S -B`。解释器路径已脱敏为 python3.12，逐项命令、退出码、耗时及明确标记省略长度的 stdout/stderr 尾部见 [验证 JSON](validation-451-20261110.json)，严格检查和 Pylint 缺失记录见 [静态检查 JSON](static-validation-451-20261110.json)。

| 范围 | 结果 |
| --- | --- |
| PPTX 源码套件 | 119 项通过，含新增 7 项数学诊断测试 |
| ET 源码套件 | 520 项通过 |
| XLSX 源码套件 | 408 项通过 |
| 通用打包安全测试 | 11 项通过 |
| 独立运行包验收 | 12 项通过；10 包独立安装、只读目录与源码指纹核对，PPTX 增加备注表格内数学内容回读 |
| 加载计划与当前选择数据校验 | 25 + 6 项通过；58 计划与 41 人工期望不等于 99 次真实模型任务 |
| 项目文档检查器回归 | 15 项通过 |
| 首步自检 | PPTX environment-ok/in_memory_export passed；ET environment-ok/ooxml-sheet available；XLSX ready，均退出 0 |
| 全 10 个 Skill 严格结构检查 | 通过；不能替代业务、Python 风格或宿主验证 |

上述 1,116 项测试均通过，无失败或跳过；新增 PPTX 7 项已包含在 119 项内，不重复累加。合成数学样本使用独立字面 OMML 分数/上下标、所选/未选 MCE 分支、备注与排除占位符；验证字段、旧文本、结构状态、预算、分页和 PNG partial 相互独立。独立包用字面 OMML 注入备注表格，回读 1215 并保留数学诊断；不是实际 PowerPoint/WPS 生成或渲染验收。

文档版本/覆盖/链接检查通过，当前加载快照为 fresh，`git diff --check` 通过。描述最后明确点名 xlsx 后，另复查 table-workflow/PPTX 严格检查、选择指纹、加载新鲜度、独立包和文档检查，记录见 [最终核对 JSON](final-checks-451-20261110.json)；重复运行不叠加测试数。没有修改 Mermaid 图，本轮未做 Mermaid 渲染或外链可用性验收。

**Pylint 未执行。** 本机选定解释器没有安装 pylint，实际调用返回退出 1 和 `No module named pylint`，未擅自安装。不能声称 Pylint Error 为零或全量 Python 合规。

尚未验证：目标 Linux、Windows、真实 DeepAgents 部署和模型触发、provider token usage、真实客户端公式显示/视觉效果、真实上传文件业务验收。本机 subprocess 捕获不证明目标执行器与模型分别收到完整输出。没有把字符量减少、结构检查或合成测试推广为这些保证。
