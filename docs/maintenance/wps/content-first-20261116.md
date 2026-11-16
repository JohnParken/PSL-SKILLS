# WPS 内容优先全文读取（2026-11-16）

## 基线与范围

- 源码基线：`8235397`；工作分支保留 `codex/et-reader-improvements`，未切换、提交或推送。
- WPS Skill 版本：`20261116.1333.0834`。
- 仅维护 WPS 自有运行时、测试、入口、参考和 WPS 文档；仓库级 README/加载快照由本轮集成维护统一处理。既有私人验收目录未读取、未修改。
- 没有新增运行时文件或依赖，原显式分发清单继续适用；新增测试由现有测试排除边界处理。临时验收包没有替换任何历史发布包。

## 行为与兼容

CLI、`read()`、`read_bytes()` 默认 `reading_mode=content`。content 不是摘要或主文投影：保留正文、表格、附加区域、支持的文本框/OfficeArt 对象文字、独立修订视图与来源/遗漏诊断。

二进制 content 复用 maximum 的有界内容恢复，但不运行 PNG/图表缓存解码器、不复制可选 metroBlob、不为无表格标记的纯段落运行 PAPX 布局检查；含表格时仍读取结构必需属性并保持共享范围/表分组校验，只省略非必要布局明细。保留单元格、可确认合并、嵌套证据及字段缓存，不执行文档域或宏。对象中独有的文字/数值可能未读，`content_reading.not_requested` 与 `binary_optional_content_not_requested` 明确提示，并保留 partial；不能将其宣称为全格式全文完整。

`--reading-mode maximum` 保留高级 PNG、图表缓存和段落属性能力，`--include-image-bytes` 仍要求显式 maximum。standard、创建/编辑/转换内部读取和发布校验保持原契约。原依赖默认 maximum 高级字段的调用者需显式指定 maximum。

content 完整结果可使用原无损 JSON 分片机制：外壳增加 reading_mode，content 的 coverage 为 complete_content_result_json_transport，maximum 保留原 coverage。schema 不变，参数摘要绑定模式，跨模式游标拒绝。高级 `wps_maximum_pages.read_page()` 为兼容仍默认 maximum，content 需要显式参数。完整 JSON 与总分片资源上限不变。

## 已运行验证

实际平台：macOS 27.2.1 arm64；Python 3.14.10；Expat 2.7.4。以下测试仅用合成夹具，未读取私人样本。

1. `read_wps.py --check`：退出 0，environment-ok，OOXML 与图表缓存解析环境可用。
2. WPS 源码回归：`python -I -S -B -m unittest discover -s skills/wps/scripts/test -p 'test_wps*.py' -q`，772 项通过，无失败、无跳过，退出 0。
3. 新增 10 项内容优先专项包括：正文/附加区域/域缓存、媒体解码和 blob 复制不调用、maximum 图片字节保留、WordArt 文字、纯段落不解析布局、表格和分组保留、OOXML 文本框/修订、完整分片重组与跨模式游标拒绝、结构/选项拒绝、CLI 默认及原件不变。修订后的既有默认测试保留内容断言，仅模式期望迁移为 content。
4. `tests/test_skill_runtime_packages.py` 的 WPS-only 5 项通过：只读独立包创建/读取/编辑、PNG、图表缓存、表分组/合并、嵌套所有权。使用该测试原有 setup/teardown，仅限定 NAMES=(wps,) 和 test_wps_* 方法；检查安装/源码/归档字节不变，临时包自动清理。
5. WPS 严格结构检查通过；`git diff --check` 通过。手工核对路径示例、内容范围、CLI 参数、依赖与迁移说明；description 已更新为内容优先用途，并通过字符/结构约束。
6. Pylint：`python3.14 -m pylint --version` 返回 No module named pylint；未安装、未执行，不声称 Pylint Error 为零。结构/语法检查不能替代 Pylint。

## 限制和未验证

- 未在 Linux/Windows、DeepAgents 真实宿主、Office/WPS 客户端渲染中验收；本机通过不等于云端 Linux 已验证。
- 未新增真实 WPS 生产者/用户文档验收；合成矩阵不代表任意版本、私有格式、对象或布局完整兼容。
- 未测量真实模型 token usage 或性能收益，不提供百分比保证。测试证明跳过特定不必要的解析/物化工作，不证明所有文件更快或总内存更低。
- 仍完整读取和验证输入，CFB 核心/必要流仍物化；不是 mmap、按需流加载或流式解析。OOXML/RTF 仍为 accepted 与 all 存储视图分别解析，避免将删除内容混入当前正文；此轮未重构双视图开销。
- 图片/图表独有文字或数值需要按任务升级 maximum；两模式均不做 OCR、不执行对象、不还原页面布局。修订/区域/对象的原有 partial 边界仍有效。
- 源码变更未提交或推送，未重打最终发布 ZIP；仓库级集成与其他 Skill 结果另由本轮集成记录说明。

## 同轮最终集成补核

项目文档检查、三技能严格检查、静态快照及最终仓库回归已完成：391项中390通过、1项TL-DOCX依赖缺失跳过；独立包最终18项中17通过、同一项跳过。ET原schema调用测试已迁移到显式full，未放宽消费器门禁。完整范围、首轮失败及资源/平台限制见[集成复核](../../validation/content-reading-integration-20261116.md)。
