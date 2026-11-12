# BIFF8 ShrFmla cUse=0 兼容修复（2026-11-12）

仓库维护记录，不随 Skill 运行包分发。当前 ET 版本 `20261112.1543.5400`；修复前为 `20261112.1447.4600`。本记录承接 [Edge 公开 ET 原件扩样验收](edge-real-samples-20261112.md) 发现的宁夏资金分配表共享公式缺口；原件仅在本机审计目录读取，不提交、不随包分发。机器证据见 [shared-formula-zero-use-20261112.json](shared-formula-zero-use-20261112.json)。

## 结论

Edge 扩样中的唯一未修复兼容缺口已按“保留异常字段、分开成员一致性与值覆盖”的原则处理：

- 两条 `ShrFmla` 的声明 `cUse=0` 不再使整组定义进入 raw salvage；
- 组定义保留在 `sheets[].formula_groups`，标 `use_count_unspecified=true`、`expected_members=0`，并附 `formula_group_use_count_unspecified`；
- 成员仍按范围、`fShrFmla` 标志、坐标和组锚点校验；`cUse=0` 不按正常使用次数解释，也不取消其他结构校验；
- 宁夏样本从 38 条 `formula_group_member_mismatch` 降为 2 条明确的组使用次数未指定诊断；`cell_value_coverage` 由 `unverified_due_to_best_effort_salvage` 恢复为 `complete_within_declared_sheets`，`omissions_affect_cell_values=false`；
- 447 个保存单元格（211 非空、79 个公式缓存）保持不变；79 个缓存此前已由独立 oracle 核对一致，本次没有重新计算或改变缓存值。

必须区分：**保存单元格值覆盖完整**不等于组表达式语义已验证。`formula_group_use_count_unspecified` 仍是 metadata-only 问题；组 token、共享表达式、缓存新鲜度和 MS-XLS 合规性均未认证。

## 实际问题与修复前证据

原件：`P020251205645964993287.et`，SHA-256 `738c9cdf844424e97328e8bc5957ee41c827c38fb1fb3153c0f635e17d8db1ff`，CFB/BIFF8，26112 字节。发布页为宁夏财政厅公开附件；未确认创建软件/版本，未确认再分发许可。

| 项目 | 修复前 | 修复后 |
|---|---|---|
| `formula_groups` | `[]` | 2 组：E6 范围 E6:J6（6 成员）、D7 范围 D7:D39（32 成员） |
| `formula_group_definition_invalid` | 3 个聚合对象（其中 1 个计数对象代表 2 条记录） | 0 |
| `formula_group_member_mismatch` | 38 | 0 |
| `formula_group_use_count_unspecified` | 0 | 2 |
| `cell_value_coverage` | `unverified_due_to_best_effort_salvage` | `complete_within_declared_sheets` |
| `omissions_affect_cell_values` | true | false |
| 保存单元格 / 非空 / 公式缓存 | 447 / 211 / 79 | 447 / 211 / 79（不变） |

两条定义原始字段：

| Workbook 流偏移 | 声明范围 | cUse | token 长度 | 观察成员 |
|---:|---|---:|---:|---:|
| 14254 | E6:J6 | 0 | 19 | 6 |
| 14511 | D7:D39 | 0 | 29 | 32 |

修复保留 `header_raw_hex`（含 cUse 字段）、`tokens_hex`、`ancillary_hex`、范围、锚点和观察成员数；不保存或执行表达式。

## 修改

- `skills/et/scripts/wps_biff8_cells.py`：共享组 `cUse=0` 仅在范围、锚点、token 长度、重叠和成员标志等其余检查通过时保留为 `use_count_unspecified`；token 长度、数组组和其余结构错误仍拒绝/salvage。
- `skills/et/scripts/wps_biff8_sheet.py`：完成组统计时，`use_count_unspecified` 组只发 metadata-only `formula_group_use_count_unspecified`，不再与 0 做成员数 mismatch 比较；正常 cUse 组仍校验成员数。
- `skills/et/references/formula-groups.md`、`biff8-readonly.md` 与 Edge 扩样记录同步说明。
- 回归：合成 cUse=0 组、cUse=0 不跳过其他校验、改编自真实定义的 payload；既有组测试更新。

## 验证

平台：macOS 27.2.1 arm64，Python 3.14.10，Expat 2.7.4；`-I -S -B`，仅标准库。

| 检查 | 结果 |
|---|---|
| ET 全量测试 | 624 通过（本轮新增/调整组测试；无失败） |
| 真实宁夏文件 | 447 格、211 非空、79 公式缓存不变；2 组保留；覆盖完整、omissions=false；诊断计数见上 |
| 固定 GitHub 4 份 ET/ETT 审计 | 通过；198 冻结值、33 缓存一致 |
| 4 份本地固定 ET/ETT | 通过；198 冻结值一致 |
| Edge 8 份原件重读 | 8/8 partial，保存格数全部匹配（32/80/62/337/447/120/78/155）；修复后 8/8 值覆盖完整 |
| cUse=0 合成回归 | 通过：保留组、关联成员、metadata-only issue、覆盖完整；坏范围仍拒绝 |
| check_skill / 项目文档 / git diff --check / 运行包回归 | 全部通过 |

## 未验证与边界

- 仅验证这一份真实 WPS 来源中的 cUse=0 变体；其他生产者/版本、其他异常 cUse、共享公式表达式语义、缓存新鲜度、富文本显示、公式计算均未验证。
- 没有真实 WPS 原生 OOXML ET、批注/超链接/图表扩样，也没有 Linux/Windows、客户端视觉或独立安装验收。
- cUse=0 被当作未指定而非正常值；如果更多来源显示其他约定，应重新评估而不静默放宽。
- 本次未修改发布 ZIP、未提交或推送；Pylint 仍未执行（解释器无 pylint）。
