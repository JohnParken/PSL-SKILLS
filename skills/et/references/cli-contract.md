# ET CLI 能力契约

维护资源说明：文中 `docs/maintenance/et/` 与 `skills/et/scripts/test/` 均以宿主确认的仓库根目录为基准，仅供源码维护，不随运行包分发；运行任务不读取或执行。没有仓库时跳过维护资料，不按 Skill 安装目录拼接。

## 入口

标准读取与外部编排共用 `<skill-root>/scripts/et_cli.py`；实际解析仍由原read_et实现，旧read_et API保留；run/read_et.py CLI 默认改为有界内容页，完整内容显式使用 --all-content，原输出显式使用 --profile full。先按主入口完成环境自检；原生XLSX交给对应技能。

```python
execute(command="\"<skill-root>/scripts/.venv/bin/python\" -I -S -B \"<skill-root>/scripts/et_cli.py\" capabilities")
execute(command="\"<skill-root>/scripts/.venv/bin/python\" -I -S -B \"<skill-root>/scripts/et_cli.py\" run --check")
execute(command="\"<skill-root>/scripts/.venv/bin/python\" -I -S -B \"<skill-root>/scripts/et_cli.py\" run ./明细.et")
```

capabilities为普通JSON：skill=et、interface_version=1、operations=[read,metadata]；不代表OOXML环境或文件验收。run原样调用 read_et.py 主入口并保留其参数、JSON、分页、stderr和退出码；除原有查看参数外，完整读取支持 `--max-json-bytes`（1–256 MiB）、`--compact` 与 `--output <裸文件名>`。read仅供程序编排，从stdin读取一个完整协议请求，不是Shell路径参数或分页接口。

## 内容 profile 与迁移

`run`/`read_et.py` 默认 `--profile content`，返回 `et_content_page_v1` 有界页；显式 `--all-content` 或 `--output` 才返回/写入完整 `et_content_v1` 和 `source_schema_version`，不冒充原工作簿 schema。投影 revision 2 保留保存值/类型/缓存风险、数字解释、合并、批注/链接文字、OOXML工作表引用图表的标题/系列/保存点和源覆盖。诊断按代码/次数及最多两处表格位置汇总，full 保留原始诊断；内部样式/偏移/重复assessment省略。图表缓存不重算、不填缺点，不读取隐藏图表对象或孤立部件；隐藏工作表状态仍返回。BIFF图表语义、独立图表表、图片/一般绘图文字未提取，见 content_scope。完整证据和旧 CLI 消费者加 `--profile full`；程序 API 与下述 read/metadata 协议仍默认 full，不改变 v1 提供方契约。

BIFF content 在解析阶段不生成一般元数据 payload_hex，Font 仍供内部富文本校验；预算按原字节计算，安全扫描与诊断不变。它不是完全省去绘图/样式解码；OOXML/文本主要是输出投影，未声明解析峰值内存改进。内容投影不可交给原 schema 消费方。`--all-content --compact` 对 content 无额外改变，full 保留旧精简/预算降级；content 超预算直接失败。`--output` 清单附 `output_projection`，文件可含 content 或 full，按文件 schema 判读。

## CLI 连续执行提示

默认内容页及analyze_et的字节分页CLI在原schema上新增 `continuation_entrypoint` 和 `next_arguments` 数组；原Python API/非分页不变。数组完整保留本次模式、sheet/range/query/header-rows、页长及预算等适用参数，附原next_cursor，末页为null；字段在序列化预算内。数组不是Shell命令且不含input/source路径，必须安全逐参数传给已确认入口及同一输入；保留 `--sheet=VALUE` / `--query=VALUE` 单元素形式，不能拆开以--开头的合法值。优先argv；宿主只收命令字符串时逐元素引用，禁止空格join后执行，et_cli另保留run前缀。查询内容和源SHA不得变化，预算可提高但不要遗漏而回落默认。无持久化会话/快照；上下文缺失时重读必要信息，不猜scope或游标。三类游标不能混用，预览和分析模式也不能混用。可靠partial3应保留JSON和缺口，不用&&/check=True机械中止；未知退出/宿主截断须先恢复完整可信结果。

## read 输入输出

逻辑请求：protocol、id、operation=read、parameters={raw:bytes,source:str,compatibility?:数据兼容占位}。raw最多64MiB（传输上限；读取器输入预算仍为32MiB）；source是来源标签，不会据此扫描或读取其他路径。读取一律使用内容优先的容错策略；`compatibility` 仅为兼容旧调用而接受，任意已知值都不改变行为，并在结果中附 `compatibility_option_ignored`。安全、加密及资源检查仍由原解析器执行；不执行公式、宏或外链，不写入ET。

