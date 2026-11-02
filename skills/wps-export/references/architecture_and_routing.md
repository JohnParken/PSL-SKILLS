# WPS 导出架构与任务路由

路径占位符说明：`<skill-root>` 是本 Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 模块职责划分

为保持各 Skill 的单一职责与环境约束的明确性，将 WPS 相关能力正交解耦为三个模块：

| 模块 | 核心定位 | 依赖特征 | 主要职责 |
| :--- | :--- | :--- | :--- |
| `skills/docx` | 文档生成与排版 | Python 基础库/python-docx | 生成规范的 OOXML 文档，处理字体、样式、公文模板、复杂表格与页面布局 |
| `skills/wps` | 离线只读内容提取 | 纯 Python 标准库 | 识别 CFB/BIFF/MS-DOC 流，有界提取文字、表格与元数据，提供回读校验依据 |
| `skills/wps-export` | WPS 兼容文档导出 | 纯标准库 RTF 流 或 LibreOffice | 检查导出运行时依赖、调用对应后端完成转换、执行结构校验与交付生命周期管理 |

## 任务执行路由

当用户或上层任务提出 WPS 相关需求时，按以下原则路由：
1. **生成全新 WPS 文档**：
   - 步骤一：调用 `docx` 能力生成内容完整、排版合规的临时 `.docx`。
   - 步骤二：调用 `wps-export` 将 `.docx` 转换为 `.wps`（支持 `--backend rtf` 纯标准库或 `--backend auto` 自适应），完成结构校验与内容回读。
   - 步骤三：接入 `artifact-lifecycle` 将导出的 `.wps` 交付，并清理中间 `.docx` 与临时状态。
2. **读取或核查已有 .wps 文件**：
   - 直接调用 `skills/wps` 脚本进行只读提取与内容识别。
3. **转换格式（.docx -> .wps）**：
   - 直接调用 `wps-export` 进行环境检查、转换与验收。

## 多后端架构规范

`wps-export` 采用统一抽象接口，支持多后端平滑切换：
1. **RTF 规范流后端（`--backend rtf`，推荐纯 Python 方案）**：
   - 纯 Python 3.10+ 标准库实现（`zipfile` + `xml.etree.ElementTree`），零外部可执行软件与 pip 扩展依赖。
   - 解析 OOXML 结构并映射为符合 WPS 文字静默加载规范的富文本流，中文字体、公文字号、复杂表格保真度高，性能在数十毫秒级。
2. **LibreOffice 后端（`--backend libreoffice`）**：
   - 使用系统预装的 `soffice --headless --convert-to "wps:MS Word 97"` 导出 OLE/CFB 二进制兼容文件。
   - 适用于云端已预装完整办公套件的镜像环境。
3. **自适应模式（`--backend auto`，默认行为）**：
   - 运行时自动嗅探当前环境：若存在可用 LibreOffice 则调用；若无 LibreOffice 则无缝自动切换至纯 Python RTF 流导出器，保证任何环境下调用不中断。
4. **扩展后端（Spire.Doc / WPS 原生 RPC 引擎）**：
   - 面向需要专属专有对象或商业文档服务的场景，预留可接入契约。
