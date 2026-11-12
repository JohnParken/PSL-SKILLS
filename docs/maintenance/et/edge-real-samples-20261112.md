# Edge 公开 ET 原件扩样验收（2026-11-12）

本记录以 PSL-SKILLS 仓库根目录为维护基准，不随 Skill 运行包分发。审计对象为当前未提交工作树中的 ET `20261112.1447.4600`；运行时文件 SHA-256、原件来源、逐文件结果与复核命令见 [机器证据](edge-real-samples-20261112.json)。本轮只增加审计证据和文档索引，不修改运行时、Skill 版本或发布 ZIP；保留此前工作树修改。

## 结论

通过 Microsoft Edge 实际下载 8 份公开 `.et` 原件，来自中山交通运输局、泉州医保局、张掖教育局、宁夏财政厅和深圳人社局五个发布方。相对于历史四份固定 ET/ETT，本轮新增八份独立文件；八份哈希互不相同且不与旧清单重复。没有生成文件、改 XLS/XLSX 后缀或把下载失败计入样本。

8/8 可完整返回 JSON，均为 `partial`、退出 3，均是 CFB 内 BIFF8。独立记录核对得到 1,311 个保存单元格地址：599 个非空值（含 83 个数值公式缓存）及 712 个空白，全部一致；102 处合并范围一致。4 份文件实际包含富文本，31 个 SST 富文本字符串、112 个 FormatRun 的原始字节及拼接文本一致。未发现本组样本的保存值读错或丢失。

**发现一处共享公式兼容性缺口，尚未修复：**宁夏资金分配表的两条 ShrFmla 使用次数字段为 0，当前实现不接受其分组定义，导致 38 条成员关联警告，整本覆盖被标为不确定。保存的 79 个公式缓存全部保留并独立核对一致。这是分组解释及覆盖判断的问题，不能表述为金额读取错误，也不能据此认定源文件符合所有 MS-XLS 约束。

## 样本与结果

文件名保留 Edge 下载后的名称；括号数字是浏览器同名保护，不是改格式。二进制原件保留在本机下载目录，未提交、未随包分发，未确认再分发许可。发布网页证明来源为公开 ET 附件，但不能单独证明其创建软件或版本。

| 发布方 / 原件 | 保存格（含空白） | 非空值 | 数值公式缓存 | 富文本字符串 | 当前结果 |
|---|---:|---:|---:|---:|---|
| 中山：运输车租赁报价表 | 32 | 18 | 0 | 0 | partial；值覆盖完整 |
| 泉州：安溪县医院价格表 | 80 | 58 | 0 | 0 | partial；值覆盖完整 |
| 泉州：安溪县中医院价格表 | 62 | 43 | 0 | 0 | partial；值覆盖完整 |
| 张掖：普通高中招生计划 | 337 | 86 | 4 | 1 | partial；值覆盖完整 |
| 宁夏：乡村振兴资金分配表 | 447 | 211 | 79 | 6 | partial；覆盖不确定，共享公式缺口 |
| 宁夏：乡村振兴绩效目标表 | 120 | 61 | 0 | 22 | partial；值覆盖完整 |
| 深圳：服务需求明细表 | 78 | 51 | 0 | 0 | partial；值覆盖完整 |
| 深圳：服务报价表 | 155 | 71 | 0 | 2 | partial；值覆盖完整 |

逐份来源页、HTTPS 下载 URL、大小、SHA-256、表名、原始诊断计数及独立预期摘要见机器证据。其他 partial 主要来自保留但未解释的非单元格元数据、CFB 头 CLSID 和其他容器流；富文本格式不等于已验证视觉显示。

## 共享公式问题定位

原件：`P020251205645964993287.et`，SHA-256 `738c9cdf844424e97328e8bc5957ee41c827c38fb1fb3153c0f635e17d8db1ff`。

| Workbook 流偏移 | 声明范围 | cUse | token 长度 | 记录载荷长度 |
|---:|---|---:|---:|---:|
| 14254 | E6:J6 | 0 | 19 | 29 |
| 14511 | D7:D39 | 0 | 29 | 39 |

`skills/et/scripts/wps_biff8_cells.py` 的 `_handle_formula_group` 要求 `0 < expected <= area`，其中 shared 的 expected 来自 `payload[7]`。两条记录均因 cUse=0 进入 `formula_group_definition_invalid`，原始记录保留，随后 38 个成员产生 `formula_group_member_mismatch`。诊断计数中 definition_invalid 为 3 个对象，其中一个是合并统计对象，代表 2 条记录，不能误称 3 组。

结果为 `cell_value_coverage=unverified_due_to_best_effort_salvage`、`omissions_affect_cell_values=true`。默认排除公式与显式使用缓存的区域统计仍可返回，且传播此风险；依赖完整覆盖的消费方无法将其视作已认证完整数据。本轮未执行跨 Skill pipeline，不声称已经复现该流程的拒绝。

[MS-XLS ShrFmla 定义](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/984826cc-8bb7-412b-9907-7bbb9b08b4ad) 将 cUse 定义为使用该共享公式的单元格数量。真实附件里的 0 与观察到的引用成员不一致；尚无生产软件证据说明这是何种约定。后续修复应分别处理可安全定界的公式组信息、成员一致性与保存值覆盖，保留异常字段和诊断；不直接将 0 解释为正常使用次数或取消安全校验。

