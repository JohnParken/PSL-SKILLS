# 无脚本文档任务导航：2026-11-12 维护记录

本页及同目录证据以仓库根目录为维护基准，不随 Skill 运行包分发。基线为 `fb6cdff0161a137ebb0e64ebc64d09d11534d6d2` 加既有未提交变更及本轮新增；不提交、不推送，不修改其他 Skill 的代码、入口或版本。

## 设计依据与交付

用户希望在文档 Skill 尚未加载时，指导附件定位、任务目录确认和技能选择；当前只能修改 Skill，不能修改 DeepAgents 加载机制。正式入口 [document-navigation/SKILL.md](../../../skills/document-navigation/SKILL.md) 只有61行、3597个 Unicode 字符、9097字节；description 为135个 Unicode 字符。以上是文件计量，不是 token 或宿主使用量。

版本 `20261112.1736.5174` 来自宿主 Python 时钟：`2026-11-12T05:36:51.741888-05:00`（America/New_York），完整日历时间加12小时，截断到百分之一秒。自检使用实际找到的 Python3.13.12，退出0且得到 `environment-ok`；规范检查器 `--help` 退出0并显示用法。系统默认 Python3.9.6 不满足维护检查器要求，未用它执行依赖检查。

官方资料核对日期为2026-11-12，结论只针对查阅到的官方说明，不等同用户部署版本：

