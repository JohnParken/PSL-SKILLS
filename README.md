# PSL-SKILLS

面向电子表格和 Word 文档处理的 Agent Skills 集合。每个 skill 包含独立的 `SKILL.md`、脚本、参考文档和资源，可按完整目录加载或分发。

## 收录内容

| Skill | 来源 | 版本 | 主要能力 |
| --- | --- | --- | --- |
| [xlsx](skills/xlsx/SKILL.md) | `minimax-skills/skills/minimax-xlsx` | 20261023.1858.1847 | XLSX/XLSM 读取、XLSX 创建、受控单元格编辑、有限公式计算、CSV/TSV 交换及数据分析；ET/ETT 的 BIFF8 子集读取 |
| [docx](skills/docx/SKILL.md) | `skills/skills/docx` | 20261023.1811.5119 | DOCX 生成、DOCX/DOTX 读取和受控编辑、模板填充、表格和图片修改、基础批注，以及受支持 WPS 文件的文本提取 |

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
- 两者的主流程均无需安装第三方 Python 包或办公软件。使用 `-I -S -B` 隔离运行环境并避免写入字节码缓存。
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
python3 -I -S -B "<skill_dir>/scripts/stdlib_xlsx.py" read ./input.et
python3 -I -S -B "<skill_dir>/scripts/stdlib_xlsx.py" create ./report.json ./report.xlsx
python3 -I -S -B "<skill_dir>/scripts/stdlib_xlsx.py" edit ./input.xlsx ./patch.json ./edited.xlsx
```

输出路径必须尚不存在。创建示例见 [calculation_example.json](skills/xlsx/assets/calculation_example.json)，其两处计算结果均应为 `226.86`。公式引擎只实现文档列出的子集，退出码 2 表示计算不完整，不能作为完整验收通过。

### DOCX

首次操作前在任务目录运行自检，要求退出码为 0，且输出包含 `check-ok` 和 `output_dir=. writable=True`：

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
python3 -I -S -B "<skill_dir>/scripts/create_docx.py" ./document.json ./output.docx --verify --require-no-placeholders
python3 -I -S -B "<skill_dir>/scripts/read_docx.py" ./output.docx
```

`--verify` 核对生成子集的内容与 XML 排版意图，不等同于页面视觉验收；字体显示、实际分页和页码仍需在文档阅读器中检查。

## 能力边界

- XLSX 现有文件编辑仅支持单元格值、公式和现有样式索引，不支持行、列或工作表的结构增删移动；宏不执行，图表和透视表不刷新。主命令 `read` 已接入 ET/ETT 的 BIFF8 只读子集，按实际内容校验；已用一个真实空白 ET 和三个真实 ETT 模板验证：ET 及其中两个 ETT 可提取子集，含图表的 ETT 仍拒绝；详见 [真实样本验证](skills/xlsx/references/et-real-validation.md)，不能视为通用 WPS 格式支持。
- DOCX 支持受控的生成和编辑子集，不提供任意复杂文档的排版还原、页面预览或 PDF 导出。WPS 支持仅限明确列出的兼容格式及文本读取。
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

DOCX 本次复制的目录未附带测试套件。维护时至少执行环境自检，以及在独立任务目录中生成、`--verify` 检查和重新读取文档；涉及其他编辑入口时补充对应行为验证。环境自检、单元测试和视觉验收各自覆盖不同内容。

## 迁移来源与许可

本次于 2026-10-23 从本地相邻项目复制，首次迁移保留原目录名称及 skill 内部文件内容，排除系统缓存文件；随后将本仓库的 `minimax-xlsx` 更名为 `xlsx`，同步更新名称和路径引用，脚本及资源内容不变。复制的是来源项目**当时的工作区内容，包含未提交修改**，不是对应提交的纯净快照。来源仓库当时的 HEAD 仅供追溯：

| 本地来源项目 | HEAD |
| --- | --- |
| `minimax-skills` | `c76df1161a0ae889604da6db9d1ecb90bf12f687` |
| `skills` | `0f0895690dfde8c203e6c2f5dbbac5a2615faf5e` |

`xlsx` 声明采用 MIT，来源项目的许可文本保存在 [licenses/minimax-skills-MIT.txt](licenses/minimax-skills-MIT.txt)。`skills` 来源项目的 README 将 DOCX 等文档 skills 描述为 source-available；本次本地 DOCX 目录没有独立许可证文件，未为其补写或推定新的许可证。本仓库不以单一许可证覆盖两个来源。
