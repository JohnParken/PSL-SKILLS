# bank-writing 跨期分析与邮件精修验收

## 范围与版本

基线：`c34e25a043f08a2bfd844a19c6914f69aa4e3ccf`；原版本 `20261114.2248.0406`，本轮 `20261115.1537.2261`。宿主采样 America/New_York 时间为 `2026-11-15T03:37:22.611721-05:00`，日历时间整体加12小时、百分之一秒截断。仅维护 bank-writing 及仓库导读、维护测试与受影响快照；无生产脚本、运行依赖或分发成员变更，不重打历史ZIP。

工作区原有未跟踪样本目录保持不动，未读取其内容、暂存或上传。全部示例及演练输入为合成素材。

## 修改与语义复核

- 通用经营分析改为本期与跨期口径：环比采用实际频率的前一可比期间，季度比上季度；补充缺期、跨年和累计相减范围。月报的“环比必须上月”保持原样。零/负基数、比率与年化、互斥可加贡献、拖累和不完整覆盖仍在通用页内，不反向依赖月报。
- 保留入口“专业判断优先于篇幅”主规则与原有“先判断是否需要新结论”段落，仅压缩文种路由前的重复提醒。原有六角括号标记、精确 `## 经营分析` 标题不作无效改动。
- description为132个Unicode字符，补齐经营分析、讲话发言、客户沟通/投诉及公关，未扩展审批、法律认证、发送或发布能力；人工复核原有银行写作正反例，新增6条选择期望。期望夹具不是自动触发准确率，也不承诺100%触发。
- 通用邮件页新增7类短结构，常见邮件可在该页完成；详细案例仍可选。只有确需服务解释、异议/投诉等专业内容才追加客户沟通，不因收件人是客户机械加载。保持整文件读取可用，不假设宿主支持章节锚点。
- 7类长例均改为合成素材与填写结构；逐项核对风险判断、准入/风控规则、审批、部署时点、财富配置及单据审查。不再在可套用正文中给出“风险整体可控”、证件尾号密码、收益锁定、未获证实的审批/部署或固定信用证法律结论；提示文字保留这些反例供识别。没有引入政策阈值或新的法律合规结论。
- README、docs索引、技能当前说明与路由已同步；修正导读中摘要新增判断统一指向经营分析的旧表述，明确按月报/非月报分流。现有Mermaid调用/交接关系无需变化，未修改图形也未运行图形解析器。历史验收记录保持原基线。

## 体积与加载证据

以下为Unicode字符、UTF-8字节与物理行（`bytes.splitlines()`），不是token或真实宿主用量。修改前从基线Git对象重新计数，未沿用其他口径的行数。

| 文件 | 修改前字符/字节/行 | 修改后字符/字节/行 |
|---|---:|---:|
| SKILL.md | 4166 / 9180 / 81 | 4166 / 9178 / 81 |
| business-analysis.md | 7934 / 20600 / 188 | 8010 / 20828 / 188 |
| email-communication.md | 1439 / 3773 / 49 | 2010 / 5108 / 61 |
| email-scenarios.md | 5154 / 11081 / 137 | 3419 / 8393 / 149 |

仅刷新当前加载快照的bank-writing条目，其现有计划只统计入口整文件读取，不覆盖全部邮件/分析路线；未把新增短表体积当作真实节省。选择快照仅更新bank-writing指纹并追加人工期望。文件清单未变，已审阅manifest保持不动。

## 独立合成请求演练

按skill-creator的独立演练方法，两个新代理仅得到真实入口与合成用户任务，不提供本轮诊断或预期答案，不读取仓库维护材料或测试期望，不写文件。实际读取集合为代理工具调用后的回报；没有DeepAgents宿主轨迹或token数据。

