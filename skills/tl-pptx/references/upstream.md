# 来源与独立运行边界

路径占位符说明：从宿主实际读取主入口 `SKILL.md` 的真实 `file_path` 去掉文件名确定 `<skill-root>`；相对读取路径先按本次已确认的基准目录解析。使用前替换占位符并引用完整路径，参考文档目录不是根目录；无法定位时停止并报告，不猜路径。

## 来源

- 项目：`https://github.com/hugohe3/ppt-master`。
- 2026-11-17 核对默认分支 `main`，固定提交 `7e0df6705de0cd24345210d31f07fcb5a32b6900`。这是本次导入时的最新默认分支，不是对未来最新状态的承诺。
- 上游许可：MIT，Copyright (c) 2025-2026 Hugo He；完整上游原文为 `<skill-root>/references/LICENSE-PPT-MASTER.txt`，仅改变保存位置/文件名，不改许可内容。预设形状数据许可原文随包保留于 `<skill-root>/scripts/pptx_shapes/data/LICENSE-APACHE-2.0.txt`、`<skill-root>/scripts/pptx_shapes/data/LICENSE-OPEN-XML-SDK-MIT.txt`；通知原文为 `<skill-root>/scripts/pptx_shapes/data/NOTICE.txt`（从 NOTICE.md 改名，原字节保留）。不能仅凭主项目 MIT 推断所有素材许可相同。
- 来源覆盖 DrawingML/native_objects、完整 pptx_package builder、PPTX→SVG、authoring_roundtrip、Master/Layout 模板、动画/转场、布尔/文字轮廓、素材与质量工具，本版已移除旁白/字幕/视频处理后端。长文件按职责拆分至每文件 1000 行以内，保留 API 和可追踪的算法，不是把少数基础 SVG 功能重写后称完整移植。
- TL 新增公开安全入口对输入做快照、预算与路径校验，在私有任务工作区调用上游核心，再独占发布与归档。保留 `convert` 快捷组包与 `build` 完整 builder 两条明确用途；import/rebuild 从验证后的源 PPTX 重建可信基线。公共生命周期副本自包含，不依赖相邻 Skill。
- 完整迁移目标与当前可调用入口分开。非官方衍生版核验自身运行清单及 SHA-256，当前七项依赖之外的工具或服务不自动启用；未开放的 CLI 不可绕过。部分支持代码或工作流文档存在不代表部署/服务/视觉验收完成。

大型离线模板/图标/声音库以独立可选数据包保留，轻量清单和许可边界见 `<skill-root>/references/offline-assets.md`、`<skill-root>/references/offline-assets.json`；主包不内嵌大型 ZIP，不自动联网。历史拆分映射保存在 `<skill-root>/references/upstream-splits.json`，其旧哈希不替代当前来源记录。

逐文件源路径、固定上游 SHA、最终 SHA-256、职责拆分映射及有意差异保存在 `<skill-root>/references/upstream.json`。该清单也记录公共归档副本来源和版本；用于维护与核验，不是普通任务的预读材料。

## 必要依赖

精确版本见 `<skill-root>/requirements.txt`：python-pptx==1.0.2 创建基础包；lxml==6.0.4 用于 XML；Pillow==12.2.0 处理图像；XlsxWriter==3.2.9 支持嵌入工作簿；typing-extensions==4.14.0 满足 python-pptx 传递依赖；PyYAML==6.0.2 处理模板规范；PyMuPDF==1.24.2 用于受支持的文档/图像适配。没有安装或服务授权的隐式扩展。

PyMuPDF 1.24.2 标准拆分 wheel 的依赖元数据另含 PyMuPDFb==1.24.1（MuPDF 共享库）；七项直接依赖不等于仅七个安装分发包。部署方须按目标平台验证完整依赖闭包，不使用 --no-deps 掩盖缺失。PyMuPDF/MuPDF 是 AGPL-3.0 或商业许可，不能据主项目 MIT 推断满足其授权条件。组件存在不证明任意 PDF 或视觉转换接口已经开放。Pylint 只是维护工具。

通用任意曲线/文字布尔、复杂 shaping 和需要额外渲染器的路径不开放；新增 text-outline 只支持用户授权静态字体的单行 ASCII/基本汉字，产物为 SVG 路径，非可编辑文字。几何布尔仅限明确支持的子集，详见 `<skill-root>/references/dependency-groups.json`。保留普通文本备注、原生图表/表格/公式和受支持的无声动画；不提供旁白合成、字幕或视频处理。

## 有意差异与验收边界

公开入口为 check/convert/build/analyze/import/rebuild、boolean、text-outline、asset-query/asset-select 及 template-import/create/register/apply/preview（受保护操作另验完整性及模板质量），当前 analyze 提供逐页原生内容及结构/备注/诊断，保留缓存来源与不支持对象限制。受保护模板操作仍须完整性与质量检查；其他功能只按本版实际接口调用。业务任务不调用原 CLI 绕过 TL 路径/资源/发布边界，不启动未知服务器、自动下载资源或自动安装依赖。

主要适配包括职责拆分、静态资源合规别名、显式导入兼容、任务输入快照、包/XML预算、基线再导入、归档及身份清理。字体目录仅扫描明确授权目录；未提供时仅系统字体根，不自动搜索 home。不搜索仓库或私人凭据。其他有意差异以逐文件追溯为准，不能仅更新 hash 掩盖改变。

包结构、关系、内容语义与客户端视觉分别验收。macOS 测试不能替代 Linux/Windows、真实 DeepAgents 回传、PowerPoint/LibreOffice 验收。源码测试、缓存、虚拟环境和维护材料不进入业务包；阶段1归档包保留，后续发布另取版本，不覆盖历史快照。
