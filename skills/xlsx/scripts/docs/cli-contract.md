# XLSX CLI 能力契约

路径占位符说明：`<skill-root>` 是 XLSX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 XLSX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 目录

- 入口与操作
- 公开工作簿快照
- 版本与兼容策略
- 报文编码
- 维护验证

## 入口与操作

先按主入口自检。标准任务和外部编排共用 `<skill-root>/scripts/xlsx_cli.py`，内部仍调用原有算法，未复制解析器或计算器。

```console
python -I -S -B "<skill-root>/scripts/xlsx_cli.py" capabilities
python -I -S -B "<skill-root>/scripts/xlsx_cli.py" run read ./输入.xlsx
python -I -S -B "<skill-root>/scripts/xlsx_cli.py" run edit ./模板.xlsx ./修改.json ./结果.xlsx
python -I -S -B "<skill-root>/scripts/xlsx_cli.py" call
python -I -S -B "<skill-root>/scripts/xlsx_cli.py" serve
```

capabilities输出普通JSON（接口版本、skill、operations、限制），成功退出0，不代替环境自检。run原样转交标准主CLI参数，保留完整JSON/原退出码/归档行为；旧stdlib_xlsx入口仍可用。call从stdin接收一个有换行的协议请求，输出一个响应，ok退出0、error退出1。serve保持一个进程的多轮会话；单次请求错误使用ok=false，会话可继续，EOF退出0、帧损坏退出1。serve进程退出0不代表其中所有操作成功，必须逐响应检查。调用方不能并发乱序使用同一会话。patch/calculate/publish抛错后对应句柄失效，不允许把可能已部分修改的对象继续发布；须检查已存在文件并从原始输入重新建立计划，不自动重跑。

| operation | parameters | result / 副作用 |
| --- | --- | --- |
| open | raw:bytes, source:str | 仅原生XLSX/XLSM快照；返回不透明handle及公开快照 |
| extract | raw:bytes | 仅完整原始ET类型化提取JSON，原16MiB/schema/覆盖门禁；拒绝分析结果与预览 |
| create | spec:dict | 复用创建规格；仅内存，不自动归档 |
| release | handle:int | 释放句柄；结束进程释放全部内存句柄 |
| region | handle,sheet,range,options? | 类型化区域tuple(columns,headers,rows,metadata)；options沿原区域能力，formula策略默认reject |
| target_headers | handle,sheet,bounds,header_rows | 返回重建表头schema；拒绝表头/键读取中的公式或错误 |
| resolve_column | selector,columns,schema | 明确列ID，歧义拒绝 |
| address / column_name | ref / number | 地址坐标tuple / 列名 |
| typed_value | cell | (kind,value)，保留文本/数值/空白/布尔/日期文字 |
| validate_query | query,columns,schema | 受检筛选/汇总配置tuple，保持原规则 |
| select_rows | rows,excluded:list,filters | 一次批量筛选，保留物理行号，不逐行启动或调用进程 |
| evaluate_many | formula,sheet,bindings:list | 最多10000组显式绑定；结果保持顺序，失败不发布 |
| matches | row,filters | 显式已验证过滤条件的bool结果 |
| index_rows | side:{rows,indices},nulls | 键索引、物理行键、空键/重复诊断 |
| summarize | table,query | 分组统计与质量信息；仅消费region及显式保留元信息的受控选择结果 |
| lookup | left,right,config | (补列表,report)；仅原有one_to_one/many_to_one、唯一维表、类型一致、事实守恒 |
| evaluate | formula,sheet,bindings:{地址:值} | 公式子集的Decimal/原结果；引用必须来自绑定，不执行宏、外链或代码 |
| patch | handle,spec | 内存修改并返回新公开快照，不写源文件 |
| calculate | handle | calculation及view；必须判断calculated_subset、issues和retained_not_refreshed |
| verify | original,candidate,writes:{(sheet,ref):expected} | 核验目标值、非目标单元格/样式/公式及未改ZIP成员；通过返回true |
| publish | handle,output | 独占新相对文件；保存后回读ZIP成员一致，返回output_written/verification/sha256 |
| delivery | output,protected_inputs:list | 对已验收最终文件复用原归档，返回delivery，不重算或删除调用方文件 |

## 公开工作簿快照

