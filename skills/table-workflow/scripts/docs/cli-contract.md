# 跨表 CLI 与依赖契约

路径占位符说明：`<skill-root>` 是 table-workflow Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 table-workflow Skill 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 标准入口

`<skill-root>/scripts/table_workflow.py` 继续提供check/inspect/plan/apply，原stdin计划schema、输出schema及0/3/1/2退出码不变。依赖根仍须从实际SKILL读取确认，显式传入--xlsx-root，ET来源另传--et-root，不猜相邻安装路径。没有内置其他技能，独立安装指本技能代码自包含、依赖通过公开CLI可用，不代表无需安装XLSX/ET。

## 默认跨进程链路

环境自检 → capabilities检查protocol、interface_version、skill、所需operations → 完整原生快照或ET完整提取 → XLSX类型化region → 显式筛选/关联/汇总 → 本workflow业务勾稽 → XLSX候选patch/calculate/verify → publish及输入指纹复核 → 最终XLSX和审计JSON分别delivery。

默认Dependencies仅启动已确认根下的XLSX/ET CLI，不改sys.path导入依赖，不操作Workbook.parts/sheets/changed等私有状态。公开view是分离快照，不是内部对象；没有任意远程函数调用、Python代码执行或pickle。会话只保留内存句柄，避免逐单元格重复启动解释器或产生无用中间文件；仍存在JSON/base64和往返开销，不宣称减少底层内存或真实token。

## 状态及保护

inspect返回有界预览、不是计算输入；plan完整试算不写文件；apply只发布已有模板副本及审计JSON，保护原件、目标键/公式/样式和非目标内容。ET原值、公式缓存与partial覆盖门禁不变。仅明确接受且覆盖契约允许的partial继续，结果仍partial；未知遗漏和缺失缓存不能放行。

退出0为本命令完成，3为partial或交付收尾不完整，1失败，2参数usage。先读取完整JSON，检查output_written/audit_written/verification、归档及清理状态，不能用进程退出或传输ok替代业务验收。归档失败保留主文件，不重跑生成、不向用户展示内部归档路径。CLI中断或超时可能已写出，先检查，不自动重试publish/delivery。

客户端每请求120秒超时、关联id与协议校验、stderr最多保留8192字节并标记截断；失败不丢弃进程诊断。超时后终止会话并将副作用标为uncertain。不会重试或回退旧接口。

## 旧接口迁移

旧的直接Python调用可显式使用workflow_runtime.LegacyDependencies兼容适配器；原业务运算与库API保留，标准CLI不自动使用它。它只用于既有嵌入方迁移和兼容回归，不作为新业务标准路径；移除前须盘点调用方并另行安排破坏性版本。模板保护最终调用同一XLSX实现，避免两份校验悄然分叉。

跨 Skill 集成测试以宿主确认的仓库根目录为基准，位于仓库 tests/test_table_workflow*.py，包含原兼容套件与实际CLI传输套件；不随本 Skill 单独分发。涉及仓库真实来源的集成测试还需已确认仓库根的tests/xlsx_real_business_20261103样本与独立预期；独立分发时运行不要求这些测试材料，不将缺少仓库fixture说成运行失败。

## 版本与兼容策略

当前能力接口为 `interface_version=1`，协议为 `psl_capabilities_v1`。Skill 的时间戳版本记录本次维护，不等于接口版本。新增操作或可选响应字段保持 v1；删改参数、类型、退出码或状态含义须提供新接口版本及迁移期，不能直接替换 v1。调用方先查询 capabilities 并检查所需 operations；缺能力停止，不自动降级到私有 API 或猜测安装位置。旧 CLI/API 保留，其退出码不能统一套用为新接口的退出码。

同一协议的标准库编码器为 `<skill-root>/scripts/table_workflow_cli_protocol.py`；三个独立 Skill 各含受控同内容副本，跨表测试逐字节检查一致性。它仅负责传输，不能替代业务 schema、授权或来源完整性校验；禁止 pickle、动态类恢复或执行对象。

## 报文编码

