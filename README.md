# PSL-SKILLS

面向银行材料写作、Word 文档处理、电子表格读写计算、WPS 文字读取与受控编辑、RTF 文档生成和 DOCX 兼容导出、WPS 表格（.et/.ett）有限读取、OFD 1.0/1.1/1.2 XML 结构与存储文字提取及显式请求下的基础 OFD 1.0 生成、受限 SVG 到原生 PPTX 的创建与有界读取、多源表格模板填报、交付物归档与受控清理，以及 Skill 开发规范与质量检查器的 Agent Skills 集合。每个 Skill 包含独立的 `SKILL.md`，并按需附带脚本、参考文档和资源，可按完整目录加载或独立分发。

## 文档导航与当前基线

- [裸文件名输出契约与迁移](docs/output-filenames.md)：全 Skill 输出范围、接口版本与兼容边界。
- [架构与功能文档](docs/README.md)：10 个独立 Skill 的模块、架构图、流程图和输入输出边界。
- 当前导读基于源码 `9fcf9fca339dd0f6491f7e64e4777f9dc57e61a7`（裸文件名输出契约，已于 2026-11-07 UTC 发布）。Skill 版本以各自 `SKILL.md` 为准；下文日期化验收记录只代表当时基线，不自动成为当前全量重测。
- [本轮文档核验](docs/skill-docs-validation-20261107.json) 与 [文档防漂移检查](tools/check_project_docs.py) 分别记录实际检查结果和可重复检查入口。
- 本项目维护 Skill 层，不修改 Deep Agents 宿主或加载机制。基础包自包含；组合型 workflow 通过声明的公开契约协作，不能推断存在自动路由服务或 token 节省保证。

- [Python 文件行数硬规范与模块拆分](docs/python-file-limit.md)：所有 Skill Python 文件最多 1000 物理行；超过为 error，1000 行允许。包含测试与内嵌引擎，不设第三方代码豁免。

## 收录内容

| Skill | 来源 | 版本 | 主要能力 |
| --- | --- | --- | --- |
| [银行专业写作（bank-writing）](skills/bank-writing/SKILL.md) | 用户附件 `bank-material-writing-enhanced(2).zip` | 20261109.1939.2825 | 银行综合材料起草、改写与审校；长文、会议及多源摘要，范文结构与文风仿写，先摘要再仿写；仅交付文本，获授权后规范交接由 docx 技能生成文件 |
| [docx](skills/docx/SKILL.md) | `skills/docx` | 20261109.1818.2343 | DOCX 生成、DOCX/DOTX 读取与受控编辑；支持段落分页、左右/悬挂缩进、制表位及普通段落制表符，短诗等简短内容默认连续排版，回传已检出的静态排版风险提示，静态验收不代表视觉分页验证；标准入口自动检测同名并递增版本命名，管理临时文件并收尾；保留归档保护与复杂 JSON 输入；不生成 DOTX；WPS 文字使用 wps 技能，ET/ETT 使用 et 技能 |
| [xlsx](skills/xlsx/SKILL.md) | `minimax-skills/skills/minimax-xlsx` | 20261109.1939.2825 | XLSX/XLSM 原生读取、XLSX 创建和受控编辑、CSV/TSV 与分析；标准任务入口自动检测同名并递增版本命名，失败立即清理中间文件；支持公式子集计算与复杂 JSON 模板；可消费 et 技能提取的受支持电子表格 JSON；不直接读取 WPS 专有格式 |
| [wps](skills/wps/SKILL.md) | 本地限定实现；WPS 文字处理 | 20261109.1939.2825 | 标准库生成RTF（默认.wps后缀，显式.rtf保留，非金山原生格式），公文默认参数参照DOCX；受支持文字读取、内容重排编辑及DOCX兼容导出；发布前核对规范化文字/块顺序/单元格及支持的合并标记；CLI自动归档与受控配置清理；保留partial诊断，不承诺无损或视觉分页；导出可选LibreOffice；ET/ETT使用et、原生XLSX/XLSM使用xlsx，不自动转换 |
| [et](skills/et/SKILL.md) | 本地限定实现；从原 WPS 家族读取实现拆分 | 20261109.1939.2825 | 离线读取 .et/.ett 的 BIFF8/OOXML 保存值与公式缓存；支持表头区域预览、按物理行号排除行、区域筛选、排序、去重、统计及分组汇总，返回统计质量报告并可追溯地填充合并文本标签；非单元格元数据有界保留并报告 partial；不重算公式、不写回或转换办公文件 |
| [ofd](skills/ofd/SKILL.md) | 独立标准库限定实现 | 20261109.1939.2825 | 读取 OFD 1.0/1.1/1.2 的 XML 结构与存储文字；仅在明确要求 OFD 新建时生成基础 OFD 1.0 多页文件，含固定文字、线框、受限图片与显式网格表格；可选随包朱雀仿宋预览示例或明确自备字体，缺字拒绝；不编辑第三方文件，不做 OCR、验签、转换或渲染 |
| [pptx](skills/pptx/SKILL.md) | ppt-master v6.8.0 转换子集与标准库入口 | 20261109.1939.2825 | 受限 SVG 创建原生 PPTX，支持 PNG/JPEG、图表、表格与基础公式；文本/备注、概况和选页有界读取；JSON字节预算与续读仍全包验证；无任意编辑或视觉验收 |
| [table-workflow](skills/table-workflow/SKILL.md) | 独立轻量编排；通过版本化CLI显式依赖 xlsx，ET 来源另依赖 et | 20261109.1939.2825 | 多个 XLSX/ET 来源按明确口径聚合匹配，计算后填入既有 XLSX 模板副本；有界结构摘要、名称续读、公开批量地址解析与舍入、公式预检及逐格溯源；不支持 ET 写回或任意明细连接 |
| [artifact-lifecycle](skills/artifact-lifecycle/SKILL.md) | DOCX已验证模块的显式自包含提取 | 20261109.1939.2825 | 本轮临时文件登记、最终交付物清单归档（默认原子独占发布防同名覆盖，支持显式 `--replace-existing` 替换）、封存与受控清理；失败立即清理中间文件与清单；不生成或验收业务内容、不清理历史发布 ZIP |
| [skill-development-standards](skills/skill-development-standards/SKILL.md) | 本地规范定义与质量控制工具 | 20261109.1939.2825 | Skill 新建、修改、导入、迁移、审查与发布质量规范；`<skill-root>` 资源路径与文档契约检查器、结构回归测试与环境自检基准 |

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

