# 跨表 CLI 与依赖契约

路径占位符说明：`<skill-root>` 是 table-workflow Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 table-workflow Skill 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 标准入口

`<skill-root>/scripts/table_workflow.py` 继续提供check/inspect/names/plan/apply，原stdin计划schema、输出schema及0/3/1/2退出码不变。依赖根仍须从实际SKILL读取确认，显式传入--xlsx-root，ET来源另传--et-root，不猜相邻安装路径。没有内置其他技能，独立安装指本技能代码自包含、依赖通过公开CLI可用，不代表无需安装XLSX/ET。

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

XLSX 能力接口为 `interface_version=2`，并声明 `delivery_filename_only=true`；类型化报文仍为 `psl_capabilities_v1`，编码和帧结构未变。v2 将 publish/delivery 的 output 收紧为任务根目录裸文件名，拒绝 `./` 或任何目录；旧调用需保持已确认任务 cwd 并改传裸文件名，不静默截取路径或回退旧宽路径 API。读取、计算、partial 与退出码保持原契约。Skill 时间戳与接口版本分开；调用方先核对版本、身份、标志与全部所需 operations。 本 workflow 明确要求 XLSX v2 及裸文件名标志；ET 为只读接口，仍要求 interface_version=1。依赖旧版本或缺标志时在业务执行前停止，须升级成套 XLSX 与 workflow；不为兼容重新开放输出目录。

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

Archive results use batch success/partial/failed/not_required and per-file copied/already_archived/failed. A delivered workbook and audit require success with nonempty, fully verified items. Missing or invalid current host questionId is failed with fallback_reason and delivery_incomplete, as are conflicts, partial results, or unverified receipts. The CLI returns 3 for a verified main result with incomplete delivery; it preserves both files and never reruns calculations to repair archival.

Final verification on Python 3.12.11/macOS: workflow 158, XLSX 373, ET 453, repository integration 48 tests passed. The workflow number includes deliberate legacy/new-path replays, not unique format scenarios. Exact legacy run stdout/stderr/exit compatibility and recursive test discovery were checked separately. Isolated temporary ZIPs had 159 XLSX, 146 ET and 17 workflow members: CRC and every member byte matched source; read-only extracted installations with empty external PATH completed native creation, cross-table fill, value/leading-zero checks and actual archival. Installed source hashes stayed unchanged; packaged strict passed. No release ZIP was replaced. Standalone native tools still work without workflow; workflow requires explicitly installed compatible XLSX/ET CLIs. Raw-tree strict returned 1 due to 70 pre-existing ET cache warnings and no errors; cache-free snapshots passed. Pylint was unavailable; AST and focused source layout checks are not full Pylint.

## 有界表名发现与批量舍入

新增 names 命令使用单文件请求 path，可选 limit（1..100，默认32）、offset（非负整数，默认0）和 sha256。非零 offset 必须提供前页 sha256；来源变化或偏移越界即拒绝。响应 table_workflow_names_v1 返回完整名称、total_names、next_offset、names_omitted 和来源状态；名称页不包含单元格，不代表完整读取。按实际返回数续读，next_offset=null 表示已列出全部可读表名，source partial 仍为 partial/退出3。名称页按23000字符预算提前停止，单个名称或文件身份超预算明确失败，不截断名称或返回无法推进的游标。旧 inspect schema 和预览默认值不变。

最终金额舍入按输出映射复用已有公开 evaluate_many（XLSX 必需能力）；空值不送入公式引擎，Decimal和错误语义不变，不改提供方默认行为。

inspect 的存储格边界复用 XLSX 公开 address_many，按最多10000地址分批；启动时必须协商该 operation，旧提供方缺失时明确停止，无私有导入或静默逐格回退。显式 LegacyDependencies 迁移适配器仍使用原地址核心。

本轮macOS/Python3.12.12合成测量：10000个存储格inspect（不含夹具创建与依赖初始化）0.160220秒，10次RPC，186482请求字节、1347852响应字节；地址解析为一个批次而非10000次请求。4000来源行/两个目标组计划基准三次CLI中位0.547590秒、46次请求，legacy中位0.145015秒；该小目标样本不代表大写入规模的普遍加速。字符/字节均不是token，未测宿主模型usage。当前未验证Linux/Windows或新的真实ET生产样本。

## 大表目标键与名称传输

names 对已核验SHA的不可变输入快照调用 XLSX open_metadata 或 ET metadata，保留 workbook_metadata_v1 来源状态与诊断；不先向workflow搬运完整cells。仅XLSX名称发现用完即release，单个Workflow对象按原快照复用元数据；后续load仍可显式取得完整数据。分页schema、SHA续读、预算和partial退出码保持不变。底层依旧完整解析和安全校验，未选表损坏也拒绝；名称响应小不代表完整格式支持或仅扫描workbook.xml。

目标行键先按原物理行顺序检查公式/错误，再通过公开 typed_values 每批最多10000格转换，按原顺序重建复合键。重复、空键、Decimal、前导零、排除行和唯一性规则不变；批次长度/类型标签不符即拒绝。原typed_value保持可用，默认workflow不逐格降级。XLSX必须声明open_metadata/release/typed_values，ET来源必须声明metadata；缺能力在输入处理前停止。完整计算、来源partial/缓存门禁、候选保护及最终发布复核未缩减。

本轮同输入before/after基准位于仓库 tests/benchmark_large_workbooks.py；指定两个已确认源码快照根目录，默认各运行3次，输入仅生成一次。维护验收证据记录实际平台、脚本哈希、耗时范围、RPC及传输字节，不换算模型token或宣称普遍性能保证。

整批交付状态：`final_deliverables` 声明工作簿及审计 JSON。两项准备和回读都成功才调用归档；准备失败返回 `delivery_batch_status=not_ready` 与 `delivery_incomplete=true`，保留已生成文件且不单独归档。归档阶段即使单项调用异常仍记录另一项结果，未全部核验成功或存在清理残留为 `incomplete`，全部完成为 `complete`。调用方不得仅用业务 status 或退出码代替完整交付核验；不为重试归档重跑 apply。
