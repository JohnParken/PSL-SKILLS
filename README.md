# PSL-SKILLS

运行检查按任务区分：仅读只做指定最小自检，输入可读即读；实际写入才检查写入能力，纯指导不执行环境检查。不主动倾倒挂载/系统信息，不以根目录只读推断任务目录不可用，安全检查保留。详见[说明](docs/minimal-runtime-checks.md)。

面向银行材料写作、Word 文档处理、电子表格读写计算、WPS 文字读取与受控编辑、RTF 文档生成和 DOCX 兼容导出、WPS 表格（.et/.ett）有限读取、OFD 1.0/1.1/1.2 XML 结构与存储文字提取、受限 SVG 到原生 PPTX 的创建与有界读取、多源表格模板填报、交付物归档与受控清理，以及 Skill 开发规范与质量检查器的 Agent Skills 集合。每个 Skill 包含独立的 `SKILL.md`，并按需附带脚本、参考文档和资源，可按完整目录加载或独立分发。

## 文档导航与当前基线

- [裸文件名输出契约与迁移](docs/output-filenames.md)：全 Skill 输出范围、接口版本与兼容边界。
- [架构与功能文档](docs/README.md)：12 个独立 Skill 的模块、架构图、流程图和输入输出边界。
- [tl-docx](docs/skills/tl-docx.md) 提供独立的 `python-docx` 路线，普通 `read` 默认以内容优先视图 聚焦复杂报告的正文、表格与图表保存值；原完整记录通过 `--full` 获取，也支持创建和受控编辑及文字修订副本。
- [文档任务导航](docs/skills/document-navigation.md)：按通用文档处理意图选择，无需先说明格式或文件位置；帮助确认附件、任务目录和所需工具 Skill。自身按需加载，不保证最先加载；当前验证见 [触发范围更新](docs/maintenance/document-navigation/intent-trigger-20261112.md)。
- 当前导读以 2026-11-12（UTC）的源码 `fb6cdff0161a137ebb0e64ebc64d09d11534d6d2` 加本轮工作区变更为基线，涵盖 ET 容错读取、文档导航与归档展示契约，Skill 版本以各自 `SKILL.md` 为准。[分批提交核验](docs/maintenance/commit-preparation/validation-20261112.md) 记录本轮实际检查；下文日期化验收记录只代表当时基线，不自动成为当前全量重测。
- [历史文档核验](docs/skill-docs-validation-20261107.json) 与 [文档防漂移检查](tools/check_project_docs.py) 分别记录实际检查结果和可重复检查入口。
- 本项目维护 Skill 层，不修改 Deep Agents 宿主或加载机制。基础包自包含；组合型 workflow 通过声明的公开契约协作，不能推断存在自动路由服务或 token 节省保证。
- [2026-11-10 优先优化记录](docs/maintenance/skill-review/optimization-451-20261110.md)：ET/XLSX 按需加载、七项描述与 PPTX 数学读取诊断，含实际验证及未验证范围。

- [Python 文件行数硬规范与模块拆分](docs/python-file-limit.md)：所有 Skill Python 文件最多 1000 物理行；超过为 error，1000 行允许。包含测试与内嵌引擎，不设第三方代码豁免。

## 收录内容

| Skill | 来源 | 版本 | 主要能力 |
| --- | --- | --- | --- |
| [文档任务导航（document-navigation）](skills/document-navigation/SKILL.md) | 独立纯指导文档 | 20261114.2127.4001 | 查看、整理、修改、生成等文档处理请求即可匹配，无需预先知道格式；确认附件与目录、定位顶层候选并加载所需技能，不解析文件或配置宿主 |
| [银行专业写作（bank-writing）](skills/bank-writing/SKILL.md) | 用户附件 `bank-material-writing-enhanced(2).zip` | 20261114.2206.4931 | 银行材料起草、改写、摘要、仿写与审校；独立月报分析、比较、创建/续写与增长机会验证，区分事实与假设；仅交付文本，表格计算按需交接，明确需要 DOCX 时由 docx 制作 |
| [docx](skills/docx/SKILL.md) | `skills/docx` | 20261114.2127.4001 | DOCX 生成、DOCX/DOTX 读取与受控编辑。全量或按章节读取正文、表头与合并行、文本框、图表保存值及缺值范围。提供静态排版诊断、自动命名与交付收尾。旧版 WPS 文字交给 wps，ET/ETT 交给 et。 |
| [tl-docx](skills/tl-docx/SKILL.md) | `skills/tl-docx` | 20261114.2127.4001 | 基于 python-docx 按关系读取内容、公式与图表缓存，局部失败明确 partial，支持报告和无损分片；支持 Profile、角色及表格文字样式、分节尺寸、多级编号、模板填充、线程批注与受控 run 合并。 |
| [xlsx](skills/xlsx/SKILL.md) | `minimax-skills/skills/minimax-xlsx` | 20261114.2127.4001 | XLSX/XLSM 原生读取、XLSX 创建和受控编辑、CSV/TSV 与分析；标准任务入口自动检测同名并递增版本命名，失败立即清理中间文件；支持公式子集计算与复杂 JSON 模板；可消费 et 技能提取的受支持电子表格 JSON；不直接读取 WPS 专有格式 |
| [wps](skills/wps/SKILL.md) | 本地限定实现；WPS 文字处理 | 20261114.2127.4001 | 标准库生成RTF（默认.wps后缀，显式.rtf保留，非金山原生格式），公文默认参数参照DOCX；读取文字、表格分组和合并、绘图、内嵌 PNG 及部分二进制图表缓存；内容重排编辑及DOCX兼容导出；发布前核对规范化文字/块顺序/单元格及支持的合并标记；CLI自动归档与受控配置清理；保留partial诊断，不承诺无损或视觉分页；导出可选LibreOffice；ET/ETT使用et、原生XLSX/XLSM使用xlsx，不自动转换 |
| [et](skills/et/SKILL.md) | 本地限定实现；从原 WPS 家族读取实现拆分 | 20261114.2127.4001 | 离线读取 .et/.ett 的 BIFF8/OOXML 保存值、公式缓存、行列尺寸、冻结窗格与当前单元格；有限解释数字格式、样式继承与富文本；支持表头区域预览（含 OOXML 保存公式与可转写的 BIFF8 公式）、按物理行号排除行、区域筛选、排序、去重、统计及分组汇总，返回统计质量报告并可追溯地填充合并文本标签；非单元格元数据有界保留并报告 partial；BIFF8 Note 批注与 HLink 超链接只读元数据分别返回 comments/hyperlinks；不重算公式、不写回或转换办公文件 |
| [ofd](skills/ofd/SKILL.md) | 独立标准库限定实现 | 20261114.2127.4001 | 默认按页读取标题、文字和页码，明确要求时读取完整结构；支持分层筛选及续读长文本；范围不等于整份XML已验证；不提供创建、编辑、OCR、验签、转换或渲染 |
| [pptx](skills/pptx/SKILL.md) | ppt-master v6.8.0 转换子集与标准库入口 | 20261114.2127.4001 | 受限 SVG 创建原生 PPTX，支持 PNG/JPEG、图表、表格与基础公式；文本/备注、概况和选页有界读取；数学结构丢失按页诊断；JSON字节预算与续读仍全包验证；无任意编辑或视觉验收 |
| [table-workflow](skills/table-workflow/SKILL.md) | 独立轻量编排；通过版本化CLI显式依赖 xlsx，ET 来源另依赖 et | 20261114.2127.4001 | 多个 XLSX/ET 来源按明确口径聚合匹配，计算后填入既有 XLSX 模板副本；有界结构摘要、名称续读、公开批量地址解析与舍入、公式预检及逐格溯源；不支持 ET 写回或任意明细连接 |
| [artifact-lifecycle](skills/artifact-lifecycle/SKILL.md) | DOCX已验证模块的显式自包含提取 | 20261114.2127.4001 | 本轮临时文件登记、最终交付物清单归档（无覆盖、安全幂等重试）、封存与受控清理；失败立即清理中间文件与清单；不生成或验收业务内容、不清理历史发布 ZIP |
| [skill-development-standards](skills/skill-development-standards/SKILL.md) | 本地规范定义与质量控制工具 | 20261114.2127.4001 | Skill 新建、修改、导入、迁移、审查与发布质量规范；`<skill-root>` 资源路径与文档契约检查器、结构回归测试与环境自检基准 |

## 目录结构

```text
PSL-SKILLS/
├── AGENTS.md                       # 项目维护约定；不等同于单 Skill 目录规范
├── .agents/skills/skill-development-standards  # 唯一规范的相对发现链接
├── README.md
├── skills/<name>/                  # 可安装 Skill 源码与必要运行资源
│   ├── SKILL.md
│   ├── requirements.txt            # 有脚本时声明实际依赖
│   ├── scripts/                    # 正式运行时；scripts/test 保留源码测试
│   ├── references/                 # 当前操作、契约、边界和来源记录
│   └── assets/                     # 运行模板、示例、字体与许可证
├── packaging/runtime-manifests/    # 每个 Skill 的显式发布文件白名单
├── tools/                         # 仓库构建、检查和测试入口，不随业务包分发
├── tests/                         # 跨 Skill 回归、发布验收与测试样本
│   └── fixtures/docx/             # 原 skills/ 根部的七个跨技能样本
├── docs/
│   ├── README.md                  # 当前版本与功能索引
│   ├── skills/                    # 架构及功能说明
│   ├── maintenance/<name>/        # 不随包的历史验收与维护证据
│   ├── validation/                # 项目重组/发布证据与历史包指纹
│   └── packaging.md               # 项目边界、构建与验证命令
├── dist/                          # 生成物；不纳入 Git
│   ├── runtime/                   # 新运行 ZIP 与逐包发布清单
│   └── archive/                   # 保留原字节的旧 ZIP，不覆盖
└── licenses/                      # 仓库来源许可
```


## 精简运行包与维护源码

全 12 个 Skill 使用 [统一打包器](tools/package_skills.py) 与 [发布说明](docs/packaging.md)。排除测试、缓存、历史验收和维护工具；OFD 写入服务暂停并排除写入资源，运行脚本与静态资源按显式清单逐字节发布，新增未分类文件使构建失败。OFD 源码内置字体及许可保留供恢复；PPTX引擎数据、XLSX模板、生命周期副本来源信息必须保留。

```console
python -I -S -B tools/package_skills.py
python -I -S -B tools/package_skills.py docx xlsx --output-dir dist/runtime/review
```