全 10 个 Skill 使用 [统一打包器](tools/package_skills.py) 与 [发布说明](docs/packaging.md)。只排除测试、缓存、历史验收和维护工具，不裁剪已支持的业务功能；运行脚本与静态资源按显式清单逐字节发布，新增未分类文件使构建失败。OFD 内置字体及许可、PPTX引擎数据、XLSX模板、生命周期副本来源信息必须保留。

```console
python -I -S -B tools/package_skills.py
python -I -S -B tools/package_skills.py docx xlsx --output-dir dist/runtime/review
```

输出目录必须未含同名包，工具不覆盖。旧 ZIP 已保留在 `dist/archive/`；不要用运行时归档清理器删除发布历史。`bank-writing` 已全部是业务参考，保留完整内容；`skill-development-standards` 的自测是其维护功能的一部分，随包保留。其他业务包不含 `scripts/test`。`table-workflow` 仍显式依赖已定位并验证的 XLSX/ET 公开接口，不伪装成无依赖基础技能。

## 环境要求

- **bank-writing**：主要面向云端 Linux 容器的对话式文本写作，开发可用 macOS、Windows 或 Linux；包内无脚本、第三方包或办公软件依赖。按任务读取参考，精确长度用宿主已有能力只读计数，不能核验时如实说明。
- **docx**：Python 3.10 及以上标准库（包含 zlib，Expat >=2.6.0，需支持 POSIX 锁与文件描述符操作）；零第三方包依赖。
- **xlsx**：Python 3.9 及以上；维护中的主流程与生命周期 runner 仅依赖标准库。
- **wps**：Python 3.10 及以上，纯 Python 创建、读取、编辑与 RTF 导出仅用标准库；OOXML 读取另需 Expat >=2.6；二进制导出可选系统 LibreOffice。
- **table-workflow**：Python >=3.12（维护基线3.12.11），仅标准库；单独安装完整 xlsx，含ET来源时再安装完整 et，通过实际读取主入口所得绝对根路径传参，不猜相邻位置。
- **et**：Python 3.9 及以上标准库；OOXML 表格分支另需 Expat >=2.6。
- **ofd**：读取需 Python >=3.10，生成另需 Python >=3.12.11；均只用自带标准库（含 zlib、Expat >=2.6.0），并检查各入口实际需要的描述符相对安全文件操作能力。零第三方包、零外部软件；缺失能力时停止，不安装组件或降低安全要求。
- **pptx**：Python >=3.10 标准库，含 zlib、Expat >=2.6.0 及 POSIX 描述符相对安全 I/O；Linux 为目标平台，Windows 不支持，不依赖外部程序。macOS 上已有维护回归不等于目标 Linux 当前版本验收。
- **artifact-lifecycle**：Python 3.10 及以上标准库（需 POSIX 锁与文件描述符操作能力）；零第三方包、零外部软件。
- **skill-development-standards**：Python 3.10 及以上标准库（需系统 IANA 时区支持以生成规范版本时间戳）；零第三方包依赖。
- 办公格式处理与生命周期纯 Python 主流程无需安装第三方 Python 包；WPS 二进制导出可选使用系统 LibreOffice。脚本使用 `-I -S -B` 隔离运行环境并避免写入字节码缓存；纯文本写作不需要 Python。
- XLSX 目录中保留的旧 legacy 工具可能依赖 pandas、openpyxl 或 LibreOffice，不属于推荐流程；不要据此为主流程安装依赖。

