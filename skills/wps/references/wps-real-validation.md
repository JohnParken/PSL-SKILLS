# 真实 WPS 文档的离线验收

先完成 [入口自检](../SKILL.md#执行环境约束与首步自检)。本验收器只读样本及预期清单，不下载、不生成/修改办公文件，不导出正文到报告。

## 输入与命令

在已确认的任务目录准备获准使用的原始 `.wps` 和独立内容预期 JSON。两个文件都必须使用任务目录内的相对路径；绝对路径、父目录、解析到任务目录外的符号链接会被拒绝。不要使用 DOC/DOCX 改后缀样例作为真实 WPS 来源证据。

```console
python -I -S -B "<skill_dir>/scripts/verify_wps_sample.py" ./samples/original.wps ./expectations.json
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

验证器的合成测试只验收工具行为。当前真实 `.wps` 正文及二进制表格仍缺来源可核实原件与独立完整内容/结构预期，不能报告真实验收已补齐。既有四份 ET/ETT 的真实保存值验收另见 [记录](et-real-validation.md)，不可用它们替代 `.wps` 正文验收。