handle仅为当前进程的不透明整数，不能跨会话/重启复用；最多64个活跃句柄，open累计输入256MiB，最多200000次请求；超限明确失败。只读任务不创建清理清单，无持久快照缓存。进程异常后重新读取原输入，不恢复猜测句柄。

公开view字段：cells按工作表及地址映射，仅value/type/formula/cache_status；merges为按表的合并范围；native为是否原生工作簿；source_status/source_issues/source_coverage/input_schema为源诊断。快照是分离的数据契约，不包含ZIP部件、XML树、样式私有对象或可调用Python对象；修改快照不修改工作簿，必须使用patch。region的保存值、缓存警告、日期系统和类型信息不能混为格式呈现或最新公式结果。

publish不隐式声称业务验收或自动归档：workflow须先verify、发布后比对返回sha及输入指纹，再调用delivery；普通业务用run或既有runner自动收尾，不重复归档。发布或回读失败可能已有文件，调用方检查实际目标而非盲目重试；工作流回执保留output_written未知/失败状态。保护能力不是针对同权限恶意进程的OS沙箱。

## 版本与兼容策略

当前能力接口为 `interface_version=1`，协议为 `psl_capabilities_v1`。Skill 的时间戳版本记录本次维护，不等于接口版本。新增操作或可选响应字段保持 v1；删改参数、类型、退出码或状态含义须提供新接口版本及迁移期，不能直接替换 v1。调用方先查询 capabilities 并检查所需 operations；缺能力停止，不自动降级到私有 API 或猜测安装位置。旧 CLI/API 保留，其退出码不能统一套用为新接口的退出码。

同一协议的标准库编码器为 `<skill-root>/scripts/xlsx_cli_protocol.py`；三个独立 Skill 各含受控同内容副本，跨表测试逐字节检查一致性。它仅负责传输，不能替代业务 schema、授权或来源完整性校验；禁止 pickle、动态类恢复或执行对象。

## 报文编码

UTF-8 JSON；进程流每行一个完整报文，不将大数据打印到模型上下文后再抄录。每帧最多 128 MiB，超过则明确失败，不截断 JSON；JSON 嵌套最多64层，拒绝重复键、浮点字面量和非有限数。上层来源文件64 MiB、组合快照128 MiB，以及解析器既有预算仍有效；base64和JSON有额外内存成本，不声称减少底层内存。

无损值编码：普通字符串、整数、布尔、null、list保持 JSON 类型；Decimal编码为 `{"$decimal":"0.10000000000000000001"}`；bytes为严格base64的 `{"$bytes":"AP8="}`；tuple为 `{"$tuple":[...]}`；dict为 `{"$map":[[键,值],...]}`，嵌套值递归编码。所有dict都编码为$map，避免用户原值与标签冲突；解码后类型化复合键仍可作为dict键，文本编号不转数值。

下文为解码后的逻辑结构；线上使用上述编码。例如XLSX地址查询能力（不是ET或跨表业务命令）的实际单行请求为：

```json
{"$map":[["protocol","psl_capabilities_v1"],["id",1],["operation","address"],["parameters",{"$map":[["ref","A1"]]}]]}
```

成功响应逻辑结构为 `{"protocol":"psl_capabilities_v1","id":1,"ok":true,"result":...}`；失败为同协议及id、`ok=false`、`error={code,message,diagnostics?,calculation?,status?,location?}`。必须校验协议、关联id、ok及结果状态；未知/损坏/截断响应停止，不当作空数据或完整成功。stdout只含报文；诊断与进程stderr、真实退出码另行保留。超时、强杀、未启动或发布中断为未知/未执行等实际状态，不自动重跑有副作用操作。模型收到工具结果仍属于宿主集成验收，print或本地捕获不证明已收到。

## 维护验证

从已确认仓库根目录运行对应 `<skill-root>/scripts/test/` 的 unittest discover；实际命令中将占位符替换为真实根路径。测试不属于普通业务必读材料，不预读全部测试或本文件。独立复制Skill目录后验证只读安装、无相邻模块及空外部程序PATH；跨表仍显式依赖已确认的XLSX/ET能力入口，不承诺单独复制跨表目录即可获得依赖。真实样本、合成测试、平台和宿主验收分别报告。
