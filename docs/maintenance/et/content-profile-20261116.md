# ET 默认内容读取验收（2026-11-16）

## 基线与范围

基于 Mac checkout HEAD `8235397`，现有分支 `codex/et-reader-improvements`，未切分支、提交或推送。ET 版本 `20261116.1332.4797`。保留既有私人验收目录，未读取其内容；本轮新增验证全部使用合成数据，无联网、安装、办公软件或真实用户文件读取。

## 实施

- `read_et.py` / `et_cli.py run` 默认 content，返回独立 `et_content_v1`，原 schema 在 `source_schema_version`；`--profile full` 恢复旧完整 CLI。Python API、`et_cli.py read/metadata`、独立审计及区域分析仍默认完整，公共 v1 协议不变。
- 内容投影保留全部已读格值/类型/精度、公式缓存与风险、数字解释、合并、批注/链接文字和源诊断/值覆盖；省略绘图对象、版式、富文本格式及非必要二进制。`output_projection` 明示遗漏，不能交给要求原工作簿 schema 的消费者。分页附精简遗漏说明，profile 参与游标绑定。
- BIFF Reader 在 content 解析时不生成一般 opaque 元数据 `payload_hex`；Font 字节暂保留给内部富文本校验。原字节与记录预算、诊断、分帧及安全扫描不变。完整 profile 保留原字段；不移除绘图中的潜在文字并谎称已覆盖。
- 这是有界原始证据留存优化与输出投影，不是全面跳过图表/绘图/样式解码；OOXML/文本目前主要减少输出。未测峰值内存、耗时或 token，不作总体性能保证。
- 简化主入口的常读容错说明，把详细记录/绘图/样式调查留给已有按需参考；同步高级命令、消费示例、运行清单与接口说明。description 人工核对仍准确，保持原文。

## 实测

环境 macOS，Python 3.14.10，Expat 2.7.4；仅维护环境，不代表目标 Linux。

- 解释器/检查器入口、ET `--check`：退出 0，`environment-ok` / `ooxml-sheet=available`。
- `python -I -S -B -m unittest discover -s skills/et/scripts/test -q`：最终 **747 项通过**，无失败/跳过。新增 10 项覆盖 BIFF5/BIFF8/OOXML/文本、图片/图表元数据省略后格值、完整证据 opt-in、缺缓存、坐标风险、DTD拒绝、默认/文件输出/分页游标及原件不变等。
- 首轮 745 项有 5 项原始证据默认断言失败，按明确 CLI 迁移加 `--profile full`，保留断言；第二轮分页遗漏说明过长触发 3000-byte 既有测试失败，改为有界简短说明后通过。不是删除检查或放宽预算。
- ET 严格结构检查、目标 `git diff --check`：通过。完整项目 docs 检查待并行维护收尾后统一确认，首次仅报告索引版本漂移及尚未写入的本记录链接，不冒充已通过。
- Pylint：`python3.14 -m pylint --version` 返回 No module named pylint，**未执行**，未安装，不能声称 Error 为零。改动 Python 文件行长静态检查无大于 200 字符的新行；这不替代 Pylint。

合成体积观察：`synthetic_cfb({'Workbook': workbook(extra_global=rec(0x7777,b'x'*6000)*8)})`，50,688 字节输入、18 个保存格；同口径 `json.dumps(..., ensure_ascii=False, default=str)` UTF-8 字节，full 110,495 → content 10,820。状态与原 issues 完全相同；full 的一般 payload_hex 为 96,000 字符，content 解析不创建这些副本。此为特定含大量未知元数据的合成样本，不是普遍压缩率、token 或内存性能。

## 未验证与交接

未新增真实 ET/ETT、Windows/Linux、DeepAgents 宿主、WPS/Office 视觉验收。图片内或绘图文字、全部私有格式、公式新鲜度不属于覆盖保证。未重打或覆盖发布 ZIP。独立运行包、跨技能和共享索引/快照由统一集成复核记录，不能以本 ET 单元通过替代。根 README/docs 索引需同步当前版本与 content/full 迁移；此记录不改历史证据。

## 同轮最终集成补核

项目文档检查、三技能严格检查、静态快照及最终仓库回归已完成：391项中390通过、1项TL-DOCX依赖缺失跳过；独立包最终18项中17通过、同一项跳过。ET原schema调用测试已迁移到显式full，未放宽消费器门禁。完整范围、首轮失败及资源/平台限制见[集成复核](../../validation/content-reading-integration-20261116.md)。
