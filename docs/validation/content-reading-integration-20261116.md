# 内容优先读取与缓存清理集成复核（2026-11-16）

## 基线与范围

- HEAD `8235397`，保留 `codex/et-reader-improvements` 分支及既有修改；未提交、推送或创建最终发布 ZIP。
- 当前版本：WPS `20261116.1333.0834`；ET `20261116.1332.4797`；DOCX `20261116.1334.1813`。
- 验证平台 macOS 27.2.1 arm64，Python 3.14.10 / Expat 2.7.4；实际使用 `-I -S -B`，无安装或运行时联网。系统 `python3` 不满足检查器最低版本，未以其失败伪装目标通过。
- 不读取、改动私人 OFD 验收目录；不读取用户故障原件。仓库集成用合成夹具与已有明确公开来源测试数据，不将其当作新真实样本验收。

## 独立审查结论

WPS content 保留支持的正文、表格结构/合并、相关区域、对象文字及独立修订视图；二进制 PNG、图表缓存与非必要段落布局解析改为显式 maximum。缺口与 partial 明示，没有将仅主文或摘要当全文。图表或图片独有文字/数值仍可能遗漏，不能声称全格式完整。

ET 内容输出使用独立 `et_content_v1`，保存值/类型/前导零、缓存状态、数字解释、合并及诊断保留；原始证据与样式省略不伪装成原 schema。BIFF 一般 opaque 元数据不再构造 hex，Font 保留内部校验需求；大小、记录预算和拒绝边界保持。完整 Python/API 读取、分析及跨技能 read/metadata 协议仍默认 full。

DOCX 清理仅处理已经核实身份与内容的本轮缓存；成功续读保留，终页、降为无缓存游标及输出/分页失败清理。未知或安全校验失败的缓存不借此删除。强杀仍不在可保证的处理范围内。

未发现尚未修复的本轮阻断性文字/保存值丢失缺陷。不是对所有私有格式、所有攻击面或未执行平台的完整认证。

## 集成缺陷与修正

首轮仓库 discovery 390 项出现 1 failure、1 error、1 skipped。两处失败均来自 `tests/test_wps_et_pipeline.py` 的旧 CLI 提取：默认 content 已不再是 XLSX 消费器接受的原 schema。XLSX 的拒绝是正确的安全/语义边界，不是空表成功或数据丢失。

- 原 schema pipeline 改为显式 `--profile full`，原精度、长编号、partial、缓存与缺缓存断言全部保留；新增 `et_content_v1` 必须被拒绝的反例。
- 检查所有生产 Python 的 ET 调用：`table-workflow` 使用 `et_cli.py read/metadata`，区域分析使用 `read_bytes` 默认 full；未发现生产链误将普通 run 默认内容结果当原 schema。未放宽 XLSX 校验，也未改其运行时。
- ET 消费参考已包含 full 参数；根 README、ET/XLSX 项目文档补充普通 run/文件输出和原 schema 交接的区别。修正 ET CLI 模块的旧“默认未变”说明，运行逻辑不变。
- 独立安装测试补强 WPS 默认 content 与显式 maximum，以及 ET BIFF/OOXML content/full 的保存值、类型、诊断一致与 schema 分离断言。
- 根 README、docs 索引同步版本及 content 默认；保留旧加载/选路快照为 `tests/*_before_content_reading_20261116.json`。人工复核 37 个相关场景，全部 79 个原选择期望保留，刷新三入口指纹。此处不是模型选路实测。

## 最终已运行结果

使用 `/opt/homebrew/bin/python3.14`（下列命令中的 python 表示该解释器）：

| 验证 | 最终结果 |
|---|---|
| `python -I -S -B -m unittest discover -s tests -p 'test_*.py' -q` | 391 项，390 通过、1 跳过，111.439 秒，退出 0 |
| `python -I -S -B -m unittest discover -s tests -p 'test_skill_runtime_packages.py' -v` | 独立最终补强版18项，17通过、1跳过，7.316秒，退出0；已含在最终391项中，不重复相加 |
| `python -I -S -B -m unittest discover -s tests -p 'test_wps_et_pipeline.py' -v` | 4项通过；已含在最终391项中 |
| `python -I -S -B tools/check_project_docs.py` | PASS；只证明版本、覆盖、本地链接，不代表语义或Mermaid渲染 |
| `python -I -S -B skills/skill-development-standards/scripts/check_skill.py skills/docx skills/et skills/wps --strict` | 三项均OK |
| `python -I -S -B tests/benchmark_skill_loading.py --check-snapshot tests/runtime_loading_profiles.json` | fresh，无差异；不是宿主usage |
| `git diff --check` | 通过 |
| Pylint | 所选解释器无pylint，未执行、不安装；AST/行长检查不替代Pylint |

唯一跳过：TL-DOCX 独立安装的读取/创建/编辑，因为所选解释器启用 site 后仍无 `python-docx==1.2.0`。不能把该项称为通过。其他独立包检查使用各自分离、只读的安装目录和空 PATH，核对源码/安装/临时包字节未变；临时包与任务目录由测试清理，没有覆盖发布包。

分项源码回归由各维护线程完成：WPS 772、ET 747、DOCX 500项通过，具体范围和命令见各自维护记录；这些计数不与仓库 discovery 合并成独立用例总数。

## 资源实测与仍未优化部分

独立复核 ET 合成样本 `synthetic_cfb({'Workbook': workbook(extra_global=rec(0x7777,b'x'*6000)*8)})`：输入50,688字节，18个保存格；以 `json.dumps(..., ensure_ascii=False, default=str)` 的UTF-8字节计，full **110,495**，content **10,820**，源status/issues相同。仅为此元数据密集合成样本的输出字节实测，不是token、峰值内存、耗时或普遍压缩率，也不是用户故障复现。

WPS测试证明可选媒体/图表解码与blob复制不调用、纯段落PAPX布局不解析；未量化普遍内存/性能收益。CFB必要流仍物化，OOXML/RTF仍分别解析accepted/all视图；WPS全结果分页仍重新完整解析并序列化，ET部分路径仍是解析后投影。不能把这些变化称为流式全量解析或保证不再出现资源不足。

## 未验证

目标Linux、Windows、真实DeepAgents调用/下载/模型收到的结果、模型路由与token usage、Office/WPS视觉或全格式兼容未验证。未取得用户故障原件、命令与报错原文，因此未确定其“空间不足”是磁盘、内存、沙箱配额还是输出预算。私有对象文字、图片OCR、图表缓存新鲜度、强杀后清理、缺失TL依赖及Pylint不作通过声明。

## 分项证据

- [WPS内容优先](../maintenance/wps/content-first-20261116.md)
- [ET默认内容](../maintenance/et/content-profile-20261116.md)
- [DOCX缓存清理](../maintenance/docx/read-cache-cleanup-20261116.md)