成功执行返回result={extraction:bytes,exit_code:int}，extraction是原生产者encode_result的完整UTF-8 JSON，受协议帧预算（128MiB）与schema约束，绝不输出截断JSON。读取ok退出0，partial退出3，失败退出1；参数usage退出2。部分结果保留可靠单元格、原值与格式解释/缓存/coverage/issues的区别。partial不能按ok处理，消费方不能擅自制造覆盖断言或使用缺失缓存。

解析抛出的错误返回ok=false及error，保留原status/location和错误信息；退出1。不要只因报文传输成功就消费数据。跨表只接受完整原始提取schema；概况、分页及et_table_analysis_v1不能替代完整原结果。输入路径与快照所有权由调用方核验。

## metadata 输入输出

metadata 命令与 operation=metadata 接受和 read 相同的 raw/source/compatibility。它执行完整的 read_bytes 解析与安全/结构校验，但**不再序列化完整提取结果**；随后只投影受支持的 biff5_readonly_v1、biff8_readonly_v1 或 ooxml_spreadsheet_readonly_v1。投影要求成功schema、已解析标记和 1–128 个合法唯一表名；未知提取schema或未解析标记拒绝，不猜表名。完整读取的 JSON 预算不影响 metadata 投影。

返回 result={metadata:{schema_version:workbook_metadata_v1,sheet_names,native:false,source_status,source_issues,source_coverage,input_schema},exit_code}。保留原表序、issues及可用的cell_value_coverage/omissions_affect_cell_values/reading_assessment，不传输单元格；ok/partial仍分别退出0/3，投影或校验失败退出1。名称元数据不能供统计或代替完整提取。旧 read/metadata 协议结果不变，消费方必须先确认metadata能力。该路径减少进程间数据，不承诺跳过底层解析或降低内存上限。

## 版本与兼容策略

当前能力接口为 `interface_version=1`，协议为 `psl_capabilities_v1`。Skill 的时间戳版本记录本次维护，不等于接口版本。新增操作或可选响应字段保持 v1；删改参数、类型、退出码或状态含义须提供新接口版本及迁移期，不能直接替换 v1。调用方先查询 capabilities 并检查所需 operations；缺能力停止，不自动降级到私有 API 或猜测安装位置。旧 CLI/API 保留，其退出码不能统一套用为新接口的退出码。

同一协议的标准库编码器为 `<skill-root>/scripts/et_cli_protocol.py`；三个独立 Skill 各含受控同内容副本，跨表测试逐字节检查一致性。它仅负责传输，不能替代业务 schema、授权或来源完整性校验；禁止 pickle、动态类恢复或执行对象。

## 完整读取的输出预算与文件输出

full profile 的 run 入口默认 JSON 上限 64 MiB；超预算时先自动精简一次（去重原始字节证据，保存值、类型、缓存、合并、诊断保留）并附 `output_auto_compacted`、转 partial，绝不输出截断 JSON。read 协议保持完整原始 schema，不自动精简；依据128 MiB帧预算扣除报文封装后，再按 Base64 的4:3及补齐开销计算原 JSON 预算（略低于96 MiB）。超过预算返回结构化 resource_limit 和退出1，不输出部分报文。metadata 也在完整报文预算内校验；若错误诊断本身不能编码或超限，返回明确标记 diagnostics_omitted 的小型错误，不伪装成成功。仅当请求ID本身无法安全放入错误帧时返回 id=null。

- `--max-json-bytes <字节>`：1048576..268435456，显式调整完整 JSON 预算。
- `--compact`：主动精简输出。
- `--output <裸文件名>`：只有 run（read_et.py）提供；独占创建任务根目录新文件，禁止目录、绝对路径、父目录和已存在文件，stdout 只返回 `et_output_manifest_v1`（status、partial、source_schema_version、cell_value_coverage、omissions_affect_cell_values、compacted、output.{file,bytes,sha256}、note），不返回完整 JSON。与有界视图参数不能同时使用。
- 超预算且未使用 `--output` 时返回明确的 `resource_limit` 失败，不静默截断。

默认有界内容页另见 `<skill-root>/references/bounded-reading.md#默认有界内容页`：100条记录，字符和UTF-8字节默认均32768。显式 `--content-page` 允许调节 sheet/page-size/cursor/预算；不带该开关的旧视图参数仍选 `wps_read_view_v1`（默认字符预算65536）。两种游标不混用。完整 JSON 的预算/compact 参数须配合 `--all-content`、`--profile full` 或 `--output`，不能用它们隐式取消默认分页。

## 报文编码

