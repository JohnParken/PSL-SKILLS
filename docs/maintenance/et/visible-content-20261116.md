# ET 默认有用内容与图表验收（2026-11-16）

## 基线与契约

基线 HEAD `afe3bf0`，Mac维护工作区保留其他并行修改；ET版本 `20261116.1514.5688`。未启动Codex、安装组件、修改私人文件、提交、推送或覆盖发布ZIP。仅维护ET及其运行清单/当前文档。

`read_et.py` / `et_cli.py run` 默认 `et_content_v1`，投影revision 2：

- 默认保留用户可用的已读文字、类型化保存值、数值/日期含义、公式文字/缓存风险、合并、批注/链接文字、工作表状态与覆盖。省去逐格样式索引/来源/偏移、重复assessment、原始二进制与内部部件关系清单。
- 诊断按代码汇总次数，最多保留两处表/格位置。不以输出瘦身改变partial、值覆盖或缺缓存含义；未知诊断代码也保留。完整诊断、内部结构、原schema消费使用显式full。
- 无公式且无错误/坐标不确定性的明确blank格以 `stored_blank_cells.count/ranges` 无损压成矩形区间。不把区间间的洞推成空白，不改空文本、缺缓存公式或合并。原物理保存格数仍在cell_count；cells为内容格，blank区间不是新推断的单元格。
- 新增OOXML工作表→绘图→图表引用链内容：标题、轴标题、系列名、分类/数值保存点，保留点索引、数值字符串及可用格式码和锚点格。隐藏图表对象、孤立图表部件与外链不读；隐藏工作表状态返回。仍包含隐藏表/行的保存单元格，不把输出当客户端屏幕渲染。
- 图表缓存缺失/稀疏/重复/无效数字明确提示，不填零，不重算、不从工作表反推缓存。重复内容字段取首项时明确报告省略。图表共享最多256图、100000点、100000个内容记录和2Mi文字预算，叠加既有ZIP/XML累计安全预算。DTD/实体与硬资源超限拒绝，普通图表损坏可保留单元格并partial。
- BIFF图表语义、独立图表表、chartEx、多级分类、图片内与一般绘图文字、布局/图表扩展未覆盖，不能称不存在或完整。full仍是原完整证据契约，未添加本轮content专用图表投影；Python API和公开read/metadata默认full不变。摘要/逐格分页不附图表，须普通内容读取。

## 实际验证

macOS / Python 3.14.10 / Expat 2.7.4，标准库及 `-I -S -B`。系统python3为3.9，未满足维护检查器3.10+要求，因此使用已安装python3.14；ET自身3.9最低运行约束未改变。

- 最终ET完整套件 **760通过，0失败、0跳过**；新内容专项13项。既有CLI诊断用例迁移到显式full或新的内容范围断言，未删除保存值/输入不变/预算断言。
- 新覆盖：标题/系列/精度字符串、稀疏点/缺缓存、隐藏对象/孤立部件/外链、DTD拒绝、点及内容记录预算、重复字段partial、图表损坏后保存值、full原接口、内部字段不回传、诊断去重、虚构1900闰日标记、文本坐标风险、blank区间洞/空文本/公式/错误/不确定位置及合并不变。
- 第一次756项回归的3个旧展示断言失败已修正为对应新契约后全量重跑；最终760不是忽略失败后的计数。一次新测试JSON序列化缺少Decimal处理导致2错，修正测试序列化后通过。
- 目标diff检查通过。源目录严格检查曾仅因3个来源未知的 `scripts/__pycache__` 缓存警告未过；未擅自删除不明归属文件。最终全仓/清单/独立安装检查由统一集成记录给出，不以单套ET通过冒充全仓通过。
- Pylint、Linux/Windows、DeepAgents真实宿主回传及Office/WPS客户端视觉未验收；不宣称真实token或峰值内存优化。

## 公开 ET/ETT 原件

重新从[固定来源清单](et-real-samples.json)的公开不可变URL下载到内存，不写入或更改样本。4份SHA-256全部匹配。使用[冻结保存值预期](real-value-expectations.json)核验198值/33公式缓存；另逐格比较full与content非blank类型、值、缓存状态，明确blank坐标与区间对应。此类公开模板原件不代表所有WPS生产者/版本兼容。

| 原件 | 已读保存格 | 内容格 | blank区间 | 冻结值/缓存 | full字节→content字节 |
|---|---:|---:|---:|---:|---:|
| newfile.et |0|0|0|0/0|39787→1734|
| Balance_Sheet.ett |717|122|25|122/21|250422→36221|
| Schedule.ett |343|66|6|66/12|180096→22748|
| Telephone_List.ett |3976|10|32|10/0|901041→2743|

全部仍为partial，不改原值覆盖结论。Schedule的1个BIFF图表明确返回未提取语义缺口，未假装已读图表内容。字节口径为同机 `json.dumps(..., ensure_ascii=False, default=str).encode('utf-8')`，不是token、一般压缩率或客户端视觉对照。

## 公开 XLSX 图表文件

使用XlsxWriter官方仓库固定提交 `af5927c110cf01571046a3138600f63b256ea89a` 的现成comparison工作簿原件，不在本轮生成、不改后缀、不冒充WPS原生ET或真实业务文件。全内存下载；二进制未分发。

| 文件/固定链接 | 字节 | SHA-256 | 图/系列/数值点 |
|---|---:|---|---|
| [chart_bar01.xlsx](https://raw.githubusercontent.com/jmcnamara/XlsxWriter/af5927c110cf01571046a3138600f63b256ea89a/xlsxwriter/test/comparison/xlsx_files/chart_bar01.xlsx) |9278|`4efc350a4ca69bcf07040c6f47cc2c96b86d4a6f64fdcdb195113f6f70802ba0`|1/2/10|
| [chart_column01.xlsx](https://raw.githubusercontent.com/jmcnamara/XlsxWriter/af5927c110cf01571046a3138600f63b256ea89a/xlsxwriter/test/comparison/xlsx_files/chart_column01.xlsx) |9263|`7ee42466f80c0e495982bbec4f1a82606f38695fa588f1754dad6bcf5f3a174b`|1/3/15|
| [chart_line01.xlsx](https://raw.githubusercontent.com/jmcnamara/XlsxWriter/af5927c110cf01571046a3138600f63b256ea89a/xlsxwriter/test/comparison/xlsx_files/chart_line01.xlsx) |9280|`5ddf28b26640a9c4978b85064724a186694d0e1f66088a8a890323db42738b97`|1/3/15|

独立用标准库直接读取源ZIP内chart XML的series/val/numRef/numCache/pt/v，与生产输出逐项比较40个保存字符串及图数量；锚点均E9。3份结果均partial。没有标题的原件不虚构标题，标题/轴名与异常场景由明确合成测试覆盖。此证据核验保存内容，不证明缓存最新、图表布局或客户端实际显示效果。

## 文档与范围检查

description人工核对并增加可提取图表场景，保留“部分格式、不重算、不修改、原生XLSX用xlsx”边界。同步入口、CLI帮助、输出契约、有界读取、图表参考、docs/skills及显式运行清单。README/docs索引、选路/静态加载快照和最终集成由主线程统一更新，历史验收基线不改。此次新增图表模块为ET自有实现，仅依赖随包运行时，不从相邻WPS Skill导入或复制业务代码。