## 如何使用

将目标 skill 的**完整目录**交给支持 Agent Skills 的宿主加载；具体安装位置由宿主决定。也可以让代理先读取对应 `SKILL.md`，再根据任务读取其引用文档并调用脚本。只复制 `SKILL.md` 或单个脚本会丢失必要模块和模板。

业务交付输出只传裸文件名，由脚本固定写入宿主确认的任务根目录 `./`；拒绝 `./文件名`、子目录、绝对、父目录及 Windows 路径，不自动截取 basename。输入路径保持原契约，内部归档 `output/{questionId}` 和受控临时工作区不变；不把内部目录或临时文件作为交付物。业务输入、输出和临时文件不写入 skill 安装目录。脚本和资源以实际读取的 `SKILL.md` 所在目录定位；任务数据使用任务目录内的相对路径。下文的 `<skill-root>` 是占位符，须从对应 Skill 主入口 `SKILL.md` 的真实 `file_path`（相对读取路径先按宿主确认的读取基准解析）去掉文件名确定并整体替换，不能取参考文档目录；无法定位时停止，完整路径须正确引用。`python3` 必须指向满足版本要求的解释器。

### 银行专业写作

读取 [bank-writing](skills/bank-writing/SKILL.md) 完成构思、起草、改写、整合或审校；摘要、参考范文仿写和先摘要再仿写按需读取专项参考，普通写作不增加额外流程。范文与目标事实分开，摘要保留关键限定，严格篇幅实际计数，直接交付文本。内容末尾的 DOCX 导出询问放在正文外；用户确认或原请求已明确要求 DOCX 时，交给本项目 [docx](skills/docx/SKILL.md)，不重复确认。写作技能不生成文件，DOCX 不可用时保留正文并说明限制。

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

