# 真实 WPS 文档的离线验收

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

先完成 入口自检 (`<skill-root>/SKILL.md#执行环境约束与首步自检`)。本验收器只读样本及预期清单，不下载、不生成/修改办公文件，不导出正文到报告。

## 输入与命令

在已确认的任务目录准备获准使用的原始 `.wps` 和独立内容预期 JSON。两个文件都必须使用任务目录内的相对路径；绝对路径、父目录、解析到任务目录外的符号链接会被拒绝。不要使用 DOC/DOCX 改后缀样例作为真实 WPS 来源证据。

```console
python -I -S -B "<skill-root>/scripts/verify_wps_sample.py" ./samples/original.wps ./expectations.json
```

清单上限 1 MiB，样本上限 32 MiB，均必须是可读普通文件。报告 JSON 的 `status=passed`、`passed=true` 且退出 0 才是字节和预期匹配；失败报告退出 1。参数解析错误退出 2，不是验收结果。

## 预期清单

根对象只允许以下字段，所有字段必填：

| 字段 | 要求 |
|---|---|
| `schema_version` | 固定 `wps_sample_expectations_v1` |
| `sha256` | 原件的 64 位小写十六进制 SHA-256 |
| `bytes` | 原始字节数，正整数，不接受布尔值 |
| `provenance` | 必须含非空字符串 `source`、`original_filename`、`producer_evidence`、`independent_expectation_source`；原文件名须以 `.wps` 结尾且不带目录 |
| `options` | 读取参数对象；不用参数时为 `{}`，不自动轮换兼容策略 |
| `expected` | 必须含 `status`、`format`、`text`、`paragraphs`；可另含 `binary_table_rows` |

`options` 仅允许统一读取入口已有的 `compatibility`、`field_policy`、`object_policy`、`include_related`、`hidden_text`、`revisions`、`chart_policy` 及各自合法枚举。指定策略不保证该格式分支支持，实际拒绝算验收失败。

`expected.status` 为 `ok` 或 `partial`；`format` 如 `ms_doc_subset`。`text` 是完整主正文的精确预期，`paragraphs` 是精确段落字符串列表；二者必须包含有意义的非空内容，不能用空白样本代替正文验收。可选 `binary_table_rows` 是按读取顺序的二维字符串列表，每个内层列表对应一行的单元格文字；空单元格使用空字符串。段落换行、TAB 和末尾换行必须与契约一致，不归一化后比较。

独立预期应在调用目标解析器之前由可信来源、人工校对或独立实现确定，并说明来源。不得从本解析器输出直接复制生成“独立预期”，也不得为了通过而改写原件或放宽预期。

## 报告的准确含义

验收先校验来源字段的结构及原件哈希，再逐项比较状态、格式、主正文、段落和可选主正文表格行。再次读取并比较哈希及文件状态，要求样本未变。报告只包含检查布尔值及失败代码，不复制正文；原件及清单本身也不写回。

`provenance_status=declared_requires_human_review` 始终提醒：脚本不能证明声明真实、确认生产软件或辨别一个文件是否曾被改后缀。`full_format_verified=false`，即使通过也不代表完整 WPS 兼容。附加故事不在当前预期字段中，不能因 `include_related=true` 就声称脚注/页眉等内容已逐项验收。

验证器的合成测试只验收工具行为。当前真实 `.wps` 正文及二进制表格仍缺来源可核实原件与独立完整内容/结构预期，不能报告真实验收已补齐。既有四份 ET/ETT 的真实保存值验收另见 记录 (`<skill-root>/references/et-real-validation.md`)，不可用它们替代 `.wps` 正文验收。

## 公开候选样本的独立算法交叉核对（2026-10-31）

已在维护 CI 中使用 `listeng/wps2md` 的公开测试文件 `tests/sample.wps`（固定提交 `c4ae028156e44ed8b57ef879dc7bd1d14750c0f1`、SHA-256 `59ea15f76da2ae5a9cf9592a2c7623918c8e385dc9f587233a44ce166b2e5e8d`）。测试时临时下载，经 Git blob SHA-1、SHA-256 和 125952 字节长度核对后，读取器返回 `partial` / 24 个主正文段落；上游测试文件明确给出的 5 个正文文字锚点全部出现。

另外，维护脚本 `tests/wps_public_story_oracle.py` 使用与生产段落解析器分离的 `struct` 逻辑独立解析 FIB/CLX/Pcdt；只复用 WPS Skill 的有界 CFB 容器层。对上述样本的 608 个 UTF-16 码元、24 个段落，**目标读取器输出正文与独立算法结果全文和段落数组均一致**；固定哈希、来源声明、CI 与缺口见候选样本证据 (`<skill-root>/references/wps-public-candidate-evidence.json`)。未将公共二进制样本纳入 Skill 分发包，运行时不联网、不安装或调用外部软件。

**这不是经证实的 WPS Office 生产来源或真实软件人工完整语义验收。** GitHub 公开仓库只能证明文件的公开可追踪性，不能自动证明具体创建工具/版本；两个独立文本算法一致也不能证明它们与 WPS 显示完全一致。表格合并、字体/段落最终格式、分页、批注及 CHPX/PAPX 关联均缺独立原生样本对照。符合本文件的“真实原件 + 独立人工/软件内容预期”级别验收依然**未完成**，不能将此候选验证替代 `verify_wps_sample.py` 的严格原生样本验收。
