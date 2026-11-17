# ET 连续读取与分析链（2026-11-16）

本轮在未提交的默认有界读取改动之上补连续执行提示，与 [上一轮首屏性能测量](bounded-default-20261116.md) 分开；新增字段会占输出预算，不沿用旧字节数作为本轮测量。

## 变化与恢复

- 默认内容页和analyze_et预览/分析字节分页CLI新增continuation_entrypoint、next_arguments；原Python API与非分页契约不变。完整数组保留模式、选择、query/header-rows、页长、预算与cursor；sheet/query采用 `--sheet=VALUE`、`--query=VALUE` 单元素以兼容--开头的合法值，末页null，不含输入/source路径或Shell命令。
- 安全逐参数传给已定位的原脚本和同一输入；优先argv，宿主只收命令字符串时逐元素引用，不能直接空格join执行；et_cli加run前缀。漏开关或高预算可按原完整数组补齐续读，不必重读首屏；上下文已丢scope/数组则重取必要信息，不猜游标。
- 范围明确直接分析；不明才概况选表、小矩形预览、确定实际表头/数据/单位/缓存口径后分析。各步都读原文件并核对source_sha256一致，不把summary或preview投影作为分析输入。
- 每步独立看stdout/stderr/真实退出码与coverage；partial3有可靠结果继续保留，禁止&&或check=True机械丢弃。宿主截断先恢复完整页，三结果族游标不混用，preview/analysis模式也不交换。
- 修正分析参考误宣称max-json-bytes/output；该入口没有这些参数。非分页预览/分析为固定64MiB字节上限，不是字符预算；header-rows为原物理行号/范围而非数量。

## 验证边界

已执行固定公开Balance_Sheet.ett和Telephone_List.ett的真实CLI连续链：Balance概况717格，A8:C10分析B/C各2个数值零与1个blank；Telephone概况3976格，第2行表头及A3:G5三行显式blank保留，numeric_count=0、sum=null，不把缺数据称为零金额。DeepAgents环境不可用，真实DeepAgents多工具编排、自动安全传参及上下文恢复未验，不能以CLI测试冒充宿主验收。无持久化快照，每页/步骤仍完整解析源文件；不声明新增解析性能收益。

## 当前Dot执行器的模型可见串联

主维护代理通过当前exec工具独立执行三次公开Balance调用：summary → preview A8:C10（32768字节）→ 同范围analysis（32768字节）。模型收到三条完整JSON和真实退出3，工具未截断；三步source_sha256一致（报告摘要1124b7…7427b，非完整哈希校验清单）。预览确认Cash in bank、Accounts receivable、Inventory三行，B/C各为number0、blank、number0；分析B/C各numeric_count=2、sum=0、blank=1，保留source partial。

这只证明本次人工编排的CLI链及小结果模型可见，不是DeepAgents验收、任意模型自主选路/续读证明，也不认证模板实际业务资产为零。


## 回归与实际运行范围

- macOS 27.2.1 arm64 / Python 3.14.10 / Expat 2.7.4：ET全部776项通过（18.651秒），其中连续执行集成8项覆盖三模式完整分页、source变化拒绝、跨模式游标拒绝、partial退出3及预算恢复。
- 自动复用next_arguments实测保留preview/analysis的32768字节预算、query、原物理表头行和模式；每页均在预算内，末页next_arguments=null。以--开头、中文、空格、单双引号和$()的sheet/query名保持字面数据，不执行Shell。
- 独立只读安装21项完成：20通过、1项因TL-DOCX依赖跳过；这不是21项全部通过。文档检查与git diff --check通过；涉及生产Python无超过200字符行。
- Pylint当前解释器不可用（No module named pylint），未安装、未执行。源码strict仍受基线未知归属缓存目录及三份pyc警告影响，未删除；干净运行包及最终仓库快照由集成审查另记。
- 未启动Codex、未改宿主加载器、未提交推送、未重打历史ZIP。Linux/Windows、本轮实际WPS客户端视觉、真实DeepAgents及任意模型自主行为未验证。

## 作者停写后的独立集成复核

- 独立审查发现并复现：首调用使用合法的--开头表名时，旧next_arguments拆成两个token会使argparse把值误认成选项。作者改为--sheet=VALUE（query同理）后，两个入口以--Data中文直接重放续读均通过；此问题已关闭。
- 独立公开样本复现预算陷阱：Balance A1:D12预览24000字节首屏可返回partial3，若只复制cursor会回落12000并明确失败。新next_arguments保留原预算；不是通过缩小统计范围或隐藏诊断规避超限。
- 冻结后同机ET776项通过（18.260秒）；跨表191、ET/XLSX交接4、输出预算2、打包安全11、选择期望6、加载评测25通过；独立运行包21项中20通过、1跳过。合计1036项执行，1035通过、1跳过，无失败。独立包包含12个干净包严格检查及只读安装；唯一跳过为选定解释器缺python-docx 1.2.0的TL-DOCX读写，不计入通过。
- 连续链测试实际覆盖概况/预览/分析三步相同source_sha256、20行分页无重漏且每页总统计不变、三种cursor家族隔离、修改来源拒绝、字面sheet/query参数、partial3不能被&&等当作普通成功，以及独占测试目录文件集合和哈希不变。此文件不把这些CLI断言扩张成模型在上下文丢失或宿主截断时必然正确恢复。
- 保留全部第一轮未提交改动与历史快照。人工复核12项ET相关选择期望、保留79项case，只刷新ET入口指纹及静态加载快照；原当前文件逐字另存before_et_read_chain_20261116。最终加载快照fresh、仓库文档检查和git diff --check通过。
- ET源码strict仍为基线缓存目录及3份pyc共4条warning、无error，退出1，未删未知归属文件；不能用干净包通过替代源码strict通过。Pylint仍未安装，未执行，不声称E/F为零。
- 没有新增跨调用持久化、宿主自动安全执行或DeepAgents会话恢复能力；宿主截断、丢失参数时停止依赖/恢复必要信息的引导已审阅，真实DeepAgents对应场景未实测。未改宿主、未用Codex、未推送。
