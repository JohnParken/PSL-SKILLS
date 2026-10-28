---
name: wps
version: "20261027.2320.5486"
description: 用于离线读取 WPS 文字（.wps）的正文和有限表格结构，以及 WPS 表格（.et/.ett）的工作表、单元格保存值与公式缓存；报告部分读取和未支持范围。未知私有格式不猜测，不重算公式，不生成、编辑、转换或还原页面布局。WPS 软件保存的原生 DOCX/XLSX 使用对应技能。
---

# WPS 家族只读提取

用于读取 `.wps/.et/.ett` 的文字、单元格和保存的公式缓存；按实际内容识别 MS-DOC、OOXML Word、OOXML 电子表格或 BIFF8 有限子集。原生 DOCX/XLSX 任务使用对应技能；本技能不生成、编辑或转换办公文件，不恢复页面布局，不执行公式、宏、外链或嵌入对象。未知容器和私有格式明确拒绝，有限读取不等于完整 WPS 兼容。

## 执行环境约束与首步自检

1. 在 harness 确认的任务工作目录运行，输入、输出与临时文件使用该目录及子目录的相对路径；不使用绝对数据路径、父目录或切换挂载绕过边界。若当前目录是 Skill 源码目录，先由 harness 明确任务目录，不直接生成任务文件。
2. 由实际读取本文件的 `file_path` 去掉 `SKILL.md` 确定 `<skill_dir>`；内部脚本和资源以它为基准，Markdown 链接相对所在文件解析。执行前替换占位符并正确引用完整路径，不猜测其他会话或掩码路径，不为调用脚本切换到安装目录。
3. 使用宿主确认可用的 Python 3.9+，按 [requirements.txt](requirements.txt) 核对仅需标准库；禁止安装包、运行时联网或调用办公软件。保留 `-I -S -B`，首次读取前运行：

```console
python -I -S -B "<skill_dir>/scripts/read_wps.py" --check
```

4. 要求退出 0 且输出 `environment-ok`。全部 OOXML 分支另需 Expat >=2.6；自检的 `ooxml-word=available` 表示此条件满足，`unavailable` 时只可读取二进制子集，不尝试 OOXML 解析。自检仅检查基础入口和所导入模块，不代表所有可选分支或文件已验收。
5. 确认输入为可读普通文件。仅读取无需写探针；保存结果时，在任务目录以独占探针实际写入、读回并清理，避免覆盖已有文件。自检失败、缺少成功标记或结果不明时停止依赖操作，环境修复后再验，不降低要求。
6. 自检不代表产物正确或具备下载能力。交付前核对实际结果、目标文件及宿主支持的交付方式；不构造猜测链接。压缩后若指令缺失，重读本文件，保留已确认的任务目录和文件名。

## 电子表格：读取 ET/ETT 保存值

完成共同自检后，在任务目录运行；将文件名换成实际输入，保留默认 JSON 和 `data` 策略，不需要先选择兼容参数：

```console
python -I -S -B "<skill_dir>/scripts/read_wps.py" ./input.et
```

1. **核对结果。** 退出 0 或 3 均读取完整 JSON；电子表格 `schema_version` 应为 `biff8_readonly_v1` 或 `ooxml_spreadsheet_readonly_v1`。退出 3 保留已读数据并报告 `issues` 与 `reading_assessment` 中的缺口；退出 1 或 2 停止后续分析，不把失败当空表。自动调用不要使用把退出 3 当失败丢弃的 `check=True`，见 [标准库调用示例](references/reading-assessment.md#消费电子表格-json)。
2. **解释保存值。** 按单元格 `type` 解释 `value`，保留数字字符串的精度和文本编号；`numeric_interpretation` 仅补充格式解释，不能替代原值。`formula` 存在时读取的是保存缓存，结合 `cache_status` 区分缺失、错误和未验证缓存，不能保证最新或重算。BIFF 的 `cell_value_coverage`、`omissions_affect_cell_values` 与原始 `issues` 区分缓存缺失和元数据遗漏，不能据此宣称完整格式支持；详细字段见 [结果契约](references/reading-assessment.md#结果字段与范围评估)。
3. **交付或分析。** 提交所需工作表/区域及明确缺口；需要汇总时先确认 schema、选定区域和缓存政策。如另有 xlsx 技能，可消费满足其限制的 JSON，仍保留源 partial 和诊断；本包不依赖相邻技能。BIFF 合并表头重建及适配器每表 256 个合并范围限制见 [分析接入](references/biff8-readonly.md#headers-and-output)。

默认 data 对有证据的非单元格元数据及受支持图表保留原始记录并报告 partial；未知记录、加密、结构与资源错误仍停止，不在失败后轮换策略。只有任务明确要求更窄子集才加 `--compatibility strict`；单独拒绝图表可加 `--chart-policy reject`。需排查格式时按结果读 [BIFF8 边界与高级策略](references/biff8-readonly.md) 或 [OOXML 保存值](references/ooxml-spreadsheet.md)；编码、容量及失败定位按需读 [解析增强边界](references/et-parser-stability.md)，容器检查另见 [CFB 调查](references/et-ett-readonly.md)。

## 文字：读取 WPS 正文与附加内容

```console
python -I -S -B "<skill_dir>/scripts/read_wps.py" ./input.wps
```

同样按退出 0/3 保留结果、1/2 停止处理。仅需正文时可加 `--format text`；电子表格不支持 text。文字不代表页面布局或完整表格还原。按任务加载以下参考，不将文字选项用于电子表格：

| 用户任务 | 操作与按需参考 |
|---|---|
| 正文或字段/对象边界 | 默认读取；具体容器、字段与对象策略见 [格式边界](references/format.md)。 |
| 脚注、尾注、批注或页眉页脚 | 加 `--include-related`，仅 JSON；`related_stories` 与正文分开。按容器读 [MS-DOC 存储区域](references/ms-doc-stories.md) 或 [OOXML 引用](references/ooxml-related-stories.md)，不推断有效页码或页眉继承。 |
| 隐藏文字或修订视图 | 先读 [隐藏与修订策略](references/word-visibility.md)。OOXML 默认 include/accepted；二进制不能过滤隐藏或修订。未知状态保留并说明，不能作为脱敏保证。 |
| 文档表格 | OOXML JSON `tables` 提供逻辑网格和来源，见 [结构化表格](references/ooxml-tables.md)。MS-DOC 仅提供有直接属性证据的一级行/单元格，合并与表格归组未知，见 [二进制表格边界](references/binary-tables.md)。 |

## 结果与按需验收

- `status=ok`、退出 0：声明范围内成功，允许空正文或空工作表，不等于完整格式认证。
- `status=partial`、退出 3：保留提取结果并说明已知缺口。MS-DOC 默认省略非空附加区域或遇表格边界也可返回 partial；纯文本诊断另写 stderr。
- 读取失败退出 1；参数错误退出 2。输出通道、Python API 异常与可运行示例见 [结果与失败契约](references/reading-assessment.md)。

需要支持范围或拒绝原因时读 [格式矩阵](references/format.md)。仅做维护验收时读 [验证方法与限制](references/validation.md)、[真实 ET/ETT 记录](references/et-real-validation.md) 或 [真实文档验收](references/wps-real-validation.md)。真实 `.wps` 验收须固定原件哈希、来源声明和独立完整预期；来源声明本身不是生产来源证明。合成测试、容器识别和真实语义验收分别报告，不据测试数量推断兼容率。