UTF-8 JSON；进程流每行一个完整报文，不将大数据打印到模型上下文后再抄录。每帧最多 128 MiB，超过则明确失败，不截断 JSON；JSON 嵌套最多64层，拒绝重复键、浮点字面量和非有限数。传输 raw 最多64 MiB，读取器实际输入预算 32 MiB；组合快照128 MiB，以及解析器既有预算仍有效；base64和JSON有额外内存成本，不声称减少底层内存。

无损值编码：普通字符串、整数、布尔、null、list保持 JSON 类型；Decimal编码为 `{"$decimal":"0.10000000000000000001"}`；bytes为严格base64的 `{"$bytes":"AP8="}`；tuple为 `{"$tuple":[...]}`；dict为 `{"$map":[[键,值],...]}`，嵌套值递归编码。所有dict都编码为$map，避免用户原值与标签冲突；解码后类型化复合键仍可作为dict键，文本编号不转数值。

下文为解码后的逻辑结构；线上使用上述编码。例如XLSX地址查询能力（不是ET或跨表业务命令）的实际单行请求为：

```json
{"$map":[["protocol","psl_capabilities_v1"],["id",1],["operation","address"],["parameters",{"$map":[["ref","A1"]]}]]}
```

成功响应逻辑结构为 `{"protocol":"psl_capabilities_v1","id":1,"ok":true,"result":...}`；失败为同协议及id、`ok=false`、`error={code,message,diagnostics?,calculation?,status?,location?}`。必须校验协议、关联id、ok及结果状态；未知/损坏/截断响应停止，不当作空数据或完整成功。stdout只含报文；诊断与进程stderr、真实退出码另行保留。超时、强杀、未启动或发布中断为未知/未执行等实际状态，不自动重跑有副作用操作。模型收到工具结果仍属于宿主集成验收，print或本地捕获不证明已收到。

## 维护验证

`run --summary` 的完整诊断超过视图预算时，ET 专属 `<skill-root>/scripts/et_read_view.py` 组合公共分页实现，返回显式 `diagnostic_summary`；不修改公共运行时副本、不影响逐格分页或 read/metadata 协议。原风险字段保留，诊断样本及省略计数不能代替原始诊断。精简结果新增 `output_projection`，文件清单 SHA-256 和字节数包含末尾换行，文件上限仍为256 MiB。

从已确认仓库根目录运行对应 `skills/et/scripts/test/` 的 unittest discover；测试目录以仓库根目录为基准，不存在于精简运行包。测试不属于普通业务必读材料，不预读全部测试或本文件。独立复制Skill目录后验证只读安装、无相邻模块及空外部程序PATH；跨表仍显式依赖已确认的XLSX/ET能力入口，不承诺单独复制跨表目录即可获得依赖。真实样本、合成测试、平台和宿主验收分别报告。

## 显式最终文件导出

`run ./input.et --output 读取结果.json --delivery` 将完整读取 JSON 声明为用户最终交付（Python 3.10+）；写后流式核对 manifest 字节数、SHA-256 和稳定文件身份，再保护原件调用本包归档。清单新增 final_deliverables、output_written、delivery，读取状态与退出码 0/3 保留；归档失败可能业务退出 0，必须检查 delivery.archive_status。--delivery 必须与 --output 同用，不能用于 --check 或有界视图。内部 --output 不自动归档。

归档失败保留已写 JSON，修复配置或目标问题后只调用 archive_delivery.py 显式清单重试，按当次宿主 questionId 归档；不要重跑读取覆盖文件。宿主另存分析 JSON 时也须明确最终清单、保留 partial/范围诊断并验收后归档。协议 read/metadata 仍只返回数据，不自动声明文件交付；旧消费者无需新增参数。

## 独立解析期分页接口

`<skill-root>/scripts/et_parse_page.py` 的 `--capabilities` 声明 `et_parse_page_v1` 与 revision 1；它不是 `psl_capabilities_v1` 的新增 operation，不改变原 read/metadata/run 协议。输入为任务相对文件，输出单条 UTF-8 JSON；无输出文件或缓存。完整扫描源诊断，存储顺序分页，行/列继承样式自动全量回退并通过 parse_mode 报告。完整输入/输出/游标/预算/遗漏规则见 `<skill-root>/references/bounded-reading.md#可选-ooxml-解析期分页独立接口`。独立 schema 不可直接交给原完整提取消费方。

默认内容中的无公式纯 blank 格以 `stored_blank_cells.count/ranges` 无损合并坐标区间，保留已读显式空白与未读/缺失的区别；非空保存值、空文本、缺缓存公式和合并范围不改。不能把区间外未返回坐标推成空白；如需逐格原结构使用 full。
