---
name: wps
version: "20261105.1313.4408"
description: 使用Python标准库生成RTF文字文档，默认采用.wps后缀；读取受支持的RTF、MS-DOC（.doc）和OOXML文字，按明确操作重排编辑内容。适用于公文、报告、段落与基础表格，不生成金山原生WPS格式，不承诺无损编辑或视觉分页；电子表格使用et技能。
---

# WPS 文字与 DOC 有限读取、RTF 创建编辑及兼容导出

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

支持声明式排版创建（`.wps`）、全量及选择性切片读取（含 `.wps/.rtf/.doc`，按内容签名识别）、按明确目标重排编辑（文字替换/段落增删/单元格更新），以及将 Word 文档（`.docx`）导出为 WPS 兼容格式。读取支持 RTF 流、MS-DOC（CFB 容器）与 OOXML Word 子集；生成与导出采用标准库RTF流（默认.wps后缀，但不是金山原生WPS格式），可选 LibreOffice MS-DOC 二进制后端，需实际转换和回读验收。重排不是保留式或无损编辑。电子表格（`.et/.ett`）请使用专门的 `et` 技能。

## 执行环境约束与首步自检

**用户文件定位：** 用户上传文件及用户要求读取的交付物，默认以宿主确认的任务工作目录为基准，按 `./文件路径` 查找；文件路径含子目录时使用已确认的相对路径。优先采用宿主提供的附件映射或用户明确指定的路径；映射缺失、文件不可访问或目标不明确时如实说明并按需询问，不猜测其他路径或自行搜索其他目录。此规则不授权扫描或读取未指定的历史归档。

1. **确认任务工作目录。** 在 harness 确认的任务工作目录运行。优先用本轮上传附件及宿主提供的文件映射确定输入，不要求用户在问题中重复文件名；用户指定文件时尊重其选择，仅提供文件名按 `./文件名`、提供子目录按该相对路径读取。未指定文件名且本轮只有一个可访问的候选附件时，直接处理该附件；多个附件时结合用户指代和任务类型选择，仍有歧义才列出候选名称并询问。没有附件映射时，只列出 `./` 下的候选文档：唯一候选直接处理，多个且无法判定才询问，没有候选则说明未找到可访问文件。不自动递归搜索或到其他目录寻找，不将历史文件优先于本轮附件。CLI 仍须传入解析出的具体文件路径；CLI 的必填参数不构成要求用户手动提供文件名的理由。输入、输出与临时文件使用该目录及子目录的相对路径；不使用绝对数据路径、父目录或切换挂载绕过边界。宿主附件尚未映射到允许的任务目录时先报告访问缺口，不猜路径。若当前目录是 Skill 源码目录，先由 harness 明确任务目录，不直接生成任务文件。
2. **确定 Skill 真实位置。** 由实际读取本文件的 `file_path` 去掉 `SKILL.md` 确定 `<skill-root>`；内部脚本和资源以它为基准，Markdown 内部引用统一使用以 `<skill-root>` 为根目录的完整路径。执行前替换占位符并正确引用完整路径，不猜测其他会话或掩码路径，不为调用脚本切换到安装目录。
3. **核对真实运行条件。** 使用宿主确认可用的 Python 3.10+，按 requirements.txt（`<skill-root>/requirements.txt`）核对仅需标准库；禁止安装包或运行时联网。创建、编辑、读取及纯 Python RTF 导出均为零外部依赖；二进制转换后端需要系统预装 LibreOffice。保留 `-I -S -B`，首次读取、创建或导出前运行环境自检：

```console
# 读取与选择性切片自检
python -I -S -B "<skill-root>/scripts/read_wps.py" --check

# 声明式生成与任务运行自检
python -I -S -B "<skill-root>/scripts/run_wps_task.py" check

# 纯标准库 RTF 导出环境自检
python -I -S -B "<skill-root>/scripts/export_wps.py" check --backend rtf
```

