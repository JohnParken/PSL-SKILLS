---
name: wps-export
version: "20261102.2311.0242"
description: 将 Word 文档（.docx）转换为金山 WPS 兼容格式（.wps），支持零外部依赖纯 Python RTF 规范流与 LibreOffice 二进制双后端，具备环境自检、结构校验及只读回读核验。适合需要交付 .wps 格式文档的场景；不包含正文排版生成，不恢复专有宏与私有对象。
---

# WPS 兼容文档导出

路径占位符说明：`<skill-root>` 是本 Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

本技能负责将已由 DOCX 技能生成的排版规范文档（`.docx`）转换为目标金山 WPS 兼容的文件（`.wps`），提供**零外部依赖纯 Python 标准库 RTF 规范流**与 **LibreOffice 二进制导出**双后端架构，支持环境自检、分级格式结构校验与内容回读核对。本技能不负责文字起草或初始排版，也不直接编辑 OOXML 结构。

## 执行环境约束与首步自检

1. **确认任务工作目录。** 在 harness 确认的任务工作目录中执行；输入、输出和临时文件使用该目录及其子目录中的相对路径；不得使用父目录路径、绝对数据路径或切换挂载绕过边界。当前工作目录若是 Skill 源码目录，先由 harness 明确任务工作目录，不直接在安装目录中生成任务交付文件。
2. **确定 Skill 真实位置。** 按本文件的路径定位规则，由实际读取主入口 `SKILL.md` 的真实 `file_path` 确定 `<skill-root>`。脚本与依赖清单基于该目录定位，内部资源引用统一以 `<skill-root>` 解析。执行前必须完整替换占位符，并将完整路径作为参数正确引用。
3. **核对真实运行条件。** 解释器需为 Python >= 3.10，标准库即可运行包装与校验脚本，保留 `-I -S -B` 参数。纯 Python RTF 后端（`--backend rtf`）零外部依赖；二进制转换后端（`--backend libreoffice`）需要系统预装 LibreOffice。默认使用自适应模式（`--backend auto`），优先调用 LibreOffice，无外部软件时自动无缝切换为纯 Python RTF 流导出。
4. **首次操作前完成自检。** 首次执行导出或验收前，必须运行以下命令进行环境探测自检：

```console
python -I -S -B "<skill-root>/scripts/export_wps.py" check
```

自检命令要求进程正常退出（返回码 0），并输出包含 `"status": "environment-ok"` 的 JSON 结果，报告可用后端列表（`backends_available`）与环境配置。
5. **按任务需要检查读写。** 确认输入的 `.docx` 文件存在且可读取；确认目标输出目录具备写入权限。需要创建临时文件时，在任务工作目录下独占创建，并在使用完毕后及时清理。
6. **失败停止依赖操作。** 若自检失败、输入文件缺失、或后端转换命令报错，立即停止后续交付与清理流程，如实报告具体的失败阶段与错误输出。严禁重试相同错误命令掩盖问题，严禁把未转换的 `.docx` 直接改名为 `.wps` 交付。
7. **区分自检与交付验收。** 环境自检可用仅代表具备转换工具，不代表导出的文档内容与版式验收通过。交付前必须执行文档结构校验，必要时进行内容回读。
8. **压缩后恢复约束。** 会话上下文若被压缩导致指令不全，重新读取真实 `SKILL.md` 后继续，保留已确认的任务目录与文件路径。

## 典型操作流程

### 1. 架构协作与任务分工

根据架构划分，完整生成并交付 `.wps` 文档的标准流程如下：
- **步骤一（生成排版）**：由 `docx` 技能依据业务要求生成规范的 `.docx` 文件（如公文、分析报告或表格）。
- **步骤二（格式转换）**：调用本技能的 `export_wps.py` 将 `.docx` 转换为 `.wps`，通过 `--backend` 选择导出引擎（推荐纯 Python 的 `rtf` 或自适应 `auto`）。
- **步骤三（分级验收）**：运行结构检验与内容回读，详见验收标准（`<skill-root>/references/acceptance_criteria.md`）。
- **步骤四（交付与清理）**：由生命周期规范（`skills/artifact-lifecycle/SKILL.md`）登记交付物，清理中间 `.docx` 与临时状态。

详细架构设计与任务路由参见架构与路由（`<skill-root>/references/architecture_and_routing.md`）。

### 2. 导出与验证命令

#### 转换 DOCX 为 WPS 兼容文档（零依赖纯 Python RTF 模式）

```console
python -I -S -B "<skill-root>/scripts/export_wps.py" convert "input_report.docx" --output "output_report.wps" --backend rtf
```

该模式完全由 Python 标准库驱动，无需安装 LibreOffice 或任何外部扩展，转换速度极快且完美保留中文字体、公文字号、段落缩进与复杂表格。

#### 转换 DOCX 为 WPS 兼容文档（自适应或指定后端）

```console
python -I -S -B "<skill-root>/scripts/export_wps.py" convert "input_report.docx" --output "output_report.wps" --backend auto
```

#### 附加只读内容回读核对

若需对导出结果进行内容回读验证，可增加 `--verify-read` 参数：

```console
python -I -S -B "<skill-root>/scripts/export_wps.py" convert "input_report.docx" --output "output_report.wps" --verify-read
```

#### 独立检验已有 .wps 文件结构

```console
python -I -S -B "<skill-root>/scripts/export_wps.py" inspect "target_document.wps"
```

该命令校验文件是否为合法的 WPS 兼容文件（支持 RTF 流或 CFB 二进制容器），并严格识别拦截假冒伪造的 OOXML ZIP 文件。

## 依赖与格式参考

- 运行时外部依赖与 Python 版本声明详见依赖清单（`<skill-root>/requirements.txt`）。
- RTF 规范流纯 Python 导出机制参见后端规范（`<skill-root>/references/rtf_backend.md`）。
- LibreOffice 后端调用参数与格式降级边界参见后端说明（`<skill-root>/references/libreoffice_backend.md`）。
- 四级验收规范与指标详见验收标准（`<skill-root>/references/acceptance_criteria.md`）。
