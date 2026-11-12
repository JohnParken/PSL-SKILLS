# ET 文本解码、覆盖与缓存契约修复（2026-11-12）

仓库维护记录，不随 Skill 运行包分发。当前 ET 版本 `20261112.1447.4600`；维护前为 `20261112.1356.4600`。本轮针对外部复核报告先复现、再修复，未改变公式计算、宏/外链安全边界或写回行为。保留进入任务前的未提交修改，未提交、推送或覆盖历史发布 ZIP。

## 对复核结论的核对

复核指出的三类正确性问题和文字预算问题均有代码与可复现证据；性能判断未在本轮测量，不作为已确认结论。

1. **OOXML 保存文本 `_xHHHH_` 未解码：真实且优先。** 复核用 XlsxWriter 3.2.9 生成的正常文件在当前版本读成 `A_x000D_B`、`A_x0001_B`、`_x005F_x000D_`，却报告 `complete_within_declared_sheets`。本轮用同一版本独立生成器在 Python 3.9 下再次生成共享字符串样本，当前版本复现；修复后分别得到回车、控制字符和字面 `_x000D_`，并保留原始编码证据。该问题影响文本匹配、去重、筛选和跨表关联，优先级高于继续增加元数据。
2. **跳表后仍报告完整覆盖：真实。** 构造 BoundSheet8 偏移不匹配和工作表 BOF 无效两份 BIFF8 样本：修复前 BOF 无效路径为 `complete_within_declared_sheets`/`omissions_affect_cell_values=false`；修复后为 `unverified_due_to_best_effort_salvage`/`true`，`sheet_coverage.not_read` 与 `read_status=not_read` 一致。偏移不匹配路径也显式接入覆盖状态；此前是否偶然显示不确定取决于是否存在被 salvage 的前置记录，不能依赖。
3. **空字符串公式缓存读取成功但统计失败：真实。** BIFF8 Formula 特殊缓存 `unverified_empty_string` 的 `value=''` 被统计端当作未知缓存状态抛出 `Unknown formula cache status`。修复后 `formula_policy=cached` 将其作为存在的空文本缓存消费，仍与 `missing`、错误缓存和未知编码区分。
4. **文字预算未统一：真实。** 内联字符串路径不调用预算；共享字符串分支调用后忽略返回值。将 `MAX_TEXT` 调小复现：修复前内联 24 字符在限额 10 下仍全部保留且无文字 issue/覆盖仍完整；共享字符串保留 24 字符而只计费 10。修复后两种路径都只保留 10 字符、标记 `text_truncated`、产生 `text_limit_reached` 并使覆盖不确定，`text_budget` 报告单位/上限/实际保留数。

## 修改范围

- 新增 `skills/et/scripts/wps_ooxml_escape.py`：`_xHHHH_` 单次解码、`_x005F_` 字面保护、代理对合并、孤立代理项保留字面转义与 issue；不回写、不执行。
- 新增 `skills/et/scripts/wps_ooxml_text.py`：把 `TextReader` 从工作表模块拆出，统一保存文本解码、编码证据和 Unicode 代码点文字预算；原模块保留兼容导出与行数限制。
- `skills/et/scripts/wps_ooxml_worksheet.py` / `wps_ooxml_sheet.py`：共享/内联/富文本/`t="str"` 文本解码与统一预算；单元格 `text_encoding`、工作簿 `shared_string_encoding`、`text_truncated`、`text_budget`；共享字符串格式化证据不再冒充普通字符串。
- `skills/et/scripts/wps_biff8_sheet.py` / `wps_biff8_result.py`：`read_status`、`sheet_coverage`；偏移不匹配/重叠/BOF 无效/缺失 EOF 显式覆盖状态。
- `skills/et/scripts/et_table_data.py`：缓存消费契约集中为 `unverified` + `unverified_empty_string`，其余缓存状态不静默消费。
- `skills/et/scripts/wps_result.py`：compact 输出丢弃原始编码字符串但保留解码值/计数，避免精简输出重新放大。
- 运行清单加入两个新模块；SKILL、参考、架构说明和版本同步。

## 验证结果

平台：macOS 27.2.1 arm64，Python 3.14.10，Expat 2.7.4；命令使用 `-I -S -B`。复核报告使用 Python 3.12.14/Expat 2.8.4，平台不同；本轮在现有维护解释器复现同一语义问题。

| 检查 | 结果 |
|---|---|
| XlsxWriter 3.2.9 独立生成共享字符串样本 | 修复后 `A\rB`、`A\x01B`、`_x000D_`、emoji 均正确；原始编码证据存在；覆盖不再因解码而误报 |
| 8 Mi 文字边界实测（260 个 32767 字符内联单元格） | 修复后保留 8,388,608 个字符，恰为上限；`text_budget.limited=true`、覆盖不确定，未超过预算 |
| ET 全量 `unittest discover -s skills/et/scripts/test -p 'test_*.py'` | 621 通过（新增 14 项：7 OOXML 文本、3 跳表/缺 EOF 覆盖、1 文字预算、1 空缓存消费、1 空缓存契约拒绝、1 诊断截断保存值不变量） |
| `check_skill.py skills/et --strict` | 通过，退出 0 |
| `tools/check_project_docs.py` | PASS，退出 0；仅结构检查 |
| `git diff --check` | 通过 |
| runtime packager / 独立运行包 / ET 维护探针 / 执行示例 / 跨表 pipeline | 全部通过 |
| table-workflow / read-view budget 回归 | 177 项、2 项通过 |
| 4 份固定 ET/ETT `verify_et_samples.py` | 通过；198 个冻结保存值保持不变 |
| 固定 GitHub 原始来源审计 `tests/wps_biff8_audit_real_samples.py` | 通过；4 份原件、198 个冻结值、33 个缓存一致 |
| 多政府站点原始 ET 扩展语料 | 下载失败（当前网络 SSL EOF），未取得新来源；不计入通过 |
| 2 份 Excel-97 零尾 BIFF8、5 份真实 xlsx | partial 可读，保存值与单元格数（1/4；420/26546/401035/68495/1399）保持不变 |

## 未验证与边界

- XlsxWriter 是独立生成器但仍是合成 OOXML，不等同真实 WPS 原生 OOXML `.et` 验收；无真实 WPS 文件的逐格语义对照。
- 文字预算只约束单元格保留文本值；富文本格式段/共享格式元数据另受最终 JSON 预算约束，未把它们并入同一计费口径。
- OOXML 解码只覆盖已解析的文本节点；批注等附加文本共享解码器但未逐一做客户端显示对照。
- 性能建议（分页按需解析、概况免完整对象、减少扫描）未测量，未在本轮改动。
- GitHub Actions `wps-biff8-audit.yml` 已扩展当前 ET 的 Linux 全量回归、严格结构、项目文档、运行包验收与固定真实来源审计；本环境无 Linux/GitHub Actions 执行能力，不能声称该工作流已运行通过。
- 多政府站点原始 ET 语料本轮下载受网络阻断，未新增真实来源；当前真实审计仍为仓库已固定的 4 份 ET/ETT。
- 云端 Linux、Windows、WPS/Excel 客户端视觉、真实宿主回传未验收；Pylint 未执行（当前解释器无 pylint，未安装依赖）。