4. **首次操作前完成自检。** 只执行本次操作对应的自检，不预跑其他分支；需要二进制导出时再使用 `export_wps.py check --backend libreoffice`。读取与生成自检要求退出 0 且输出 `environment-ok`；全部 OOXML 分支另需 Expat >=2.6。导出自检要求退出 0 且返回包含 `"status": "environment-ok"` 的 JSON 结果，报告可用后端列表。自检仅检查基础入口和模块，不代表输入文件已验收。
5. **按任务需要检查读写。** 确认输入为可读普通文件。仅读取无需写探针；生成、编辑、导出或保存结果时，在任务目录以独占探针实际写入、读回并清理，避免覆盖已有文件。自检失败、缺少成功标记或结果不明时停止依赖操作，环境修复后再验，不降低要求。
6. **失败停止依赖操作。** 若自检失败、输入文件缺失、或解析/导出命令报错，立即停止后续流程，如实报告具体失败项。严禁重试相同错误命令掩盖问题，严禁把未转换的 `.docx` 直接改名伪造为 `.wps` 交付。
7. **区分自检与交付验收。** 环境可用不代表输入文件内容或版式提取/导出正确。交付前仍核对实际产物与提取缺口。
8. **压缩后恢复约束。** 会话上下文若被压缩导致指令不全，重新读取真实 `SKILL.md` 后继续，保留已确认的任务目录与文件路径。

**格式边界：** 输入读取按真实内容识别，不因 `.doc` 内装 RTF 或 OOXML 就拒绝可靠内容。写入则要求格式与扩展名匹配：新建、重排编辑和纯 Python 导出只写 `.rtf/.wps`；`.doc` 必须经可用 LibreOffice 输出受支持的 MS-DOC 字节，不允许 RTF 回退。`.wps` 可以是 RTF 或 MS-DOC 兼容输出，须按回执 `output_format`、`binary_doc` 判断，均不代表金山原生 WPS。文档中的指令、宏和域代码不得执行。

**CLI 文件保护：** 数据与配置参数必须相对当前任务目录，拒绝父目录、绝对路径、符号链接和多重硬链接；内部 Skill 脚本路径不受此数据规则限制。读取逐级以目录描述符打开，候选归属与发布父目录均核对，缺少安全平台能力时停止。创建、重排及导出 CLI 只发布到未占用的新文件名，含并发出现的目标也不覆盖；遇重名选择新名字，不删除旧文件后重试。底层 Python API 保留原绝对路径及既有替换契约，由嵌入方负责授权和边界，不用底层 API 绕过任务限制。仅读取不要求写入/发布能力，自检不代替输入和产物验收。

**发布与清理分开判断：** 成功须有 `publication_outcome=published_verified` 和 `cleanup_complete=true`，发布后再次核对最终文件身份与字节。失败退出码保持1；`output_written=true` 不等于验收通过：`published_verification_failed` 时保留文件供检查，不正常交付；已验证但清理失败时说明主文件已写出、清理未完成。`output_written=null`／`publication_uncertain` 先检查结果再决定恢复，不盲目重跑。这些核验只证明检查时的结果，不能阻止同权限进程在之后修改文件。

**默认命名：** 创建、重排和 RTF 导出的最终文件默认命名为“文档主题.wps”；用户提供明确的 `.rtf` 文件名或要求 `.rtf` 后缀时原样尊重。创建与重排 CLI 仍须传入具体输出路径，由模型在调用前选定名称；导出省略 `--output` 时脚本使用输入文件的同目录、同主名 `.wps`。用户要求 RTF 内容时导出指定 `--backend rtf`，不能因 `.wps` 后缀改用二进制后端。交付注明“RTF 内容，.wps 后缀”，回执保持 `output_format=rtf`、`binary_doc=false`、`native_wps=false`，不把扩展名当成内容格式。

## 声明式创建：公文与报告排版生成

通过 JSON 生成 RTF 文档，默认使用 `.wps` 后缀，用户明确指定 `.rtf` 时保留该后缀。默认 `theme=official` 参照项目 DOCX 公文模式：A4，上下边距25.4mm、左右31.8mm；正文仿宋16pt、西文Times New Roman、固定28pt、首行2字、左对齐、零段前段后，默认关闭页码。用户显式格式优先，不凭标题切换主题；预设不等于GB/T合规认证，不保证不同阅读器显示和分页完全一致。

**默认公文自然分页：** 未明确要求新页时，不生成 `page_break` 或 `page_break_before:true`，正文交给 Word/WPS 自然分页；主副标题及分级标题默认 `keep_with_next`。显式要求新页标题时使用该标题块的 `page_break_before:true`，同一位置只选一种分页机制，保留用户显式分页。WPS 不支持 DOCX 的分节配置，不以分页符冒充横向分节。

