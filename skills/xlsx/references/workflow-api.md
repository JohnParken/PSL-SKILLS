# 旧内存调用兼容与CLI迁移

路径占位符说明：`<skill-root>` 是 XLSX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 XLSX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

本页仅保留旧Python嵌入方的兼容说明；新的编排使用 `<skill-root>/scripts/docs/cli-contract.md` 的稳定能力CLI，标准任务共用同一CLI的run命令。不要为新workflow继续导入下述模块或访问内部对象。普通读取、创建和编辑无需加载本页。先按主入口自检，在已确认任务目录处理相对数据路径。宿主必须显式传入实际读取本主入口所得根目录；不允许调用方猜相邻安装位置，也不复制本 Skill 解析器或计算器。

- `<skill-root>/scripts/stdlib_xlsx.py` 的 `Workbook.from_bytes(data, source)` 接受不可变原生 XLSX/XLSM 快照，保留原包检查和扩展名边界，不支持 ET 原件。
- `<skill-root>/scripts/xlsx_wps_result.py` 的 `load_result_bytes(raw, address)` 接受原始 ET 读取 JSON 的 UTF-8 bytes，执行与原 `load_result(path,address)` 相同的 16 MiB、深度64、重复键、数值、schema、单元格、合并及源覆盖检查。后者现在读取后复用同一验证器，原 CLI 行为不变。返回已验证的只读工作簿适配对象；不是 et_table_analysis_v1 或任何预览的通用转换器。
- `<skill-root>/scripts/xlsx_analysis.py` 的 `region_from_book(book, sheet, cell_range=None, header=True, formulas='reject', header_rows=None, header_exclude=(), include_spanning_headings=False)` 只消费上述已验证对象，返回与 region 文件入口相同的 `(columns, headers, rows, metadata)`。`calculate` 仅原生 Workbook，修改调用方拥有的内存对象，不保存输入；缓存、错误、资源和多级表头规则与 CLI 相同。
- 同模块 `summarize_region(table, query)` 复用原聚合/勾稽实现，table 必须源自 region_from_book（允许受检调用方按显式物理行号或现有 filters 选择 rows，并保留原 metadata 与选择诊断）。不是不可信 JSON 的解析入口；不接受模型手拼 tuple 来绕过类型验证。
- `<skill-root>/scripts/xlsx_match.py` 的 index_rows 用于已保真 region 的键诊断；公式或派生运算复用 `<skill-root>/scripts/formula_engine.py` 的 evaluate；单位换算可用 `<skill-root>/scripts/xlsx_advanced.py` 的 exact。调用者不能因选用这些库函数就忽略它们的明确口径、Decimal 精度与支持范围。

调用方负责授权、相对任务路径、快照指纹、来源完整性、业务口径和结果状态。ET 的 source_status/issues/coverage/reading_assessment 继续保留；若原生产者没有覆盖断言，不生成替代断言或推断为完全成功。多张表共享快照可省去重复解析和落盘 JSON，但不证明宿主 token 缓存。

文件交付通常使用 XLSX runner；拥有已验证 Workbook 的编排器可在内存 patch/calculate、拒绝 incomplete/failed 后 save 到独占新文件，回读验收成功后调用 `<skill-root>/scripts/xlsx_delivery.py` 的 finish_delivery(result,output,protected_inputs)。它只归档最终文件，保留 delivery 的原状态及机器话术；不负责调用方辅助文件的清理。没有辅助文件不重复登记；如有辅助文件，必须先接公共生命周期。归档失败不重算，任何 temporary_residuals 都不能声称清理成功。

接口可用性仍须实查；旧安装缺这些函数应报告依赖版本不满足，不把独立分发误解为内置 ET 或无需 XLSX。跨 Skill 集成测试位于仓库 `tests/test_table_workflow*.py`，以宿主确认的仓库根目录为基准，不随单个 Skill 分发，也不拼接到 `<skill-root>`。

维表补列可调用 `<skill-root>/scripts/xlsx_lookup.py` 的 `lookup_regions(left,right,config)`，输入为上述包含显式 header_schema 的受检区域 tuple，返回 `(enriched_table,report)`。config 必须恰含 left_keys/right_keys/columns/relationship/max_rows；columns 项为 column/as；关系仅 one_to_one 或 many_to_one，max_rows 为1..10000。拒绝空关联键、组件混合类型、左右类型不一致、重复维表键、未匹配事实行和超过200000单元格的补列结果。追加列ID为 lookup:别名，路径为单级别名；不覆盖原列。报告保留行对、未用维表行数量、原事实单元格和每个数值列总额守恒。调用方保留两个来源各自的完整性、公式及选择诊断，按职责提供业务分组与来源链；不能将补列结果宣称为任意多表连接或完整 ET 数据。该接口不解析模型 JSON，也不修改原始 tuple。
