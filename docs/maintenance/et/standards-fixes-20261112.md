# ET 规范扫描修复（2026-11-12）

仓库维护记录，不随 Skill 运行包分发。当前 ET 版本 `20261112.0721.3457`；维护前为 `20261112.0626.4896`，HEAD 为 `57b24ad` 加已有工作区变更。版本采样为 America/New_York 2026-11-11 19:21:34.57，整体加12小时后格式化。保留原工作区，未提交、推送或覆盖历史 ZIP。

## 修改范围与契约

- read 协议继续交付完整原始 schema，不自动精简。先扣除完整响应封装开销，再按 Base64 四字节承载三字节及尾部补齐计算原 JSON 预算。失败不输出半条报文。
- 最终 read/metadata 响应编码纳入异常处理；错误诊断自身超限或无法编码时，返回小型结构化错误并明确 diagnostics_omitted。只有请求 ID 自身不能安全放入错误帧时使用 id=null，不能当作成功关联。
- run 的默认64 MiB、一次自动精简及文件输出行为未改变；协议编码器共享副本未修改。CLI 契约和架构说明分别写明两个入口的实际预算。
- wps_cfb.py 三处多语句同行拆开，未改流读取算法；类说明和相关参考区分“基础分配表不能建立而失败”与“流链异常停止问题链、保留已验证前缀及风险”。
- DXF 诊断删除旧 strict refuses，BIFF 流超限提示使用真实传入字节预算。
- 新增 scripts/test/test_et_protocol_budget.py 共10项回归，覆盖 Base64 膨胀、精确边界/多1字节、多字节文本及 partial、metadata和最终帧异常、大错误诊断降级、非法ID及提示一致性。
- 检查并同步 README 与 docs 的 ET 版本和契约；保留既有历史验收版本。人工复核6项涉及ET的选择期望，描述和能力边界不变；仅刷新 ET 入口指纹及12个涉及ET的静态读取计划，保留其他技能记录。该快照不是模型或宿主使用量实测。

## 实际检查

环境为 macOS 27.2.1 arm64、Python3.14.10、Expat2.7.4，使用 -I -S -B。以下命令从仓库根执行，python为上述解释器。

| 检查 | 结果 |
|---|---|
| tools/run_skill_tests.py skills/et/scripts/test | 594项通过，新增10项包括在内 |
| tools/run_skill_tests.py tests -p 'test_table_workflow*.py' | 177项通过 |
| tests/test_runtime_packager.py | 11项通过 |
| tests/test_skill_runtime_packages.py | 12项通过，临时独立包、只读安装及来源指纹检查 |
| tests/test_skill_selection_current.py | 6项通过 |
| tests/test_skill_loading_benchmark.py | 25项通过 |
| tests/test_host_result_reading_contract.py | 2项静态文案契约通过 |
| tests/test_read_view_copy.py | 2项通过 |
| benchmark_skill_loading.py --check-snapshot tests/runtime_loading_profiles.json | fresh，issues为空 |
| check_skill.py skills/et --strict | 通过 |
| tools/check_project_docs.py | 通过，不能代替语义/图表渲染 |
| et_cli.py run --check | environment-ok，ooxml-sheet=available |
| ET 113个Python文件AST/物理行数扫描 | 全部可解析；最长934行；无超过200字符行、无多语句同行 |
| git diff --check（本轮范围） | 通过 |
| Pylint 全量113个Python文件 | F=0、E=6、W=203、C=1577、R=230，退出30；详见下节 |

测试只使用临时目录并由测试清理，不产生新的发布包。未修改OFD文件。

## Pylint 全量扫描

在专用隔离虚拟环境使用 Python 3.12.12、Pylint 4.1.3、astroid 4.3.4，扫描运行脚本和测试共113个文件，参数为 `-B -m pylint --persistent=n --max-line-length=200 --output-format=json`；PYTHONPATH包含ET脚本及测试目录。完整命令、分类统计和诊断见 [Pylint原始报告](pylint-20261112.json)。先前解释器缺少Pylint的问题已通过独立环境解决。

- Fatal 0、Error 6、Warning 203、Convention 1577、Refactor 230；退出码30，不能宣称全量合规或Error清零。
- 6条Error均为 `E1101 no-member`，集中于 `wps_biff8_globals.py` 的 `GlobalSubstreamMixin`：413、447行的metadata_bytes，486、487行的dxf_property_count，511、536行的dxf_record_count。
- 已核实实际使用方 `Reader` 继承该Mixin，并在初始化时设置全部三个字段；纯内存构造Reader也确认字段存在。目前为Mixin隐式契约造成的静态推断诊断，未复现运行时AttributeError，但仍保留全部原始诊断，未屏蔽规则。
- 无导入失败诊断；`C0301 line-too-long`及`C0321 multiple-statements`均为0。大量导入顺序提示需结合隔离执行时的路径初始化判断，不能机械移动导入。
- 本轮直接改动的5个Python文件单独统计为E/F=0、W=9、C=42、R=6；不代表其余文件通过。
- 本轮不扩大修复新增Pylint问题；后续宜显式声明Mixin契约，再分批清理未使用导入、文档字符串及复杂度问题。

## 验证限制

- 小预算内存测试验证协议边界控制流，并未实测真实百兆文件或宿主大结果返回。
- 未重新下载/对照真实ET原件、未验证用户失败原件；本轮未改解析值算法。
- 未执行Linux/Windows、WPS/Excel视觉或真实DeepAgents选路/结果回传。
- Pylint已扫描但仍有上述6条Error及其他分类诊断；测试通过不能替代静态规范验收。

## 后续授权：Mixin Error 修复（2026-11-12）

当前版本 `20261112.0731.3094`，采样 America/New_York 2026-11-11 19:31:30.94，加12小时。上面的扫描结果保留为修复前证据。

将 metadata_bytes、dxf_property_count、dxf_record_count 的实例初始化统一归属 GlobalSubstreamMixin._init_global_counters，由 Reader 构造器显式调用；不使用共享类属性、不增加运行时依赖、不禁用 Pylint 规则。新增每实例初始化及互不影响的回归测试。字段初值和读取语义不变；计数器仍在任何解析调用前建立。

完整扫描113个Python文件：Pylint 4.1.3 / Python 3.12.12，F=0、E=0、W=203、C=1577、R=230，退出28，stderr为空。6条E1101已消除，其他分类数量未变；退出非零来自仍存在的W/C/R，不能称全规则合规。无导入失败，无超200字符行、多语句同行或超1000行诊断。完整证据见 [修复后Pylint报告](pylint-after-mixin-20261112.json)，保留原报告便于比较。

macOS / Python 3.14.10 隔离回归：ET 595项、跨表177项、独立包12项、技能选择6项、加载快照25项全部通过。严格技能检查、项目文档检查、快照fresh和diff-check通过；最长Python文件934行。ET自检environment-ok且ooxml-sheet=available。另系统默认python3自检显示Expat不足、OOXML不可用，故不使用它替代3.14的OOXML验收。

仅刷新ET版本及关联静态快照，能力description和选择边界不变；不等同模型选路实测。未重新生成发布包、未提交推送。Linux/Windows、真实用户失败原件及DeepAgents宿主仍未验证。
