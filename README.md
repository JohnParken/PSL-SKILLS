# PSL-SKILLS

面向银行材料写作、Word 文档处理、电子表格读写计算、WPS 家族（.wps/.et/.ett）只读内容识别、OFD 1.0 版式文档只读提取、交付物原子归档与受控清理生命周期，以及 Skill 开发规范与质量检查器的 Agent Skills 集合。每个 Skill 包含独立的 `SKILL.md`，并按需附带脚本、参考文档和资源，可按完整目录加载或独立分发。

## 收录内容

| Skill | 来源 | 版本 | 主要能力 |
| --- | --- | --- | --- |
| [银行专业写作（bank-writing）](skills/bank-writing/SKILL.md) | 用户附件 `bank-material-writing-enhanced(2).zip` | 20261029.2126.3558 | 银行综合材料起草、改写与审校；长文、会议及多源摘要，范文结构与文风仿写，先摘要再仿写；仅交付文本，获授权后规范交接由 docx 技能生成文件 |
| [docx](skills/docx/SKILL.md) | `skills/skills/docx` | 20261030.2134.3052 | DOCX 生成、DOCX/DOTX 读取与受控编辑；支持段落分页、左右/悬挂缩进、制表位及普通段落制表符，短诗等简短内容默认连续排版，提供字号/行距及段落宽度风险提示，静态验收不代表视觉分页验证；标准入口自动检测同名并递增版本命名，管理临时文件并收尾；保留归档保护与复杂 JSON 输入；不生成 DOTX；.wps/.et/.ett 使用 wps 技能 |
| [xlsx](skills/xlsx/SKILL.md) | `minimax-skills/skills/minimax-xlsx` | 20261029.2203.2365 | XLSX/XLSM 原生读取、XLSX 创建和受控编辑、CSV/TSV 与分析；标准任务入口自动检测同名并递增版本命名，失败立即清理中间文件；支持公式子集计算与复杂 JSON 模板；可消费 wps 提取的受支持电子表格 JSON；不支持直接读取 WPS 专有格式（.wps/.et/.ett），请使用 wps 技能 |
| [wps](skills/wps/SKILL.md) | 本地限定实现；迁移既有 WPS 家族读取 | 20261029.2012.5682 | .wps/.et/.ett 内容识别、MS-DOC/Strict 及 Transitional OOXML Word/OOXML 电子表格/BIFF8 子集只读；支持 MS-XLS 标准 DXF（0x088D）差异格式结构校验与属性保留；纯表格/文字分流，内联文字、日期与公式缓存提取，保留 partial 诊断；不生成、编辑、转换或重算 |
| [ofd](skills/ofd/SKILL.md) | 独立标准库限定实现 | 20261029.2012.5682 | OFD 1.0 字面文字与元数据只读提取；保留页码、原始坐标与来源存储顺序；公开生产工具样例通过；政务原件验收未完成；不渲染、OCR、验签或转换 |
| [artifact-lifecycle](skills/artifact-lifecycle/SKILL.md) | DOCX已验证模块的显式自包含提取 | 20261029.2203.2365 | 本轮临时文件登记、最终交付物清单归档（默认原子独占发布防同名覆盖，支持显式 `--replace-existing` 替换）、封存与受控清理；失败立即清理中间文件与清单；不生成或验收业务内容、不清理历史发布 ZIP |
| [skill-development-standards](skills/skill-development-standards/SKILL.md) | 本地规范定义与质量控制工具 | 20261029.2024.0082 | Skill 新建、修改、导入、迁移、审查与发布质量规范；`<skill-root>` 资源路径与文档契约检查器、结构回归测试与环境自检基准 |

## 目录结构

