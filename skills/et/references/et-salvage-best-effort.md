# 默认容错读取与历史 salvage 边界

维护资源说明：文中 `docs/maintenance/et/` 与 `skills/et/scripts/test/` 均以宿主确认的仓库根目录为基准，仅供源码维护，不随运行包分发；运行任务不读取或执行。没有仓库时跳过维护资料，不按 Skill 安装目录拼接。

路径占位符说明：`<skill-root>` 是 ET Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 ET 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 默认策略：容错读取，显式 salvage 已废弃

统一读取入口（文件 API、`read_et.py`、`et_cli.py run`、BIFF8/OOXML 分支）**默认就是内容优先的容错策略**，无需再开启独立 salvage。旧参数 `--salvage-unknown-metadata` 仍被接受但不改变行为，结果附加 `salvage_option_ignored` 并保持 partial；旧 `--compatibility`、`--chart-policy` 同理，附加 `compatibility_option_ignored`。不要在文档或任务中把显式 salvage 当作更宽松的额外开关。

默认容错的范围：

- 有界未知或未实现的 Global/Worksheet 记录保留原始负载、位置和 Continue 关联，结果标记 `partial` 与覆盖不确定，不中止单元格读取。
- 未知 CodePage 声明、缺少必需 `Date1904` 或 Worksheet `WsBool` 时保留原始保存值并附加 issue；`date_system=unknown` 时不推断日期解释，缺失声明不视为符合 MS-XLS。
- 单条值承载记录损坏时跳过该记录/单元格，保留其它单元格，并设置 `omissions_affect_cell_values=true` 与保守的 `cell_value_coverage`。
- 非单元格元数据（行列表、视图、合并、公式组、DXF、表样式、条件格式、数据验证、图表、绘图、批注、链接、名称）校验失败时保留有界原始证据与 issue，不中止单元格读取。
- 宏存储只读不加载、不执行；图表/宏/对话框子表跳过并报告；外部链接与对象从不跟随或执行。
- 诊断条目超限时截断并设置 `issues_truncated`，不因诊断数量中止。

## 覆盖语义与使用限制

有可靠单元格结果但覆盖不完整时，状态为 `partial`（退出码 3），`cell_value_coverage` 标记为 `unverified_due_to_best_effort_salvage` 或等价保守值，`omissions_affect_cell_values=true`。**不能把这类结果直接用于财务结算、自动对账、交易决策或任何依赖完整单元格范围的程序。** 只做人工初步阅读；重要值应与独立 WPS/Excel 软件或可信原始台账对比。未知记录可能存储尚不认识的单元格值、计算结果或关系，因此即使未报错也可能遗漏数据。

## 必须保留的不可恢复边界

内容优先不等于无边界。以下仍拒绝或不执行：FILEPASS/加密（不解密）、ZIP 逃逸/符号链接/重复或歧义成员/加密 ZIP、DTD 与实体、CFB FAT/MiniFAT 链循环与交叉引用、指针越界、声明长度不符、输入变化 TOCTOU、超过 32 MiB 输入或 32 MiB 单流预算、记录负载大于 8224 字节、宏执行、公式重算、外链跟随与联网。不把失败、截断 JSON 或空表伪装成成功。

## 政府官网真实 ET 附件验证（历史证据）

维护专用流程从中山市农业农村局、汕头市商务局、中山市发展和改革局、深圳市司法局网站公告中取得 **7 个公开 .et 附件直链**，使用 16 MiB 限制、HTTPS 和官方域名白名单下载；**6 份原件实际下载且均为 CFB/OLE**，1 份深圳来源发生 TLS `BAD_ECPOINT` 失败（不等于解析失败）。文件仅在维护 CI 内存中被读取、计算 SHA-256，不保存原文或单元格内容到仓库。

历史阶段（显式 salvage 尚存在时）对 `Name (0x0018)` 做有限静态内部命名区域解释后，同样的六份固定 SHA 原件在普通 data 和显式 salvage 模式下均可返回 `partial`；此前已能读取的五份文件保持一致，单元格计数依次为 109、298、127、251、64。第六份中山市低空场景供需清单此前在偏移 13817 被拒绝，增强后不需要开启 salvage 就能读取 2 张工作表、126 个已知单元格。独立维护级 BIFF 记录分帧统计确认其已知单元格记录数为 **66+60=126**，但尚无独立全量单元格值、字体格式与 WPS 软件的语义对照。

该记录 50 字节，原始 SHA 和 Name 负载 SHA 均与此前拒绝样本一致；规范中的 Lbl 头表明非宏、非内置、UTF-16 12 字符、局部表 1、单条 `PtgArea3d (0x3B)`、内部 XTI 索引 0，矩形区域为绝对坐标。新解析器实际核验内部 SupBook/ExternSheet 与 BoundSheet8 的范围及指向，并仍仅保留 Name 的结构证据、不计算其表达式。宏、外链、复杂公式/额外字节、无效字符串与内置未知名称仍保留为原始证据或不解释。详见只读名称边界（`<skill-root>/references/biff8-defined-names.md`）及真实政府来源机器报告（`docs/maintenance/et/et-salvage-government-evidence.json`）。

此前政府样本“5 份可读、1 份在 Name 拒绝”的记录是兼容增强前的历史基线，不得与后来的 6 份受限可读混淆。2026-11-01 深圳司法局候选链接 TLS `BAD_ECPOINT` 仍属于下载失败，不计入解析失败。原件尚未随 Skill 分发，维护测试只输出哈希、数量与去标识化记录头，不暴露名称文字或表格正文。

## 合成验收与真实原件（历史基线）

以下数字保留各自历史含义，不代表本轮容错重构重跑：`skills/et/scripts/test/test_wps_biff8_salvage.py` 曾覆盖全局及表内未知记录、同 ID 多次出现、未知 CodePage、缺少 Date1904、缺少 WsBool、错误/加密/核心结构仍拒绝、元数据预算、低层显式 CLI 与原件字节不变；Linux Python 3.11/3.13 下 **474/474** 项 WPS 全量测试通过（GitHub Actions `41261297990`）。四份固定 ET/ETT 在普通与宽松模式下均匹配独立冻结的 **198 个保存值**（含 33 个未重算的公式缓存）。机器证据见 `docs/maintenance/et/et-salvage-evidence.json`。

当前默认容错重构的验证记录见 `docs/maintenance/et/validation.md#内容优先容错重构验证`；真实 WPS OOXML `.et`、云端 Linux 与 Pylint 仍未验证。
