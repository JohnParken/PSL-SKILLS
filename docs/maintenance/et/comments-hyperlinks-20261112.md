# ET BIFF8 批注与超链接只读读取（2026-11-12）

仓库维护记录，不随 Skill 运行包分发。当前 ET 版本 `20261112.1338.2600`；维护前为 `20261112.0731.3094`。版本采样为 America/New_York 2026-11-12 01:38:26，整体加 12 小时后按百分之一秒截断。保留进入任务前的未提交修改，未提交、推送或覆盖历史发布 ZIP。

## 修改范围

- 新增 `skills/et/scripts/wps_biff8_hyperlinks.py`：按 MS-XLS HLink（0x01B8）与 HLinkTooltip（0x0800）、MS-OSHARED Hyperlink Object / HyperlinkString 字段做有界只读解析。返回 ref、display、target frame、moniker、location、tooltip、target 及 `target_resolved=false` / `followed=false` / `link_executed=false`；OLE moniker 和无法安全继续的剩余字段保留原始记录与 `ole_moniker_opaque`/`unparsed_remaining_fields` 标记。目标从不解析、下载、执行或跟随。
- `wps_biff8_drawing_notes.py`：在既有 note 链边界、TxO 文本/续段、Note 作者与链接校验之后，解码批注文字与作者并关联单元格；`note_drawings` 只保留摘要和 `comment_count`，正文在 `sheets[].comments` 返回一次，避免重复放大。原始记录、`drawing_sha256` 和未知 OfficeArt 证据保持。
- `wps_biff8_drawing.py` / `wps_biff8_sheet.py`：新增 comments/hyperlinks 状态与结果字段；HLink/HLinkTooltip 调度、每个工作表一个“目标未解析”聚合 issue、每表 4096 条链接上限与文本输出预算；坏记录保留 raw 并附 issue，后续单元格继续读取。
- `packaging/runtime-manifests/et.json` 加入新运行模块；参考、架构说明、CLI/消费说明与版本同步更新。

## 契约边界

- `sheets[].comments`：`cell`、`author`、`text`、`object_id`、`kind='biff8_note'`、`source`（Note/TxO 记录偏移与物理行列）、`layout_decoded=false`、`display_applied=false`、`target_followed=false`。不并入单元格值，不渲染气泡，不执行或跟随。
- `sheets[].hyperlinks`：`kind='biff8_hlink'`、`cell`（多格时另有 `range`）、`target_kind`（internal/external_string/ole_moniker_opaque/none）、`display`、`target_frame`、`moniker`、`location`、`target`、`tooltip`、`target_resolved=false`、`followed=false`、`link_executed=false` 与 `source`（记录偏移、Ref8U 原始字节、CLSID、streamVersion、flags）。目标不解析、不联网；未解析目标使结果保持 partial。
- 既有单元格值、缓存、合并、可见性、partial/coverage 语义不变；原始 note/HLink 记录在可解析成功时以结构字段和来源偏移保留，opaque/损坏时仍进入 `uninterpreted_metadata` raw。

开发依据：[HLink](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/f0b9de34-b147-4f96-a1a3-ac21f6fd48fa)、[HLinkTooltip](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/e85d171a-9138-44bb-8e53-9cef48abbc0e)、[Hyperlink Object](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-oshared/c0437222-4868-4660-81f8-6b7ee236d3f8)、[HyperlinkString](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-oshared/1a9f6231-df63-4a95-b998-5c2b95b82965)、[Ref8U](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/809af298-1e4f-499f-9bca-3cd1021f4934)；机器来源记录见 [comments-hyperlinks-evidence.json](comments-hyperlinks-evidence.json)。运行时不联网、不解析目标。

## 验证结果

真实平台：macOS 27.2.1 arm64，Python 3.14.10，Expat 2.7.4，非 root。运行时测试保留 `-I -S -B`，无运行依赖安装。

| 检查 | 结果 |
|---|---|
| `python -I -S -B -m unittest discover -s skills/et/scripts/test -p 'test_*.py'` | 603 通过（维护前 595；新增 8 项：comments 端到端/文本变体与 hyperlinks 6 项及端到端）。 |
| `python -I -S -B skills/skill-development-standards/scripts/check_skill.py skills/et --strict` | 通过，退出 0。 |
| `python -I -S -B tools/check_project_docs.py` | PASS，退出 0；仅结构检查。 |
| `python -I -S -B -m unittest discover -s tests -p 'test_runtime_packager.py'` | 通过。 |
| `python -I -S -B -m unittest discover -s tests -p 'test_skill_runtime_packages.py'` | 通过（含 manifest/只读包/来源指纹）。 |
| `python -I -S -B -m unittest discover -s tests -p 'test_et_maintenance_probes.py'` | 通过。 |
| `python -I -S -B -m unittest discover -s tests -p 'test_execute_skill_examples.py'` | 通过。 |
| `python -I -S -B -m unittest discover -s tests -p 'test_wps_et_pipeline.py'` | 通过。 |
| 4 份固定 ET/ETT `verify_et_samples.py` | 通过；198 个冻结保存值（含 33 个公式缓存）保持一致，Schedule 仍为默认 partial。 |
| 2 份真实 Excel-97 零尾 BIFF8 | 仍分别 partial/1 单元格、partial/4 单元格；未因新增字段改变保存值。 |
| `git diff --check` | 通过。 |

## 未验证与边界

- 本轮新增能力只有标准库合成回归；仓库没有真实 WPS 批注或超链接原件，未做独立内容对照、视觉或 WPS/Excel 客户端验收。
- OLE moniker 仅保留有界原始证据，不解析目标；外部目标只记录字符串，从不联网、下载或跟随。
- Pylint 未执行：`/opt/homebrew/bin/python3.14 -m pylint` 返回 `No module named pylint`；未安装依赖，不声称 Error 清零。
- 未执行云端 Linux、Windows、真实宿主 `/large_tool_results/` 回传或模型选路验收；当前只有 macOS 维护环境结果。
- 新增字段改变完整提取 JSON 的 sheet 字段集合，但未改 `schema_version`；字段为附加内容，旧消费者忽略未知字段仍可读取。未重新生成发布 ZIP、未提交或推送。