```text
PSL-SKILLS/
├── AGENTS.md
├── .agents/skills/skill-development-standards  # 指向规范目录的相对链接
├── README.md
├── docs/
│   ├── check-execution.md         # 自检结果与执行回传入口契约
│   └── linux-release-acceptance.md # 云端 Linux 生产发布验收清单与方案
├── tools/
│   └── run_skill_check.py         # 仓库级自检适配器（执行 8 个环境入口并输出分离通道 JSON）
├── tests/                         # 单元测试、自检契约回归、生命周期一致性与版本验收记录
├── licenses/
│   └── minimax-skills-MIT.txt
└── skills/
    ├── skill-development-standards/ # 开发规范、<skill-root> 检查器及其测试
    ├── bank-writing/              # 纯文本写作与七份分类参考
    ├── docx/
    │   ├── SKILL.md
    │   ├── requirements.txt
    │   ├── scripts/               # 生成、读取、编辑、递增命名与收尾工具
    │   ├── references/            # 文档结构、生成、复杂 JSON、故障排查和编辑说明
    │   └── assets/                # 批注 XML 资源
    ├── xlsx/
    │   ├── SKILL.md
    │   ├── requirements.txt
    │   ├── scripts/               # 标准库入口、分析模块、复杂模板与测试
    │   ├── references/            # 输入格式、能力边界、公式计算与生命周期说明
    │   └── assets/                # XLSX 模板和示例数据
    ├── wps/
    │   ├── SKILL.md
    │   ├── requirements.txt
    │   ├── scripts/               # 独立正文/表格读取、CFB、BIFF8、DXF 与合成回归
    │   └── references/            # 固定 FIB 范围、DXF 记录、来源与验证限制
    ├── ofd/                       # OFD 1.0 文本与元数据安全只读提取
    └── artifact-lifecycle/        # 交付物原子归档与受控临时文件清理公共技能
        ├── SKILL.md
        ├── requirements.txt
        ├── scripts/               # 生命周期 CLI、原子归档、受控清理与测试
        └── references/            # 契约规则与来源一致性说明
```

## 环境要求

- **bank-writing**：主要面向云端 Linux 容器的对话式文本写作，开发可用 macOS、Windows 或 Linux；包内无脚本、第三方包或办公软件依赖。按任务读取参考，精确长度用宿主已有能力只读计数，不能核验时如实说明。
- **docx**：Python 3.10 及以上标准库（包含 zlib，Expat >=2.6.0，需支持 POSIX 锁与文件描述符操作）；零第三方包依赖。
- **xlsx**：Python 3.9 及以上；维护中的主流程与生命周期 runner 仅依赖标准库。
- **wps**：Python 3.9 及以上，全部 OOXML 分支另需 Expat >=2.6；仅标准库，独立包内读取，不转换文件。
- **ofd**：仅 Python >=3.10 自带标准库（含 zlib、Expat >=2.6.0）；零第三方包、零外部软件。自带能力缺失则诊断并停止，不额外安装组件。
- **artifact-lifecycle**：Python 3.10 及以上标准库（需 POSIX 锁与文件描述符操作能力）；零第三方包、零外部软件。
- **skill-development-standards**：Python 3.10 及以上标准库（需系统 IANA 时区支持以生成规范版本时间戳）；零第三方包依赖。
- 上述办公格式处理与生命周期主流程均无需安装第三方 Python 包或办公软件，使用 `-I -S -B` 隔离运行环境并避免写入字节码缓存；纯文本写作不需要 Python。
- XLSX 目录中保留的旧 legacy 工具可能依赖 pandas、openpyxl 或 LibreOffice，不属于推荐流程；不要据此为主流程安装依赖。

## 如何使用

将目标 skill 的**完整目录**交给支持 Agent Skills 的宿主加载；具体安装位置由宿主决定。也可以让代理先读取对应 `SKILL.md`，再根据任务读取其引用文档并调用脚本。只复制 `SKILL.md` 或单个脚本会丢失必要模块和模板。

业务输入、输出和临时文件应放在宿主确认的任务工作目录中，不写入 skill 安装目录。脚本和资源以实际读取的 `SKILL.md` 所在目录定位；任务数据使用任务目录内的相对路径。下文的 `<skill-root>` 是占位符，须从对应 Skill 主入口 `SKILL.md` 的真实 `file_path`（相对读取路径先按宿主确认的读取基准解析）去掉文件名确定并整体替换，不能取参考文档目录；无法定位时停止，完整路径须正确引用。`python3` 必须指向满足版本要求的解释器。

### 银行专业写作

读取 [bank-writing](skills/bank-writing/SKILL.md) 完成构思、起草、改写、整合或审校；摘要、参考范文仿写和先摘要再仿写按需读取专项参考，普通写作不增加额外流程。范文与目标事实分开，摘要保留关键限定，严格篇幅实际计数，直接交付文本。内容末尾的 DOCX 导出询问放在正文外；用户确认或原请求已明确要求 DOCX 时，交给本项目 [docx](skills/docx/SKILL.md)，不重复确认。写作技能不生成文件，DOCX 不可用时保留正文并说明限制。

