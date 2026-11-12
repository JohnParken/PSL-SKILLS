# ET 公式字面转义、XF 槽位与覆盖状态修复（2026-11-12）

仓库维护记录，不随 Skill 运行包分发。当前 ET 版本 `20261112.1356.4600`；维护前为上一轮 `20261112.1338.2600`。保留进入任务前的未提交修改，未提交、推送或覆盖历史发布 ZIP。

## 问题是否真实存在

三项均在本仓库复现为真实问题；严重程度不同：

1. **公式字符串字面量与转义缺失（真实兼容缺口，不是数据损坏）**：BIFF8 `PtgStr`（0x17，ShortXLUnicodeString）此前没有任何转写分支，含字符串常量的独立公式不会返回 `formula_text`，也没有对字符串内双引号的转义规则。单元格缓存和保存值不受影响；修复为有界转写字符串常量，外层加双引号、内部 `"` 双写，不执行、不重算。修复前 `="A""B"` 类公式的 `formula_text` 为缺失；修复后可返回 `="A""B"`。
2. **坏 XF 记录丢失索引槽（真实静默错配）**：此前 malformed XF（payload 非 20 字节）只保留 raw，不向 `styles` 追加槽位，后续 XF 索引整体前移；引用后续 XF 的单元格会拿到前一个样式/数字格式且不一定有诊断。合成复现：默认 2 个 XF 后存在 malformed XF、numFmt 7、numFmt 200 三个槽，引用槽 3 的单元格修复前得到 numFmt 200，修复后得到 7。现在 malformed XF 保留 `valid=false` 的占位槽与 raw，引用坏槽的单元格保留值、标记 `xf_slot_invalid`、以 General 回退并附聚合 issue。
3. **覆盖状态依赖诊断列表（真实覆盖误报）**：BIFF `_has_missing_formula_cache` 扫描 `issues`，OOXML 用 `report.has('missing_formula_cache')`；当诊断达到上限/被截断时，缺失公式缓存的 issue 可能不在列表，结果却返回 `complete_within_declared_sheets` 且 `omissions_affect_cell_values=false`。BIFF 用 `MAX_ISSUES=1`、OOXML 用 `MAX_ISSUE_CODES=1` 均已复现。修复为显式解析状态：BIFF 的 `missing_formula_cache`/覆盖标志与 OOXML `IssueLog.mark/marked` 独立于诊断条目；`issues_truncated=true` 时仍保持 `missing_formula_caches` 与 omissions。

## 修改文件

- `skills/et/scripts/wps_biff8_formula_text.py`：新增 PtgStr ShortXLUnicodeString 有界解码与 Excel 字符串字面量转义（内部双引号双写）。
- `skills/et/scripts/wps_biff8_globals.py`：malformed XF 追加不可用占位槽，保留索引位置与 raw。
- `skills/et/scripts/wps_biff8.py` / `wps_biff8_sheet.py` / `wps_biff8_result.py`：缺失公式缓存改为显式状态；覆盖/状态从解析状态计算；引用坏 XF 槽的单元格回退诊断。
- `skills/et/scripts/wps_ooxml.py` / `wps_ooxml_worksheet.py` / `wps_ooxml_sheet.py`：`IssueLog` 增加独立 coverage flags；缺失缓存标记与覆盖计算不再扫描诊断条目。
- 测试：新增公式字符串转义、malformed XF 槽位、BIFF/OOXML 诊断截断下覆盖状态四项回归（`test_wps_biff8.py`、`test_et_compatibility_recovery.py`）。
- 参考：`biff8-readonly.md`、`ooxml-spreadsheet.md`、`format.md` 同步说明。

## 验证结果

平台：macOS 27.2.1 arm64，Python 3.14.10，Expat 2.7.4，非 root；命令使用 `-I -S -B`。

| 检查 | 结果 |
|---|---|
| ET 全量 `unittest discover -s skills/et/scripts/test -p 'test_*.py'` | 607 通过（本轮新增 4 项；维护前 603） |
| `check_skill.py skills/et --strict` | 通过，退出 0 |
| `tools/check_project_docs.py` | PASS，退出 0 |
| `git diff --check` | 通过 |
| runtime packager / 独立运行包 / ET 维护探针 / 执行示例 / 跨表 pipeline | 通过 |
| 4 份固定 ET/ETT `verify_et_samples.py` | 通过；198 个冻结保存值、33 个公式缓存保持一致 |
| 2 份真实 Excel-97 零尾 BIFF8 | 保持 partial 可读，保存值不变 |
| 5 份真实 xlsx | 保持 5/5 partial，单元格数 420/26546/401035/68495/1399 |

## 未验证与边界

- 新增公式字符串转写仅覆盖 PtgStr；共享/数组/外链/公式组、错误字符串缓存和字符串内嵌控制字符的显示语义仍未做客户端对照。
- 坏 XF 槽位只保留索引位置和 raw；其内部字体/对齐等字段未解释，引用坏槽的单元格按 General 回退，不声称恢复原格式。
- 覆盖状态解耦已用截断上限复现回归；未遍历所有诊断上限/聚合路径，仍由全量测试覆盖现有已知路径。
- 无真实 WPS 原件含 PtgStr 或 malformed XF 的独立对照；未执行 Linux/Windows、WPS/Excel 客户端、真实宿主回传。
- Pylint 未执行：当前解释器 `No module named pylint`，未安装依赖，不声称 Error 清零。
