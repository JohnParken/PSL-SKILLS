# PSL-SKILLS

面向电子表格、Word 文档处理和 OFD 有限只读提取的 Agent Skills 集合。每个 skill 包含独立的 `SKILL.md`、脚本、参考文档和资源，可按完整目录加载或分发。

## 收录内容

| Skill | 来源 | 版本 | 主要能力 |
| --- | --- | --- | --- |
| [xlsx](skills/xlsx/SKILL.md) | `minimax-skills/skills/minimax-xlsx` | 20261026.2106.1644 | XLSX/XLSM 原生读取、XLSX 创建和受控编辑、CSV/TSV 与分析；可消费 wps 提取的受支持电子表格 JSON；不支持读取 WPS 格式（.wps/.et/.ett），请使用 wps skill |
| [docx](skills/docx/SKILL.md) | `skills/skills/docx` | 20261027.1715.1211 | DOCX 生成、DOCX/DOTX 读取与受控编辑；不生成 DOTX；.wps/.et/.ett 使用 wps 技能 |
| [wps](skills/wps/SKILL.md) | 本地限定实现；迁移既有 WPS 家族读取 | 20261026.2106.1644 | .wps/.et/.ett 内容识别、MS-DOC/Strict及Transitional OOXML Word/OOXML电子表格/BIFF8 子集只读；日期与公式缓存、部分读取诊断 |
| [ofd](skills/ofd/SKILL.md) | 独立标准库限定实现 | 20261026.2117.0168 | OFD 1.0 字面文字与元数据只读；原始坐标与来源；公开生产工具样例通过；政务原件验收未完成；不渲染、OCR、验签或转换 |

## 目录结构

```text
PSL-SKILLS/
├── AGENTS.md
├── .agents/skills/skill-development-standards  # 指向规范目录的相对链接
├── README.md
├── licenses/
│   └── minimax-skills-MIT.txt
└── skills/
    ├── skill-development-standards/  # 开发规范、检查器及其测试
    ├── xlsx/
    │   ├── SKILL.md
    │   ├── requirements.txt
    │   ├── scripts/          # 标准库入口、分析模块、兼容工具和测试
    │   ├── references/       # 输入格式、能力边界和使用说明
    │   └── assets/           # XLSX 模板和示例数据
    ├── wps/
    │   ├── SKILL.md
    │   ├── requirements.txt
    │   ├── scripts/          # 独立正文/表格读取、CFB 与合成回归
    │   └── references/       # 固定 FIB 范围、来源与验证限制
    ├── ofd/                  # 独立安全只读提取与验证记录
    └── docx/
        ├── SKILL.md
        ├── requirements.txt
        ├── scripts/          # 生成、读取、编辑和检查工具
        ├── references/       # 文档结构、生成和编辑说明
        └── assets/           # 批注 XML 资源
```

## 环境要求

- **xlsx**：Python 3.9 及以上；维护中的主流程仅依赖标准库。
- **docx**：Python 3.10 及以上，标准库包含 zlib，Expat 至少为 2.6.0。
- **ofd**：仅 Python >=3.10 自带标准库（含 zlib、Expat >=2.6.0）；零第三方包、零外部软件。自带能力缺失则诊断并停止，不额外安装组件。
- **wps**：Python 3.9+，全部 OOXML 分支另需 Expat >=2.6；仅标准库，独立包内读取，不转换文件。
- 以上主流程均无需安装第三方 Python 包或办公软件。使用 `-I -S -B` 隔离运行环境并避免写入字节码缓存。
- XLSX 目录中保留的旧工具可能依赖 pandas、openpyxl 或 LibreOffice，不属于推荐流程；不要据此为主流程安装依赖。

## 如何使用

将目标 skill 的**完整目录**交给支持 Agent Skills 的宿主加载；具体安装位置由宿主决定。也可以让代理先读取对应 `SKILL.md`，再根据任务读取其引用文档并调用脚本。只复制 `SKILL.md` 或单个脚本会丢失必要模块和模板。

业务输入、输出和临时文件应放在宿主确认的任务工作目录中，不写入 skill 安装目录。脚本和资源以实际读取的 `SKILL.md` 所在目录定位；任务数据使用任务目录内的相对路径。下文的 `<skill_dir>` 必须替换为实际 skill 目录，`python3` 必须指向满足版本要求的解释器。

### XLSX

首次操作前运行自检，要求退出码为 0 且 JSON 中 `status` 为 `ready`：

```console
python3 -I -S -B "<skill_dir>/scripts/stdlib_xlsx.py" check
```

按 [计算与文件契约](skills/xlsx/references/python-calculation.md) 准备输入，确认输入可读、输出目录可写后，在任务目录执行：

```console
python3 -I -S -B "<skill_dir>/scripts/stdlib_xlsx.py" read ./input.xlsx
python3 -I -S -B "<skill_dir>/scripts/stdlib_xlsx.py" create ./report.json ./report.xlsx
python3 -I -S -B "<skill_dir>/scripts/stdlib_xlsx.py" edit ./input.xlsx ./patch.json ./edited.xlsx
```

输出路径必须尚不存在。创建示例见 [calculation_example.json](skills/xlsx/assets/calculation_example.json)，其两处计算结果均应为 `226.86`。公式引擎只实现文档列出的子集，退出码 2 表示计算不完整，不能作为完整验收通过。

### DOCX

