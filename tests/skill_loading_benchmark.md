# Skill 加载评测（仓库维护工具）

这些文件以仓库根为基准，不随任何单独 Skill 分发，不修改 LangChain Deep Agents 或其加载机制。`benchmark_skill_loading.py` 比较固定提交 `4b5a61f51d12d2a9d293898c2f862cbf7271dc8d` 的参考读取计划与工作区；不执行模型，也不声称计划已经被宿主遵守。

## 重现

使用已确认的 Python 3.10+，在仓库根执行：

```console
python -I -S -B tests/benchmark_skill_loading.py
python -I -S -B -m unittest discover -s tests -p 'test_skill_loading_benchmark.py' -v
```

第一条只向 stdout 输出 JSON。先等被测 Skill 冻结并人工核对读取计划，再保存当前快照；已有历史证据不能覆写。运行以下校验，fresh 退出0，stale或无法读取/重算退出1，stdout始终给出结构化JSON：

```console
python -I -S -B tests/benchmark_skill_loading.py --check-snapshot tests/runtime_loading_profiles.json
python -I -S -B tests/benchmark_skill_loading.py --check-snapshot tests/runtime_loading_profiles_history_20261107.json
python -I -S -B -m unittest discover -s tests -p 'test_skill_selection_current.py' -v
```

第二条故意校验历史证据，预期stale/退出1，不能为使其通过而刷新。历史副本逐字保留原快照，SHA256为 `aea64b2970ccd3c46043d8b0656d34080d302e2731c37c45253daf9b67e840cd`；原快照在本轮开始前已过期，不能把该过期归因于本轮新问题。原 `skill_selection_cases.json` 与 `skill_selection_results.json` 继续保留历史内容。

schema 3 绑定评测脚本SHA256、固定baseline提交、profile全集、场景说明与能力边界、两侧有序读取事件及每文件哈希/字符/字节/可选token与汇总。校验重新从Git读取baseline、从工作区读取after，不信任快照自身提供的路径或哈希。丢失/新增profile、路径/顺序/重复事件变动、缺文件、两侧文件变化、脚本变化、旧schema均拒绝。Python版本和平台保留为生成时信息，不作为文件新鲜度要求。只检查计划中列出的文件，不证明未读取的运行时代码或其他业务资源未变；业务回归独立执行。报告生成与检查期间不得并发改文件，这不是原子文件系统快照。

默认只需标准库，无需安装依赖。带token快照须以相同encoding、已装库版本和离线缓存参数复查；省略参数时不能认证旧token值，会标为stale，而不换算估计。

可选精确 token 计数只使用预先安装的 tiktoken 和已有缓存（维护依赖，不增加 Skill 运行依赖）：

```console
python -I -B tests/benchmark_skill_loading.py --encoding cl100k_base --tokenizer-cache /confirmed/existing/cache
```

此模式不使用 `-S`，以允许读取已经安装的维护依赖。必须将缓存参数替换为宿主确认位置；不会下载、安装或写入缓存，缺包/缺资产/哈希错误返回非零退出。不要把其他 encoding 的 token 计数当作当前 Deep Agents 模型的计费值。默认未测 token 时字段为 null，绝不按字符或字节换算估计值。

## 测量层次

1. 字符数、UTF-8 字节数、文件 SHA256 和读取事件数：可复现的静态代理。按文件完整读取计数，不推测章节加载；每个文件事件单独计数，重复读取不静默去重。
2. 可选 tokenizer：真实编码函数得到的文本 token 数，记录库版本及 encoding。逐文件求和不包括 chat 包装、系统提示、工具 schema、模型输出、provider 缓存计费或多轮完整上下文重放。
3. Deep Agents usage：本工具不测。必须单独使用真实宿主 trace 与 provider usage 才能判断实际路由、重复读取和节省。不能把工具输出大小或本工具结果标为真实 agent benchmark。
4. 正确性：本工具测试只验证评测实现和路由资源存在，不证明生成、编辑、统计或模型选路正确。CLI 回归、独立包验收及 Linux 测试需另行执行与记录。

历史 `wps-default-et`、`wps-selected-cells` 名称为兼容报告保留，before 和 after 均纠正为 `et/SKILL.md`；不会将旧快照中错误 WPS 路由造成的差异冒充优化。WPS 简单标题/正文/基础表格创建在旧入口已有完整示例，generation 链接不是必读，因此 before/after 都仅计入口；只有高级创建才在两侧加载 generation。XLSX 旧入口明确要求创建参考、共同写入契约和任务收尾，因此其简单创建 before 计这三份参考加入口，after 按新入口闭环计；复杂公式、故障、混合格式仍取所需参考并集。复杂公式场景的 partial 是验收要求，并非本脚本已经执行得出的结果。