1. **季度复盘，三句话。** 二季度末A/B余额600/400万元，三季度末750/350万元，两期口径一致且互斥全量，无月度或原因记录。实际读取入口和business-analysis。输出总量1000→1100、季度环比10%，A/B增量150/−50、贡献150%/−50%；明确余额差不是期间流入，原因与质量未证实，建议补月度、日均、期后及交易记录。通过本次请求的数值与限定人工复核，未加载月报。
2. **普通客户会后短邮件。** 仅讨论融资和系统对接、同意继续核对需求，无审批、利率、测试排期。实际读取入口和email-communication。输出请求客户核对需求清单及再沟通时间，无额外审批/部署承诺；未加载email-scenarios或customer-communication。通过本次请求的内容与读取集合人工复核。

以上为显式指定技能后的两次代理执行，不是DeepAgents自动选择、所有文种质量或法律合规认证。财富与信用证示例仅做规则与双语字段人工审阅，未进行真实金融业务验收。

## 实际检查

实际平台：macOS 27.2.1 arm64，Python 3.12.12，Expat 2.6.3。默认 `/usr/bin/python3` 未满足规范最低版本，自检退出1；找到已有Python 3.12.12后重新自检，`environment-ok`与检查器help均退出0。没有安装运行时或第三方包。写入探针独占创建、读回并清理；测试临时资源沿用既有测试框架管理。

下列 `PY` 指上述已验证解释器，命令从仓库根执行。

| 检查 | 命令/范围 | 实际结果 |
|---|---|---|
| 新增文本回归 | `PY -I -S -B -m unittest discover -s tests -p 'test_bank_writing_reference_contract.py' -v` | 4项通过，退出0；静态契约不是模型行为证明 |
| 严格规范与内部引用 | `PY -I -S -B skills/skill-development-standards/scripts/check_skill.py skills/bank-writing --strict` | OK，退出0 |
| 计划加载快照 | `PY -I -S -B tests/benchmark_skill_loading.py --check-snapshot tests/runtime_loading_profiles.json` | fresh，issues为空，退出0 |
| 全仓根回归 | `PY -I -S -B -m unittest discover -s tests -p 'test_*.py'` | 359项，358通过、1跳过、0失败/错误，85.981秒，退出0；包含通用打包安全与独立包验收 |
| 最终相关回归 | `unittest.TestLoader.loadTestsFromNames` 加载下列5个目标 | 16项全部通过，0跳过，退出0 |
| 项目文档 | `PY -I -S -B tools/check_project_docs.py` | PASS，退出0；结构检查不替代语义及Mermaid验收 |
| 补丁空白检查 | `git diff --check` | 首次检测到示例行尾Markdown双空格，改为块引用空行后退出0 |

最终相关回归在块引用排版调整后从仓库根执行，使用 `-I -S -B`，将已确认的仓库 `tests/` 加入维护进程的模块路径，加载：`test_bank_writing_reference_contract`（4）、`test_skill_selection_current`（6）、`test_host_result_reading_contract`（3）、`test_user_file_discovery`（2）以及 `test_skill_runtime_packages.SkillRuntimePackages.test_bank_writing_complete_reference_closure`（1）。独立包由现有白名单临时构建、解包为只读目录，核对全部参考引用闭包；未覆盖或发布任何历史ZIP。

全仓唯一跳过为 `test_tl_docx_reads_creates_and_edits_from_isolated_install`：本轮解释器未安装python-docx 1.2.0；单独复跑该项也返回相同跳过原因，不把它计为通过。该依赖属于未改动的TL-DOCX，bank-writing无该依赖。Pylint在已验证Python 3.12.12及现有Python 3.14均未安装，`-m pylint --version`探测退出1；**Pylint未执行**，范围为新增仓库测试 `tests/test_bank_writing_reference_contract.py`，不声称Error为零，未擅自安装，也未加入Skill运行依赖。新增Python文件78行、最大行长99字符；运行测试通过不能替代Pylint。

未验证：目标云端Linux、Windows、真实DeepAgents自动选路与宿主缓存/计费、真实用户金融材料或业务审核。未提供可用Linux容器入口，其他平台检查不能替代目标环境验收。
