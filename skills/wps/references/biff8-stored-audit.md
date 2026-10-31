# BIFF8 原始证据审计：富字符串、公式缓存与 XF

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 目标与范围

这是 `.et/.ett` 的 **BIFF8 独立只读增强工作线**。面向现有 BIFF8 读取器已经解析出的 `biff8_readonly_v1` JSON，额外汇总以下“存储证据”：

- **SST 富文本**：从已验证的 `shared_string_formatting[].runs_raw_hex` 获取每个 run 的 UTF-16 CP 半开区间、原始 `FontIndex`，验证被单元格引用的文本是否与已读取的字符单位数一致；遇到代理对中间切分则不声称有效字体区间。仅有原始字体索引，不推断**最终字体**或渲染。未引用的 SST 字符串无法通过公开的单元格结果反推原文，只返回来源不明标记。
- **Formula 保存缓存**：统计各工作表 `cache_status`（数值、文本、布尔、错误及缺失），保留“需重新计算”标志计数及可能过时风险。**不执行公式、不解码表达式、不验证缓存是否最新，不将缺失当作零或空字符串**。
- **复杂 XF 格式证据**：从已经读取的 20 字节 XF 记录保留原始字体索引、数字格式标识及其余格式/对齐字节，始终标记 `effective_formatting=unknown`、`style_inheritance_resolved=false`；不将格式字节解释成业务最终显示效果。

该独立审计器不修改 `read_wps.py`、`wps_biff8.py`、`wps_ooxml.py`，默认普通读取 JSON、文字读取分支、`ok/partial` 及退出码均保持原状。它**只消费**现有受控 BIFF8 结果；不能对 DOCX、MS-DOC、OOXML 工作簿或其他 WPS 私有容器调用。没有引入 WPS/LibreOffice、第三方包、网络或写入。

## 操作步骤

先从任务工作目录执行目标 Skill 的自检，并核对 `environment-ok`。由实际读取 `SKILL.md` 的 `file_path` 计算 `<skill-root>` 并替换为真实目录；输入仍使用宿主确认的任务目录相对路径，不切换到源码目录：

```console
python -I -S -B "<skill-root>/scripts/read_wps.py" --check
python -I -S -B "<skill-root>/scripts/wps_biff8_audit.py" ./input.et
```

入口输出单份独立 JSON，`schema_version=biff8_stored_evidence_audit_v1`，只包含证据索引、CP 区间、公式状态计数和原始 XF 元数据，**不回传工作表文本或公式计算结果**。若上游 `read_wps.read` 返回 `ok` 则退出 0、`partial` 则退出 3；上游不支持、校验失败或输入不是 BIFF8 子集则退出 1，按 `source_status` 说明，不重试兼容策略。输入原件不会被修改。

## 结果字段与拒绝边界

`rich_format_runs[].status` 可以是 `direct_run_locations_only`、`unreferenced_string_text_unavailable`、`conflicting_cell_text`、`text_units_do_not_match_metadata`、`run_splits_unicode_scalar` 或 `audit_span_budget_exceeded`。实际位置仅以 UTF-16 码元计数；末尾 `FormatRun` 哨兵不作为文字格式区间，首个 run 前未指明的内容标记为 `unattributed_prefix_utf16_units`。如果 run 落在 Unicode 代理对中间，直接返回空区间及原因，**不臆造样式**。

`formula_caches` 的每张表和汇总中包含 `cache_status_counts`，`possibly_stale` 和 `recalculation_flagged`，但没有具体公式执行结果。任何 `unverified`、`error_unverified`、`unverified_empty_string`、`missing` 均不证明最新或计算正确。已有 `cell_value_coverage` 和原始诊断以读取器原 JSON 为准，审计输出不替代业务数据。

`xf_sources` 仅包含 `xf_index`、`font_index_raw`、`number_format_id_raw`、`format_and_alignment_tail_hex` 及未知格式说明。不推断合并单元格、列宽、字体效果、边框生效状态或日期显示结果。

受控上限：128 工作表、200000 单元格、4096 个带格式 SST 条目、20000 个详细 run 区间、4096 个 XF。超出区间预算时清空受影响条目并返回 `audit_span_budget_exceeded`，不声称完整输出；超出主要输入预算则拒绝。本次仅针对已有合成 BIFF8 样本进行单元测试，没有新增独立 WPS Office 原生富文本/公式缓存/格式的应用级验收。原始真实 ET/ETT 文件样本及范围另见真实样本核验（`<skill-root>/references/et-real-validation.md`）。

## 回归检查

Linux 自动化完成了 Python 3.11 与 3.13 环境的独立脚本测试（各 10 项）、WPS 全量回归（各 434 项）和严格 Skill 检查，均通过。实际 CI、初次 CLI 隔离导入问题及未完成的原生 ET/ETT 应用级验证记于机器证据（`<skill-root>/references/biff8-stored-audit-evidence.json`）。这不是原生富文本/复杂格式的真实生产者验收。

本工作线的验证应包含以下命令（按真实已确认的仓库路径或 Skill 实际根目录执行）。严格 Skill 检查的脚本是**仓库维护入口**，不是分发 Skill 的运行时依赖：

```console
python -I -S -B "<skill-root>/scripts/read_wps.py" --check
python -I -S -B -m unittest discover -s skills/wps/scripts -p 'test_*.py' -v
python -I -S -B skills/skill-development-standards/scripts/check_skill.py skills/wps --strict
```

执行前从 harness 确认是否在仓库维护工作目录；后两条命令所用路径以仓库根目录为基准，不能在普通用户任务中照抄并假定存在。
