# ET 兼容读取补强与 xlrd 对照（2026-11-11）

仓库维护记录，不是 Skill 运行时依赖。当前 ET 版本 `20261112.0626.4896`；采样时钟为 America/New_York `2026-11-11T18:26:48.962839-05:00`，整体加12小时后按百分之一秒截断。保留进入任务前的未提交修改，未提交、推送或覆盖历史发布包。

## 本轮变更

- 图表恢复：未平衡 Begin/End 不再吞掉明确的工作表恢复记录，补充 RK/MulRK/MulBlank；不完整图表正文的 EOF 不再误终止工作表。合法 SERIESDATA 中 SIIndex 后的 Number/BoolErr/Blank/Label 仍作为图表缓存保留，不作为工作表值。图表边界异常和缺工作表 EOF 标记值覆盖不确定。
- OOXML：遍历重复 sheetData 和行中的不同坐标，首个可读值及首条行元数据保留；重复冲突、缺失数据段及数据段/行内无法解释的元素明确标记覆盖风险，预算不重置。
- 普通统计与预览：重叠合并保留物理保存值，省略歧义锚点映射并给出 partial；合并填充和受影响的表头重建仍拒绝歧义。使用有界扫线检查，支持 OOXML 高坐标及裁切成单格的交集。
- 筛选与诊断：AutoFilterInfo 先校验再更新状态，非法定义不污染后续有效定义；明确 `may_affect_cell_values=false` 的展示元数据恢复不再统一宣称值丢失。未知和值承载记录仍保守标记。
- 输出：ET 专属 et_read_view.py 组合共享投影，仅在概况完整诊断超预算时给出显式 diagnostic_summary，保留源风险、条目数、代码样本和遗漏说明；逐格分页保留完整诊断。精简输出增加 output_projection；自动精简后的 reading_assessment 状态同步。文件 bytes/SHA-256 包含末尾换行，文件预算也包含换行，仍最多256 MiB。
- 说明：修正 SST 索引越界一律失败、旧图表拒绝规则、文件输出可越过256 MiB等过时说法；同步 README、docs/README、ET 架构图、路由及运行契约。

未引入 xlrd 运行依赖。description 仍为118个 Unicode 字符；人工核对“读 ET/ETT 保存值、区域统计”适用，“任意编辑、重算、原生 XLSX”不适用，能力范围不需重写描述。未执行真实宿主选路测试。

## 对照依据与真实样本

