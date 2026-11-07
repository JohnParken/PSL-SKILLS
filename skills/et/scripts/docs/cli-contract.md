# ET CLI 能力契约

路径占位符说明：`<skill-root>` 是 ET Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 ET 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 入口

标准读取与外部编排共用 `<skill-root>/scripts/et_cli.py`；实际解析仍由原read_et实现，旧read_et CLI/API保留。先按主入口完成环境自检；原生XLSX交给对应技能。

```console
python -I -S -B "<skill-root>/scripts/et_cli.py" capabilities
python -I -S -B "<skill-root>/scripts/et_cli.py" run --check
python -I -S -B "<skill-root>/scripts/et_cli.py" run ./明细.et
python -I -S -B "<skill-root>/scripts/et_cli.py" read
```

capabilities为普通JSON：skill=et、interface_version=1、operations=[read,metadata]；不代表OOXML环境或文件验收。run原样调用旧主入口并保留参数、JSON、分页、stderr和退出码。read仅供程序编排，从stdin读取一个完整协议请求，不是Shell路径参数或分页接口。

## read 输入输出

逻辑请求：protocol、id、operation=read、parameters={raw:bytes,source:str,compatibility?:data|strict|legacy_excel_clsid}。raw最多64MiB；source是来源标签，不会据此扫描或读取其他路径。默认data，安全、加密及资源检查仍由原解析器执行；不执行公式、宏或外链，不写入ET。

成功执行返回result={extraction:bytes,exit_code:int}，extraction是原生产者encode_result的完整UTF-8 JSON，维持原16MiB预算与schema。读取ok退出0，partial退出3，失败退出1；参数usage退出2。部分结果保留可靠单元格、原值与格式解释/缓存/coverage/issues的区别。partial不能按ok处理，消费方不能擅自制造覆盖断言或使用缺失缓存。

解析抛出的错误返回ok=false及error，保留原status/location和错误信息；退出1。不要只因报文传输成功就消费数据。跨表只接受完整原始提取schema；概况、分页及et_table_analysis_v1不能替代完整原结果。输入路径与快照所有权由调用方核验。

## metadata 输入输出

metadata 命令与 operation=metadata 接受和 read 相同的 raw/source/compatibility。仍完整执行 read_bytes 和 encode_result 的解析、安全、结构及16MiB结果预算校验，随后只投影受支持的 biff8_readonly_v1 或 ooxml_spreadsheet_readonly_v1；未知提取schema或未解析标记拒绝，不猜表名。

返回 result={metadata:{schema_version:workbook_metadata_v1,sheet_names,native:false,source_status,source_issues,source_coverage,input_schema},exit_code}。保留原表序、issues及可用的cell_value_coverage/omissions_affect_cell_values/reading_assessment，不传输单元格；ok/partial仍分别退出0/3，投影或校验失败退出1。名称元数据不能供统计或代替完整提取。旧read/run结果不变，消费方必须先确认metadata能力。该路径减少进程间数据，不承诺跳过底层解析或降低内存上限。

## 版本与兼容策略

当前能力接口为 `interface_version=1`，协议为 `psl_capabilities_v1`。Skill 的时间戳版本记录本次维护，不等于接口版本。新增操作或可选响应字段保持 v1；删改参数、类型、退出码或状态含义须提供新接口版本及迁移期，不能直接替换 v1。调用方先查询 capabilities 并检查所需 operations；缺能力停止，不自动降级到私有 API 或猜测安装位置。旧 CLI/API 保留，其退出码不能统一套用为新接口的退出码。

同一协议的标准库编码器为 `<skill-root>/scripts/et_cli_protocol.py`；三个独立 Skill 各含受控同内容副本，跨表测试逐字节检查一致性。它仅负责传输，不能替代业务 schema、授权或来源完整性校验；禁止 pickle、动态类恢复或执行对象。

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
