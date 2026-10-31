# 验证方法、样本证据与未验证范围

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 目录

- 当前验证范围 (`<skill-root>/references/validation.md#当前验证范围`)
- 分支增量：MS-DOC 段落来源范围 (`<skill-root>/references/validation.md#分支增量ms-doc-段落来源范围`)
- 可复现的本地检查 (`<skill-root>/references/validation.md#可复现的本地检查`)
- 样本与未验证边界 (`<skill-root>/references/validation.md#样本与未验证边界`)
- 历史证据 (`<skill-root>/references/validation.md#历史证据`)

## 分支增量：MS-DOC 段落来源范围

本次分支针对二进制 MS-DOC 的原始段落 CP 范围增加**默认关闭**的可选来源信息：`--binary-paragraph-sources`。默认 JSON/退出码和原有文本处理不变；对经过字段过滤、占位符或标记替换的故事明确拒绝输出虚假精确来源。未新增 WPS 格式兼容模式，不修改 CFB/CLX 安全边界。

本轮已在 Linux/Python3.13 标准库环境，对独立 CP 范围算法运行 **5 项隔离单元测试，全部通过**。合成 CP 计算测试的结果不能代替完整解析器验收；可选字段模式无实际文本转换时保留来源，实际字段/占位替换时明确放弃精确对齐。仓库分支新增 11 项 MS-DOC/CFB 合成集成测试。**GitHub Actions Linux** 两个独立环境（Python 3.11、3.13）均完整执行 WPS **435 项测试且全部通过**，环境自检通过、`check_skill.py skills/wps --strict` 通过（运行 `41178515332`，基于提交 `859106446b74f34e3d3b50dc287a2b42dbcd198d`）。这不是目标业务容器的真实宿主集成验证，也不是原生 WPS 文件的语义验收。没有真实 WPS 原生全文及独立段落位置预期，本次不宣称实际生产兼容性提升已被验证。分支审查完成前保持候选状态。

## 当前验证范围

历史基线 `20261029.1850.4782` 新增全局 DXF（0x088D）的结构读取与原始属性保留；data 自动选择已有精确 Word 头兼容路径，data/legacy 接受已观察的 CFB minor 0x003B 并诊断。strict 仍要求 minor 003E 与零头 GUID。未知私有记录不跳过，格式属性不应用于保存值，不宣称完整 WPS 私有格式支持。共用文件有界读取、加密/歧义流及资源门禁，不在失败后扫描或轮换策略。

机器可读证据 (`<skill-root>/references/reading-enhancement-evidence.json`) 的 `current` 为该开发分支当前摘要，已更新为上述 GitHub Actions 成功运行证据；`history` 只说明对应旧版，不作为当前版本、测试数量或包状态。历史基线在 Darwin arm64、Python3.12.14/Expat2.8.3 下 WPS 424 项与仓库表格分析接入 2 项通过，无失败或跳过。新增测试包括原文/保存值/缓存保留、CLI 退出 3 与完整 JSON、原件不变、DXF 错误边界/作用域/属性预算及 JSON 可序列化。本轮不重跑真实样本、不生成发布包；未运行 DOCX/OFD 全量测试或模型触发评测。Linux 目标容器不可用（docker/podman 未安装），Windows 未执行；本机结果不替代目标验收。结构检查、合成回归和真实文件语义验收分别报告。

## 可复现的本地检查

先完成 入口自检 (`<skill-root>/SKILL.md#执行环境约束与首步自检`)，替换 `<skill-root>`，在宿主确认的任务目录运行：

```console
python -I -S -B "<skill-root>/scripts/read_wps.py" --check
python -I -S -B -m unittest discover -s "<skill-root>/scripts" -p "test_*.py"
```

自检要求退出 0 和 `environment-ok`，并单独确认 OOXML 所需 Expat 可用；测试要求退出 0，区分通过与跳过。运行 契约示例 (`<skill-root>/references/reading-assessment.md#可运行的契约示例`) 要求退出 0 和 `contract-examples-ok`。独立包验证检查 ZIP CRC、包内路径、每个文件与源码的 SHA-256、隔离运行及执行前后安装文件不变。

仓库严格检查是维护工具，不是独立运行包的依赖。维护者从仓库根目录使用 Python 3.10+ 运行 `python -I -S -B skills/skill-development-standards/scripts/check_skill.py skills/wps --strict`；普通使用无需获取相邻技能或仓库检查器。


## 样本与未验证边界

- 允许的四份原始ET/ETT有固定来源和SHA；198个保存值与独立冻结观察对照，其中33个公式缓存未重算、不能证明最新。空ET不证明有内容ET兼容；具体范围见真实ET/ETT验收 (`<skill-root>/references/et-real-validation.md`)及固定预期 (`<skill-root>/references/real-value-expectations.json`)。
- 新增CodePage声明、普通Unicode头保留位兼容目前主要是合成验证；已有样本没有这些新情形，不据其回归宣称新增能力已获生产验收。真实用户仍失败的无图片ET/ETT与独立预期尚缺。
- 本批 CFB 兼容路径及 DXF 仅有合成验证；缺少反馈原件，不能报告原失败已复现或真实修复验收。DXF 校验 FrtHeader、作用域标志、XFProps 数量、属性类型及长度边界、填充/渐变互斥和内部边框标志；属性负载仍是原始十六进制，不验证其内部值语义或完整格式效果。每记录最多 1024 属性、全局合计 4096，另受原有 8224 字节记录及元数据预算限制。
- 真实`.wps`正文/二进制表格生产来源验收未完成；合成二进制一级行单元格证据不是完整网格、合并或布局还原。真实验收方法见文档验收 (`<skill-root>/references/wps-real-validation.md`)。
- 不验证页面视觉、完整原生WPS格式、私有流等价、公式重算、宏/外链执行或显示顺序；原值、格式解释、缓存与缺口按结果契约 (`<skill-root>/references/reading-assessment.md`)分别消费。
- 独立模型选择和本地前向任务不等于真实宿主自动发现/触发；专用命名linter未安装，语法/人工命名审阅及结构检查不能冒充其结果。

## 历史证据

旧版环境、计数、描述字符数和默认策略仅保留在证据history (`<skill-root>/references/reading-enhancement-evidence.json`)及仓库维护记录，避免与当前契约混用。历史记录中的依赖工具或验证方法不授权当前标准库流程调用外部软件。业务读取只按当前入口和实际结果处理，不必先阅读维护历史。