[xlrd 官方说明](https://xlrd.readthedocs.io/en/latest/)聚焦历史 XLS 保存值和公式缓存；[xlrd 2.0.2 sheet.py](https://github.com/python-excel/xlrd/blob/2.0.2/xlrd/sheet.py)对嵌入 BOF 子流扫描至 EOF，并不提供所有损坏文件的恢复保证。[MS-XLS Chart Sheet Substream](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/732ff614-d939-416b-b7c7-6d983471ff11)明确 SERIESDATA 包含单元格形状的图表缓存记录，因此不能把所有 Number 等记录都认领为工作表数据。

本轮从既有 [原件清单](et-real-samples.json) 的固定 URL 下载4份公开文件；先验证长度及 SHA-256，再调用既有 verify_et_samples.py，与 [冻结预期](real-value-expectations.json) 对照。4份通过，共198个非空保存值，含33个未验证时效性的公式缓存；工作表、单元格、合并、公式和图表指标及图表哈希保持预期。原件读取前后哈希不变。

另从官方固定标签下载 xlrd 2.0.2 源码，仅放入本轮临时维护目录，没有安装或分发。源码 ZIP SHA-256 为 `476da00efde77eb80226bb7a3d8c6dcf26424a0d921676fcd2f0267049aa8154`。在独立 Python 进程中，xlrd.open_workbook(file_contents=原件, formatting_info=True) 与 ET read_bytes(原件) 比较逐表非空地址集合及类型/保存值：number/date 比较数值，布尔比较布尔，错误比较错误文本，文本逐字比较；不比较视觉样式、空白格枚举或日期显示语义。

| 原件 | ET存储格数（含空白） | 与xlrd逐格比较的非空值 | 差异 |
|---|---:|---:|---:|
| newfile.et | 0 | 0 | 0（仅空表结构） |
| Balance_Sheet.ett | 717 | 122 | 0 |
| Schedule.ett | 343 | 66 | 0 |
| Telephone_List.ett | 3976 | 10 | 0 |

机器记录见 [xlrd 对照结果](compatibility-recovery-20261111.json)。所有原件输出保持 partial；198项一致仅证明这些固定样本的保存值，不证明工业级全面等价、全部 ET 私有格式或原生 WPS OOXML 兼容率。

## 验证结果

真实平台：macOS 27.2.1 arm64，Python 3.14.10，Expat 2.7.4，非 root。运行时测试保留 `-I -S -B`；无运行依赖安装。下表命令的 `python` 指该已确认解释器，均从仓库根执行，真实样本脚本则从独占样本任务目录执行。

| 检查 | 结果 |
|---|---|
| et_cli.py run --check | 退出0，environment-ok、ooxml-sheet=available、artifact_validation=not_run |
| `python -I -S -B -m unittest discover -s skills/et/scripts/test -p 'test_*.py'` | 584通过；进入任务前570通过，本轮新增14项针对性回归 |
| `python -I -S -B tests/test_runtime_packager.py` | 11通过 |
| `python -I -S -B tests/test_skill_runtime_packages.py` | 12通过；独立构建/只读安装、无相邻隐式依赖、空PATH及原文件指纹检查 |
| `python -I -S -B -m unittest discover -s tests -p 'test_table_workflow*.py'` | 177通过 |
| `python -I -S -B -m unittest discover -s skills/xlsx/scripts/test -p '*wps*'` | 13通过 |
| `python -I -S -B tests/test_read_view_copy.py` | 2通过，校验声明差异之外字节一致及各自哈希 |
| `python -I -S -B tests/test_read_view_budget.py` | ET/WPS两个隔离进程各7项通过，外层2项通过 |
| ET源码严格检查 | 通过 |
| `python -I -S -B tools/check_project_docs.py`、`git diff --check` | 通过；架构图调用关系人工核对，未执行Mermaid解析/渲染 |
| 修改Python的AST与行数检查 | 通过；本轮涉及文件均未超过1000行、单行未超过200字符，不代替Pylint |

初次跨表回归的2项失败来自同一继承测试：它只移除了 reading_assessment，保留了已有顶层完整覆盖声明。使用本轮改造前 ET 快照独立复现失败；本轮修改测试使其同时移除所有覆盖声明，恢复“覆盖未知应拒绝”的原意，没有修改 table-workflow 生产逻辑。

初次共享组件校验发现进入任务前 ET 上限已为64 MiB，而 WPS 为16 MiB，来源记录尚称完全相同。保留已有行为，仅修正 ET 错误提示及来源记录，明确这两处差异并在一致性测试中精确核验；ET 摘要增强放在独立组合模块，不复制新公共实现。另修正共享预算测试的夹具跨模块导入污染：游标 CLI 验证使用已生成的合成 OOXML，不再在 WPS 进程导入 ET BIFF 运行模块；该改后缀夹具不算真实 ET 验收。

Pylint **未执行**：针对18个涉及Python文件尝试 `python -B -m pylint --max-line-length=200 ...`，退出1，stdout为空，stderr为 `No module named pylint`；未安装依赖，不声称Pylint Error为零。

## 尚未验证与边界

- 未执行云端 Linux 容器、Windows、WPS/Excel客户端视觉或用户实际失败文件验收。当前没有可用 Docker/Podman 入口；macOS结果不能替代目标环境。
- 真实 WPS 原生 OOXML `.et` 的语义对照未执行；新增损坏恢复场景以合成 BIFF/OOXML 回归验证。
- 无法区分缺图表 EOF 后的缓存与工作表值时，仍可能保留为不透明证据，必须提示覆盖不确定；不做任意字节扫描或猜值。
- 宿主 `/large_tool_results/` 完整续读、三通道独立回传、模型真实 token usage 与触发效果未验收。本地子进程测试分别捕获 stdout/stderr/退出码；当前工具呈现合并 output 和退出状态，不证明其他宿主集成。
- 无新发布 ZIP、提交或推送。维护用临时源码、原件与安装包不作为业务交付物，不触发云归档。
