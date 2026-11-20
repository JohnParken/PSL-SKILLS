# 文字读取证据与维护边界

维护资源说明：文中 `docs/maintenance/wps/` 与 `skills/wps/scripts/test/` 均以宿主确认的仓库根目录为基准，仅供源码维护，不随运行包分发；运行任务不读取或执行。没有仓库时跳过维护资料，不按 Skill 安装目录拼接。

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

公开候选 `.wps` 文件已获得一次固定哈希与独立算法的全文/段落对照，但来源声明不能证明创建工具或版本，且尚缺 WPS 软件导出的独立完整预期；具体见真实验收 (`docs/maintenance/wps/wps-real-validation.md`)。 政府官网公开附件中已有 7 份 CFB 文件下载并核实签名，其中 6 份正文在受控 MS-DOC 子集内返回 partial 并与另一套算法逐段一致；仍不能据此证明 WPS Office 制作版本或完整显示兼容。详情见政府文件实测 (`docs/maintenance/wps/wps-government-validation.md`)。不得将其视为已完成原生格式兼容认证。

中国农业银行官网公开附件的只读实测中，五份 `.doc` 有三份为 ZIP/OOXML、一份为受限可读 MS-DOC CFB（partial）、一份 CFB 被严格容器校验拒绝；另三份总行附件实际为 PDF。它们并非已验证的原生 `.wps`，不得用其成功数宣称 WPS 专有格式兼容。详细结果见农业银行官方附件验收（`docs/maintenance/wps/wps-abc-official-validation.md`）。

中国银行/建设银行/工商银行官网公开附件的受限维护验收已记录：10 个官方链接中 4 份实际下载，2 份中行 OOXML 返回 ok，工商银行 1 份 OOXML 和 1 份 CFB/MS-DOC 返回 partial（其中 CFB 的正文算法交叉核对一致）；建行 6 份因官网 TLS 旧版重协商要求未能安全下载。**这不是原生 `.wps` 的验收通过率**，详见三行官网实测（`docs/maintenance/wps/wps-three-banks-official-validation.md`），禁止将下载失败统计为解析失败。

本轮只优化**现有结果质量和验收**：`drawn_shape_anchor_not_rendered` 与图片标记共同计入 `reading_assessment.coverage.objects=not_decoded`；保留绘图对象 CP、SPA 原始偏移等受控来源数据，不复制未渲染对象中的原文。维护 CI 已对 7 份政府官网真实 CFB 文件固定 SHA-256、现有 partial/段落数及 6 份独立正文算法比较；生产 Skill 不下载这些文件。相关证据见 验证记录（`docs/maintenance/wps/validation.md#读取质量基线与资源测量`）。

本轮对现有 MS-DOC 文本转换流程进行了**不改变结果契约的内存优化**：`--object-policy placeholder` 或 `--field-policy cached_result` 虽被选中，但文档实际没有需要转换的标记时，不再逐字符另建一份文本列表。存在实际转换标记时仍使用既有处理路径及 `partial`/拒绝规则，不放宽对象、字段、表格或 CFB 安全检查。真实官网文件的资源测量见 验证记录（`docs/maintenance/wps/validation.md#真实大型-wps-测量和无转换快速路径`）。
