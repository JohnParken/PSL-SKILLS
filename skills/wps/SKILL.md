---
name: wps
version: "20261025.2304.0172"
description: 用于离线读取 .wps/.et/.ett 文件中可识别的文字、电子表格单元格和保存的公式缓存，并报告部分读取或不支持的范围。按实际内容读取有限格式子集，未知私有格式不猜测解析，不重算公式、不生成、编辑或转换办公文件，也不恢复页面布局；WPS 软件保存的原生 DOCX/XLSX 应使用对应技能。
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

## 默认读取

```console
python -I -S -B "<skill_dir>/scripts/read_wps.py" ./input.wps
```

将输入名换成实际 `.wps/.et/.ett`；默认严格策略和 JSON 输出。先阅读结果的 `status`、范围和诊断，再决定是否需要下表中的选项。兼容策略只在理解对应格式边界后显式选择，不自动轮换策略以取得成功。

## 按任务选择

| 用户任务 | 操作与按需参考 |
|---|---|
| 只读文档正文 | 使用默认命令；只需纯文本时加 `--format text`。文字不代表页面或二进制表格行列还原；具体容器、字段与对象策略见 [格式边界](references/format.md)。 |
| 读取脚注、尾注、批注或页眉页脚 | 加 `--include-related`，仅 JSON；`related_stories` 与主正文分开。按容器阅读 [MS-DOC 存储区域](references/ms-doc-stories.md) 或 [OOXML 引用](references/ooxml-related-stories.md)，不声称恢复有效页码或页眉继承。 |
| 选择隐藏文字或修订视图 | 先读 [隐藏与修订策略](references/word-visibility.md)。OOXML 可选 `--hidden-text include\|exclude\|annotate` 和 `--revisions accepted\|original\|all`；默认 include/accepted。二进制仅默认或 revisions=all、hidden-text=include/annotate，不能过滤隐藏或修订。未知状态保留并说明，不能用作脱敏保证。 |
| 读取文档中的表格 | 默认 JSON 的 OOXML `tables` 提供逻辑网格和来源，见 [结构化表格](references/ooxml-tables.md)。MS-DOC 只有文字和制表符，不重建行列。 |
| 提取电子表格单元格与缓存 | 仅 JSON，保持文档选项默认。按实际结果阅读 [BIFF8](references/biff8-readonly.md) 或 [OOXML 保存值](references/ooxml-spreadsheet.md)；原值、缓存和有限数字解释分开，缓存可能过时，不重算。 |
| 分析电子表格结果 | 本包可用 `wps_biff8.py` 显式重建 BIFF 合并表头，见 [表头与输出](references/biff8-readonly.md#headers-and-output)。如另有 xlsx 技能，可将保存的 JSON 交给其分析命令；本包读取不依赖相邻技能，也不转换原文件。 |

## 解释结果与验收

- `status=ok`、退出 0：声明范围内成功，允许空正文或空工作表，不等于完整格式认证。
- `status=partial`、退出 3：已提取内容但存在已知缺口；阅读 `issues`、`diagnostics` 及 `reading_assessment`，交付时说明缺口。纯文本部分结果的诊断另写 stderr。
- 读取失败退出 1：无部分正文；CLI 参数解析错误退出 2。具体失败状态、输出通道、Python API 异常及可运行示例见 [结果与失败契约](references/reading-assessment.md)。

需要判断支持范围或拒绝原因时读 [格式矩阵](references/format.md)；需要验收证据时读 [验证方法与限制](references/validation.md) 和 [真实 ET/ETT 记录](references/et-real-validation.md)。合成测试、容器识别和真实格式语义验收分别报告，不据测试数量推断 WPS 兼容率。