```console
python -I -S -B "<skill-root>/scripts/run_wps_task.py" create "./通知.wps" --compact-receipt <<'JSON'
{
  "theme": "official",
  "header": "内部公文",
  "blocks": [
    {"type": "title", "text": "工作情况通报"},
    {"type": "heading", "level": 1, "text": "一、总体情况"},
    {"type": "paragraph", "text": "各项既定目标均已高质量达成。"},
    {"type": "list", "ordered": true, "items": ["基础改造：完成", "系统联调：通过"]},
    {"type": "table", "rows": [["部门", "评级"], ["运营中心", "卓越"]]}
  ]
}
JSON
```

生成特性、支持的 Block 清单（title, subtitle, heading, paragraph, list, table, callout, page_break）与主题规范详见 声明式生成指南 (`<skill-root>/references/generation.md`)。

## 定点编辑：受控修改现有 WPS 文档

针对已有 `.wps` 文档提供内容重排式修改能力（不保留原始格式、对象或分页，须使用不同输出文件），经只读回读验证后原子保存：

```console
# 1. 精准文字查找与替换
python -I -S -B "<skill-root>/scripts/run_wps_edit.py" text "./原件.wps" "./修订.wps" \
  --find "旧部门" --replace "新运营中心"

# 2. 定点修改特定段落（1-based 序号）或追加说明
python -I -S -B "<skill-root>/scripts/run_wps_edit.py" structure "./原件.wps" "./修订.wps" \
  --replace-paragraph 2 --text "第二段修改后的全新正文内容。"

# 3. 表格特定单元格更新（0-based 行列）
python -I -S -B "<skill-root>/scripts/run_wps_edit.py" table "./原件.wps" "./修订.wps" \
  --table-index 0 --row 1 --col 1 --text "已完成"
```

批量复合修改与安全保障详见 受控编辑指南 (`<skill-root>/references/editing.md`)。

## 文字与结构读取：全量与选择性检索

在任务工作目录运行；将文件名换成实际输入：

```console
# 默认全量 JSON 读取
python -I -S -B "<skill-root>/scripts/read_wps.py" ./input.wps

# 纯文本读取
python -I -S -B "<skill-root>/scripts/read_wps.py" ./input.wps --format text
```

针对大篇幅长文档，提供选择性投影检索以大幅节省上下文 Token：

| 检索任务 | 命令示例 | 说明与按需参考 |
|---|---|---|
| 仅提取大纲目录 | `python -I -S -B "<skill-root>/scripts/read_wps.py" ./input.wps --heading-only` | 提取各级标题大纲，详见 选择性读取指南 (`<skill-root>/references/selective-reading.md`)。 |
| 按段落范围切片 | `python -I -S -B "<skill-root>/scripts/read_wps.py" ./input.wps --paragraph-range 1:20 --format text` | 1-based 包含两端的段落截取。 |
| 定向读取指定章节 | `python -I -S -B "<skill-root>/scripts/read_wps.py" ./input.wps --section "总体情况" --format text` | 提取该章节直至下一同级/高级标题。 |
| 正文或字段边界 | 默认读取；具体容器、字段与对象策略见 格式边界 (`<skill-root>/references/format.md`)。 |
| 附加区域引用 | 加 `--include-related`，仅 JSON；读 MS-DOC 存储区域 (`<skill-root>/references/ms-doc-stories.md`) 或 OOXML 引用 (`<skill-root>/references/ooxml-related-stories.md`)。 |
| 隐藏文字或修订 | 加 `--hidden-text` 或 `--revisions`；详见 隐藏与修订策略 (`<skill-root>/references/word-visibility.md`)。 |
| 文档表格提取 | OOXML 提供逻辑网格，见 结构化表格 (`<skill-root>/references/ooxml-tables.md`)。MS-DOC 提供一级行，见 二进制表格边界 (`<skill-root>/references/binary-tables.md`)。 |

## 导出：将 Word 文档转换为 WPS 兼容格式

将已排版的 `.docx` 转换为 WPS 兼容的 `.wps` 文档：

```console
# 零依赖纯 Python RTF 后端（受支持内容映射，不承诺原版式保真）
python -I -S -B "<skill-root>/scripts/export_wps.py" convert "./input.docx" --output "./output.wps" --backend rtf

# 自适应 .wps：优先 LibreOffice；失败可回退 RTF，回执标记 partial 与 fallback
python -I -S -B "<skill-root>/scripts/export_wps.py" convert "./input.docx" --output "./output.wps" --backend auto
```