首次操作前在任务目录运行自检，要求退出码为 0、输出包含 `check-ok`，且 `output_dir=...` 行中 `writable=True`。自检会检查程序实际选定的输出目录，必要时创建目录并清理写入探针：

```console
python3 -I -S -B "<skill_dir>/scripts/create_docx.py" --check
```

按照 [生成说明](skills/docx/references/generation.md) 准备 `document.json`，例如：

```json
{
  "theme": "business",
  "blocks": [
    {"type": "title", "text": "项目报告"},
    {"type": "paragraph", "text": "本期工作进展。"}
  ]
}
```

```console
python3 -I -S -B "<skill_dir>/scripts/create_docx.py" ./document.json output.docx --verify --require-no-placeholders
```

新建命令只接收输出文件名，不带 `./` 或目录。Python 从 `ROUTER_CONFIG_VARIABLES` 的 `commonParam` 取得 `idRecord`：有效时保存到 `./{idRecord}/output/`，配置缺失或解析失败时回退到 `./` 并记录固定原因码；目录或写入失败不触发回退。成功 JSON 的 `output` 返回实际相对路径，后续读取、验收及交付使用该路径。`commonParam` 支持列表或 JSON 编码列表；具体 ID、文件名规则及低层 API 边界见 [输出目录与文件名](skills/docx/references/generation.md#输出目录与文件名)。其他读取和编辑命令仍接收相对路径。

`--verify` 核对生成子集的内容与 XML 排版意图，不等同于页面视觉验收；字体显示、实际分页和页码仍需在文档阅读器中检查。

### OFD

独立入口见 [ofd](skills/ofd/SKILL.md)。先运行自检，再在任务目录读取相对输入路径；结果保留文字来源和诊断，不恢复页面或语义阅读顺序：

```console
python3 -I -S -B "<skill_dir>/scripts/read_ofd.py" --check
python3 -I -S -B "<skill_dir>/scripts/read_ofd.py" ./input.ofd
```

OFD 离线分发包沿用仓库现有位置 `skills/ofd-<version>.zip`；完整目录独立安装，不依赖相邻 wps/docx/xlsx。版本化 ZIP 按现有 `.gitignore` 规则不纳入版本控制，源目录和 README 是维护入口。

## 能力边界

- XLSX 原生编辑仅支持受控单元格操作，不支持任意结构编辑；ET/ETT 的 CFB/BIFF8 引擎、样本清单与回归已迁至 wps。docx/xlsx 不支持读取 WPS 格式（.wps/.et/.ett），请使用 wps skill；读取入口明确拒绝，不自动调用。四个既有 ET/ETT 真实样本可在显式兼容/opaque 策略下提取有限子集；详见 [真实样本记录](skills/wps/references/et-real-validation.md)。这不是通用 WPS 格式支持。
- DOCX 支持受控的生成和编辑子集，不提供任意复杂文档的排版还原、页面预览或 PDF 导出。WPS 家族读取能力见独立 wps 技能；不再由 docx 提供。
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

原生行为测试要求 Python >=3.10、Expat >=2.6.0；跳过测试不算行为验收。范围、命令和证据见 [DOCX 验证记录](skills/docx/references/validation.md)。环境自检、固定预期回归和视觉验收覆盖不同内容，当前流程不调用办公软件做视觉验证。

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
| .docx | 正文与显式结构 | 生成及受控编辑 | 原生行为回归；不保证任意排版 |
| .dotx | ZIP/XML读取 | 同后缀定点编辑，不生成DOTX | 规则受控，不以改后缀转换 |
| .xlsx | 原生读取与分析 | XLSX生成、受控编辑/公式子集 | 不等于完整Excel引擎 |
| .xlsm | 原生读取 | 保留宏包的受控编辑 | 不执行宏、不创建宏工程 |
| .csv/.tsv | 数据读取与交换 | 按已实现数据导出路径交换 | 无工作簿样式/结构保真 |
| .ofd | ofd 提取 1.0 字面文字与元数据 | 不支持 | 合成回归及生产工具原始样例；政务 1.1 正确拒绝；无全文/视觉或全规范认证 |
| .wps/.et/.ett | wps按实际结构读取有限子集 | 不生成、不编辑、不转换 | 五真实原件仅partial；新增容器主要合成验证 |
| 约定schema的.json | 配置、读取结果、分析衔接 | 按各入口固定schema写出 | 非任意JSON编辑器 |

仓库没有PDF、PPT/PPTX、ODT/ODS/ODP、RTF专用技能或通用转换器。旧DOC/XLS内部结构只能由wps识别其有限MS-DOC/BIFF子集，不能称完整支持。外部同名软件生成的真实DOCX/XLSX仍按原生格式处理。推荐下一步先补有合法来源的真实样本与独立内容预期，再按实际办公需求单独规划PDF/PPT等技能；不要通过后缀或依赖推断已支持。

本批四技能任务选择的固定正反例见 `tests/skill_selection_cases.json`。20 项人工边界核对及未见标准答案的独立选择模拟均匹配预期；这是有限用例的选择评测，不是真实宿主自动触发验收。

OFD 政务原件相关验收证据已因既有 Library scope 拒绝撤回，不计为有效授权验收。此前本批 OFD ZIP 含该项派生元数据和说明，不应使用，保留但停止交付；仅从清理后的源码重建新的本地 OFD 包，不读取旧 ZIP，不上传。其他合成回归与允许样本证据单独保留。