前次多轮编辑升级的同素材新旧执行、匿名评阅和读者检查见[写作评测记录](tests/bank_writing_upgrade_evaluation/summary.json)，结果包含持平与退步。摘要与仿写三路径的完整输入输出、计数和内容检查、独立评阅及本地分发校验见[专项评测记录](tests/bank_writing_summary_imitation_evaluation/summary.json)。两组记录均限于虚构场景，不等同于宿主自动触发验收；本次在 macOS 实测，云端 Linux 与 Windows 未验证。

当前版本仅修订能力描述，正文与参考资料沿用上述已评测版本；名称与描述的独立选择模拟见[描述评测记录](tests/bank_writing_description_evaluation/summary.json)，不代表宿主自动注册或触发验收。

### XLSX

首次操作前运行自检，要求退出码为 0 且 JSON 中 `status` 为 `ready`：

```console
python3 -I -S -B "<skill-root>/scripts/stdlib_xlsx.py" check
```

按 [计算与文件契约](skills/xlsx/references/python-calculation.md) 准备输入，确认输入可读、输出目录可写后，在任务目录执行：

```console
python3 -I -S -B "<skill-root>/scripts/stdlib_xlsx.py" read ./input.xlsx
python3 -I -S -B "<skill-root>/scripts/stdlib_xlsx.py" create ./report.json ./report.xlsx
python3 -I -S -B "<skill-root>/scripts/stdlib_xlsx.py" edit ./input.xlsx ./patch.json ./edited.xlsx
```

输出路径必须尚不存在。创建示例见 [calculation_example.json](skills/xlsx/assets/calculation_example.json)，其两处计算结果均应为 `226.86`。公式引擎只实现文档列出的子集，退出码 2 表示计算不完整，不能作为完整验收通过。

标准任务入口 `run_xlsx_task.py` 支持从配置与 stdin 接收完整规格，并在生成前定点检测主输出与归档副本同名，自动递增版本命名（如“_第2版.xlsx”）；业务成功或失败均立即清理本轮登记的中间文件和清单。复杂 JSON 建议通过 Python `json.dumps` 序列化后由 stdin 传入，详见 [复杂 JSON 调用](skills/xlsx/references/complex-json.md)。

### DOCX

当前平台的 Bash 执行示例统一使用 `execute(command="…")`；每条表示一次工具调用，JSON 输入与 Python API 源码保留各自格式。历史维护记录中的终端命令仅供追溯，不作为业务调用示例。

