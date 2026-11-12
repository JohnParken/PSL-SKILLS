# BIFF8 展示预算拆分与 RString 读取（2026-11-12）

仓库维护记录，不随 Skill 运行包分发。当前 ET 版本 `20261112.1731.0300`；修复前为 `20261112.1629.4300`。本记录处理最新复核中的最高优先级两项：保存值被展示/格式预算拖垮，以及 RString 值记录缺失。原始 ET/ETT 未提交、未随包分发。

## 问题是否真实

### 1. 自定义格式耗尽值文字预算：真实，已修复

复现：32,897 个 Number 单元格共用同一个 255 字符自定义格式。修复前 `put()` 把每格值文本和格式文本都计入同一个 8 Mi UTF-16 单位预算，超过 8,388,608 后抛出：

```text
ResourceLimitError: Repeated cell text exceeds output text budget
```

CLI 退出 1，返回 `resource_limit`，没有提取结果。这正是展示元数据占用值读取预算。

修复后：

- 值文本单独使用 `MAX_OUTPUT_TEXT_UNITS = 8Mi`；
- 自定义格式、批注、链接等展示文本使用 `MAX_DISPLAY_TEXT_UNITS = 2Mi`；
- 格式文本按 number-format id 去重计费；
- 展示预算超限只省略展示字段并附 `display_text_budget_truncated`，保存值继续返回；
- 结果 `output_text_budget` 报告两套预算用量、上限与 `display_limited`；
- `custom_format_raw_omitted`、`custom_number_formats_omitted` 明确省略范围。

同一 32,897 格场景修复后：`status=ok`、32,897 格全部返回、`display_units=255`、无失败。

### 2. RString 值记录未支持：真实，已修复

BIFF8 `RString (0x00D6)` 此前未在 `CELL_RECORDS` 中处理，落为未知记录。合成探针：

- A1 RString 文字丢失；
- B1 Number 正常；
- 覆盖被诚实标为不确定；
- 产生 `unknown_biff_record_salvaged`。

修复后：

- 先解出有界 plain Unicode 文字，再读取可选 rich-run 尾部；
- run 尾部损坏时保留已确定文字并附 `rstring_format_runs_invalid`（metadata-only）；
- run 存在时返回 `rich_text_runs`/`runs_raw_hex`，附 `rstring_rich_format_not_applied`，不应用字体；
- 文字本体损坏才设置值遗漏；
- 后续单元格不受影响。

## 修改

- `skills/et/scripts/wps_biff8.py`：新增 `MAX_DISPLAY_TEXT_UNITS`、value/display 分开计费、展示预算去重 decision、`reserve_display_text()`、结果预算字段。
- `skills/et/scripts/wps_biff8_sheet.py`：`put()` 格式文本改用展示预算；HLink/HLinkTooltip 展示文本改用展示预算；`RString` 加入单元格分派。
- `skills/et/scripts/wps_biff8_cells.py`：新增 `_handle_rstring()`，纯文字优先、run 证据有界。
- `skills/et/scripts/wps_biff8_drawing.py`：批注文字/作者改用展示预算，超限省略展示文本但保留 comments 关联。
- `skills/et/scripts/wps_biff8_result.py`：输出 `output_text_budget`、省略格式表和标记。
- 测试：`test_wps_biff8_rstring.py`（4 项）、`test_wps_biff8_output_budget.py`（3 项）；更新旧 unhandled-record 测试。
- 文档：SKILL、BIFF8 输出、format 预算表。

## 验证

平台 macOS 27.2.1 arm64、Python 3.14.10、Expat 2.7.4，`-I -S -B`，仅标准库。

| 检查 | 结果 |
|---|---|
| ET 全量测试 | 643 通过（新增 11 项：4 RString、7 展示预算/compact/OOXML） |
| 32,897 格 + 255 字符格式实测 | 修复前 `resource_limit` 无结果；修复后 32,897 格全返回，display 255、无失败 |
| RString 合成 | 文字/run 返回；坏 run 保留文字；坏文字才遗漏 |
| 结构/文档/差异/运行包/table-workflow 检查 | 全部通过 |

## 未验证与边界

- 32,897 格为合成 CFB v4；不是真实 WPS 原件，但复现了相同预算路径。
- RString 为合成 MS-XLS 记录；未取得真实 WPS RString 原件，也未做跨 Continue RString 验证（当前实现只读单记录内文字/run，Continue 仍按未知边界处理）。
- BIFF8 展示预算覆盖格式/批注/链接；OOXML 本轮也加入自定义格式展示预算（按 numFmt id 去重、超限省略 `custom_format_raw`），但 OOXML 批注/链接展示文本和真正按表/范围按需读取未在本轮实现。
- 未执行 Linux、Windows、客户端视觉、宿主大结果或 Pylint；Pylint 未执行（解释器无 pylint）。

## OOXML 展示预算（同轮）

OOXML 的 `TextReader` 也增加独立展示预算（默认 2Mi 代码点）；单元格自定义格式文本按 `numFmtId` 去重计费，超限时省略 `custom_format_raw`、从顶层 `custom_number_formats` 移除受影响格式并设置 `custom_format_raw_omitted`/`custom_number_formats_omitted`，保存值继续读取。结果 `text_budget` 增加 `display_units`、`display_limit`、`display_limited`。

新增 2 项 OOXML 展示预算回归；与 BIFF8 一样，`complete` 仅表示保存值未缺失，不表示格式/样式语义已验证。OOXML 批注/链接展示文本尚未接入独立预算。
