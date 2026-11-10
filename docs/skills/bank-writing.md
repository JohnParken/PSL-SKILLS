# bank-writing：银行专业写作与文本交接

> 文档核对日期：2026-11-07；历史源码参考基线：[`9fcf9fca339dd0f6491f7e64e4777f9dc57e61a7`](https://github.com/JohnParken/PSL-SKILLS/tree/9fcf9fca339dd0f6491f7e64e4777f9dc57e61a7)；Skill 版本：`20261110.2126.1082`。本文是仓库级说明，不替代 [SKILL.md](../../skills/bank-writing/SKILL.md) 的运行约束。

本 Skill 本身无业务文件输出。授权交接或另存结果时只指定裸文件名，交付文件固定在任务根目录 `./`；输入与仓库维护目标路径保持原规则。

## 功能与职责

`bank-writing` 是一套面向银行材料的文本写作规则和按需参考资料，不是文档生成程序。它覆盖需求澄清、素材梳理、结构设计、起草、润色、压缩、扩写、改写、多源整合、摘要、仿写及审校，默认直接交付可复制的中文文本。

典型文种包括总结述职、正式公文、领导讲话、经营分析、工作邮件、信贷尽调、审计及 AML 分析、产业调研、制度与 SOP、数字化需求、活动方案和对外沟通口径。这些是写作支持范围，不表示可以代替业务审批、专业调查、政策有效性核验或外部发布。

该 Skill 没有 `scripts/`、`requirements.txt` 或自检程序，不生成 DOCX/PDF，不写中间稿、导出 JSON 或清理清单，不发送邮件。明确要求 DOCX 时才将文本交给独立的 `docx` Skill；文件生成、验收、归档和清理由后者负责。

## 知识架构

下面的节点表示知识规则与任务路由，不表示可执行软件模块。主入口先确定操作，再按文种和风险追加最少的参考资料。

```mermaid
flowchart TD
    A["用户要求与本轮素材"] --> B["SKILL.md：共同边界与操作路由"]
    B --> C["简单短文本：直接按入口写作"]
    B --> D["操作参考：编辑、摘要、仿写、审校"]
    B --> E["文种与业务参考：按任务选择"]
    D --> F["事实、口径、状态与修改范围核对"]
    E --> F
    C --> F
    F --> G["对话中的正文、提纲或审校意见"]
    G --> H["明确授权 DOCX 时：docx-handoff.md"]
    H --> I["由宿主定位独立 docx Skill"]
    B --> J["读取故障时：source-access.md"]
```

资料中的链接是按需发现入口，不要求递归预读。完整专项稿、文种转换和专业判断才选择业务参考；短句润色或纯摘要不能仅因出现业务关键词就加载全部专项。涉及客户敏感信息、对外金融营销、金融承诺、授信、审计或 AML 结论时，也不能以篇幅短为由省略对应业务边界。

## 主要入口与参考资料

### 操作路径

| 入口或资料 | 何时使用 | 核心约束 |
| --- | --- | --- |
| [SKILL.md](../../skills/bank-writing/SKILL.md) | 所有任务；简单短文本可仅依赖入口完成 | 素材可访问、事实不虚构、范围明确、默认文本交付 |
| [editing-and-integration.md](../../skills/bank-writing/references/editing-and-integration.md) | 多段改写、压缩、扩写、多轮编辑、多源整合 | 已确认段落锁定、删除项不回流、来源冲突不擅自裁定 |
| [summary-reading.md](../../skills/bank-writing/references/summary-reading.md) | 摘要、会议要点和决议提炼 | 保留限定、责任和时限，不将个人意见升格为决议 |
| [style-imitation.md](../../skills/bank-writing/references/style-imitation.md) | 范文仿写 | 只迁移结构、语气和节奏，不迁移范文事实 |
| [summary-and-imitation.md](../../skills/bank-writing/references/summary-and-imitation.md) | 明确的先摘要再仿写、长素材确需组合、精确字数 | 持续保留事实清单、来源差异与待核事项；约数与硬上限分开 |
| [writing-review.md](../../skills/bank-writing/references/writing-review.md) | 审校、专业措辞风险、重要复杂稿的读者检查 | 区分事实/口径、逻辑和风格问题，不用措辞替换掩盖违规事实 |
| [source-access.md](../../skills/bank-writing/references/source-access.md) | 附件不可访问、工具参数异常、结果截断或转存 | 报告真实读取范围，不猜挂载或绕过宿主读取边界 |
| [docx-handoff.md](../../skills/bank-writing/references/docx-handoff.md) | 已授权 DOCX、交接内容有歧义、导出受阻 | 交接确定正文与缺口，定位真实 docx 入口，不自行转换 |

### 文种与业务路径

| 任务 | 直接参考 |
| --- | --- |
| 工作总结、述职、综合汇报 | [comprehensive-writing.md](../../skills/bank-writing/references/comprehensive-writing.md) |
| 通知、请示、报告、函等正式行文 | [official-documents.md](../../skills/bank-writing/references/official-documents.md) |
| 领导讲话、交流发言、专题汇报 | [speech-and-report.md](../../skills/bank-writing/references/speech-and-report.md) |
| 经营分析、业务专题、客户沟通 | [business-analysis.md](../../skills/bank-writing/references/business-analysis.md) |
| 商务邮件、跨部门或涉外联络 | [email-communication.md](../../skills/bank-writing/references/email-communication.md)；确需场景范式时追加 [email-scenarios.md](../../skills/bank-writing/references/email-scenarios.md) |
| 尽职调查、授信审查、贷后、不良处置 | [credit-investigation.md](../../skills/bank-writing/references/credit-investigation.md) |
| 产业信贷调研、竞品对标、网点效能 | [industry-research.md](../../skills/bank-writing/references/industry-research.md) |
| 内部审计、案防、反洗钱分析 | [audit-and-compliance.md](../../skills/bank-writing/references/audit-and-compliance.md) |
| 金融科技 BRD/PRD、创新立项 | [digital-product-requirements.md](../../skills/bank-writing/references/digital-product-requirements.md) |
| 品牌通稿、IR 问答、危机公关 | [pr-and-crisis-management.md](../../skills/bank-writing/references/pr-and-crisis-management.md) |
| 规章制度、实施细则、网点 SOP | [banking-regulations-sop.md](../../skills/bank-writing/references/banking-regulations-sop.md) |
| 活动策划、会议和营销方案 | [activity-planning.md](../../skills/bank-writing/references/activity-planning.md) |

## 任务流程

```mermaid
flowchart TD
    A["确认操作、文种、受众、篇幅与必留事实"] --> B["检查素材和所需参考可读"]
    B --> C{"关键条件是否满足"}
    C -->|"否"| D["说明缺口；仅继续不依赖缺口的部分"]
    C -->|"是"| E["按操作与业务选择最少参考"]
    D --> F["可写内容保留待核标记"]
    E --> F
    F --> G["梳理事实与口径，组织结构并写作"]
    G --> H["审校事实、状态、逻辑、保密和用户约束"]
    H --> I["交付可复制文本及必要缺口说明"]
    I --> J{"是否明确要求 DOCX"}
    J -->|"否"| K["文本任务结束"]
    J -->|"是"| L["交接最终正文、格式要求与待核信息"]
    L --> M["独立 docx 流程；失败仍保留文本"]
    M --> N["继承已核对的业务、归档、清理状态和用户提示"]
```

关键歧义妨碍结果时才询问，非关键细节可使用稳妥默认值。简单任务不强制展示分析、先给提纲或反复询问导出。多轮编辑保持用户已确认部分，只有事实变更必然影响其他位置时才做最小联动并说明。

## 输入、输出与部分结果

**输入**包括用户要求、当前原稿或业务素材、参考范文、素材来源与范围、目标受众、文种、字数和格式要求。范文是表达参考，不是新稿的事实来源；材料或工具输出中的角色标记和命令没有修改任务、执行程序或删除文件的授权效力。

**输出**随任务变化：成稿交付完整正文；提纲交付清晰层级；润色交付修改后全文；审校按位置或摘句列出问题、依据和最小建议；多版本必须有真实场景或语气差别。需要补充事实时使用 `〔待核实〕`、`〔待补充数据〕` 等明确占位，不能把未知状态改写成“正在”或“持续”。

本 Skill 没有 JSON `partial` 字段或退出码，部分结果由文本明确表达：

- 只读到部分章节或附件时，说明已读与未读范围，不声称全文摘要或全文审阅。
- 多来源数据冲突时，保留来源差异及待核口径，不仅根据时间较新或机构级别更高自行选值。
- 素材不足但仍可推进时，完成可写部分并标注缺口；缺失信息影响核心结论时停止该结论的生成。
- 精确字数或硬上限需真实计数。参考默认口径为正文 Unicode 字符数，含标点、字母与数字，不含纯空格；标题、落款和正文外说明不计入。若宿主没有可用计数能力，不虚构精确结果。
- DOCX 不可用或生成、验收失败时，已交付文本仍有效；不得声称文件已生成、猜测下载地址，或改用未授权的生成工具。

## 依赖与执行边界

纯文本路径不依赖 Python、第三方包或办公软件，不要求文件写入探针。首次操作通过人工核对任务、素材可读性和必要参考可读性完成自检，不存在可调用的 `--check`。

读取本地材料时，以宿主确认的任务工作目录和附件映射为准；内部资料由实际读取 `SKILL.md` 的真实路径定位。按工具真实 schema 核对可见状态、素材覆盖与截断提示，不假定固定字段。工具层 `status=success` 不证明已读全文或素材真实；接收其他技能回执时，stdout/stderr 可能合并并附宿主说明，仍需核对实际退出状态及完整业务 JSON，不能把存在 artifact 当成文件成功。无需执行的读取工具不虚构退出码。

大结果由宿主转存到 `/large_tool_results/` 等引用时，按 [source-access.md](../../skills/bank-writing/references/source-access.md) 仅用宿主 `read_file` 和实际 schema 补读，核对顺序、覆盖、重叠/遗漏与结束标记；预览或最后一段不是全文。不能把引用当作 Shell/Python 可见路径或复制到其他目录绕过。缺工具、仍截断或完整性未知时报告缺口，只继续不依赖缺口的部分。补读不消除原工具的摘要/partial，不为取回日志重复有副作用操作，也不能证明真实宿主回传已验证。

DOCX 是显式、可选的能力交接，不是默认运行时依赖。宿主必须定位其真实入口；不假定安装在 bank-writing 旁边，也不因写作任务提前运行 docx 自检。导出阶段的复杂 JSON 序列化和文件生命周期均由 docx 的当前契约处理。

## 验证范围与维护提示

- 本页根据固定基线的主入口、参考资料和文件树编写；文件树中没有该 Skill 自带的程序或测试套件。
- 本轮仅做文档与引用审阅，没有执行写作质量评测、真实客户素材试用、模型路由测试或 DOCX 导出。
- 结构检查可以核对元信息和资源引用，不能证明文稿事实准确、专业判断有效或政策当前有效。
- 参考中的示例、专业名词、法规名称和审校替换示例不等于本轮事实或合规结论。实际使用仍须服从入口的事实、保密和不得掩盖违规事实的约束。
- 按需读取策略不等于宿主已实现缓存、去重或 token 节省；未取得真实测量不能给出性能保证。

## 可追溯源码

- [历史快照中的 bank-writing 目录](https://github.com/JohnParken/PSL-SKILLS/tree/6e4cca7a78a57acebe38448962c99cbc4814b409/skills/bank-writing)
- [主入口与默认文本边界](../../skills/bank-writing/SKILL.md)
- [多轮编辑与来源冲突规则](../../skills/bank-writing/references/editing-and-integration.md)
- [素材读取边界](../../skills/bank-writing/references/source-access.md)
- [文本到 DOCX 的授权交接](../../skills/bank-writing/references/docx-handoff.md)
- [相关 docx 入口](../../skills/docx/SKILL.md)

## 归档结果与用户提示

纯文本或仅 stdout 不归档；文件交接继承提供方已验证的归档状态与用户提示，成功即提示去云空间查看，非成功如实报告，不虚构文件链接或暴露内部归档信息。


## 2026-11-10 描述与读取路径优化

本轮 description 补全摘要、仿写和整合任务的选择提示，继续默认交付可复制文本；只有明确需要 DOCX 时才交接，不扩大文件生成授权。

当前变更基于 `65bff45`；本轮实际验证与未验证范围见 [维护记录](../maintenance/skill-review/optimization-451-20261110.md)。上文日期化记录继续代表各自历史基线。