重复场景分开：独立任务冷读取两次；同一任务中原文完整保留、文件未变时计划读取一次；压缩后指令缺失则重新读取。它们是不同假设，不代表宿主已有缓存，也不允许跨任务复用授权。

## 真实宿主验收清单

在相同模型、Deep Agents 版本、工具 schema、输入文件和业务预期下，分别使用 baseline 与修改版运行普通概况、静态创建、复杂公式 partial、混合格式、故障和重复任务。保存下列信息，保持业务数据脱敏：

- Skill 文件版本/哈希、实际 read_file 的路径/范围/完整或截断标志、重复事件及原因；不要用推测的计划代替 trace。
- 业务结果、退出码、partial/遗漏/清理状态以及独立预期；失败也保留 stdout/stderr。
- provider 原始 input/output/cache token usage 和统计范围，区分每轮合计与最终轮；不要混加相互包含的指标。
- 实际平台、Python/Expat 条件、单独 Skill 部署和 Linux 验证结果。

本轮快照若为 Darwin，仅证明在 macOS 执行静态测量；Linux、真实模型轨迹与 provider usage 未由该快照验证。


## 后续有界读取与跨表场景

新增 DOCX 完整表格、PPTX 完整/概况/选页/备注分页/partial、ET 概况/表头/直接统计/原始证据/partial续读、跨表名称发现/计划填报/partial来源、OFD新建/partial分页及PPTX+ET混合任务。仍按整文件读取计划计数，不因任务命名就声称实际执行通过。

PPTX的新视图与table-workflow后续表名发现并非baseline已有能力；报告的 `baseline_capability_note` 明确说明。before/after读文件数相同不等于功能相同，新增说明变长也不代表总体任务更慢。分页输出量、公开RPC请求数/耗时与回归正确性另行记录，不能混成token收益。DOCX表格/块续读和ET直接分析原已支持，本轮主要修正入口覆盖说明与分流。

OFD新建仍读取生成契约，保留字体使用权、资源限制、视觉未验证与归档交接；本轮仅扩展该场景评测，不据此声称修改或重新验收OFD生成器。更新快照前须等待所有被测入口修改完成。


## 当前直达路由与人工选择期望

XLSX纯概况after使用入口完整命令，不预读bounded-reading；baseline无该完整命令仍读该参考，混合读取按同一条件取并集。ET表头预览before读取table-analysis，after读取table-preview；基础列统计after读取basic-statistics，复杂query仍读取table-analysis。PPTX+ET基础统计混合任务取对应并集。baseline已支持ET预览和统计，但没有本轮新分页预算，不能称为完整功能对等对比。17项XLSX专项before经data-analysis再读专项，after读analysis-contract与专项；机构层级两侧保留management-wide依赖，结果导出两侧保留task-delivery。场景使用已有输入/配置，不包含额外公式重算、创建配置或可选导出；真正任务有这些条件时必须另取所需参考，不能机械套本计划。

`skill_selection_current_cases.json` 是人工编写的当前能力边界期望，不是模型选路观测。当前10个入口逐个绑定entry_sha256，包含各技能正反、软件品牌与真实格式、旧DOC/原生DOCX、银行短句、已有runner回执、简单单表与跨表、混合读取等31例。测试核对真实catalog、入口哈希、正反/混合覆盖、重复或矛盾期望；任一入口变动即失败提醒，须先人工复核含义再更新哈希，不能自动刷新以掩盖未复核。该测试不计算或宣称准确率、召回率或宿主token节省。

## 归档提示维护后的当前快照（2026-11-07）

归档提示与入口版本更新后，人工复核31个选择案例和57个读取计划，任务/格式边界及计划路径不变，仅刷新当前入口哈希与加载快照。更新前的当前快照逐字保存在 `runtime_loading_profiles_before_archive_notice_20261107.json`；它与原 `runtime_loading_profiles_history_20261107.json` 均为历史证据，预期检查为 stale，不为通过而改写。新快照仍只测计划读取字节/字符，不代表宿主 token、选路或云端上传验证。

## 环境与首步自检前移（2026-11-09）

人工复核31个选择案例及57个读取计划，入口调整未改变技能的任务、格式边界与按需参考；保留各技能自己的依赖和自检。更新前的当前选择与加载快照分别保存在 `skill_selection_current_cases_before_environment_alignment_20261109.json` 与 `runtime_loading_profiles_before_environment_alignment_20261109.json`。新快照由 macOS / Python 3.12.12 重算，只反映静态计划读取的字符、字节与哈希，不代表真实模型选路、宿主 usage 或业务验收。

本轮人工复核31项选择期望与57个计划读取范围：仅调整结果判读和大结果续读，业务 description 与选择边界不变。更新前保留本地快照；新快照仍是静态字符/字节，不代表真实宿主读取、模型选路或 token usage。