## 后续修复（同日）

上述 cUse=0 缺口已按审计建议修复，未改写本记录的原始审计数字。修复后该文件保留 2 条共享公式组定义（E6:J6 6 个成员、D7:D39 32 个成员），38 条成员 mismatch 降为 2 条 `formula_group_use_count_unspecified` metadata-only 诊断，保存单元格 447/211 非空/79 缓存不变；`cell_value_coverage` 恢复为 `complete_within_declared_sheets`，但组表达式、使用次数字段语义和 MS-XLS 合规性仍未认证。专项记录见 [ShrFmla cUse=0 兼容修复](shared-formula-zero-use-20261112.md)。

## 验证方法与实际运行

- 环境：macOS 27.2.1 arm64，Python 3.14.10，Expat 2.7.4；解释器 `/opt/homebrew/bin/python3.14`，保留 `-I -S -B`，仅标准库。读取器运行离线，不调用办公软件。
- `et_cli.py run --check`、`analyze_et.py --check` 均退出 0，返回 `environment-ok`、`ooxml-sheet=available` 和 `artifact_validation=not_run`；规范检查器环境与 `--help` 自检通过。
- 8 份完整读取、8 份 `--summary` 均退出 3，stderr 为空，JSON 完整可解码。完整输出在本机审计工作目录保存，原件读取前后哈希一致。
- 独立保存值核对使用已有 `tests/wps_source_oracle.py`，不导入 Skill 解析器。其旧实现仅接受普通同记录 SST；本轮按照 [XLUnicodeRichExtendedString 定义](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/173d9f51-e5d3-43da-8de2-be7f22e119b9) 在内存适配同记录富文本长度字段，再由原 oracle 解码单元格；独立核对富文本 run 字节和拼接文字。8 份原件均无 Continue；不得推广为跨记录富文本验收。
- 数字按 Decimal 精确相等核对后才规范化计算摘要；文字精确比较。83 个公式只比较保存缓存，没有重新计算。另从原始记录独立核对全部地址集合、空白及合并范围。
- 8 份分别测试默认 exclude 和显式 cached，共 16 次区域统计，逐列数值数量与合计和独立记录值一致，源 issues/coverage 完整传播。选区含标题和合计，仅用于验证数学与缓存策略，不作为业务总额。
- 仓库根执行 `/opt/homebrew/bin/python3.14 -I -S -B -m unittest discover -s skills/et/scripts/test -p 'test_*.py' -q`：退出 0，621 项通过，用时 11.338 秒。
- 最终结构、文档和差异检查的命令及结果记录在机器证据 `final_checks`。

复现单份问题时，在宿主确认的样本工作目录中，将 `<et-root>` 替换为实际读取 `skills/et/SKILL.md` 确认的目录：

```console
python3 -I -S -B "<et-root>/scripts/et_cli.py" run --check
python3 -I -S -B "<et-root>/scripts/et_cli.py" run ./P020251205645964993287.et
python3 -I -S -B "<et-root>/scripts/analyze_et.py" ./P020251205645964993287.et --sheet Sheet1 --range A1:L40
```

自检须先通过；后两条预期退出 3，保存完整 JSON 并检查上述诊断，不能将非零状态当作空表。完整复核代码作为执行证据保存在 JSON 的 `verification_commands`，含本轮实际路径，不属于安装包入口；重现时先确认任务位置和输出文件，避免覆盖已有证据。

## 下载受阻与验收边界

- 中山页面初始附件链接触发 Edge“无法安全下载”；改用同站 HTTPS 附件地址正常完成，没有关闭防护或点击保留不安全下载。
- 云南国资页面可打开，但此次 Edge 可见页面未出现附件；重庆财政附件点击未产生可确认的下载。本轮不计入样本，不声称这些文件已验收。
- 深圳下载目录已有历史 `12753063.et`，未将其当本轮新增。两份本轮重新下载的文件用各自源 URL、大小和哈希区分；其一可能与历史下载内容相同，但相对原四份固定审计集均为新增。
- 早期核对命令曾把 cells/merges 结构假设错、把 `--header-rows 0` 当成无表头；修正审计调用后通过。这些是测试适配错误，不计为 Skill 缺陷。旧 oracle 对富文本的拒绝同样属于工具覆盖限制。
- 本轮没有新的真实 WPS 原生 OOXML ET、批注/超链接/图表样本，没有应用渲染、公式新鲜度或全格式验证；未执行云端 Linux、Windows、独立安装和发布包测试。旧四份固定样本未在本轮重跑。
- 脚本真实输出由 subprocess 捕获并在本机完整解码；模型收到的是明确选取的核对摘要，早期一次全量诊断展示被宿主截断，随后改为读取本机完整结果并输出有界统计。不能将本地捕获视为生产宿主已实现完整 stdout/stderr 回传契约。
- 未改 Python 源码，本轮 Pylint 未执行；现有维护解释器未安装 Pylint。description 的只读、有限格式、不重算、不写回边界仍准确，无需改写。README、docs 的相关导读已增加本记录入口，历史验收数字保持原基线。

## 工作记录

- Main-agent work：来源筛选、Edge 下载、独立记录核对、现有回归、问题定位、证据审阅及最终验收；本轮未修复共享公式缺口。
- Subagent execution record：无子代理调用；这是审计工作，由主代理完成。