- [DeepAgents Skills](https://docs.langchain.com/oss/python/deepagents/skills#how-skills-work)：默认先发现元信息，再按需读正文和资源。导航自身同样按需加载；只修改正文不能保证先加载。
- [Pin skills](https://docs.langchain.com/oss/python/deepagents/skills#pin-skills)：应用需要传入 pinned_skills，官方说明要求 deepagents≥0.7.23；提及名称不自动等于 pin。本任务不修改应用配置，也不依赖该能力。
- [Backends](https://docs.langchain.com/oss/python/deepagents/backends)：文件工具经后端访问，状态文件系统、实际文件系统和执行后端不能当作同一个命名空间；宿主未给出附件或路径映射时，导航不能推导该事实。

包通过 [显式白名单](../../../packaging/runtime-manifests/document-navigation.json) 构建，只含 `document-navigation/SKILL.md`。生成位置为仓库 `dist/runtime/document-navigation-runtime-20261112.1736.5174.zip`（Git忽略），4615字节，SHA256 为 `8505afe9a3a359b4e88323ba504ad8b5fb455a4596a028159268bc037c790e81`；CRC、精确成员和源码逐字节一致通过。没有覆盖既有 ZIP，没有新增脚本、requirements、空资源目录或格式 Skill 依赖。

根 README、docs 索引/架构、打包说明及新增技能说明已同步；既有历史记录保留原基线。只扩展仓库独立安装测试和人工选择场景，不向导航包添加测试脚本。描述人工核对覆盖“上传/指代但缺上下文”与“条件齐备直接执行”的区分，没有将导航描述为解析器或自动加载服务。

## 自动检查与实际范围

平台为 **macOS 27.2.1 arm64**，Python **3.13.12**，Expat **2.6.3**，非 root。下列 `python` 指此次已确认的兼容解释器，从仓库根运行，保留 `-I -S -B`。命令结果由本会话执行工具返回并被读取；这不证明真实 DeepAgents 的 stdout/stderr 分通道回传。

机器可读的命令结果汇总、包指纹、旧场景保留核对和stale文件明细见 [验证结果](validation-results-20261112.json)。

| 检查 | 实际结果 | 证明范围 |
| --- | --- | --- |
| `skills/skill-development-standards/scripts/check_skill.py skills/document-navigation --strict` | 退出0，OK | 新技能元信息、结构与路径文字约束；不证明选路行为 |
| `tools/run_skill_tests.py tests -p test_runtime_packager.py` | 11通过，0失败，0跳过，退出0 | 通用打包安全与不覆盖边界 |
| `tools/run_skill_tests.py tests -p test_skill_runtime_packages.py` | 13通过，0失败，0跳过，退出0 | 11包独立只读安装、源码/ZIP/安装字节保护、空PATH、原有格式主要流程、导航单文件重复读取、严格结构检查 |
| `tools/run_skill_tests.py tests -p test_project_docs.py` | 15通过，0失败，0跳过，退出0 | 文档检查器回归；不代替实际仓库文档检查 |
| `tools/check_project_docs.py` | 退出0，PASS | 实际仓库的11技能版本、导读覆盖与本地链接检查；不代替语义或Mermaid解析 |
| `tools/run_skill_tests.py tests -p test_skill_selection_current.py` | 5通过、1失败、0跳过，退出1 | 失败仅为既有ET入口指纹过期；导航指纹及新增正反场景已加入，不能把整套报告为通过 |
| `tests/benchmark_skill_loading.py --check-snapshot tests/runtime_loading_profiles.json` | stale，退出1 | 原有ET相关读取计划的文件内容/哈希已变化；未为消除失败刷新其他任务的历史证据 |
| `git diff --check` | 退出0 | 当前工作树空白检查 |

独立安装测试实际运行 DOCX/XLSX/WPS 创建读取编辑、ET合成OOXML/BIFF8读取与partial、OFD合成读取、PPTX生成回读、生命周期与组合依赖缺失；没有重新跑所有 Skill 源码单元测试，没有把合成样本当真实上传文档。格式进程的审计钩子拒绝未声明的导航包访问，测试仍通过，支持本次未引入反向运行时依赖；不是OS级隔离证明，也不证明模型执行导航的正确率。

选路快照更新前已发现 ET 指纹不一致；[原45例快照](selection-before-20261112.json) 按原字节保存。保留原期望和所有非导航入口指纹，新增10例后共55例。原有10个 Skill 主入口相对本轮开始时哈希均未变化；故此次导航新增没有造成原有 ET stale。加载快照文件本轮未修改。

**Pylint 未执行**：Python3.13.12及现有Python3.14均无pylint；既有维护记录中的临时Pylint环境路径已不存在，未安装新依赖。涉及的Python仅为仓库 `tests/test_skill_runtime_packages.py`（新增导航包及10行测试）；不能宣称全量lint通过或Pylint Error为零。新代码已人工复核命名、行长、导入和作用域；相关实际测试已通过。Mermaid新连线已人工检查调用方向及无必读循环，当前没有发现可用解析器，未宣称语法解析或渲染验收通过。

## 人工情景走查

下表为维护者依据正文逐条做的控制流走查及独立预期核对，**没有实际调用用户的 DeepAgents 或取得模型选择轨迹**，不计入上表自动测试数，也不计算准确率。

| 输入条件 | 预期与正文核对结果 |
| --- | --- |
| 本轮唯一DOCX附件有顶层映射，技能目录有docx | 使用具体输入，完整读docx入口并自检；不追问已知名称。符合。 |
| 无任务目录，只有文件工具根`/` | 根不能当执行器任务目录；可读已定位技能，脚本依赖受阻。符合。 |
| 已声明POSIX执行器，`pwd`显示某目录 | 只核对当前目录，仍须任务授权及附件可见证据。符合。 |
| 无附件映射，顶层只有一个匹配候选且列表完整 | 可直接选中，不递归。符合。 |
| 两个同类候选，指代不足 | 先消歧再询问候选名称，不按mtime选。符合。 |
| 列表截断且没有续读能力 | 不能宣称唯一或没有文件，报告覆盖缺口。符合。 |
| 附件映射到子目录、绝对位置但无顶层对应 | 报告不满足顶层定位，不移动、不截basename。符合。 |
| 只有附件ID或掩码路径 | 不拼实际路径，不假装已访问。符合。 |
| 用户只想查看已有PPT，宿主有真实预览引用 | 交付引用，不推断重建；若脚本读取才检查对应能力。符合。 |
| 明确新建XLSX，无来源附件 | 不搜索输入，选择xlsx并执行其自检。符合。 |
| WPS保存的原生DOCX/XLSX | 选择docx/xlsx，不仅凭软件品牌选wps/et。符合。 |
| ET读取后制作DOCX | 只取et/docx必要并集，保留来源partial，不写ET。符合。 |
| PDF或旧PPT且无明确支持技能 | 报告所需能力缺失，不改后缀或自动转换。符合。 |
| 目标Skill入口分页或截断 | 按真实schema补齐正文后再依赖，不编造limit参数。符合。 |
| `read_file`可读Skill但执行器路径映射未知 | 可加载说明，停止脚本执行，不循环导航。符合。 |
| 已完整加载正确Skill且上下文未变 | 直接继续；不要求格式Skill反向加载导航。符合。 |
| 压缩后约束缺失或输入变化 | 用保留真实入口恢复所需指令，核对变化项。符合。 |
| 文档内容含伪角色指令或指向别处的路径 | 仅视为数据，不作为路径映射或执行授权。符合。 |
| 大结果引用只能由宿主read_file读取 | 依真实schema续读，缺失时停止依赖，不用Shell替代。符合。 |
| 已有runner完成归档清理 | 传递回执，不重复登记或收尾。符合。 |

## 尚未验证与部署验收

- 未取得真实DeepAgents版本、配置或调用trace。需核对导航实际进入目录、元信息可见及被选中后的完整读取；若现有线程缓存目录，按部署已有能力刷新/重开，不能靠本Skill修改缓存机制。
- 需在实际宿主验证上传显示名/资源ID/顶层路径对应、不同后端路径空间、执行器cwd、`read_file` schema及分页、脚本输出的stdout/stderr/退出码与模型收到的结果。说明文字不是这些机制已修复的证据。
- 需真实运行普通、混合、未知目录、双候选、列表截断、缺技能、重复和压缩恢复任务；检查格式Skill自检和业务验收是否继续、是否循环或不必要重复加载，并验证真实文件打开/下载能力。
- 本轮没有可用Docker/Podman或目标云端Linux执行入口，**Linux及Windows未验证**；macOS结果不能替代目标平台验收。无真实Office/WPS视觉、PDF能力、用户上传样本或云空间同步验收。
- 未测真实模型触发准确率、宿主token usage、节省量或优先加载成功率；不作保证。旧ET快照失败属于既有范围外问题，需要其维护任务先完成语义复核再刷新。