UTF-8 JSON；进程流每行一个完整报文，不将大数据打印到模型上下文后再抄录。每帧最多 128 MiB，超过则明确失败，不截断 JSON；JSON 嵌套最多64层，拒绝重复键、浮点字面量和非有限数。上层来源文件64 MiB、组合快照128 MiB，以及解析器既有预算仍有效；base64和JSON有额外内存成本，不声称减少底层内存。

无损值编码：普通字符串、整数、布尔、null、list保持 JSON 类型；Decimal编码为 `{"$decimal":"0.10000000000000000001"}`；bytes为严格base64的 `{"$bytes":"AP8="}`；tuple为 `{"$tuple":[...]}`；dict为 `{"$map":[[键,值],...]}`，嵌套值递归编码。所有dict都编码为$map，避免用户原值与标签冲突；解码后类型化复合键仍可作为dict键，文本编号不转数值。

下文为解码后的逻辑结构；线上使用上述编码。例如XLSX地址查询能力（不是ET或跨表业务命令）的实际单行请求为：

```json
{"$map":[["protocol","psl_capabilities_v1"],["id",1],["operation","address"],["parameters",{"$map":[["ref","A1"]]}]]}
```

成功响应逻辑结构为 `{"protocol":"psl_capabilities_v1","id":1,"ok":true,"result":...}`；失败为同协议及id、`ok=false`、`error={code,message,diagnostics?,calculation?,status?,location?}`。必须校验协议、关联id、ok及结果状态；未知/损坏/截断响应停止，不当作空数据或完整成功。stdout只含报文；诊断与进程stderr、真实退出码另行保留。超时、强杀、未启动或发布中断为未知/未执行等实际状态，不自动重跑有副作用操作。模型收到工具结果仍属于宿主集成验收，print或本地捕获不证明已收到。

## 维护验证

从已确认仓库根目录运行 python -I -S -B -m unittest discover -s tests -p 'test_table_workflow*.py'；python 指宿主确认的兼容解释器。测试不属于普通业务必读材料，不预读全部测试或本文件。独立复制Skill目录后验证只读安装、无相邻模块及空外部程序PATH；跨表仍显式依赖已确认的XLSX/ET能力入口，不承诺单独复制跨表目录即可获得依赖。真实样本、合成测试、平台和宿主验收分别报告。

## Measured maintenance evidence

macOS arm64 / Python 3.12.11; synthetic 4000 rows, two groups independently expected to total 2000 each. Run repository tests/benchmark_table_workflow_cli.py from the confirmed repository root to repeat. Three runs per backend including dependency checks and parsing: legacy median 0.174 s; CLI median 0.573 s. Each CLI run exchanged 49 requests, 1,448,090 request bytes and 2,626,450 response bytes. These are serialization/isolation costs, not performance improvement or model-token measurements. Bulk filtering and derived evaluation avoid one request per source row.

The actual subprocess chain covers native XLSX, synthetic BIFF/OOXML ET, partial, formulas, template protection and independent read-only installations. Existing real bank XLSX tests use an independent OOXML/Fraction expectation; dimension classification and target templates are synthetic. This does not establish new real ET producer acceptance. Linux/Windows, model-visible host results and desktop visual validation remain untested. Existing ET caches are retained; raw-tree strict warnings are reported separately from clean-source-snapshot strict checks.

Archive configuration behavior is unchanged: absent or invalid router configuration yields skipped with fallback_reason, not copied; an actual copy conflict/failure is delivery_incomplete. The primary workbook remains available in both cases.

Final verification on Python 3.12.11/macOS: workflow 158, XLSX 373, ET 453, repository integration 48 tests passed. The workflow number includes deliberate legacy/new-path replays, not unique format scenarios. Exact legacy run stdout/stderr/exit compatibility and recursive test discovery were checked separately. Isolated temporary ZIPs had 159 XLSX, 146 ET and 17 workflow members: CRC and every member byte matched source; read-only extracted installations with empty external PATH completed native creation, cross-table fill, value/leading-zero checks and actual archival. Installed source hashes stayed unchanged; packaged strict passed. No release ZIP was replaced. Standalone native tools still work without workflow; workflow requires explicitly installed compatible XLSX/ET CLIs. Raw-tree strict returned 1 due to 70 pre-existing ET cache warnings and no errors; cache-free snapshots passed. Pylint was unavailable; AST and focused source layout checks are not full Pylint.
