# WPS 纯 Python 最大化读取维护记录（2026-11-13）

## 范围与前提

本页以宿主确认的 PSL-SKILLS 仓库根目录为基准；`skills/wps/` 是 Skill 源码，`docs/maintenance/wps/` 是不随独立运行包分发的维护证据。用户目标是尽可能读取 WPS Office `12.1.0.26899` 所保存文档的大部分内容；本轮明确选择“暂无真实样本”和“只保留纯 Python”，因此实现的是通用、显式的最大化读取模式，不是该软件版本的生产者配置或兼容认证。

保持的硬边界：不增加第三方依赖或运行时联网，不调用 LibreOffice/WPS 读取，不放宽 CFB/FAT/CLX、ZIP/XML、RTF 结构与资源检查，不解密，不执行字段、宏或对象，不把金山私有 `.wps`、未知容器、损坏或超预算输入降级成成功。

## 实现

统一入口 `skills/wps/scripts/read_wps.py` 新增 `reading_mode=maximum` / `--reading-mode maximum`：

- 固定使用 `compatibility=data`、二进制 `text_policy=recover`、字段缓存结果、对象占位、隐藏文字保留、全部存储修订视图和附加区域读取；冲突的显式策略在读取前拒绝。
- CLI 仅允许完整 JSON；纯文本、摘要、选择性读取和分页视图会丢失独立区域，因此参数阶段拒绝。
- 输出增加 `reading_mode=maximum` 与 `maximum_reading`，明确字段未执行、对象未解码、布局和生产者版本未验证。

格式分支：

1. MS-DOC/CFB 复用既有有界文字恢复、字段/对象策略和六类附加故事，不改变版本白名单。
2. OOXML 在原 ZIP/XML、可见性与修订处理之后，从正文及已由主文引用选中的关联区域定位外层 `w:txbxContent`；文字和有限表格以独立 `related_stories[type=textboxes]` 返回。新增 `skills/wps/scripts/wps_ooxml_textboxes.py`，文本框 story 上限 4096、累计文字上限 8 Mi UTF-16 码元。绘图锚点、页面、Markup Compatibility Choice/Fallback 和主文顺序保持 unknown/partial。
3. RTF 可捕获已闭合且首控制字可证明为页眉、页脚、脚注或批注的目标组，在继承代码页和 `uc` 设置下隔离解析；主正文不混入附加文字，story 保留原字节范围，节页/锚点/阅读顺序保持 unknown/partial。最多返回 4096 个目标组，超出只保留有界前缀和遗漏诊断。

运行清单 `packaging/runtime-manifests/wps.json` 已加入新 OOXML 文本框模块。默认 standard 路径与原有投影/游标选项未增加新参数，避免改变既有游标绑定。

## 验证

实际平台：macOS，DeepSeek Harness bundled Python 3.12，命令保留 `-I -S -B`。

- 变更前基线：WPS 环境自检输出 `environment-ok`；原有 551 项测试全部通过。
- 新增/更新合成测试：maximum 对 OOXML 全修订文字与独立文本框、RTF 三类目标组、MS-DOC 附加故事和既有 recover 策略、冲突选项、CLI 完整 JSON/投影拒绝；RTF 原 include-related 不支持断言改为独立区域契约。
- 当前 WPS 全量回归：558 项测试全部通过，无失败或跳过。

后续严格检查、Pylint、运行包安装、项目文档检查和 `git diff --check` 结果在完成后补记；上述单元测试是合成格式与契约验证，不是目标 Linux 业务容器、真实 WPS 生产者或客户端视觉验收。

## 未验证与后续样本要求

没有 WPS Office `12.1.0.26899` 保存的真实 `.wps/.doc/.docx/.rtf` 样本，未验证该版本特有扩展、MC 分支选择、文本框重复、复杂表格、艺术字、图片、公式、图表、页眉页脚继承、批注锚点、密码文档或再次保存。若要建立版本级兼容证据，需要同一内容分别保存为四种格式，并提供纯文字、复杂表格、图片/文本框、页眉页脚/脚注/批注和修订样本及独立预期；真实样本结论不得由本轮合成测试替代。