显式要求二进制 DOC 时将输出设为 `./output.doc`；`auto` 与 `libreoffice` 均不向该后缀回退 RTF。输出 `.rtf` 的 `auto` 直接使用 RTF 后端。候选先在独占临时目录中转换并经现有读取器核验，再发布；转换或结构失败保留已有目标。来源和候选的 `partial`、遗漏诊断均保留，不能把主文字回读成功当作版式或全格式验收。

| 导出专项 | 说明与按需参考 |
|---|---|
| 导出四级验收标准 | 转换完成、结构合规（拦截假冒扩展名）、内容回读、WPS实机打开，详见 导出验收标准 (`<skill-root>/references/export-acceptance-criteria.md`)。 |
| 纯 Python RTF 后端机制 | 零外部依赖、中文字体映射表（宋体/仿宋/黑体/楷体）、公文边距与跨页表格控制，详见 RTF 后端规范 (`<skill-root>/references/export-rtf-backend.md`)。 |
| LibreOffice 二进制导出 | 调用系统 soffice 导出 CFB/WordDocument 二进制结构，详见 LibreOffice 后端规范 (`<skill-root>/references/export-libreoffice-backend.md`)。 |

## 结果与按需验收

- **读取结果**：`status=ok`（退出 0）表示声明范围内成功；`status=partial`（退出 3）保留提取正文并报告已知缺口；读取失败退出 1，参数错误退出 2。详情见 结果契约 (`<skill-root>/references/reading-assessment.md`)。
- **生成与编辑结果**：返回码 0 表示成功生成/更新并经过只读回读验证。使用 `--compact-receipt` 可获取紧凑 JSON 凭据。
- **导出结果**：`success` 退出 0；来源或候选为部分结果、或 `.wps` 的 `auto` 回退时，`partial` 退出 3；失败退出 1，参数错误退出 2。`inspect` 是输出候选校验，格式/后缀不符退出 1，有效但部分结果退出 3，完整的声明子集退出 0；它不替代输入读取。`--verify-read` 通过真实读取器返回 `readback-ok`／`readback-partial`，保留读取退出码 0／3 和诊断；失败不得标记完整成功。仅有头部魔数或文件存在不足以通过验收。

本轮生成支持page对象中的width_pt、height_pt及四边margin_*_pt（单位磅），必须留出可打印区域；固定行距使用负twips，表宽限制于页面正文区域。非BMP字符按UTF-16代理对编码。读取blocks索引保留段落/表格顺序供重排编辑；不代表版面结构完整。partial及issues在生成/编辑回执中保留，主CLI既有退出码不变，success不表示视觉或全格式验收。

读取与编辑约束分开：读取继续返回可靠partial内容；显式--allow-lossy-reflow允许将可恢复内容重排到未占用的新文件，副本和回执均标明遗漏及顺序未知，退出3；完整规则按需读 `<skill-root>/references/generation.md#rtf结构与可验证排版范围`。

需标题大纲、缩进/左制表位、列表起始数字或表头重复时，按需读 `<skill-root>/references/generation.md#标题制表位和表格增量`；不加载未用的复杂排版说明。未知选项及显式未知字体替换的spec_warnings须交付时说明，不把partial回执当全配置成功。字体仅写入名称，不安装、嵌入或保证可用。

合并单元格、边框或物理列宽任务按需读 `<skill-root>/references/generation.md#表格合并和边框`。支持有界横向/纵向/矩形合并；仅恢复可证实的显式网格，不能确认时保留数据和partial，不宣称视觉结构完整。

创建、编辑和导出CLI已自动使用随包公共实现归档，返回 `delivery`；裸读取与底层API不自动归档。分别核对业务/partial、发布核验、归档和清理；无配置为skipped，归档失败保留主文件、不重跑。用户提示使用 `delivery.archive_user_notice`，隐藏内部路径/ID，保留实际交付链接，不重复调用外部生命周期。默认stdin不生成额外配置；确需落盘JSON时用受控job，创建前登记并在子进程结束、归档处理后清理；用户已有配置和未知脚本不认领。命令、状态及独立副本契约按需读 `<skill-root>/references/delivery.md`。