输出目录必须未含同名包，工具不覆盖。旧 ZIP 已保留在 `dist/archive/`；不要用运行时归档清理器删除发布历史。`bank-writing` 已全部是业务参考，保留完整内容；`skill-development-standards` 的自测是其维护功能的一部分，随包保留。其他业务包不含 `scripts/test`。`table-workflow` 仍显式依赖已定位并验证的 XLSX/ET 公开接口，不伪装成无依赖基础技能。

## 环境要求

**TL 系列依赖政策：** 名称以 `tl-` 开头的 Skill 可按需使用第三方组件，不限于 Python 标准库。实际依赖、版本和部署条件须在各 Skill 的依赖清单、入口与自检中明确；允许使用不代表组件已安装或能力已实现。非 `tl-` Skill 继续遵守各自原有约束，独立运行、路径、归档清理和验收规则不变。维护要求见 [AGENTS.md](AGENTS.md#tl-系列-skill-的组件依赖)。

- **document-navigation**：只有 `SKILL.md`；使用宿主已有的上下文、技能目录和读取/列目录能力，无脚本及 Python 依赖。所选格式 Skill 的执行条件另行自检。
- **bank-writing**：主要面向云端 Linux 容器的对话式文本写作，开发可用 macOS、Windows 或 Linux；包内无脚本、第三方包或办公软件依赖。按任务读取参考，精确长度用宿主已有能力只读计数，不能核验时如实说明。
- **docx**：Python 3.10 及以上标准库（包含 zlib，Expat >=2.6.0，需支持 POSIX 锁与文件描述符操作）；零第三方包依赖。
- **tl-docx**：Python 3.10 及以上、`python-docx==1.2.0`（唯一直接依赖）及任务目录的安全文件操作。部署时提供该组件的上游依赖。
- **xlsx**：Python 3.9 及以上；维护中的主流程与生命周期 runner 仅依赖标准库。
- **wps**：Python 3.10 及以上，纯 Python 创建、读取、编辑与 RTF 导出仅用标准库；OOXML 读取另需 Expat >=2.6；二进制导出可选系统 LibreOffice。
- **table-workflow**：Python >=3.12（维护基线3.12.11），仅标准库；单独安装完整 xlsx，含ET来源时再安装完整 et，通过实际读取主入口所得绝对根路径传参，不猜相邻位置。
- **et**：Python 3.9 及以上标准库；OOXML 表格分支另需 Expat >=2.6。
- **ofd**：读取需 Python >=3.10、Expat >=2.6.0、内置 zlib 和安全文件操作能力；仅用标准库。OFD 写入服务暂不可用。
- **pptx**：Python >=3.10 标准库，含 zlib、Expat >=2.6.0 及 POSIX 描述符相对安全 I/O；Linux 为目标平台，Windows 不支持，不依赖外部程序。macOS 上已有维护回归不等于目标 Linux 当前版本验收。
- **artifact-lifecycle**：Python 3.10 及以上标准库（需描述符相对安全文件操作及不跟随链接等能力）；零第三方包、零外部软件。实际操作前以自检结果为准。
- **skill-development-standards**：Python 3.10 及以上标准库（需系统 IANA 时区支持以生成规范版本时间戳）；零第三方包依赖。
- 上述明确为标准库实现的主流程使用 `-I -S -B`；`tl-` 系列按自身声明加载第三方组件，例如 `tl-docx` 使用 `-I -B`，不加屏蔽 site-packages 的 `-S`。WPS 二进制导出可选使用系统 LibreOffice；纯文本写作不需要 Python。
- XLSX 目录中保留的旧 legacy 工具可能依赖 pandas、openpyxl 或 LibreOffice，不属于推荐流程；不要据此为主流程安装依赖。

## 如何使用

将目标 skill 的**完整目录**交给支持 Agent Skills 的宿主加载；具体安装位置由宿主决定。也可以让代理先读取对应 `SKILL.md`，再根据任务读取其引用文档并调用脚本。含运行资源的 Skill 只复制入口或单个脚本会丢失必要模块和模板；`document-navigation` 的完整目录只有 `SKILL.md`。

用户提出“看看这个文件”“整理附件”“修改文档”“生成报告”等文档处理需求时即可选择 `document-navigation`，无需先知道格式、后缀或文件位置，也不必说出技能名。已有明确格式和输入同样适用；加载后复用已确认信息，同一任务已完整加载导航时不重复读取。它仍由宿主按需选择，正文只有实际加载后才生效，不能保证最先加载或补出宿主未提供的附件映射；格式 Skill 保留独立入口，不反向要求加载导航。

宿主返回的 `/large_tool_results/` 是结果引用，仅用宿主 `read_file` 按实际 schema 读取并核对覆盖；不交给 execute、cat、Shell 或 Python，不猜映射、不把路径掩码当真实路径。缺少读取工具或结果不完整时报告缺口并停止依赖。此约束不改变普通业务文件的读取规则。

对于 Skill 资源等普通路径，Deep Agents 等宿主的 `read_file` 路径属于读取后端，不必然是 `execute` 的实际文件路径。运行脚本前应确认两者映射，使用执行器可访问的实际路径；还须确认执行器当前目录为任务目录且输入可见。无法确认则停止，不猜路径或切换目录。此说明不改变宿主加载机制。

用户上传文件与待查交付物仅在已确认任务 `./` 顶层定位，优先附件映射和指定文件名；无映射仅列顶层候选，有歧义询问，找不到或指定位置不在顶层则报告，不递归搜索。此约定不改变内部归档、Skill 资源或程序原有输入安全边界。

业务交付输出只传裸文件名，由脚本固定写入宿主确认的任务根目录 `./`；拒绝 `./文件名`、子目录、绝对、父目录及 Windows 路径，不自动截取 basename。输入路径保持原契约，内部归档 `output/{questionId}` 和受控临时工作区不变；不把内部目录或临时文件作为交付物。业务输入、输出和临时文件不写入 skill 安装目录。脚本和资源以实际读取的 `SKILL.md` 所在目录定位；任务数据使用任务目录内的相对路径。下文的 `<skill-root>` 是占位符，须从对应 Skill 主入口 `SKILL.md` 的真实 `file_path`（相对读取路径先按宿主确认的读取基准解析）去掉文件名确定并整体替换，不能取参考文档目录；无法定位时停止，完整路径须正确引用。`python3` 必须指向满足版本要求的解释器。

### 银行专业写作

读取 [bank-writing](skills/bank-writing/SKILL.md) 完成经营解读、构思、起草、改写、整合或审校。月报任务先判断是否需要新经营判断：根据数据总结表现、找亮点/拖累或增长机会，即使只要三句话，也读取 [独立月报参考](skills/bank-writing/references/monthly-report.md)；只压缩已有结论才走摘要，按已确认结论写工作总结才走综合写作。月报分析与成稿由独立参考完成；只在复杂分解需要时追加经营分析，不把纯摘要或润色升级为分析。

写作指导先核对受众、决策目的和证据，区分事实、假设、建议及已批安排；不以合规措辞粉饰风险，不套用未核验准入或制度阈值。文种最小要素与三级审校见[专业写作增强说明](docs/maintenance/bank-writing/professional-writing-20261114.md)。

经营分析依次核对口径、比较基期、贡献与拖累、增长质量及用户需要的机会；区分数据事实、计算结果、原因假设与建议。批量表格计算按需由宿主定位已有 XLSX/ET 公开能力，保留来源、缓存和部分结果限制，不新增计算引擎或强制依赖。当前独立月报路线、合成演练与未验证范围见 [月报写作增强验收](docs/maintenance/bank-writing/monthly-writing-20261114.md)。

摘要、参考范文仿写和先摘要再仿写按需读取专项参考。范文与目标事实分开，摘要保留关键限定，严格篇幅实际计数，直接交付文本。默认不追问导出；用户确认或原请求已明确要求 DOCX 时，交给本项目 [docx](skills/docx/SKILL.md)，不重复确认。写作技能不生成文件，DOCX 不可用时保留正文并说明限制。

前次多轮编辑升级的同素材新旧执行、匿名评阅和读者检查见[写作评测记录](tests/bank_writing_upgrade_evaluation/summary.json)，结果包含持平与退步。摘要与仿写三路径的完整输入输出、计数和内容检查、独立评阅及本地分发校验见[专项评测记录](tests/bank_writing_summary_imitation_evaluation/summary.json)。两组记录均限于虚构场景，不等同于宿主自动触发验收；当时在 macOS 实测，云端 Linux 与 Windows 未验证。

描述调整时的独立选择模拟见[描述评测记录](tests/bank_writing_description_evaluation/summary.json)，不代表宿主自动注册或触发验收。当前入口还包含按任务直达参考与归档交接约定；纯文本写作本身不触发文件归档。

### 多源表格填报

[table-workflow](skills/table-workflow/SKILL.md) 负责“来源表 + 已有目标模板 → 指标映射 → 聚合与期间比较 → XLSX 副本及核验 JSON”。需要发现单文件全部表名时先用源 SHA 绑定的 `names` 续读；结构/表头定位使用有界 `inspect`，再按需读取计划契约；plan 在内存试算，apply 才写入最终文件并归档。格式读取、表头/统计、公式、写入仍复用 ET/XLSX。XLSX 能力接口 v2（裸文件名标志）及 ET v1 会协商 `open_metadata`/`metadata`、`typed_values`、`address_many` 及既有 `evaluate_many` 等必需能力；缺失即停止，不猜兄弟目录或回退到私有导入。简单建表或单表读取不加载本流程。

支持受控 one-to-one/many-to-one 维表补列后按键聚合，匹配唯一目标键；可配置业务勾稽与容差、缺期和空值政策，默认失败阻断、显式降级保留 partial。明确单位、期间、重复键、空值、小计及缓存政策；不支持任意明细级多表连接或原生ET写回。未知/数据性 partial、模板不支持公式、未刷新图表/透视/外链缓存均不得作为完整成功交付。模型负责业务语义，脚本不凭列名猜测口径。

合成集成回归见 [测试](tests/test_table_workflow.py)，环境、端到端结果、输出规模与未验证项见 [初次验证记录](tests/table_workflow_validation.json)，后续公式策略隔离、计划重放、状态与保护验证见 [针对性复核](tests/table_workflow_review.json)，维表关联、勾稽与真实公开样本验收见 [本轮增强记录](tests/table_workflow_quality_validation.json)。仅本机 macOS/Python3.12.11 验证，含明确标注的公开 XLSX 样本；不代表 Linux、Windows、WPS/Excel 软件视觉或宿主 token 节省验收。没有修改已有发布ZIP。

### XLSX

首次操作前运行自检，要求退出码为 0 且 JSON 中 `status` 为 `ready`：

```console
python3 -I -S -B "<skill-root>/scripts/stdlib_xlsx.py" check
```

新建表默认对有内容且未显式设置样式的单元格添加底边线，对符合条件的首行使用淡蓝表头，并估算列宽与长文本换行及行高；显式外观配置优先，估算效果受查看器影响。小型已知值清单使用 [主入口简单静态创建](skills/xlsx/SKILL.md#简单静态创建仅本入口)，示例省略显式单元格样式以保留默认淡蓝表头；单入口包含 stdin 规格、回读、归档与清理，无需预读多个参考。含公式、既有文件修改或复杂外观时，再按主入口路由加载所需契约。以下是低层兼容命令示例，写文件交付优先使用 runner；先确认输入可读、输出目录可写：

```console
python3 -I -S -B "<skill-root>/scripts/stdlib_xlsx.py" read ./input.xlsx
python3 -I -S -B "<skill-root>/scripts/stdlib_xlsx.py" create ./report.json report.xlsx
python3 -I -S -B "<skill-root>/scripts/stdlib_xlsx.py" edit ./input.xlsx ./patch.json edited.xlsx
```

输出必须是尚不存在的裸文件名，固定写入任务根目录。创建示例见 [calculation_example.json](skills/xlsx/assets/calculation_example.json)，其两处计算结果均应为 `226.86`。公式引擎只实现文档列出的子集，退出码 2 表示计算不完整，不能作为完整验收通过。

标准任务入口 `run_xlsx_task.py` 支持从配置与 stdin 接收完整规格，并在生成前定点检测主输出与归档副本同名，自动递增版本命名（如“_第2版.xlsx”）；业务成功或失败均立即清理本轮登记的中间文件和清单。复杂 JSON 建议通过 Python `json.dumps` 序列化后由 stdin 传入，详见 [复杂 JSON 调用](skills/xlsx/references/complex-json.md)。

### DOCX

修改已有 DOCX 的字体/字号按 [原文档格式编辑](skills/docx/references/structure-editing.md#已有文档字体修改) 使用 `run_docx_edit.py structure` 的 `format_run`，只传目标字段，保留原缩进、行距与样式继承。禁止提取文字或 compact 后重新 `create` 来冒充保格式编辑；表格内段落、页眉页脚、复杂范围等不支持的目标须明确报告并停止，不静默跳过或重建。低层 OOXML 候选必须经过 [受控格式核验与发布](skills/docx/references/ooxml-editing.md#字体修改与候选文件保护)，不能用基础结构或 `--original` 文本检查代替。回归覆盖与实际平台见 [DOCX 编辑保护](docs/skills/docx.md#已有文档字体修改与格式保留)。

运行分发使用专用 [打包器](tools/package_docx_runtime.py) 和 [显式清单](packaging/runtime-manifests/docx.json)，默认输出新的 `dist/runtime/docx-runtime-<version>.zip`。运行包包含标准库业务代码、自然分页、归档与清理。测试、维护历史和兼容样本留在源码。构建与独立验收方法见 [DOCX 运行包边界](docs/skills/docx.md#运行包与源码维护边界)。

调用前以宿主实际工具 schema 为准，将命令参数与 Python stdin 中的 JSON 区分；下方 shell 示例说明命令形态，不预设所有宿主均使用同一工具接口。历史维护记录中的终端命令仅供追溯。

普通生成按 [主入口简单创建流程](skills/docx/SKILL.md#简单创建仅本入口) 执行。先核对宿主实际工具 schema：执行工具的参数与传给 Python stdin 的文档 JSON 分开；要求 `command` 时传完整非空命令。缺参且确认尚未执行才修正一次，断流或状态不明不盲目重发。工具接入、流式拼接和 Qwen/DeepSeek 排查按需见 [工具调用故障](skills/docx/references/tool-calling.md)；Skill 不自动配置宿主门禁或模型参数，未验证真实模型成功率改善。

依赖、只读、工具间文件不可见、版本/长会话、断流及下载问题按需查 [环境与交付故障表](skills/docx/references/troubleshooting.md)。表中按当前标准库实现修正平台经验；不强制固定虚拟环境、不把 check 当下载验证、不在执行状态未知时直接重发。

标准新建直接调用 `run_docx_task.py create`；代码自动检查 Python/Expat、实际文件锁、安全文件操作及任务根目录写入，必要条件失败在生成前返回退出 1 和具体原因。模型无需单独调用 check 或判断自检日志。缺少归档配置、归档不可用或复制失败时保留已验收主文件并报告归档状态；清理未完成时主文件仍可保留，但 `task_status=cleanup_incomplete`，runner 返回退出码 3。`check` 仅保留为可选诊断命令。

按照 [主入口](skills/docx/SKILL.md#简单创建仅本入口) 组织普通生成 JSON，复杂任务按需读取专项 reference，直接送入统一入口的 stdin，不另建临时 .py 或 document.json。未指定排版时显式使用 official；用户指定格式优先。命令示例：

```python
execute(command="python3 -I -S -B \"<skill-root>/scripts/run_docx_task.py\" create \"客户服务改进方案.docx\" <<'JSON'\n{\"theme\":\"official\",\"blocks\":[{\"type\":\"title\",\"text\":\"客户服务改进方案\"},{\"type\":\"paragraph\",\"text\":\"完善服务流程，提升客户服务质量。\"}]}\nJSON\n")
```

自动入口创建前登记自己的 worker.py/spec.json，强制验收及占位符检查，处理归档并等待 worker 退出后自动清理。最终 DOCX 保留；收尾完成后删除恢复 manifest。回执独占写入且不创建暂存硬链接，持久化完整归属指纹后由父进程核对；证明已提交后的 worker 清理异常只提示，回执或主文件变化仍拒绝。生成或编辑失败、子进程超时同样自动清理本轮临时文件及清单，不默认保留待重试材料；需重试时从原始输入重新发起任务。`task_status` 和 `cleanup.cleanup_complete` 与归档状态分开核对。自检还需要标准库 POSIX 锁与描述符能力，当前只验证 macOS。旧 create_docx.py 生成 CLI 已 deprecated，仅 stderr 提示迁移、JSON stdout 和退出码兼容；底层库与五类编辑等尚有用途的接口保留，不会自动清理调用方自建的 Python/JSON；自定义或多步编辑流程继续显式登记清单，见 [自动任务及清理边界](skills/docx/references/task-cleanup.md#自动新建任务入口)。对于包含中文、引号、换行或字面量 `$()` 的复杂输入，统一使用经过验证的 Python 字典与 `json.dumps` 模板经 stdin 传入，详见 [复杂 JSON 调用模板](skills/docx/references/complex-json.md)。

文档输入、主输出、归档、图片及 Skill 内部资源采用不同且明确的基准，统一见 [文件定位与读取规则](skills/docx/SKILL.md#文件定位与读取规则)。所有命令从宿主确认的任务工作目录执行，不切换到输入子目录或 Skill 安装目录。

归档目录 `./output/{questionId}/` 默认仅保存副本，不主动扫描或读取其文档内容；仅在用户明确要求时读取指定归档文件。必要的定点同名检查、输入保护与本轮副本验证仍保留，详见 [归档目录使用边界](skills/docx/references/delivery.md#归档目录使用边界)。

示例文件名须按实际任务替换：优先采用用户指定名称，否则按文档标题或主题命名，避免默认使用 `output.docx`；详见 [交付文件命名](skills/docx/references/generation-runtime.md#交付文件命名)。

归档只使用本轮宿主环境提供的最新 questionId；缺失或无效返回 failed 并保留已验收主文件，不猜测、不复用历史回执中的 ID。宿主必须保证配置新鲜，脚本只能校验本次值是否合法，不能证明宿主未传旧值。归档清单仅列已验收的最终交付物，不包含中间文件。禁止覆盖既存归档；同一任务同名且内容、身份核验一致时返回 already_archived，异内容返回 archive_conflict。重试只重试归档，不重新生成主文件。归档目录及子目录禁止临时文件；暂存只在任务根目录。

最终交付不是任意写文件：正式创建/编辑/导出 CLI 自动收尾；DOCX 读取导出及 ET 完整 JSON 导出用 `--delivery` 明确最终用途。低层内部文件和 XLSX 协议 publish 不自动归档，最终交付前必须显式清单收尾；OFD 仅 stdout 读取，宿主保存为用户最终文件后另行归档。未调用属于待交接，不能当作无需归档或归档成功。

`--verify` 核对生成子集的内容与 XML 排版意图，不等同于页面视觉验收；字体显示、实际分页和页码仍需在文档阅读器中检查。

成功验收并核实文件后，在交付文件链接后简短提示“本次已按公文格式生成。还支持商务、学术排版，也可按需调整字体、字号和页边距。”用户指定主题或覆盖样式时按实际结果描述；提示不写入文档，用户要求只返回文件或不再提示时省略。完整规则见 [生成后格式引导](skills/docx/references/generation-runtime.md#生成后格式引导)。

读取大表格时，compact 默认只含前两行；全文需要原有纯文本读取，或 `--compact-json --tables rows` 按行续读。可选 `--max-output-bytes` 计真实 UTF-8 JSON 与末尾换行，返回源 SHA/参数绑定的 `next_cursor`；元数据或单块放不下时明确失败，不静默截断。旧 `next_block` 路径和新绑定游标的区别见 [读取契约](skills/docx/references/inspection.md#utf-8-预算与绑定续读)。预算不是 token 数，也不替代宿主完整回传检查。

使用 `read_docx.py 月报.docx` 一次返回全部支持的要素。加 `--section '完整标题'` 读取该节及下级章节的正文、表格、文本框、图表和图片引用。分页用 `--max-output-bytes` 与 `--cursor`，保持章节参数一致，复用首次解析结果。末页自动清理缓存，提前结束时传 `--release-cursor`。纯文本调用加 `--format text`。图表缺值保持空值，需由源工作簿确认。 [复杂报告读取](skills/docx/references/inspection.md#复杂报告读取)

### WPS 文字（.wps/.rtf）

独立入口见 [wps](skills/wps/SKILL.md)。简单读取与标题/正文/基础表格创建只需主入口；其他任务按操作直达参考，不递归预读全部指南。支持读取受限的 WPS 文字/RTF、内容重排编辑，以及生成 RTF 文档（可使用 `.wps` 后缀）和将 DOCX 导出为 WPS 兼容格式。生成结果默认使用 `.wps` 后缀，用户显式请求 `.rtf` 时保留；内容仍是 RTF 流，回执明确标注，不能称为金山原生 WPS 二进制。首次操作按任务类型分别自检：

```console
python3 -I -S -B "<skill-root>/scripts/read_wps.py" --check
python3 -I -S -B "<skill-root>/scripts/run_wps_task.py" check
python3 -I -S -B "<skill-root>/scripts/export_wps.py" check
```

读取正文可使用全量 JSON、纯文本或大纲/章节切片；RTF 全文纯文本按顺序包含表格，JSON 的 text 保持段落口径，表格及顺序见 tables/blocks；超限及不支持的筛选不会静默冒充完整结果。可选 `--max-output-bytes` 为有界视图增加真实 UTF-8 JSON 字节预算，保留完整诊断及源绑定续读，详见 [有界读取](skills/wps/references/bounded-reading.md)；创建命令和共同边界见 WPS 主入口，编辑/导出按需加载其直达参考；仅创建支持 `--compact-receipt`，不省略现有 partial/警告。ET/ETT交给et，原生XLSX/XLSM交给xlsx，不自动转换。纯 Python 工作流无需第三方包；系统安装 LibreOffice 时可选择二进制导出后端。读取及导出退出码 3 表示 `status=partial`，须保留已读内容、降级原因和缺口诊断。输入按内容识别，`.doc` 内装 RTF/OOXML 仍可读取；创建/重排/纯 Python 导出只写 `.rtf/.wps`，`.doc` 必须实际取得 MS-DOC 字节。`auto` 仅对 `.wps` 允许明确标记的 RTF 回退；检查与回读不能证明视觉或原生格式完整性。

使用 `--reading-mode maximum` 读取 MS-DOC 的一级表格分组、可确认的横向和纵向合并、直接段落属性、绘图锚点和组内文本框关联。`binary_images` 返回存储 PNG 元数据，显式加 `--include-image-bytes` 可取得有界原始字节。`binary_charts` 返回可定位的图表缓存及缺值范围，外部工作簿只记录引用状态。 [最大化读取](skills/wps/references/reading-assessment.md#最大化读取模式) · [图表缓存](skills/wps/references/binary-shapes.md#存储图表缓存)

WPS 图表读取新增保存值展示：按分类索引对齐原始数值与简单百分比/小数格式，逐字段报告缺失，保留缓存来源和未刷新标记。不得自动访问外部工作簿、反算缺失计数或把存储系列当成已验证的视觉系列；不做图片 OCR。合法内嵌 XLSX 可按明确单行/列引用补读已存值，不覆盖图表缓存；外链、缺失单元格和不支持格式保持明确缺口。

WPS 公开读取默认 maximum（CLI/API），standard 必须显式选择；OOXML/RTF 当前正文与修订存储视图分开，二进制不能确认当前修订视图时明确 partial。完整 JSON 保留16 MiB上限；maximum 新增全结果无损分片，正文、表格、附加区域、修订和诊断均可续读重组，不静默丢弃。单页字符与UTF-8预算含外壳；游标绑定源与选项，摘要校验不是密钥认证。旧主文分页仅允许显式 standard。创建/编辑/导出内部回读保持 standard。

读取容错：OOXML 主文按合法包关系定位；独立附加部件损坏保留正文和其他可读内容，standard 未请求的关联文字明确提示。公式、未知块内容与损坏位置保留诊断。MS-DOC maximum 对已分类的独立映射/文字损坏返回部分结果；安全、资源及共享结构错误不放宽。maximum 支持完整 JSON 或 [全结果无损分片](skills/wps/references/maximum-pagination.md)，不把仅正文分页当完整附加区域读取。

WPS新建默认公文参照当前DOCX：上下25.4mm、左右31.8mm，仿宋16pt／Times New Roman、固定28pt、首行2字、零段前段后；标题层级、页眉页脚和默认关闭页码同步。未明确要求新页时自然分页，保留用户显式配置。RTF与OOXML不是同一格式，不保证字体回退或跨阅读器分页一致；现有DOCX→RTF导出不解析完整样式继承，不作为统一排版的捷径。仓库对照回归见 [测试](tests/test_wps_docx_official_parity.py)，350项历史测试结果、缓存告警与未验证范围见 [排版对齐记录](tests/wps_docx_official_alignment_20261104.json)。后续 DOC 回读与格式匹配修复的377项 WPS、2项对照测试、8份已有输入回归及真实二进制导出未通过的范围，见 [导出契约验证](tests/wps_export_contract_validation_20261104.json)。默认 `.wps`、显式 `.rtf` 的命名与回读回归见 [后缀验证记录](tests/wps_default_suffix_validation_20261104.json)。

### WPS 表格（.et/.ett）

真实扩样（2026-11-12）：通过 Edge 新下载五个政府发布方的 8 份 ET 原件，599 个非空保存值（含 83 个缓存）、712 个空白及 102 处合并核对一致；发现并修复一处共享公式 cUse=0 的兼容性缺口，保存值未丢失。详见 [本轮审计与限制](docs/maintenance/et/edge-real-samples-20261112.md) 与 [专项修复](docs/maintenance/et/shared-formula-zero-use-20261112.md)；这不是完整格式或 WPS 版本认证。

独立入口见 [et](skills/et/SKILL.md)。离线提取工作表、单元格保存值与公式缓存；使用实际文件结构识别 BIFF8 或 OOXML 子集。支持候选表头区域预览，还可对指定区域按物理行号排除行、筛选、排序、去重、做列统计和分组汇总，并返回统计质量报告，输出派生 JSON；不重算公式，不修改原表，也不转换办公文件。可安全定界的非单元格元数据异常保留证据并继续；损坏图表和重复数据段恢复明确可读值，同时报告覆盖歧义，图表缓存不冒充单元格。BIFF8 Note 批注与 HLink/HLinkTooltip 元数据分别返回在 comments/hyperlinks，目标不解析、不跟随。重叠合并不阻断普通统计；加密、安全和硬资源边界仍保留。概况大诊断可显式摘要，不能代替完整读取。当前实现、xlrd 样本对照与验收限制见 [兼容读取补强记录](docs/maintenance/et/compatibility-recovery-20261111.md)。

```console
python3 -I -S -B "<skill-root>/scripts/read_et.py" --check
python3 -I -S -B "<skill-root>/scripts/read_et.py" ./input.et
python3 -I -S -B "<skill-root>/scripts/analyze_et.py" --check
python3 -I -S -B "<skill-root>/scripts/analyze_et.py" ./input.et --sheet "数据" --range A1:D8 --preview
python3 -I -S -B "<skill-root>/scripts/analyze_et.py" ./input.et --sheet "数据" --range A1:D100 --header-rows 1
```

尚不确定表头时，先用 `--preview` 查看明确矩形内的物理位置、空白/缺失、保存缓存与合并锚点，再选择表头和数据范围。预览返回独立 `et_table_preview_v1`，不推断表头、不填值、不计算统计；默认最多 10 行，分页和遗漏见 [表头区域预览](skills/et/references/table-preview.md)。

区域内穿插已确认的小计、总计或重复表头时，可在 `--query` 的 JSON 中用 `exclude_rows` 列出原始物理行号；不自动识别这些行。排除项必须位于所选数据行内，结果保留原始行号和单独的排除计数；被排除的合并锚点不会向其他行填充标签。选区诊断与最终匹配数据数量分别报告，排除行不会隐藏源 partial。

分析默认排除公式参与计算；显式选择缓存政策才使用已保存缓存，错误或缺失缓存保留诊断，不补零。合并区域默认不填充；可显式填充普通文本标签并记录来源，数字和公式标签不扩散。统计覆盖全部匹配行，行与分组预览有界，源 partial 不升级为完整成功。筛选、排序、去重、分组等查询及限制见 [统计与结果表处理](skills/et/references/table-analysis.md)。

基础表头和统计可按入口直达 [基础统计](skills/et/references/basic-statistics.md)，复杂筛选/排序/分组再读完整查询参考。读取、预览和分析可选 UTF-8 字节预算；分析分页保留全区域统计、质量分母、覆盖及绑定游标，不以返回页缩小计算范围。

分析的 `quality_report` 覆盖全部匹配数据，报告数值纳入/排除分母、缺失与缓存状态、有界位置样本、表头标签及分组覆盖；样本截断不缩小统计范围。它是事实诊断，不是业务正确性评分，也不证明公式缓存最新；仍需保留源 partial 和选区诊断。字段与边界见 [统计质量报告](skills/et/references/table-analysis.md#统计质量报告)。

退出码 0 表示当前声明范围的操作完成；退出码 3 保留结果，读取/预览保留源诊断，分析同时保留源诊断和 `analysis_issues`；退出码 1 表示读取、查询或资源失败，退出码 2 表示参数错误。

### OFD

独立入口见 [ofd](skills/ofd/SKILL.md)。 CLI 与 Python `read_ofd()` 默认返回按页连接的文本（`ofd-content-2`），省略文档/页参数时从第1文档第1页开始；按页总数和游标继续。旧结构调用迁移到 `--view full` 或 `read_ofd_structure()`；`--capabilities` 返回接口版本2。宿主以 `execute(command=...)` 提交完整命令。先运行自检，再在任务目录按页读取标题、文字和页码；用户明确要求结构时才使用 full。正文和显式结构模式都保留诊断，不恢复页面或语义阅读顺序：

```python
execute(command="python -I -S -B \"<skill-root>/scripts/read_ofd.py\" --check")
execute(command="python -I -S -B \"<skill-root>/scripts/read_ofd.py\" './input.ofd'")
# 仅明确要求结构时：
execute(command="python -I -S -B \"<skill-root>/scripts/read_ofd.py\" './input.ofd' --view full")
```

OFD 创建、生成、编辑等写入服务暂不提供。已有文件的读取与分析保留；源码中的生成实现、字体和历史证据仅供后续恢复，不属于运行包。旧 ZIP 不会自动改变，部署时应更换为按当前清单新建的只读运行包。

OFD 离线运行包由统一工具构建到 `dist/runtime/ofd-runtime-<version>.zip`；完整目录独立安装，不依赖相邻 wps/docx/xlsx。版本化 ZIP 按现有 `.gitignore` 规则不纳入版本控制，源目录和 README 是维护入口。

### PPTX

独立入口见 [pptx](skills/pptx/SKILL.md)。在满足其环境约束的解释器中先自检。用户上传文件只在任务目录顶层定位，文件名含空格时作为一个参数引用；配置中明确引用的 SVG 和图片可使用安全的任务相对路径：

```console
python3 -I -S -B "<skill-root>/scripts/pptx.py" check
python3 -I -S -B "<skill-root>/scripts/pptx.py" inspect "input.pptx" --summary
python3 -I -S -B "<skill-root>/scripts/pptx.py" inspect "input.pptx" --slides 1-3 --notes omit --max-output-bytes 12000
```

`inspect` 默认仍返回完整结果；选页、备注省略与字节预算不绕过未选页、关系和媒体的整包安全检查。备注读取包括受支持的文本形状和原生表格段落。分页只在完整幻灯片边界切分，继续时原样传回 `--cursor` 并重复原选项，单页过大明确失败。有限 PNG 零填充兼容需显式启用，结果保留 partial 并退出 3；不是对任意损坏文件的恢复。创建只接受声明的 SVG/配置子集并拒绝覆盖，不代表任意 PPTX 编辑或视觉验收。详细接口见 [PPTX 文档](docs/skills/pptx.md)。

### 交付物归档与受控清理（artifact-lifecycle）

独立入口见 [artifact-lifecycle](skills/artifact-lifecycle/SKILL.md)。用于在创建临时文件前登记本轮路径，在业务交付物验收后进行原子归档与安全清理。

首次操作前运行自检，要求退出码为 0 且 JSON `status` 为 `environment-ok`、`task_writable` 为 true：

```console
python3 -I -S -B "<skill-root>/scripts/lifecycle.py" check
```

标准五步工作流：
1. **登记（plan）**：在创建临时文件前显式登记本轮中间文件路径（如 worker.py、spec.json），保护输入原件与历史交付物。
2. **生成并验收**：业务技能生成文档并执行验证；失败时等待生产进程退出，再按原清单以 `failed` 封存并调用清理，不把公共 CLI 当作自动捕获任意业务失败的 runner。
4. **封存与清理（seal & clean）**：seal 封存清单，clean 仅删除本轮已登记、归属和身份检查通过且可安全删除的临时文件；全部收尾后移除清单（`manifest_removed=true`）。未知、占用或身份变化项保留并报告 `cleanup_incomplete`。
5. **交付**：核对完整归档结果后，归档成功即提示“🎉 文件归档成功，请到云空间查看。 ☁️✨”，无需等待宿主云空间映射确认，不宣称已验证上传完成或展示时效。失败、部分成功、跳过、待交接和未知分别如实报告，不套用全部成功；业务与清理状态独立，主业务失败不称生成成功。隐藏归档路径、目录、ID及原始日志，保留真实主文件链接，不虚构下载。

### Skill 开发规范与检查器（skill-development-standards）

用于开发、审查与发布 Skill。主入口见 [Skill 开发规范](skills/skill-development-standards/SKILL.md)。

首次检查前确认目标目录及 `SKILL.md` 可读，执行下列两项只读自检；两条均须退出 0，第一条输出 `environment-ok`，第二条显示用法。使用已确认的 Python 3.10+：

```console
python3 -I -S -B -c "import sys; assert sys.version_info >= (3, 10), 'Python >=3.10 required'; print('environment-ok')"
python3 -I -S -B "<skill-root>/scripts/check_skill.py" --help
```

以下示例在已确认的仓库根目录执行，对指定 Skill 目录进行严格结构检查（检查 `<skill-root>` 资源路径规范、YAML 标头、依赖声明、文件名规范与链接有效性）：

```console
python3 -I -S -B "<skill-root>/scripts/check_skill.py" skills/docx --strict
```

运行检查器自身的单元测试：

```console
python3 -I -S -B -m unittest discover -s "<skill-root>/scripts/test" -p 'test_check_skill.py' -v
```

## 能力边界

- **PPTX**：受限 SVG 创建可编辑的原生对象，支持 PNG/JPEG、有限图表、表格和公式；可读取 Transitional PPTX 子集的文本、备注及对象计数并检查包结构。不提供任意既有 PPTX 编辑、完整格式导入或桌面显示验证；创建拒绝覆盖同名文件，归档由随包运行时自动完成。
- **XLSX**：原生编辑仅支持受控单元格操作，不支持任意结构编辑；不直接读取 WPS 专有格式，ET/ETT 使用 [et](skills/et/SKILL.md) 技能。其 BIFF8 子集、样本清单与验证记录见 ET Skill；公式读取的是保存缓存，不重算。
- **WPS**：读取受支持的 WPS 文字/RTF，并可生成 RTF 文档（可使用 `.wps` 后缀）、受控编辑及 DOCX 兼容导出；RTF 流不是金山原生 WPS 格式，不承诺无损编辑或页面视觉还原。未知私有记录、加密、损坏结构与资源错误按入口契约拒绝并报告。
- **ET**：支持 .et/.ett 的 BIFF8 与 OOXML 有限读取、表头区域预览，以及按物理行号排除行、区域筛选、排序、去重、列统计和分组汇总，并提供统计质量报告；保留源 partial 与缓存缺口，不自动识别表头、小计或总计，不认证业务正确性。处理的是保存值与派生 JSON，不重算公式、不写回原格式或转换办公文件，不将有限子集表述为完整格式支持。
- **DOCX**：支持受控的生成和编辑子集，不提供任意复杂文档的排版还原、页面预览或 PDF 导出。WPS 文字由独立 wps 技能提供，ET/ETT 由 et 技能提供。
- **失败立即清理**：生成或编辑失败、子进程超时同样立即清理本轮中间辅助文件与清单，不因待重试保留临时材料；重试需从原始输入重新发起。
- 更详细的限制、错误处理、验收和中间文件清理规则以各 skill 的 `SKILL.md` 为准。

## 版本规则

所有 Skill 的 `version` 使用带引号的 `YYYYMMDD.HHmm.ssff` 时间戳，末四位表示秒和百分之一秒。以当前纽约时间（`America/New_York`）的日历时间加 12 小时，跨日时日期同步进位。例如原时间 `2026-10-22 18:08:11.20` 生成 `20261023.0608.1120`。每次维护目标 Skill 时更新其版本；同批修改可共用一个采样时间。

使用 Python 3.10 及以上生成版本（需要系统可用的 IANA 时区数据；缺失时报告环境问题，不回退到机器默认时区）：

```python
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

now = datetime.now(ZoneInfo("America/New_York"))
shifted = now.replace(tzinfo=None) + timedelta(hours=12)
version = shifted.strftime("%Y%m%d.%H%M.%S%f")[:-4]
print(version)
```

这是日历时间加 12 小时的版本编码，不是时区转换。检查器验证格式与日历合法性，不验证历史版本与当前时刻相等。规则及校验逻辑由本项目维护，已不同于最初迁移的来源版本。

## 开发验证

维护中的业务主流程保持各自声明的依赖边界，不将维护工具依赖混入独立包。每次功能或代码优化，必须检查并同步相关 README、docs、架构/流程图、示例和验证记录，核对 description、正文、引用说明、依赖及实际 CLI 行为；无需文档变更时说明已检查及原因，不强制无关改动。结构检查不能替代能力与证据核对。

从仓库根运行文档防漂移检查及其回归：

```console
python3 -I -S -B tools/check_project_docs.py
python3 -I -S -B -m unittest discover -s tests -p 'test_project_docs.py' -v
git diff --check
```

检查覆盖当前 Skill 清单、导读版本、仓库内相对链接与锚点；不联网验证外部链接，不执行全部示例，也不代替 Mermaid 解析、图意审阅或业务测试。

仓库维护约定见 [AGENTS.md](AGENTS.md)。新建、修改、导入、迁移或发布 skill 前，先读取 [Skill 开发规范](skills/skill-development-standards/SKILL.md)。规范附带标准库检查器和测试；`.agents/skills/skill-development-standards` 通过相对符号链接指向同一份规范。

规范及检查器从本地 `skills` 项目迁移；本项目已增加时间戳版本校验，并为规范补充了“执行环境约束与首步自检”章节。使用 Python 3.10 及以上，从仓库根目录检查本次维护的目标，例如：

```console
python3 -I -S -B skills/skill-development-standards/scripts/check_skill.py skills/docx --strict
python3 -I -S -B -m unittest discover -s skills/skill-development-standards/scripts/test -p 'test_check_skill.py' -v
```

各 Skill 自带测试位于 `scripts/test/`，生产流程不调用测试。仓库维护可使用 `tools/run_skill_tests.py <测试目录>`：显示发现数量，零发现、导入错误或测试失败均返回非零；不同 Skill 分进程执行，避免同名夹具模块互相污染。下列标准库 unittest 命令用于源码维护；业务运行包不携带测试，独立安装由仓库发布验收从包外驱动。开发规范 Skill 保留其流程必需的自测，避免删减维护能力。跨 Skill 集成及仓库适配器测试留在仓库 `tests/`。

在仓库根目录运行 XLSX 与 ET 自带测试：

```console
python3 -I -S -B -m unittest discover -s skills/xlsx/scripts/test -p 'test_*.py' -v
python3 -I -S -B -m unittest discover -s skills/et/scripts/test -p 'test_*.py' -v
```

DOCX 已包含标准库固定预期回归，覆盖生成、编辑、ZIP/XML 拒绝边界和格式路由。从仓库根目录使用满足 DOCX 环境要求的解释器执行：

```console
python3 -I -S -B tools/run_skill_tests.py skills/docx/scripts/test
```

原生行为测试要求 Python >=3.10、Expat >=2.6.0；跳过测试不算行为验收。范围、命令和证据见 [DOCX 维护测试与历史验证记录](docs/maintenance/docx/maintenance-validation.md)。环境自检、固定预期回归和视觉验收覆盖不同内容，当前流程不调用办公软件做视觉验证。

PPTX 从仓库根目录运行隔离环境回归；要求满足其 Linux/Python/Expat 与描述符操作条件。测试覆盖的结构与内容不等于桌面软件显示或真实宿主触发验收：

```console
python -I -S -B tools/run_skill_tests.py skills/pptx/scripts/test
python -I -S -B tools/run_skill_tests.py tests/pptx
```

PPTX 的历史 Linux 基线验证记录见 [标准库 PPTX 验证记录](tests/pptx/verification.json)：92 项回归（新增 26 项）、严格结构检查，以及开发阶段独立读取器的内容核对。桌面视觉、真实业务样本、Windows 与专用宿主触发未验证；开发核对工具不属于运行依赖。

## 迁移来源与许可

`pptx` 以 `JohnParken/ppt-master` v6.8.0、提交 `5e8856be0da8e6bc85c4aa28acc614478f7b7137` 的转换子集为基线，保留上游 MIT 及形状数据的原始许可与通知；范围见 [PPTX 来源说明](skills/pptx/references/provenance.md)，逐文件来源记录见 [上游迁移清单](tests/pptx/upstream_manifest.json)。
本次从 v6.5.0 迁移文字度量与基线、图表样式及跨页包名分配、合并表格样式和公式备用显示，并同步只读与资源预算边界。运行仍仅需 Python 标准库；完整上游的浏览器、来源转换、图片 API、模板回填与动画工作流未移植。

本次于 2026-10-23 从本地相邻项目复制，首次迁移保留原目录名称及 skill 内部文件内容，排除系统缓存文件；随后将本仓库的 `minimax-xlsx` 更名为 `xlsx`，同步更新名称和路径引用，脚本及资源内容不变。复制的是来源项目**当时的工作区内容，包含未提交修改**，不是对应提交的纯净快照。来源仓库当时的 HEAD 仅供追溯：

| 本地来源项目 | HEAD |
| --- | --- |
| `minimax-skills` | `c76df1161a0ae889604da6db9d1ecb90bf12f687` |
| `skills` | `0f0895690dfde8c203e6c2f5dbbac5a2615faf5e` |

`xlsx` 声明采用 MIT，来源项目的许可文本保存在 [licenses/minimax-skills-MIT.txt](licenses/minimax-skills-MIT.txt)。`skills` 来源项目的 README 将 DOCX 等文档 skills 描述为 source-available；本次本地 DOCX 目录没有独立许可证文件，未为其补写或推定新的许可证。本仓库不以单一许可证覆盖两个来源。

## 当前办公格式范围

| 后缀/输入 | 读取 | 生成或编辑 | 验证与边界 |
|---|---|---|---|
| .pptx | Transitional 子集文本、备注、对象计数与包摘要 | 从受限 SVG 新建原生 PPTX；不编辑现有文件 | 标准库结构验证及内容回读；PNG/JPEG、有限图表/表格/公式；无桌面视觉验收 |
| .docx | 正文、显式结构和复杂报告元素 | 生成及受控编辑；标准入口定点防同名递增版本命名，失败立即清理中间文件 | 原生行为回归；图表为保存值，实际排版由客户端决定 |
| .dotx | ZIP/XML读取 | 同后缀定点编辑，不生成DOTX | 规则受控，不以改后缀转换 |
| .xlsx | 原生读取与分析 | XLSX生成、受控编辑/公式子集；标准任务入口自动防同名覆盖递增命名，失败立即清理中间文件 | 不等于完整Excel引擎 |
| .xlsm | 原生读取 | 保留宏包的受控编辑 | 不执行宏、不创建宏工程 |
| .csv/.tsv | 数据读取与交换 | 按已实现数据导出路径交换 | 无工作簿样式/结构保真 |
| .ofd | 1.0/1.1/1.2 XML 结构、存储文字、页资源与复合引用 | 暂不提供创建、生成或编辑 | 无 OCR、格式转换或页面渲染；不作全文/视觉或全规范认证 |
| .wps/.rtf/.doc | 按真实 RTF、MS-DOC 或 OOXML 结构读取有限子集 | 新建/重排及纯 Python 导出只写 .rtf/.wps；.doc 导出需 LibreOffice 实际生成并核对文件格式签名 | 输入读取不按后缀拒绝可靠内容；RTF 不是二进制 DOC 或金山原生 WPS；不承诺完整格式或视觉还原 |
| .et/.ett | et 按实际结构读取 BIFF8/OOXML 有限子集及保存值/公式缓存；支持表头区域预览、按物理行号排除行、区域筛选、排序、去重、统计、分组汇总及质量报告 | 只读预览或分析输出派生 JSON；不生成、编辑或转换办公文件 | 不自动识别表头、小计或总计；质量报告不认证业务正确性；公式默认排除，显式使用的缓存未证明最新；保留 partial，不写回原表 |
| 约定schema的.json | 配置、读取结果、分析衔接 | 按各入口固定schema写出 | 非任意JSON编辑器 |

仓库没有 PDF、旧式 PPT、ODT/ODS/ODP、RTF 专用技能或通用转换器；PPTX 的新建与读取仅覆盖上表明确子集。旧 DOC 的有限 MS-DOC 文字子集由 wps 读取；ET/ETT 的 BIFF8/OOXML 表格子集由独立 et 读取，不因此扩大为通用旧式 XLS 支持。外部同名软件生成的真实DOCX/XLSX仍按原生格式处理。后续优先补充有合法来源的真实样本与独立内容预期，再按实际办公需求规划 PDF、PPTX 扩展等能力；不要通过后缀或依赖推断已支持。

当前十技能正反与混合选路场景见 [当前人工边界期望](tests/skill_selection_current_cases.json)，它不是模型准确率。旧四技能的 [固定正反例](tests/skill_selection_cases.json) 与 20 项选择模拟保留为历史证据，不作为当前十技能或真实宿主自动触发验收。

OFD 政务原件相关验收证据已因既有 Library scope 拒绝撤回，不计为有效授权验收。此前本批 OFD ZIP 含该项派生元数据和说明，不应使用，保留但停止交付；仅从清理后的源码重建新的本地 OFD 包，不读取旧 ZIP，不上传。其他合成回归与允许样本证据单独保留。


## 自检与执行结果回传

自检成功仅证明相应环境或结构检查通过，不等于文件已生成或产物已验收。各入口保留原文本/JSON契约，环境检查增加明确范围标记 `check_kind=environment artifact_validation=not_run`。保留同次调用的可见输出、状态、退出码和截断信息；输出缺失或状态不明时如实报告，不把非零退出的结果丢弃。

宿主可能分别回传 stdout/stderr，也可能合并或添加状态文字；不要假定通道、字段或包装格式。先核对工具执行状态、可见退出码和截断，再按对应入口的契约判断业务状态、partial 与覆盖；工具调用成功不等于进程或业务成功。仅在能可靠分离且确认完整时解析 JSON，无法判断就停止依赖操作并报告。不要为了补日志盲目重跑写操作。宿主提供的虚拟结果路径只能通过其指定的读取工具访问，不能当成本机 Shell 路径。`print` 不保证宿主或模型收到输出，也不改变现有机器可读接口。

大结果先选对应入口实际提供的有界视图，逐段保留来源身份、游标、覆盖、遗漏及 partial；仅在支持续读且来源未变时继续。投影、预览和截断不是全文。OFD `reading` 支持按文档/页范围和模板来源筛选，以绑定输入哈希、范围及筛选条件的游标续读长文本；选中页仍在内存，未选XML内容不在该视图的检查范围。

仓库维护与接入可用 [run_skill_check.py](tools/run_skill_check.py) 执行固定自检入口并输出独立回传 JSON；[入口清单、用法及边界](docs/check-execution.md)列出全部 Skill 和未验证范围。该适配器不随独立 Skill 发布，不安装或修改项目专属 execute 工具；原 Skill 命令仍可直接执行，宿主按原生接口回传。

适配器登记的 9 个环境自检入口包括（不是全部 Skill 自检的全集）：
- `docx-read`：`docx/scripts/read_docx.py --check`
- `docx-task`：`docx/scripts/run_docx_task.py check`
- `docx-create`：`docx/scripts/create_docx.py --check`
- `docx-archive`：`docx/scripts/archive_delivery.py --check`
- `wps`：`wps/scripts/read_wps.py --check`
- `ofd`：`ofd/scripts/read_ofd.py --check`
- `xlsx`：`xlsx/scripts/stdlib_xlsx.py check`
- `pptx`：`pptx/scripts/pptx.py check`
- `artifact-lifecycle`：`artifact-lifecycle/scripts/lifecycle.py check`
以及 1 个结构检查入口 `skill-structure`（调用 `check_skill.py --strict`）。适配器当前结构白名单不包含 et/table-workflow；两者使用各自真实自检入口，结构检查直接调用 `check_skill.py`。不为凑齐适配器注册而改动业务契约。

## 云端 Linux 生产发布验收规范

[Linux 验收清单](docs/linux-release-acceptance.md) 原方案固定于 2026-10-29 的三个历史 ZIP，不是当前十技能已通过报告，也不表示这些 ZIP 等同当前源码；当前源码验收应按该页新增的入口选择测试。原方案明确：
- 固定发布包 SHA-256 哈希校验与完整解包校验。
- 采用非 root 运行用户、安装目录只读、任务目录可写以及 `/tmp` 限制等真实生产环境约束。
- 业务成功、业务失败、序列化失败、语法错误、归档受阻、删除受阻等 9 类案例的独立验证标准。
- 外部控制器强制中断（SIGKILL、进程组终止、整容器强停与宿主重启）下的文件保护与恢复验收，明确被 SIGKILL 的进程无法自行执行收尾，必须由外部恢复控制器核验清单与回执后完成清理，避免使用模糊进程名或破坏交付物。
- 宿主集成 10 项能力验证（空参数/重复 call_id、双通道回传、退出码保留、结果截断标记、`/large_tool_results/` 专有读取、脱敏保护及云空间真实状态反馈）。

## 历史维护与验收记录

以下日期化记录保留各轮版本、测试数、平台及发布状态，后续源码变更不回写历史事实。当前使用以本文前半部、各 Skill 主入口及 [当前架构文档](docs/README.md) 为准。

## 三技能上线修复（2026-10-29）

本次版本为20261029.2043.2946。bank-writing明确素材指令边界及按需导出，保持纯文本交付；DOCX明确简单占位符和已支持的结构验证范围；XLSX明确公式子集与编辑边界。三个description均为100–150字符，人工核对适用/不适用场景；未声明已完成新版模型触发评测。

XLSX十个受支持写文件CLI在业务处理前检查输入、配置和输出路径；DOCX读取/兼容生成与五类编辑CLI检查任务文件路径，公开生成交付及标准runner同时检查图片资源路径。拒绝绝对路径、父目录、符号链接组件及输出输入别名。低层库仍保留显式路径能力，调用方负责任务隔离；不是同权限恶意并发写者沙箱。

路由配置诊断默认关闭，开启时只输出是否配置和脱敏标记。XLSX包装入口返回子进程状态、真实退出码及明确的通道省略记录，不回显任意stderr或无效JSON正文。清理仅覆盖登记项；DOCX新建、受控编辑和XLSX配置runner在业务验收后删除本轮中间文件及清单，归档失败/跳过也收尾。保留材料可能含业务数据，归属核实及宿主留存策略不可省略。


本轮验证记录：[上线修复与分发包验收](tests/production_readiness_20261029_20432946.json)。源码回归共579项通过；运行平台为macOS/Python3.12.14，不能替代目标Linux生产宿主验收。

## 最终收尾规则（20261029.2053.4764）

任务结束时必须清理本轮全部中间文件，包括脚本、配置、回读结果、日志、暂存、锁、回执和清单；仅将当前与历史交付物作为任务生成的留存文件。用户原件、主文件与其他任务文件仍受保护，不属于可删除中间文件。创建前完整登记，普通内部暂存由创建函数收尾；生成或编辑失败后，待生产进程退出立即按原清单清理归属明确且安全可删的临时项，不因可能重试而保留。删除失败、仍占用、身份变化或状态不明时保护相关文件并报告收尾未完成，不扫描删除未知文件；再次生成从原始输入建立新任务。

自动DOCX新建/编辑与XLSX在主文件验收后，即使归档失败/跳过也清理。公共CLI clean最后删除清单；底层cleanup_task只是内部阶段，最终API使用finalize_cleanup。清单删除后不能继续resume/clean；实际残留返回cleanup_incomplete，DOCX与XLSX自动入口退出3，不能把主文件成功当成收尾完成。

本版验证记录：[最终清理与发布包验收](tests/terminal_cleanup_20261029_20534764.json)。macOS/Python3.12.14源码回归581项通过，4个Skill严格检查通过；只读解压目录56项回归通过，执行前后安装文件哈希一致，4个ZIP的CRC与源码逐字节核对通过。目标Linux生产容器、Windows、真实宿主回传/下载、强杀与宿主崩溃恢复、Office视觉验收、模型触发评测及专用命名检查器未验证。

## 失败立即清理（20261029.2101.4877）

生成或编辑失败、子进程超时也自动清理本轮脚本、配置和清单，不再默认保留待重试材料；重试从原始输入重新建立任务。原失败退出码保留。用户原件及已有交付物不删除；锁占用、归属变化或删除失败明确报告清理未完成。旧版retry协议仅用于历史中断状态兼容，不是新任务失败策略。

本版macOS源码回归575项通过；四包严格检查、CRC和源码逐字节比对通过；只读解包回归58项通过，安装内容哈希未变。验证证据：[failure_cleanup_20261029_2101_4877.json](tests/failure_cleanup_20261029_2101_4877.json)。Linux生产容器、Windows、真实宿主回传和强杀恢复、Office视觉验收、模型触发及专用命名检查仍未验证。

## 任务路径专项复核（20261029.2106.1859）

扫描全部7个Skill的98份Markdown，并核对相关入口实现。修正DOCX虚设输入子目录的示例及图片基准的入口歧义；写作、DOCX、XLSX明确工作目录始终为宿主确认的`./`，仅按真实附件路径使用子目录，不为示例创建或切换输入目录。运行时代码未改动，三个description能力与触发范围未变。

全部7个Skill严格检查通过，8项路径回归通过；从测试任务根目录实际验证兼容CLI、字典API与标准stdin入口均读取根目录图片并生成DOCX，输入未变，无虚设输入子目录或辅助文件残留。三份新ZIP的CRC及源码逐字节核对通过。详见[路径审查证据](tests/task_path_audit_20261029_2106_1859.json)。仅macOS验证，未做生产Linux/Windows或真实模型路径选择评测。

## 最终发布包复核（20261029.2109.3562）

原三个2106发布包均与当时源码完全一致。复核修正DOCX失败重试/清理的残留旧说明、归档暂存位置说明，以及XLSX部分计算或无配置调用时归档残留可能误报清理完成的问题；DOCX/XLSX更新为20261029.2109.3562，纯文本写作包保持20261029.2106.1859。描述能力与触发范围未扩大。

最终三个包完整成员、CRC与源码逐字节一致；源码及解包严格检查通过。只读安装目录执行DOCX258项、XLSX302项回归全部通过，2项共享实现一致性检查通过；安装文件哈希未变。成功、失败、超时及清理拒绝分别核对，不将cleanup_incomplete视作完成。写作包不生成本地中间文件；旧CLI及调用方自建辅助文件仍需显式登记收尾。验证限macOS，生产Linux/Windows、真实宿主回传下载、强杀恢复、Office视觉验收、模型选择与路径理解、专用命名linter未验证。详见[最终复核证据](tests/prelaunch_final_20261029_2109_3562.json)。

## 工具调用故障规避（20261029.2116.5828）

业务 Skill 的脚本执行示例采用 `execute(command=完整命令)`；宿主需提供可执行后端，完整提交 stdin 并按实际回传核对结果。没有执行能力时停止并报告，Python API 与协议接口仍保留原契约。


针对用户提供的五类历史故障，更新三个Skill的调用与结果读取说明：阻止空参数调用、JSON改正后才重试、任务临时文件仅用./、退出3按完整摘要区分业务与清理、大结果引用使用指定读取工具。宿主侧需实现call_id去重、完成参数后再调度、非零退出完整回传及明确文件可见性；未声称这些宿主问题已由文档修复。运行时代码未修改，描述能力与触发范围不变。

严格检查与34项相关回归通过；实际验证错误列表JSON在生成前拒绝且无残留，文档中的合法列表和解码后的heredoc命令生成通过，保留产物并清理辅助文件。三个ZIP完整成员、CRC及源码字节一致。仅macOS本地执行，不替代真实execute/read_file隔离、重复注入、模型调用成功率或生产Linux验收。[验证记录](tests/tool_stability_20261029_2116_5828.json)。

大结果读取契约（20261029.2119.1860）：按用户确认，三个Skill统一明确`/large_tool_results/`必须由`read_file`读取；不可用时报告缺口，不回退到execute/Python/Shell。严格检查及三个ZIP完整成员、CRC、源码字节核对通过；仅文档变更，未执行真实宿主read_file调用。

## 复杂JSON调用模板（20261029.2126.3558）

简单JSON可直接写；复杂JSON使用Python dict/list与json.dumps序列化。DOCX/XLSX提供经过验证的源码和完整execute调用表示，默认Python源码及JSON均走stdin，复用既有runner，不新增运行时生成器。写作仍为纯文本，导出时交接DOCX模板。实际保存Python临时文件时创建前登记，由外层等待退出后在成功、业务失败、序列化失败及语法错误下seal/clean，不自删、不用成功条件链跳过清理。

7项模板测试覆盖20个子场景，源码和只读解包均通过；另34项现有清理/边界回归通过。三个Skill源码及解包严格检查通过，ZIP完整成员/CRC/源码字节一致，安装内容哈希未变；运行时Python与上版逐字节一致，描述触发范围保持不变。验证为macOS，生产Linux/Windows、真实宿主与模型效果、强杀清理及专用命名linter未验证。[验证记录](tests/complex_json_templates_20261029_2126_3558.json)。

上线前复核（20261029.2132.0838）：修正DOCX复杂JSON模板缺失official主题、旧失败收尾retain/retry示例、旧JSON非独占落盘示例，并移除XLSX模板中误带的DOCX专用说明。复杂输入统一引用已验证的Python序列化模板；临时文件成功和失败均收尾，清理受阻仍如实报告。DOCX/XLSX更新包，写作保持20261029.2126.3558；原有修改及历史包保留，三个最终包完整成员、CRC和源码字节一致。三个源码及只读解包严格检查通过；模板7项/20子场景在源码与最终解包均通过，两项生命周期副本一致性检查通过；本轮只读解包完整回归DOCX 258项、XLSX 302项通过，随后仅补充文档，运行时Python字节未变。description适用范围人工复核无须调整。macOS结果不替代云端Linux验收；真实宿主execute/read_file、模型路由、强杀或宿主崩溃清理、Windows及命名linter未验证。详见[复核记录](tests/prelaunch_review_20261029_2132_0838.json)。

## 路径与自检规范标准化及仓库适配器（2026-10-29）

规范与测试演进更新为 20261029.2024.0082。统一全仓库 7 个 Skill 共 98 份 Markdown 的 `<skill-root>` 资源路径占位符说明，禁止内部使用相对 Markdown 资源链接。

实现了仓库级回传适配器 [run_skill_check.py](tools/run_skill_check.py) 及自检回归 [test_self_check_contracts.py](tests/test_self_check_contracts.py) 与 [test_check_execution.py](tests/test_check_execution.py)。支持 8 个环境自检入口，统一回传 `execution_status`、`stdout`/`stderr` 分离通道、真实 `exit_code` 及 `check_kind=environment artifact_validation=not_run` 契约，防止模型将环境通过误判为业务产物验收。

## WPS DXF 记录校验与格式增强（2026-10-29）

支持 MS-XLS 0x088D DXF（差异格式）标准结构校验与属性保留，新增 [test_wps_dxf.py](skills/et/scripts/test/test_wps_dxf.py) 与 [test_wps_dxf_integration.py](skills/et/scripts/test/test_wps_dxf_integration.py)。WPS 电子表格在读取时将 DXF 记录保留于元数据，不将格式混入单元格值，不将其误判为 WPS 私有记录。WPS 技能全量 424 项回归测试通过。

## 同名保护与递增版本命名（20261029.2203.2365）


源码回归 591 项通过（DOCX 269、XLSX 304、公共生命周期 16、来源一致性 2）；三包严格检查、CRC、源码逐字节核对及只读安装回归 52 项通过，安装内容哈希未变。description 能力与适用范围人工核对后保持不变。验证为 macOS/Python 3.12.14 和隔离合成样本，未执行云端 Linux/Windows、真实生产宿主、Office 视觉或模型触发验收；专用命名 linter 不可用。新版本三包保留于 skills 目录，旧包未覆盖。证据：[同名保护验收](tests/archive_collision_20261029_2203_2365.json)。本轮主代理完成实现、审查与验收，未调用子代理。


### Skill 层按需加载优化验收

XLSX 简单静态创建改为单入口闭环，WPS 保留读取/基础创建并按任务直达低频参考；不修改 Deep Agents 加载机制。评测方法及真实宿主复验清单见 [加载评测说明](tests/skill_loading_benchmark.md)，当前静态计划见 [读取快照](tests/runtime_loading_profiles.json)，上一轮 XLSX/WPS 的 macOS/Linux 回归、独立复制及公开样本核对见 [上一轮验收记录](tests/skill_loading_validation_20261107.json)。字符/字节变化不是 token 节省；真实模型路由与 provider usage 尚未测量。


### 有界读取与跨表迭代

- ET 按概况、矩形预览、直接区域统计、完整证据分流；有界参考仅描述电子表格，不再示例 WPS 文字。
- DOCX 入口明确 compact 表格默认仅前两行、`--tables rows` 与 `next_block` 续读。保留原安全、归档与创建约束；入口7458字节，静态回归上限从7000调整为8000并增加完整性断言，字节不是token。
- PPTX inspect 保留原完整默认，新增 summary、slides、notes、UTF-8字节预算和整页续读。未选页、关系和媒体仍全包检查；超大单页明确拒绝，不静默截断，源partial继续退出3。
- table-workflow 新增 names 单文件有界表名发现及SHA绑定续读；inspect继续保留原最多8表预览。XLSX公开接口v1增添address_many，每批最多10000地址；新workflow明确要求该能力。金额舍入复用既有evaluate_many，保留Decimal和错误边界。

计划加载评测扩展至39个场景，包括PPTX、跨表、ET直接统计、OFD创建和partial。新能力的baseline缺口单独注明，静态读取计划、工具输出和RPC测试不替代真实模型路由或token计量。本轮验证范围和限制见 [迭代验收记录](tests/skill_reading_iteration_validation_20261107.json)。该记录保留当时验收快照；后续源码分批发布不更新发布ZIP。


### 大表写入与跨进程传输优化

XLSX 的 create/patch/calculate 使用单次操作内的行与单元格索引，操作结束即丢弃；非规范DOM结构沿用原查找路径，保留插入、错误和非目标保护规则。公开接口v1增添 typed_values 与 open_metadata，ET增添metadata；旧操作不变。table-workflow的目标键批量转换、names元数据路径显式协商新能力，完整解析/安全校验、SHA、partial及公式缓存门禁保持。

同输入基准与本轮真实样本/平台/独立部署范围见 [大表优化验收](tests/large_workbook_optimization_validation_20261107.json)。耗时与字节是指定样本的维护测量，不是通用加速或token保证；未修改Deep Agents或替换发布ZIP。两份验收记录中的 publication 字段均为各自 checked_utc 时的历史状态，不代表后续 Git 发布状态。


## Skill 级 Deep Agents 适配补强（2026-11-07）

本轮只维护 Skill、说明与仓库验收工具，不修改 Deep Agents 宿主或加载机制。WPS 有界读取只说明文字能力；当前十技能中文正反与混合场景使用人工能力边界期望，历史四技能模型选择记录原样保留，不将新期望称为模型准确率。

DOCX 新增可选 UTF-8 字节预算及源/参数绑定续读；ET/WPS 输出支持可选字节预算，ET 预览与分析使用明确的 has_more、覆盖和绑定游标，保留旧默认契约、源 partial、全区域统计和质量分母。新预算计算实际 JSON 与末尾换行，元数据或单块无法容纳时明确失败。XLSX 专项从入口直达共同契约与命中参考，ET 表头/基础统计独立成页，四个易混淆入口重新划分文本、原生格式、兼容处理与自动收尾职责。

加载快照仍为计划读取，不是实际模型轨迹；新增新旧快照哈希与路径集合过期检查，旧快照保留历史副本。当前人工期望见 [当前选路场景](tests/skill_selection_current_cases.json)，复现与边界见 [加载评测说明](tests/skill_loading_benchmark.md)。最终自动回归见 [本轮统一验收](tests/deepagents_adaptation_validation.json)，真实样本、只读独立安装与组合流程见 [独立验收详情](tests/deepagents_adaptation_acceptance_details.json)。这些记录区分通过、失败与未验证；字符/字节不是 token，真实宿主 usage 和未执行平台不作通过声明。该验收记录反映当时仅本地修改、未提交和未推送的状态；后续分批发布验收见下节，历史发布 ZIP 未替换。

## 归档提示更新（2026-11-07）

本次九个业务 Skill 版本为 20261107.1943.5370。完整归档成功即提示“🎉 文件归档成功，请到云空间查看。 ☁️✨”，无需等待后续宿主映射确认；部分、失败、跳过及未知按真实结果报告，不宣称已验证上传或展示时效。纯文本/stdout 不虚构归档。

验收见 [归档提示统一验证](tests/archive_notice_validation_20261107.json)：macOS 上 2209 项回归通过，9 份无缓存独立副本 strict 通过；源码目录已有 WPS/ET 缓存告警保留，未清理未知文件。该记录中的未提交、未推送为当时的历史状态，后续 Git 发布单独记录；未更新发布 ZIP，上传及展示时效未验证。

## 分批发布验收（2026-11-07）

按读取分页、任务路由、归档契约、评测证据四批整理；前三批分别为 `4cae8b3`、`d387e86`、`a21b30a`。每批从精确暂存树重算加载快照并执行对应检查，不改写用户工作区来拆分提交。最终 57 个计划读取场景快照为 fresh；历史快照原样保留并预期为 stale。

本次最终组合在 macOS/Python 3.12.12 上执行 28 个测试进程，共 2282 项、0 失败、0 跳过；14 项嵌套预算执行另记，不重复计入总数。九份干净副本 strict 通过，八个只读生产安装的十一项自检通过，跨技能副本与独立归档行为在回归中覆盖。原工作区 WPS/ET strict 仍因原有缓存分别产生 56/70 项警告，未删除或提交这些缓存。详细证据见 [分批发布验证](tests/batched_publish_validation_20261107.json)。

保留历史 OFD 验收目录和两份归属不明旧报告为未跟踪，不提交真实样本、不替换 ZIP。Linux/Windows、真实宿主模型选路与 usage、Office 视觉、云端上传和可见时效未验证；Pylint 未安装，AST/定点格式检查不替代完整 lint。仓库现有 CI 仅由 pull request 或 workflow_dispatch 触发，直接推 main 不等于已通过远端 CI；提交与推送结果以 Git 历史和精确远端 SHA 为准。

## 裸文件名输出分批发布（2026-11-07）

输出约束已按 DOCX/公共归档、WPS/PPTX/OFD、XLSX/workflow、公共文档及证据四批提交：`ebf67ad`、`ff27f9b`、`fdc8765`、`9fcf9fc`。四批普通快进推送后核实当时远端 main 为 `9fcf9fca339dd0f6491f7e64e4777f9dc57e61a7`，未强推。随后补正仓库导读的三个旧输出示例及发布基线，增加文档示例回归；补正后的 14 项文档测试及 429 个本地链接检查通过，加载快照仍为 fresh；业务源码和 Skill 版本不变。

提交前重新执行 29 个测试进程，共 2317 项全部通过；十份干净独立副本 strict 通过，当前加载快照 fresh，文档版本与 427 个本地链接检查通过。已保存的 [输出验证记录](docs/output-filenames-validation.json) 中“未提交/未推送”描述的是采样时状态，保留为历史证据；发布结果以 Git 历史为准。原三项未知文件和既有缓存未提交或清理，历史 ZIP 未替换。远端精确提交的 status 与 PR workflow 查询均为空，仓库现有工作流仅由 PR 或手动触发，不能称为远端 CI 已通过。

归档回复展示边界已统一：内部回执用于核验，最终回复及下载链接不得带出归档地址；实际主文件引用仍可交付。范围、检查证据与未验证项见 [归档展示复核](docs/maintenance/artifact-lifecycle/archive-reply-privacy-20261112.md)。

### 最终交付入口与完整性边界

文件写入不是归档完成。业务入口先声明用户最终交付清单，区分工作文件、读取中间结果与最终文件，再验证并收尾；禁止扫描工作目录把所有文件自动归档。

| 场景 | 归档方式与完成条件 |
| --- | --- |
| DOCX、WPS、XLSX 的正式创建/编辑/导出 CLI；PPTX 创建/重建 | 正式入口调用随包归档实现；分别核对业务、发布、归档与清理，不以退出 0 代替归档成功 |
| DOCX 读取落盘 | 用户要求最终交付时用 `--delivery`；普通 `-o` 仍是内部读取结果 |
| ET 读取落盘 | 用户要求最终 JSON 时使用 `--output 文件名 --delivery`；普通读取中间 JSON 不归档 |
| OFD 提取 | 默认 stdout 阅读不归档；用户明确要求保存提取结果时，由宿主保存验收后将准确文件名显式交接归档；不恢复 OFD 写入服务 |
| DOCX 底层 pack/comment/merge_runs、XLSX 内存 API 或协议 publish | 属内部步骤，不等于完成交付；执行相应验收后显式调用正式收尾，协议 publish 后必须另执行 delivery |
| 跨表工作流 | XLSX 与审计 JSON 是固定两件最终清单，两件均准备校验好才归档；准备失败保留已生成文件并标记整批未就绪，不擅自作为单件交付 |

WPS 已声明交付但业务、发布或内部清理未就绪时，返回 failed 与阻塞原因，不能误报 not_required。跨表逐件归档可部分成功，整批完成必须全部核验且没有清理残留；失败时只重试归档，不重新生成覆盖原件。每次归档读取宿主当前 questionId，宿主仍须保证它确为最新轮次。归档目录和子目录不放临时校验文件；云上传及真实模型是否完整执行这些步骤仍需宿主验收，不能由单元测试证明绝无遗漏。

WPS 11 / 12.1 生成的 `.doc` 直接按实际内容读取，不以应用版本或扩展名代替格式判断。默认 maximum 保留可读内容与局部缺口；五种 FIB 结构和内容路由有合成回归，但不等于所有软件构建、对象及版式均获兼容认证。

WPS maximum 的可选图表检查保留64候选上限：达到上限不再解析后续候选，正文和已读图表保持可用，明确partial与未检查数量，不把候选数当图表数。其他共享结构和资源安全门禁保持拒绝。分页消费者区分standard主文text与maximum全JSON片段，不混用契约或游标。

银行写作入口本轮精简为素材读取与纯文本指导，不要求执行环境自检；文件制作细节只在DOCX交接时读取，精确长度规则独立按需加载且不新增脚本。字符/字节与验证范围见 [精简记录](docs/maintenance/bank-writing/entry-slimming-20261114.md)。

无脚本指导 Skill 可在入口集中说明资源定位，执行型仍按完整路径规范；bank-writing参考头及专业组合已按此调整，边界与验证见[无脚本指导验收](docs/maintenance/bank-writing/scriptless-guidance-20261114.md)。

银行写作按专业判断风险而非句数决定是否追加专项，纯摘要/编辑不背书原结论；本轮章节、描述及路由核查见[记录](docs/maintenance/bank-writing/routing-priority-20261114.md)。
