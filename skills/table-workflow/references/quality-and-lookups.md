# 维表关联与业务质量规则

路径占位符说明：`<skill-root>` 是 table-workflow Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 table-workflow Skill 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

仅当任务需要维表补列、业务勾稽或特殊值政策时加载本页。先按 `<skill-root>/SKILL.md` 自检，基础计划见 `<skill-root>/references/plan-contract.md`。以下是加入基础计划的 JSON 片段，不是含真实附件 SHA 的完整可执行计划。运行脚本仍为 `<skill-root>/scripts/table_workflow.py`，所有数据留在已确认任务目录。

## 目录

- 维表补列
- 勾稽规则
- 空值、缺期和零分母
- 状态、溯源与验收

## 维表补列

账户事实表有账户、金额、客户列；客户维表有客户、机构列。先以客户查机构，再按机构汇总金额，避免误把维表同一金额重复累计。

```json
{"lookups":[{
  "id":"accounts_with_org","left":"accounts","right":"customers",
  "left_keys":["C"],"right_keys":["A"],"relationship":"many_to_one",
  "columns":[{"column":"B","as":"所属机构"}],
  "keys":["lookup:所属机构"],"repeated_keys":"aggregate",
  "max_rows":10000,"basis":"账户明细按客户唯一编码补所属机构"
}]}
```

- accounts/customers 都必须先按 sources 规范声明原始区域、指纹、单位、期间及公式政策；维表也必须经过同一受检读取及 partial 门禁。sources.keys 用于原区域质量检查；lookup.left_keys/right_keys 是实际关联键；lookup.keys 才是补列后的指标分组键，三者不可混淆。
- 最多16个lookup，按声明顺序执行；id不得与源或既有lookup重复。left可为原始source或更早的lookup；right仅可为原始维表source。自循环/前向引用拒绝，不从维表列推导执行代码。
- 只支持 one_to_one、many_to_one。右键恒唯一，one_to_one左键也唯一；每侧1..8个键，位置对应，每个组件须类型单一且左右相同。空键、数字/文本混合或类型不一致均拒绝；不自动去空格、丢前导零或转数值。
- 未匹配事实行一律拒绝，不丢行、不补零；未使用的维表行保留数量诊断。输出每条事实恰好一次。max_rows显式为1..10000，同时最多200000个补列后单元格，不截断。
- columns最多32项；as为1..100字符的明确别名，不与已有列ID或完整单级表头路径冲突。追加列ID为 lookup:别名，可用该ID或唯一的单级path选择。只添加列，不改原列；关联报告保留左右物理行号，链式关联保留每条边。
- 指标source可以是lookup.id，measure仍必须来自原事实列。追加维表列只作为分组属性，不能作为金额聚合；count统计事实行。单位和期间继承左侧事实源，维表的时效性由明确来源/筛选落实，不自动选择历史生效版本。
- 关联自动核对事实行数、所有原始类型/值及每个原列数值项数与精确和。此守恒仅证明没有放大或丢失事实，不证明归属映射业务正确。金额汇总的非数值、空值、重复键等原门禁继续执行。

## 勾稽规则

先定义指标（必要时用derived表达“期初＋发生额”），再声明比较。必须给出业务basis，不能只因为公式可算就声称口径正确。

```json
{"checks":[
 {"id":"balance","kind":"equal","left":"closing_calculated","right":"closing_ledger",
  "scope":"each_target","absolute_tolerance":"0.01","relative_tolerance":"0",
  "basis":"同机构、同期间期初加发生额等于期末","on_failure":"reject","missing":"reject"},
 {"id":"rate_bounds","kind":"range","left":"share_percent","minimum":"0","maximum":"100",
  "scope":"each_target","basis":"占比应在0至100百分数之间"}
]}
```