普通生成按 [主入口四步流程](skills/docx/SKILL.md#新建与受控编辑) 执行。先核对宿主实际工具 schema：执行工具的参数与传给 Python stdin 的文档 JSON 分开；要求 `command` 时传完整非空命令。缺参且确认尚未执行才修正一次，断流或状态不明不盲目重发。工具接入、流式拼接和 Qwen/DeepSeek 排查按需见 [工具调用故障](skills/docx/references/tool-calling.md)；Skill 不自动配置宿主门禁或模型参数，未验证真实模型成功率改善。

依赖、只读、工具间文件不可见、版本/长会话、断流及下载问题按需查 [环境与交付故障表](skills/docx/references/troubleshooting.md)。表中按当前标准库实现修正平台经验；不强制固定虚拟环境、不把 check 当下载验证、不在执行状态未知时直接重发。

标准新建直接调用 `run_docx_task.py create`；代码自动检查 Python/Expat、实际文件锁、安全文件操作及任务根目录写入，必要条件失败在生成前返回退出 1 和具体原因。模型无需单独调用 check 或判断自检日志。缺少归档配置、归档不可用、复制或收尾清理失败只提示；已验收且指纹未变的主文件仍成功返回退出 0。`check` 仅保留为可选诊断命令。

按照 [生成说明](skills/docx/references/generation.md) 组织 JSON，直接送入统一入口的 stdin，不另建临时 .py 或 document.json。未指定排版时显式使用 official；用户指定格式优先。命令示例：

```python
execute(command="python3 -I -S -B \"<skill-root>/scripts/run_docx_task.py\" create \"客户服务改进方案.docx\" <<'JSON'\n{\"theme\":\"official\",\"blocks\":[{\"type\":\"title\",\"text\":\"客户服务改进方案\"},{\"type\":\"paragraph\",\"text\":\"完善服务流程，提升客户服务质量。\"}]}\nJSON\n")
```

自动入口创建前登记自己的 worker.py/spec.json，强制验收及占位符检查，处理归档并等待 worker 退出后自动清理。最终 DOCX 保留；收尾完成后删除恢复 manifest。回执独占写入且不创建暂存硬链接，持久化完整归属指纹后由父进程核对；证明已提交后的 worker 清理异常只提示，回执或主文件变化仍拒绝。生成或编辑失败、子进程超时同样自动清理本轮临时文件及清单，不默认保留待重试材料；需重试时从原始输入重新发起任务。`task_status` 和 `cleanup.cleanup_complete` 与归档状态分开核对。自检还需要标准库 POSIX 锁与描述符能力，当前只验证 macOS。旧 create_docx.py 生成 CLI 已 deprecated，仅 stderr 提示迁移、JSON stdout 和退出码兼容；底层库与五类编辑等尚有用途的接口保留，不会自动清理调用方自建的 Python/JSON；自定义或多步编辑流程继续显式登记清单，见 [自动任务及清理边界](skills/docx/references/task-cleanup.md#自动新建任务入口)。对于包含中文、引号、换行或字面量 `$()` 的复杂输入，统一使用经过验证的 Python 字典与 `json.dumps` 模板经 stdin 传入，详见 [复杂 JSON 调用模板](skills/docx/references/complex-json.md)。

文档输入、主输出、归档、图片及 Skill 内部资源采用不同且明确的基准，统一见 [文件定位与读取规则](skills/docx/SKILL.md#文件定位与读取规则)。所有命令从宿主确认的任务工作目录执行，不切换到输入子目录或 Skill 安装目录。

归档目录 `./output/{questionId}/` 默认仅保存副本，不主动扫描或读取其文档内容；仅在用户明确要求时读取指定归档文件。必要的定点同名检查、输入保护与本轮副本验证仍保留，详见 [归档目录使用边界](skills/docx/references/delivery.md#归档目录使用边界)。

示例文件名须按实际任务替换：优先采用用户指定名称，否则按文档标题或主题命名，避免默认使用 `output.docx`；详见 [交付文件命名](skills/docx/references/generation.md#交付文件命名)。

新建命令只接收输出文件名，不带 `./` 或目录。新建主文件固定生成到任务工作目录 `./`，生成及所要求的验证成功后，Python 从 `ROUTER_CONFIG_VARIABLES` 的 `commonParam` 取得有效 `questionId`，复制一份到 `./output/{questionId}/`。标准新建与编辑入口自动定点检测同名主文件及归档副本，自动递增版本命名（如“_第2版.docx”），以实际返回的 `output` 路径交付。归档默认原子独占发布，同名保留历史副本；仅在明确授权的独立归档调用中传 `--replace-existing` 允许替换。字符串参数取值后自动 strip 两端空白，再进行原有校验；内部内容及非字符串类型保持。配置缺失或无效时跳过归档并记录固定原因码；归档失败保留主文件、主流程成功状态和退出码 0，仅通过 `archive_status=failed` 与 `warnings` 报告归档警告。JSON 的 `output` 指向主文件，`archive_output` 和 `archive_status` 说明副本路径和归档结果。宿主配置为外层参数列表：提取 `name="commonParam"` 项的 `value`，解析内层列表后提取 `questionId`；兼容原字典外层配置，内层支持列表或 JSON 编码列表；具体 ID、文件名规则及低层 API 边界见 [输出目录与文件名](skills/docx/references/generation.md#输出目录与文件名)。新建与五个编辑 CLI 均调用公开交付 API，再由同一个 `archive_outputs()` 自动归档。DOCX 所有新建临时文件直接平铺在宿主任务目录 `./`，不创建独立临时工作目录；生成辅助文件采用 `docx-task-<随机值>.*` 独占命名，包编辑和检查在内存处理。标准新建由 run_docx_task.py 包裹 `create_document_delivery()`，自定义交付或编辑使用对应 `*_delivery()`，接口见 [公开交付 API](skills/docx/references/delivery.md#公开交付-api)；底层 API 仅用于中间处理。归档处理必须执行，失败只警告；`--check` 不能代替归档。编辑命令仍接收相对输出路径；当轮其他最终交付物用显式清单归档，不扫描历史或临时文件，见 [统一归档](skills/docx/references/delivery.md)。

`--verify` 核对生成子集的内容与 XML 排版意图，不等同于页面视觉验收；字体显示、实际分页和页码仍需在文档阅读器中检查。

成功验收并核实文件后，在交付文件链接后简短提示“本次已按公文格式生成。还支持商务、学术排版，也可按需调整字体、字号和页边距。”用户指定主题或覆盖样式时按实际结果描述；提示不写入文档，用户要求只返回文件或不再提示时省略。完整规则见 [生成后格式引导](skills/docx/references/generation.md#生成后格式引导)。

### WPS（.wps/.et/.ett）

独立入口见 [wps](skills/wps/SKILL.md)。离线读取 WPS 文字（.wps）正文及有限表格结构，以及 WPS 表格（.et/.ett）保存值与公式缓存；按实际文件结构识别 MS-DOC、OOXML Word、OOXML 电子表格或 BIFF8 子集，支持 MS-XLS 标准 DXF（`0x088D`）差异格式结构校验与属性保留。

首次操作前运行自检，要求退出码为 0 且输出包含 `environment-ok`：

```console
python3 -I -S -B "<skill-root>/scripts/read_wps.py" --check
```

在任务目录读取电子表格或文字文档：

```console
# 读取 ET/ETT 电子表格保存值与公式缓存
python3 -I -S -B "<skill-root>/scripts/read_wps.py" ./input.et

# 读取 WPS 文字正文（可加 --format text 仅输出纯文本，加 --include-related 读取附加区域）
python3 -I -S -B "<skill-root>/scripts/read_wps.py" ./input.wps
```

退出码 0（声明范围内完整成功）与 3（`status=partial`，保留已读数据并报告 `issues` 与 `reading_assessment` 缺口诊断）均输出完整 JSON；退出码 1 表示读取失败，退出码 2 表示参数错误。不重算公式、不修改文件、不转换格式。

### OFD

独立入口见 [ofd](skills/ofd/SKILL.md)。先运行自检，再在任务目录读取相对输入路径；结果保留文字来源和诊断，不恢复页面或语义阅读顺序：

```console
python3 -I -S -B "<skill-root>/scripts/read_ofd.py" --check
python3 -I -S -B "<skill-root>/scripts/read_ofd.py" ./input.ofd
```

OFD 离线分发包沿用仓库现有位置 `skills/ofd-<version>.zip`；完整目录独立安装，不依赖相邻 wps/docx/xlsx。版本化 ZIP 按现有 `.gitignore` 规则不纳入版本控制，源目录和 README 是维护入口。

### 交付物归档与受控清理（artifact-lifecycle）

独立入口见 [artifact-lifecycle](skills/artifact-lifecycle/SKILL.md)。用于在任务生成业务交付物后，统一进行临时文件登记、交付物原子归档与安全清理。

首次操作前运行自检，要求退出码为 0 且 JSON `status` 为 `environment-ok`、`task_writable` 为 true：

```console
python3 -I -S -B "<skill-root>/scripts/lifecycle.py" check
```

标准五步工作流：
1. **登记（plan）**：在创建临时文件前显式登记本轮中间文件路径（如 worker.py、spec.json），保护输入原件与历史交付物。
2. **生成并验收**：业务技能生成文档并执行验证；失败时标记 `status=failed` 并自动清理已登记文件。
3. **原子归档（archive）**：对最终交付物执行归档，默认原子独占发布，同名保留历史副本；仅在明确授权时可使用 `--replace-existing`。
4. **封存与清理（seal & clean）**：seal 封存清单，clean 彻底删除登记的临时文件并最终移除清单文件（`manifest_removed=true`）。
5. **交付**：向用户交付最终主文件链接，并输出对应云空间归档提示。

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
python3 -I -S -B -m unittest discover -s "<skill-root>/scripts" -p 'test_check_skill.py' -v
```

## 能力边界

- **XLSX**：原生编辑仅支持受控单元格操作，不支持任意结构编辑；ET/ETT 的 CFB/BIFF8 引擎、样本清单与回归已迁至 wps。docx/xlsx 不支持直接读取 WPS 专有格式（.wps/.et/.ett），请使用 wps 技能；读取入口明确拒绝，不自动调用。四个既有 ET/ETT 真实样本可在显式兼容/opaque 策略下提取有限子集；详见 [真实样本记录](skills/wps/references/et-real-validation.md)。这不是通用 WPS 格式支持。
- **WPS**：支持根据实际结构识别 MS-DOC、OOXML Word、OOXML 电子表格或 BIFF8 有限子集；对 MS-XLS 标准 DXF（`0x088D`）差异格式进行结构校验与属性保留，但格式不应用到保存值显示；未知私有记录、加密、损坏结构与资源错误明确拒绝并停止，不跳过未知记录后宣称成功。
- **DOCX**：支持受控的生成和编辑子集，不提供任意复杂文档的排版还原、页面预览或 PDF 导出。WPS 家族读取能力由独立 wps 技能提供，不再由 docx 提供。
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

本项目主流程保持纯 Python 标准库。每次更新内容时，必须人工核对 description、正文、引用说明、README、依赖和实际 CLI 行为是否一致，按需同步优化描述；结构检查不能替代能力与证据核对。

仓库维护约定见 [AGENTS.md](AGENTS.md)。新建、修改、导入、迁移或发布 skill 前，先读取 [Skill 开发规范](skills/skill-development-standards/SKILL.md)。规范附带标准库检查器和测试；`.agents/skills/skill-development-standards` 通过相对符号链接指向同一份规范。

规范及检查器从本地 `skills` 项目迁移；本项目已增加时间戳版本校验，并为规范补充了“执行环境约束与首步自检”章节。使用 Python 3.10 及以上，从仓库根目录检查本次维护的目标，例如：

```console
python3 -I -S -B skills/skill-development-standards/scripts/check_skill.py skills/docx --strict
python3 -I -S -B -m unittest discover -s skills/skill-development-standards/scripts -p 'test_check_skill.py' -v
```

在仓库根目录运行 XLSX 自带测试：

```console
python3 -I -S -B -m unittest discover -s skills/xlsx/scripts -p 'test_*.py' -v
```

DOCX 已包含标准库固定预期回归，覆盖生成、编辑、ZIP/XML 拒绝边界和格式路由。从仓库根目录使用满足 DOCX 环境要求的解释器执行：

```console
python3 -I -S -B -m unittest discover -s skills/docx/scripts -p 'test_*.py' -v
```

原生行为测试要求 Python >=3.10、Expat >=2.6.0；跳过测试不算行为验收。范围、命令和证据见 [DOCX 维护测试与历史验证记录](skills/docx/references/maintenance-validation.md)。环境自检、固定预期回归和视觉验收覆盖不同内容，当前流程不调用办公软件做视觉验证。

## 迁移来源与许可

本次于 2026-10-23 从本地相邻项目复制，首次迁移保留原目录名称及 skill 内部文件内容，排除系统缓存文件；随后将本仓库的 `minimax-xlsx` 更名为 `xlsx`，同步更新名称和路径引用，脚本及资源内容不变。复制的是来源项目**当时的工作区内容，包含未提交修改**，不是对应提交的纯净快照。来源仓库当时的 HEAD 仅供追溯：

| 本地来源项目 | HEAD |
| --- | --- |
| `minimax-skills` | `c76df1161a0ae889604da6db9d1ecb90bf12f687` |
| `skills` | `0f0895690dfde8c203e6c2f5dbbac5a2615faf5e` |

`xlsx` 声明采用 MIT，来源项目的许可文本保存在 [licenses/minimax-skills-MIT.txt](licenses/minimax-skills-MIT.txt)。`skills` 来源项目的 README 将 DOCX 等文档 skills 描述为 source-available；本次本地 DOCX 目录没有独立许可证文件，未为其补写或推定新的许可证。本仓库不以单一许可证覆盖两个来源。

## 当前办公格式范围

| 后缀/输入 | 读取 | 生成或编辑 | 验证与边界 |
|---|---|---|---|
| .docx | 正文与显式结构 | 生成及受控编辑；标准入口定点防同名递增版本命名，失败立即清理中间文件 | 原生行为回归；不保证任意排版 |
| .dotx | ZIP/XML读取 | 同后缀定点编辑，不生成DOTX | 规则受控，不以改后缀转换 |
| .xlsx | 原生读取与分析 | XLSX生成、受控编辑/公式子集；标准任务入口自动防同名覆盖递增命名，失败立即清理中间文件 | 不等于完整Excel引擎 |
| .xlsm | 原生读取 | 保留宏包的受控编辑 | 不执行宏、不创建宏工程 |
| .csv/.tsv | 数据读取与交换 | 按已实现数据导出路径交换 | 无工作簿样式/结构保真 |
| .ofd | ofd 提取 1.0 字面文字与元数据 | 不支持 | 合成回归及生产工具原始样例；政务 1.1 正确拒绝；无全文/视觉或全规范认证 |
| .wps/.et/.ett | wps按实际结构读取有限子集；支持MS-XLS标准DXF（0x088D）差异格式校验与属性保留 | 不生成、不编辑、不转换 | 五真实原件仅partial；新增容器主要合成验证 |
| 约定schema的.json | 配置、读取结果、分析衔接 | 按各入口固定schema写出 | 非任意JSON编辑器 |

仓库没有PDF、PPT/PPTX、ODT/ODS/ODP、RTF专用技能或通用转换器。旧DOC/XLS内部结构只能由wps识别其有限MS-DOC/BIFF子集，不能称完整支持。外部同名软件生成的真实DOCX/XLSX仍按原生格式处理。推荐下一步先补有合法来源的真实样本与独立内容预期，再按实际办公需求单独规划PDF/PPT等技能；不要通过后缀或依赖推断已支持。

本批四技能任务选择的固定正反例见 `tests/skill_selection_cases.json`。20 项人工边界核对及未见标准答案的独立选择模拟均匹配预期；这是有限用例的选择评测，不是真实宿主自动触发验收。

OFD 政务原件相关验收证据已因既有 Library scope 拒绝撤回，不计为有效授权验收。此前本批 OFD ZIP 含该项派生元数据和说明，不应使用，保留但停止交付；仅从清理后的源码重建新的本地 OFD 包，不读取旧 ZIP，不上传。其他合成回归与允许样本证据单独保留。


## 自检与执行结果回传

自检成功仅证明相应环境或结构检查通过，不等于文件已生成或产物已验收。各入口保留原文本/JSON契约，环境检查增加明确范围标记 `check_kind=environment artifact_validation=not_run`。宿主需要把 stdout、stderr、真实退出码及调用标识实际送回模型，不能只保存日志或丢弃非零退出的 stdout。

仓库维护与接入可用 [run_skill_check.py](tools/run_skill_check.py) 执行固定自检入口并输出独立回传 JSON；[入口清单、用法及边界](docs/check-execution.md)列出全部 Skill 和未验证范围。该适配器不随独立 Skill 发布，不安装或修改项目专属 execute 工具；原 Skill 命令仍可直接执行，宿主按原生接口回传。

适配器支持的 8 个环境自检入口包括：
- `docx-read`：`docx/scripts/read_docx.py --check`
- `docx-task`：`docx/scripts/run_docx_task.py check`
- `docx-create`：`docx/scripts/create_docx.py --check`
- `docx-archive`：`docx/scripts/archive_deliverables.py --check`
- `wps`：`wps/scripts/read_wps.py --check`
- `ofd`：`ofd/scripts/read_ofd.py --check`
- `xlsx`：`xlsx/scripts/stdlib_xlsx.py check`
- `artifact-lifecycle`：`artifact-lifecycle/scripts/lifecycle.py check`
以及 1 个结构检查入口 `skill-structure`（调用 `check_skill.py --strict`）。

## 云端 Linux 生产发布验收规范

针对云端 Linux 生产环境上线，仓库在 [docs/linux-release-acceptance.md](docs/linux-release-acceptance.md) 制定了生产级全流程验收清单与方案，明确：
- 固定发布包 SHA-256 哈希校验与完整解包校验。
- 采用非 root 运行用户、安装目录只读、任务目录可写以及 `/tmp` 限制等真实生产环境约束。
- 业务成功、业务失败、序列化失败、语法错误、归档受阻、删除受阻等 9 类案例的独立验证标准。
- 外部控制器强制中断（SIGKILL、进程组终止、整容器强停与宿主重启）下的文件保护与恢复验收，明确被 SIGKILL 的进程无法自行执行收尾，必须由外部恢复控制器核验清单与回执后完成清理，避免使用模糊进程名或破坏交付物。
- 宿主集成 10 项能力验证（空参数/重复 call_id、双通道回传、退出码保留、结果截断标记、`/large_tool_results/` 专有读取、脱敏保护及云空间真实状态反馈）。

## 三技能上线修复（2026-10-29）

本次版本为20261029.2043.2946。bank-writing明确素材指令边界及按需导出，保持纯文本交付；DOCX明确简单占位符和已支持的结构验证范围；XLSX明确公式子集与编辑边界。三个description均为100–150字符，人工核对适用/不适用场景；未声明已完成新版模型触发评测。

XLSX十个受支持写文件CLI在业务处理前检查输入、配置和输出路径；DOCX读取/兼容生成与五类编辑CLI检查任务文件路径，公开生成交付及标准runner同时检查图片资源路径。拒绝绝对路径、父目录、符号链接组件及输出输入别名。低层库仍保留显式路径能力，调用方负责任务隔离；不是同权限恶意并发写者沙箱。

路由配置诊断默认关闭，开启时只输出是否配置和脱敏标记。XLSX包装入口返回子进程状态、真实退出码及明确的通道省略记录，不回显任意stderr或无效JSON正文。清理仅覆盖登记项；DOCX新建、受控编辑和XLSX配置runner在业务验收后删除本轮中间文件及清单，归档失败/跳过也收尾。保留材料可能含业务数据，归属核实及宿主留存策略不可省略。

标准DOCX新建/编辑和XLSX任务runner在生成前检查主路径及归档同名，自动递增“_第2版”等后缀；归档默认拒绝覆盖，明确授权后可单独归档并使用--replace-existing。并发任务使用独立任务目录。发布包、测试命令和未验证范围以本轮仓库验证记录为准；旧ZIP保留，不因维护而删除。

本轮验证记录：[上线修复与分发包验收](tests/production_readiness_20261029_20432946.json)。源码回归共579项通过；运行平台为macOS/Python3.12.14，不能替代目标Linux生产宿主验收。

## 最终收尾规则（20261029.2053.4764）

任务结束时必须清理本轮全部中间文件，包括脚本、配置、回读结果、日志、暂存、锁、回执和清单；仅将当前与历史交付物作为任务生成的留存文件。用户原件与其他任务文件仍受保护，不属于可删除中间文件。创建前完整登记，普通内部暂存由创建函数收尾；若删除失败、归属不明或强杀留下未确认残留，报告收尾未完成，不声称只剩交付物，也不扫描删除未知文件。待重试不是任务结束；最终停止后按原清单完成清理，不永久保留恢复材料。

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

支持 MS-XLS 0x088D DXF（差异格式）标准结构校验与属性保留，新增 [test_wps_dxf.py](skills/wps/scripts/test_wps_dxf.py) 与 [test_wps_dxf_integration.py](skills/wps/scripts/test_wps_dxf_integration.py)。WPS 电子表格在读取时将 DXF 记录保留于元数据，不将格式混入单元格值，不将其误判为 WPS 私有记录。WPS 技能全量 424 项回归测试通过。

## 同名保护与递增版本命名（20261029.2203.2365）

DOCX 标准新建/编辑与 XLSX 标准任务入口在生成前定点检查主路径和归档同名，保留主题并选择“_第2版”“_第3版”等可用名称；以返回的实际 output 交付。归档默认原子独占发布，同名保留历史副本；只有明确授权的独立归档调用可传 `--replace-existing`。失败仍清理本轮中间文件，删除受阻则报告 `cleanup_incomplete`。旧直接入口不自动改名，主输出行为保留；共享归档默认保护同步到公共生命周期包。

源码回归 591 项通过（DOCX 269、XLSX 304、公共生命周期 16、来源一致性 2）；三包严格检查、CRC、源码逐字节核对及只读安装回归 52 项通过，安装内容哈希未变。description 能力与适用范围人工核对后保持不变。验证为 macOS/Python 3.12.14 和隔离合成样本，未执行云端 Linux/Windows、真实生产宿主、Office 视觉或模型触发验收；专用命名 linter 不可用。新版本三包保留于 skills 目录，旧包未覆盖。证据：[同名保护验收](tests/archive_collision_20261029_2203_2365.json)。本轮主代理完成实现、审查与验收，未调用子代理。
