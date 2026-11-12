# BIFF8 元数据损坏与值覆盖分离（2026-11-12）

仓库维护记录，不随 Skill 运行包分发。当前 ET 版本 `20261112.1629.4300`；修复前为 `20261112.1543.5400`。本记录承接 [ShrFmla cUse=0 兼容修复](shared-formula-zero-use-20261112.md) 的原则：**保存单元格值覆盖**与**元数据语义**分开表达。已知非值元数据损坏只产生 `partial` 和诊断，不应单独把 `cell_value_coverage` 降为不确定。

## 结论

以下已知 BIFF8 元数据现在使用 metadata-only salvage：记录保留原始字节与 issue，保存单元格继续读取，`omissions_affect_cell_values` 不再因这些记录被置 true，`cell_value_coverage` 可保持 `complete_within_declared_sheets`：

- 合并范围（`MergeCells` 截断/长度不符）；
- 共享/数组公式组定义（锚点、范围、token、重叠、成员计数等损坏）；
- 条件格式/数据验证组（组边界/分区校验失败）；
- 孤立条件格式/验证规则记录；
- 绘图之外的 Note 文本/链接记录；
- `HLink`/`HLinkTooltip` 损坏、孤儿、数量/文字预算；
- OOXML 合并数量上限。

以下仍视为可能影响值覆盖、保持保守不确定：

- 未知 BIFF 记录（可能承载未识别数据）；
- 未知 OfficeArt 原子、未知绘图对象、损坏/无法定界的嵌入式图表记录；
- 匿名嵌套 BOF..EOF 子流；
- SST/单元格/公式缓存等值承载记录失败；
- 声明工作表未读出、缺 EOF；
- 文字预算截断、缺公式缓存。

## 修复前后示例

| 输入损坏 | 修复前 | 修复后 |
|---|---|---|
| malformed `MergeCells` + 正常 Number 单元格 | `partial`，覆盖可能不确定 | `partial`，`merge_record_invalid`，值覆盖完整 |
| 无效共享公式组 + 锚点缓存 | `partial`，组元数据 salvage 降覆盖 | `partial`，组 issue + 成员 mismatch，值覆盖完整 |
| 无效条件格式组 + 正常单元格 | 覆盖可能不确定 | `partial`，组 issue，值覆盖完整 |
| malformed HLink + 正常单元格 | 覆盖可能不确定 | `partial`，`hlink_invalid`，值覆盖完整 |
| OOXML 第 3 个合并超过上限 | `omissions_affect_cell_values=true` | 合并诊断，值覆盖完整 |
| 未知 BIFF 记录 + 正常单元格 | 覆盖不确定 | 保持不确定（不静默放宽） |

## 修改

- `skills/et/scripts/wps_biff8_retain.py`：新增 `salvage_metadata()`，固定 `may_affect_cell_values=False`，并说明只适用于不可能承载单元格值的已知记录。
- `skills/et/scripts/wps_biff8_cells.py`：公式组定义/成员元数据的 salvage 全部改 metadata-only。
- `skills/et/scripts/wps_biff8_sheet.py`：合并、条件格式/验证、孤立规则、Note 外置记录、HLink/HLinkTooltip 改 metadata-only；未知记录、意外 Continue、catch-all 记录校验失败保持保守。
- `skills/et/scripts/wps_ooxml_worksheet.py`：合并数量上限不再设置 `context.uncertain`，诊断标 `cell_value_impact=metadata_only`。
- 参考：`biff8-readonly.md` 覆盖定义、`formula-groups.md`、`reading-assessment.md`、`advanced-spreadsheet-routing.md`、`format.md` 同步。
- 回归：新增 `test_et_metadata_value_coverage.py`，覆盖 BIFF8 公式组/合并/条件格式/Note/HLink、OOXML 合并上限、以及未知记录仍不确定；更新 OOXML merge cap 旧断言。

## 验证

平台 macOS 27.2.1 arm64、Python 3.14.10、Expat 2.7.4，`-I -S -B`。

| 检查 | 结果 |
|---|---|
| ET 全量测试 | 632 通过（含 8 项元数据/值覆盖回归） |
| Edge 8 份公开 ET 重读 | 8/8 partial，保存格数 32/80/62/337/447/120/78/155；8/8 值覆盖完整、omissions=false |
| 固定 GitHub 4 份 ET/ETT 审计 | 198 冻结值、33 缓存一致 |
| 4 份本地固定 ET/ETT | 198 冻结值一致 |
| 宁夏 cUse=0 文件 | 447 格/211 非空/79 缓存不变；组保留；覆盖完整 |
| 结构/文档/差异/运行包检查 | 全部通过 |

## 边界

- 这是**值覆盖语义**调整，不是减少诊断或放宽安全校验；所有 issue 仍存在，结果仍 partial。
- 元数据损坏本身仍使结果 partial；`cell_value_coverage=complete` 只表示已读取单元格值未因该元数据失败而缺失，不表示元数据语义已验证。
- 未知记录、未知 OfficeArt/对象、图表、值承载记录跳过、跳表、文字截断仍保持保守覆盖。
- 仅合成故障和 8 份 Edge 样本重读；没有真实 WPS 文件专门包含这些损坏元数据，没有 Linux、客户端或 WPS 版本认证；Pylint 未执行。