- 最多64个唯一id，合计最多20000个检查观察值。kind仅equal/range；引用已经定义的指标。所有检查在最终单元格舍入之前执行，不能冒充显示金额或公式缓存的勾稽。
- scope=each_target逐个唯一目标键；target_total只合计这些目标键；source_total合计全部已选择来源分组，含未进入目标的额外来源组。source_total仅允许聚合指标，禁止仅覆盖目标的derived。未匹配目标补出的空值不属于来源分组。
- equal要求两指标单位和期间声明完全一致。差值为abs(left-right)，允许差为max(absolute_tolerance,relative_tolerance*abs(right))，小于等于即通过；相对容差以右指标为基准，两种容差均默认0。绝对容差非负，相对容差在0..1；基准为0时相对项为0。
- range为含边界的minimum..maximum，允许显式绝对容差向两端扩展，相对容差必须0。阈值单位/期间沿用左指标。总计均值、比例等非可加指标是否有意义由业务口径决定，脚本不自动批准。
- on_failure默认reject，在创建最终文件前阻断；明确partial才保留失败项并继续。任一操作数为空，或总计中含空值、没有观察值，则unassessed；missing默认reject，可明确partial继续，绝不因双方同为空就判通过，也不跳过空值做“完整总计”。
- 完整定义、范围、差值/边界、容差及逐项状态进入审计。回执仅返回计数与少量检查摘要，failed_count和unassessed_count分开；业务核验通过不消除其他来源partial或缓存告警。

## 空值、缺期和零分母

维表未匹配/空关联键仍严格拒绝，以下策略不覆盖结构、格式或关联失败。

- 聚合missing=reject默认拒绝空金额；omit忽略空值并为partial，整组无数值仍拒绝；blank把含空值的整个组设为空并为partial，不计算部分和。非数字、布尔或金额文本始终拒绝。
- 聚合missing_target=reject默认拒绝缺失目标分组；blank保留该目标行并将指标留空，明确标注partial。整个筛选为空时，仅显式blank允许输出空指标，不能当作业务零。此策略也可表达某实体在已明确期间的来源缺失。
- 通用derived可加missing=blank，遇声明绑定的上游空指标即传播空值；默认reject。数值输入产生除零、不支持函数等错误仍拒绝，不兜底为零或空值。
- 要对零分母留空，使用明确的ratio操作：numerator/denominator指早前指标，scale默认1（百分数可100）。zero_denominator和missing分别默认reject，可分别设blank；负基数保持有符号分母，不自动改成绝对值。

```json
{"derived":[{"id":"growth","operation":"ratio","numerator":"change","denominator":"prior",
 "scale":"100","unit":"%","period":"2026-10/09","basis":"增量除以上期乘100",
 "zero_denominator":"blank","missing":"reject"}]}
```

- 来源可加period_check，仅消费显式YYYY-MM文本，不推断Excel日期序列、展示格式或月份别名。column不能同时属于该source.keys；按source.keys组成的每个已观察实体核对expected月份集合。
- expected为1..120个不同有效月份；筛选后的每行须属于expected，空/数值/意外月份始终拒绝。缺期及空筛选默认missing=reject，可明确partial继续，保留缺失实体与月份。完全未出现的目标实体由missing_target处理；不会凭相邻行替代上月/上年。

```json
{"period_check":{"column":"D","expected":["2025-12","2026-01"],"missing":"reject"}}
```

## 状态、溯源与验收

需要将任何空指标写入目标时，本阶段只接受没有任何公式的模板；有公式就阻断，避免依赖空值的公式按零重算。没有空写入时，原有全簿公式预检、非目标保护和缓存风险门禁不变。

所有接受的缺期、空值、零分母及失败勾稽均使结果partial/退出3。来源错误、未知遗漏、缺缓存、关联基数不满足等仍失败/退出1；不会用降级策略掩盖这些错误。退出0只表示该次执行完成，业务语义仍需按输入及用户规则核对。

审计source_regions保留每个来源和衍生表元数据；lookups保留定义、行对和事实守恒；metrics保留按事实物理行的分组成员；checks保留完整观察值。沿lookup的left/right ID及物理行，可追溯至原路径、SHA、工作表和范围。绑定原件指纹后再执行，不把摘要作为完整数据，不缓存跨任务授权或旧计算值。

新增行为是table_workflow_plan_v1的可选字段扩展；未使用分支的旧计划保持原严格行为。旧安装遇到新字段或缺xlsx_lookup接口应拒绝，更新已确认依赖后重新自检；不忽略新政策继续执行。正常任务不加载维护历史。