小型已知值清单使用 [主入口简单静态创建](skills/xlsx/SKILL.md#简单静态创建仅本入口)，单入口包含 stdin 规格、回读、归档与清理，无需预读多个参考。含公式、既有文件修改或复杂外观时，再按主入口路由加载所需契约。以下是低层兼容命令示例，写文件交付优先使用 runner；先确认输入可读、输出目录可写：

```console
python3 -I -S -B "<skill-root>/scripts/stdlib_xlsx.py" read ./input.xlsx
python3 -I -S -B "<skill-root>/scripts/stdlib_xlsx.py" create ./report.json report.xlsx
python3 -I -S -B "<skill-root>/scripts/stdlib_xlsx.py" edit ./input.xlsx ./patch.json edited.xlsx
```

输出必须是尚不存在的裸文件名，固定写入任务根目录。创建示例见 [calculation_example.json](skills/xlsx/assets/calculation_example.json)，其两处计算结果均应为 `226.86`。公式引擎只实现文档列出的子集，退出码 2 表示计算不完整，不能作为完整验收通过。

标准任务入口 `run_xlsx_task.py` 支持从配置与 stdin 接收完整规格，并在生成前定点检测主输出与归档副本同名，自动递增版本命名（如“_第2版.xlsx”）；业务成功或失败均立即清理本轮登记的中间文件和清单。复杂 JSON 建议通过 Python `json.dumps` 序列化后由 stdin 传入，详见 [复杂 JSON 调用](skills/xlsx/references/complex-json.md)。

### DOCX

修改已有 DOCX 的字体/字号按 [原文档格式编辑](skills/docx/references/structure-editing.md#已有文档字体修改) 使用 `run_docx_edit.py structure` 的 `format_run`，只传目标字段，保留原缩进、行距与样式继承。禁止提取文字或 compact 后重新 `create` 来冒充保格式编辑；表格内段落、页眉页脚、复杂范围等不支持的目标须明确报告并停止，不静默跳过或重建。低层 OOXML 候选必须经过 [受控格式核验与发布](skills/docx/references/ooxml-editing.md#字体修改与候选文件保护)，不能用基础结构或 `--original` 文本检查代替。回归覆盖与实际平台见 [DOCX 编辑保护](docs/skills/docx.md#已有文档字体修改与格式保留)。

运行分发使用专用 [打包器](tools/package_docx_runtime.py) 和 [显式清单](packaging/runtime-manifests/docx.json)，默认输出新的 `dist/runtime/docx-runtime-<version>.zip`，不覆盖旧包。全部业务代码、标准库安全边界、自然分页、归档与清理保持不变；测试、维护历史/加载审计和兼容测试样本留在源码、不进入运行包。构建与独立验收方法见 [DOCX 运行包边界](docs/skills/docx.md#运行包与源码维护边界)。

当前平台的 Bash 执行示例统一使用 `execute(command="…")`；每条表示一次工具调用，JSON 输入与 Python API 源码保留各自格式。历史维护记录中的终端命令仅供追溯，不作为业务调用示例。

普通生成按 [主入口简单创建流程](skills/docx/SKILL.md#简单创建仅本入口) 执行。先核对宿主实际工具 schema：执行工具的参数与传给 Python stdin 的文档 JSON 分开；要求 `command` 时传完整非空命令。缺参且确认尚未执行才修正一次，断流或状态不明不盲目重发。工具接入、流式拼接和 Qwen/DeepSeek 排查按需见 [工具调用故障](skills/docx/references/tool-calling.md)；Skill 不自动配置宿主门禁或模型参数，未验证真实模型成功率改善。

依赖、只读、工具间文件不可见、版本/长会话、断流及下载问题按需查 [环境与交付故障表](skills/docx/references/troubleshooting.md)。表中按当前标准库实现修正平台经验；不强制固定虚拟环境、不把 check 当下载验证、不在执行状态未知时直接重发。

标准新建直接调用 `run_docx_task.py create`；代码自动检查 Python/Expat、实际文件锁、安全文件操作及任务根目录写入，必要条件失败在生成前返回退出 1 和具体原因。模型无需单独调用 check 或判断自检日志。缺少归档配置、归档不可用、复制或收尾清理失败只提示；已验收且指纹未变的主文件仍成功返回退出 0。`check` 仅保留为可选诊断命令。

按照 [主入口](skills/docx/SKILL.md#简单创建仅本入口) 组织普通生成 JSON，复杂任务按需读取专项 reference，直接送入统一入口的 stdin，不另建临时 .py 或 document.json。未指定排版时显式使用 official；用户指定格式优先。命令示例：

```python
execute(command="python3 -I -S -B \"<skill-root>/scripts/run_docx_task.py\" create \"客户服务改进方案.docx\" <<'JSON'\n{\"theme\":\"official\",\"blocks\":[{\"type\":\"title\",\"text\":\"客户服务改进方案\"},{\"type\":\"paragraph\",\"text\":\"完善服务流程，提升客户服务质量。\"}]}\nJSON\n")
```

自动入口创建前登记自己的 worker.py/spec.json，强制验收及占位符检查，处理归档并等待 worker 退出后自动清理。最终 DOCX 保留；收尾完成后删除恢复 manifest。回执独占写入且不创建暂存硬链接，持久化完整归属指纹后由父进程核对；证明已提交后的 worker 清理异常只提示，回执或主文件变化仍拒绝。生成或编辑失败、子进程超时同样自动清理本轮临时文件及清单，不默认保留待重试材料；需重试时从原始输入重新发起任务。`task_status` 和 `cleanup.cleanup_complete` 与归档状态分开核对。自检还需要标准库 POSIX 锁与描述符能力，当前只验证 macOS。旧 create_docx.py 生成 CLI 已 deprecated，仅 stderr 提示迁移、JSON stdout 和退出码兼容；底层库与五类编辑等尚有用途的接口保留，不会自动清理调用方自建的 Python/JSON；自定义或多步编辑流程继续显式登记清单，见 [自动任务及清理边界](skills/docx/references/task-cleanup.md#自动新建任务入口)。对于包含中文、引号、换行或字面量 `$()` 的复杂输入，统一使用经过验证的 Python 字典与 `json.dumps` 模板经 stdin 传入，详见 [复杂 JSON 调用模板](skills/docx/references/complex-json.md)。

文档输入、主输出、归档、图片及 Skill 内部资源采用不同且明确的基准，统一见 [文件定位与读取规则](skills/docx/SKILL.md#文件定位与读取规则)。所有命令从宿主确认的任务工作目录执行，不切换到输入子目录或 Skill 安装目录。

归档目录 `./output/{questionId}/` 默认仅保存副本，不主动扫描或读取其文档内容；仅在用户明确要求时读取指定归档文件。必要的定点同名检查、输入保护与本轮副本验证仍保留，详见 [归档目录使用边界](skills/docx/references/delivery.md#归档目录使用边界)。

示例文件名须按实际任务替换：优先采用用户指定名称，否则按文档标题或主题命名，避免默认使用 `output.docx`；详见 [交付文件命名](skills/docx/references/generation-runtime.md#交付文件命名)。

新建命令只接收输出文件名，不带 `./` 或目录。新建主文件固定生成到任务工作目录 `./`，生成及所要求的验证成功后，Python 从 `ROUTER_CONFIG_VARIABLES` 的 `commonParam` 取得有效 `questionId`，复制一份到 `./output/{questionId}/`。标准新建与编辑入口自动定点检测同名主文件及归档副本，自动递增版本命名（如“_第2版.docx”），以实际返回的 `output` 路径交付。归档默认原子独占发布，同名保留历史副本；仅在明确授权的独立归档调用中传 `--replace-existing` 允许替换。字符串参数取值后自动 strip 两端空白，再进行原有校验；内部内容及非字符串类型保持。配置缺失或无效时跳过归档并记录固定原因码；归档失败保留主文件、主流程成功状态和退出码 0，仅通过 `archive_status=failed` 与 `warnings` 报告归档警告。JSON 的 `output` 指向主文件，`archive_output` 和 `archive_status` 说明副本路径和归档结果。宿主配置为外层参数列表：提取 `name="commonParam"` 项的 `value`，解析内层列表后提取 `questionId`；兼容原字典外层配置，内层支持列表或 JSON 编码列表；具体 ID、文件名规则及低层 API 边界见 [输出目录与文件名](skills/docx/references/generation-runtime.md#输出目录与文件名)。新建与五个编辑 CLI 均调用公开交付 API，再由同一个 `archive_outputs()` 自动归档。DOCX 所有新建临时文件直接平铺在宿主任务目录 `./`，不创建独立临时工作目录；生成辅助文件采用 `docx-task-<随机值>.*` 独占命名，包编辑和检查在内存处理。标准新建由 run_docx_task.py 包裹 `create_document_delivery()`，自定义交付或编辑使用对应 `*_delivery()`，接口见 [公开交付 API](skills/docx/references/delivery.md#公开交付-api)；底层 API 仅用于中间处理。归档处理必须执行，失败只警告；`--check` 不能代替归档。编辑和读取报告命令也只接收裸文件名；当轮其他最终交付物用显式清单归档，不扫描历史或临时文件，见 [统一归档](skills/docx/references/delivery.md)。

`--verify` 核对生成子集的内容与 XML 排版意图，不等同于页面视觉验收；字体显示、实际分页和页码仍需在文档阅读器中检查。

成功验收并核实文件后，在交付文件链接后简短提示“本次已按公文格式生成。还支持商务、学术排版，也可按需调整字体、字号和页边距。”用户指定主题或覆盖样式时按实际结果描述；提示不写入文档，用户要求只返回文件或不再提示时省略。完整规则见 [生成后格式引导](skills/docx/references/generation-runtime.md#生成后格式引导)。

读取大表格时，compact 默认只含前两行；全文需要原有纯文本读取，或 `--compact-json --tables rows` 按行续读。可选 `--max-output-bytes` 计真实 UTF-8 JSON 与末尾换行，返回源 SHA/参数绑定的 `next_cursor`；元数据或单块放不下时明确失败，不静默截断。旧 `next_block` 路径和新绑定游标的区别见 [读取契约](skills/docx/references/inspection.md#utf-8-预算与绑定续读)。预算不是 token 数，也不替代宿主完整回传检查。

### WPS 文字（.wps/.rtf）

独立入口见 [wps](skills/wps/SKILL.md)。简单读取与标题/正文/基础表格创建只需主入口；其他任务按操作直达参考，不递归预读全部指南。支持读取受限的 WPS 文字/RTF、内容重排编辑，以及生成 RTF 文档（可使用 `.wps` 后缀）和将 DOCX 导出为 WPS 兼容格式。生成结果默认使用 `.wps` 后缀，用户显式请求 `.rtf` 时保留；内容仍是 RTF 流，回执明确标注，不能称为金山原生 WPS 二进制。首次操作按任务类型分别自检：

```console
python3 -I -S -B "<skill-root>/scripts/read_wps.py" --check
python3 -I -S -B "<skill-root>/scripts/run_wps_task.py" check
python3 -I -S -B "<skill-root>/scripts/export_wps.py" check
```

读取正文可使用全量 JSON、纯文本或大纲/章节切片；可选 `--max-output-bytes` 为有界视图增加真实 UTF-8 JSON 字节预算，保留完整诊断及源绑定续读，详见 [有界读取](skills/wps/references/bounded-reading.md)；创建命令和共同边界见 WPS 主入口，编辑/导出按需加载其直达参考；仅创建支持 `--compact-receipt`，不省略现有 partial/警告。ET/ETT交给et，原生XLSX/XLSM交给xlsx，不自动转换。纯 Python 工作流无需第三方包；系统安装 LibreOffice 时可选择二进制导出后端。读取及导出退出码 3 表示 `status=partial`，须保留已读内容、降级原因和缺口诊断。输入按内容识别，`.doc` 内装 RTF/OOXML 仍可读取；创建/重排/纯 Python 导出只写 `.rtf/.wps`，`.doc` 必须实际取得 MS-DOC 字节。`auto` 仅对 `.wps` 允许明确标记的 RTF 回退；检查与回读不能证明视觉或原生格式完整性。

WPS新建默认公文参照当前DOCX：上下25.4mm、左右31.8mm，仿宋16pt／Times New Roman、固定28pt、首行2字、零段前段后；标题层级、页眉页脚和默认关闭页码同步。未明确要求新页时自然分页，保留用户显式配置。RTF与OOXML不是同一格式，不保证字体回退或跨阅读器分页一致；现有DOCX→RTF导出不解析完整样式继承，不作为统一排版的捷径。仓库对照回归见 [测试](tests/test_wps_docx_official_parity.py)，350项历史测试结果、缓存告警与未验证范围见 [排版对齐记录](tests/wps_docx_official_alignment_20261104.json)。后续 DOC 回读与格式匹配修复的377项 WPS、2项对照测试、8份已有输入回归及真实二进制导出未通过的范围，见 [导出契约验证](tests/wps_export_contract_validation_20261104.json)。默认 `.wps`、显式 `.rtf` 的命名与回读回归见 [后缀验证记录](tests/wps_default_suffix_validation_20261104.json)。

### WPS 表格（.et/.ett）

独立入口见 [et](skills/et/SKILL.md)。离线提取工作表、单元格保存值与公式缓存；使用实际文件结构识别 BIFF8 或 OOXML 子集。支持候选表头区域预览，还可对指定区域按物理行号排除行、筛选、排序、去重、做列统计和分组汇总，并返回统计质量报告，输出派生 JSON；不重算公式，不修改原表，也不转换办公文件。

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

独立入口见 [ofd](skills/ofd/SKILL.md)。先运行自检，再在任务目录读取相对输入路径；结果分别返回 XML 结构、存储文字与页面出现记录及其诊断，不恢复页面或语义阅读顺序：

```console
python3 -I -S -B "<skill-root>/scripts/read_ofd.py" --check
python3 -I -S -B "<skill-root>/scripts/read_ofd.py" ./input.ofd
```

仅在用户明确指定 OFD 并要求新建时，按 [基础生成说明](skills/ofd/references/generation.md) 提供显式 JSON 模型；支持 OFD 1.0 固定位置横排文字、线框、受限 PNG/JPEG 和简单网格表格，不自动排版、转换或编辑第三方文件。生成前另运行 `python3 -I -S -B "<skill-root>/scripts/generate_ofd.py" --check`；在已确认任务目录生成到尚不存在的 `.ofd` 文件名，失败不覆盖原件或已有目标。

可显式选择随包 [朱雀仿宋预览示例](skills/ofd/references/bundled-fonts.md)，或提供明确的自备字体路径／安装内私有字体引用；不扫描系统、不自动回退，缺字或不满足技术嵌入限制时停止。私有字体目录受 Git 忽略规则和专用打包器排除；字体权利状态不因提供文件而自动获得确认。当前结构与回读测试不代表 OFD 客户端视觉验收，Linux、Windows 和实机打开仍按各验证记录标为未验证。

字体与独立包历史验证见 [朱雀记录](tests/ofd_zhuque_validation_20261104.json)，其中源码路径以仓库根目录为基准；后续输入简化与私有字体引用分别见 [输入契约记录](tests/ofd_font_input_simplification_20261104.json) 和 [私有字体记录](tests/ofd_private_fonts_validation_20261104.json)。这些记录保留各自测试时的版本与结果，不视为全部针对当前源码重跑。

OFD 离线运行包由统一工具构建到 `dist/runtime/ofd-runtime-<version>.zip`；完整目录独立安装，不依赖相邻 wps/docx/xlsx。版本化 ZIP 按现有 `.gitignore` 规则不纳入版本控制，源目录和 README 是维护入口。

### PPTX

独立入口见 [pptx](skills/pptx/SKILL.md)。在满足其环境约束的解释器中先自检，再检查用户提供的相对文件路径：

```console
python3 -I -S -B "<skill-root>/scripts/pptx.py" check
python3 -I -S -B "<skill-root>/scripts/pptx.py" inspect ./input.pptx --summary
python3 -I -S -B "<skill-root>/scripts/pptx.py" inspect ./input.pptx --slides 1-3 --notes omit --max-output-bytes 12000
```

`inspect` 默认仍返回完整结果；选页、备注省略与字节预算不绕过未选页、关系和媒体的整包安全检查。分页只在完整幻灯片边界切分，继续时原样传回 `--cursor` 并重复原选项，单页过大明确失败。有限 PNG 零填充兼容需显式启用，结果保留 partial 并退出 3；不是对任意损坏文件的恢复。创建只接受声明的 SVG/配置子集并拒绝覆盖，不代表任意 PPTX 编辑或视觉验收。详细接口见 [PPTX 文档](docs/skills/pptx.md)。

### 交付物归档与受控清理（artifact-lifecycle）

独立入口见 [artifact-lifecycle](skills/artifact-lifecycle/SKILL.md)。用于在任务生成业务交付物后，统一进行临时文件登记、交付物原子归档与安全清理。

首次操作前运行自检，要求退出码为 0 且 JSON `status` 为 `environment-ok`、`task_writable` 为 true：

```console
python3 -I -S -B "<skill-root>/scripts/lifecycle.py" check
```

标准五步工作流：
1. **登记（plan）**：在创建临时文件前显式登记本轮中间文件路径（如 worker.py、spec.json），保护输入原件与历史交付物。
2. **生成并验收**：业务技能生成文档并执行验证；失败时等待生产进程退出，再按原清单以 `failed` 封存并调用清理，不把公共 CLI 当作自动捕获任意业务失败的 runner。
3. **原子归档（archive）**：对最终交付物执行归档，默认原子独占发布，同名保留历史副本；仅在明确授权时可使用 `--replace-existing`。
4. **封存与清理（seal & clean）**：seal 封存清单，clean 仅删除本轮已登记、归属和身份检查通过且可安全删除的临时文件；全部收尾后移除清单（`manifest_removed=true`）。未知、占用或身份变化项保留并报告 `cleanup_incomplete`。
5. **交付**：核对完整归档结果后，归档成功即提示“🎉 文件归档成功，请到云空间查看。 ☁️✨”，无需等待宿主云空间映射确认，不宣称已验证上传完成或展示时效。失败、部分成功、跳过、待交接和未知分别如实报告，不套用全部成功；业务与清理状态独立，主业务失败不称生成成功。隐藏归档路径、目录、ID及原始日志，保留真实主文件链接，不虚构下载。

### Skill 开发规范与检查器（skill-development-standards）

用于开发、审查与发布 Skill。主入口见 [Skill 开发规范](skills/skill-development-standards/SKILL.md)。

自检检查器入口可用性：

```console
python3 -I -S -B "<skill-root>/scripts/check_skill.py" --help
```

对指定 Skill 目录进行严格结构检查（检查 `<skill-root>` 资源路径规范、YAML 标头、依赖声明、文件名规范与链接有效性）：

```console
python3 -I -S -B "<skill-root>/scripts/check_skill.py" skills/docx --strict
```

运行检查器自身的单元测试：

```console
python3 -I -S -B -m unittest discover -s "<skill-root>/scripts/test" -p 'test_check_skill.py' -v
```

## 能力边界

- **PPTX**：受限 SVG 创建可编辑的原生对象，支持 PNG/JPEG、有限图表、表格和公式；可读取 Transitional PPTX 子集的文本、备注及对象计数并检查包结构。不提供任意既有 PPTX 编辑、完整格式导入或桌面显示验证；创建拒绝覆盖同名文件，归档显式交给公共生命周期技能。
- **XLSX**：原生编辑仅支持受控单元格操作，不支持任意结构编辑；不直接读取 WPS 专有格式，ET/ETT 使用 [et](skills/et/SKILL.md) 技能。其 BIFF8 子集、样本清单与验证记录见 ET Skill；公式读取的是保存缓存，不重算。
- **WPS**：读取受支持的 WPS 文字/RTF，并可生成 RTF 文档（可使用 `.wps` 后缀）、受控编辑及 DOCX 兼容导出；RTF 流不是金山原生 WPS 格式，不承诺无损编辑或页面视觉还原。未知私有记录、加密、损坏结构与资源错误按入口契约拒绝并报告。
- **ET**：支持 .et/.ett 的 BIFF8 与 OOXML 有限读取、表头区域预览，以及按物理行号排除行、区域筛选、排序、去重、列统计和分组汇总，并提供统计质量报告；保留源 partial 与缓存缺口，不自动识别表头、小计或总计，不认证业务正确性。处理的是保存值与派生 JSON，不重算公式、不写回原格式或转换办公文件，不将有限子集表述为完整格式支持。
- **DOCX**：支持受控的生成和编辑子集，不提供任意复杂文档的排版还原、页面预览或 PDF 导出。WPS 文字由独立 wps 技能提供，ET/ETT 由 et 技能提供。
- **交付物归档与同名保护**：归档默认原子独占发布，同名拒绝覆盖并保留历史副本；仅在明确授权的独立归档调用中传 `--replace-existing` 允许替换。
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
| .docx | 正文与显式结构 | 生成及受控编辑；标准入口定点防同名递增版本命名，失败立即清理中间文件 | 原生行为回归；不保证任意排版 |
| .dotx | ZIP/XML读取 | 同后缀定点编辑，不生成DOTX | 规则受控，不以改后缀转换 |
| .xlsx | 原生读取与分析 | XLSX生成、受控编辑/公式子集；标准任务入口自动防同名覆盖递增命名，失败立即清理中间文件 | 不等于完整Excel引擎 |
| .xlsm | 原生读取 | 保留宏包的受控编辑 | 不执行宏、不创建宏工程 |
| .csv/.tsv | 数据读取与交换 | 按已实现数据导出路径交换 | 无工作簿样式/结构保真 |
| .ofd | 1.0/1.1/1.2 XML 结构、存储文字、页资源与复合引用 | 仅显式请求时按 JSON 模型生成基础 OFD 1.0 多页文件；不编辑第三方 OFD | 固定文字、线框、受限图片与简单网格；字体覆盖及嵌入限制逐次检查；无自动排版或格式转换，不作全文/视觉或全规范认证 |
| .wps/.rtf/.doc | 按真实 RTF、MS-DOC 或 OOXML 结构读取有限子集 | 新建/重排及纯 Python 导出只写 .rtf/.wps；.doc 导出需 LibreOffice 实际生成并核对文件格式签名 | 输入读取不按后缀拒绝可靠内容；RTF 不是二进制 DOC 或金山原生 WPS；不承诺完整格式或视觉还原 |
| .et/.ett | et 按实际结构读取 BIFF8/OOXML 有限子集及保存值/公式缓存；支持表头区域预览、按物理行号排除行、区域筛选、排序、去重、统计、分组汇总及质量报告 | 只读预览或分析输出派生 JSON；不生成、编辑或转换办公文件 | 不自动识别表头、小计或总计；质量报告不认证业务正确性；公式默认排除，显式使用的缓存未证明最新；保留 partial，不写回原表 |
| 约定schema的.json | 配置、读取结果、分析衔接 | 按各入口固定schema写出 | 非任意JSON编辑器 |

仓库没有 PDF、旧式 PPT、ODT/ODS/ODP、RTF 专用技能或通用转换器；PPTX 的新建与读取仅覆盖上表明确子集。旧 DOC 的有限 MS-DOC 文字子集由 wps 读取；ET/ETT 的 BIFF8/OOXML 表格子集由独立 et 读取，不因此扩大为通用旧式 XLS 支持。外部同名软件生成的真实DOCX/XLSX仍按原生格式处理。后续优先补充有合法来源的真实样本与独立内容预期，再按实际办公需求规划 PDF、PPTX 扩展等能力；不要通过后缀或依赖推断已支持。

当前十技能正反与混合选路场景见 [当前人工边界期望](tests/skill_selection_current_cases.json)，它不是模型准确率。旧四技能的 [固定正反例](tests/skill_selection_cases.json) 与 20 项选择模拟保留为历史证据，不作为当前十技能或真实宿主自动触发验收。

OFD 政务原件相关验收证据已因既有 Library scope 拒绝撤回，不计为有效授权验收。此前本批 OFD ZIP 含该项派生元数据和说明，不应使用，保留但停止交付；仅从清理后的源码重建新的本地 OFD 包，不读取旧 ZIP，不上传。其他合成回归与允许样本证据单独保留。


## 自检与执行结果回传

自检成功仅证明相应环境或结构检查通过，不等于文件已生成或产物已验收。各入口保留原文本/JSON契约，环境检查增加明确范围标记 `check_kind=environment artifact_validation=not_run`。宿主需要把 stdout、stderr、真实退出码及调用标识实际送回模型，不能只保存日志或丢弃非零退出的 stdout。

仓库维护与接入可用 [run_skill_check.py](tools/run_skill_check.py) 执行固定自检入口并输出独立回传 JSON；[入口清单、用法及边界](docs/check-execution.md)列出全部 Skill 和未验证范围。该适配器不随独立 Skill 发布，不安装或修改项目专属 execute 工具；原 Skill 命令仍可直接执行，宿主按原生接口回传。

适配器登记的 9 个环境自检入口包括（不是全部 Skill 自检的全集）：
- `docx-read`：`docx/scripts/read_docx.py --check`
- `docx-task`：`docx/scripts/run_docx_task.py check`
- `docx-create`：`docx/scripts/create_docx.py --check`
- `docx-archive`：`docx/scripts/archive_deliverables.py --check`
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

标准DOCX新建/编辑和XLSX任务runner在生成前检查主路径及归档同名，自动递增“_第2版”等后缀；归档默认拒绝覆盖，明确授权后可单独归档并使用--replace-existing。并发任务使用独立任务目录。发布包、测试命令和未验证范围以本轮仓库验证记录为准；旧ZIP保留，不因维护而删除。

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

DOCX 标准新建/编辑与 XLSX 标准任务入口在生成前定点检查主路径和归档同名，保留主题并选择“_第2版”“_第3版”等可用名称；以返回的实际 output 交付。归档默认原子独占发布，同名保留历史副本；只有明确授权的独立归档调用可传 `--replace-existing`。失败仍清理本轮中间文件，删除受阻则报告 `cleanup_incomplete`。旧直接入口不自动改名，主输出行为保留；共享归档默认保护同步到公共生命周期包。

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
