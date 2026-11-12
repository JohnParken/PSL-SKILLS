# OFD 默认文本读取重构（2026-11-11）

本记录以仓库根目录为基准，仅供源码维护，不随 OFD 运行包分发。当前 Skill 版本 `20261111.1750.2513`；在已有未提交的分页读取改动上继续维护，保留其他 Skill 的并行修改、用户验收文件和历史记录。未提交、推送、覆盖发布 ZIP 或部署。

## 行为与兼容迁移

- CLI 直接传入文件、Python `read_ofd(path)` 以及显式 `--view reading` 均默认返回 `ofd-content-2`。省略范围时读第1文档第1页，响应含文档/页总数，调用方继续逐文档逐页读取；单页超预算使用游标。`done` 只表示本次所选范围完成。
- 同页 TextCode 按现有存储顺序连接，记录仅含页码、页内字符偏移及文字；不插入猜测的分隔符、不去重、不从第一行或文件名推断标题。唯一纯文字 DocInfo/Title 才作为标题，缺失/歧义返回 null，过长保留明确省略标记。
- 原完整结构接口迁移为 `read_ofd_structure(...)` 或 `read_ofd(..., view="full")`，CLI 使用 `--view full`；`ofd-read-3` 结构字段与范围兼容语义保留。summary/page-text 仍是显式完整读取后的投影。查询 `--capabilities` / `capabilities()` 可获得接口版本2及默认值。
- 旧 reading 的 detail 字段通过显式 `--reading-format detail` 保留。content-1 升级为 content-2，去掉逐 TextCode 索引，偏移改为连接后的页文本位置。content-2 游标绑定格式，旧 content 或其他格式游标须从所选范围重新开始；不能跨格式接续。
- 同来源、同代码、同详情的重复诊断合并并附 `count`，保留 partial 和其他不同诊断。扫描、字形、未知对象、资源缺口及未完成范围仍须报告。

## 实际减少的工作

reading 复用相同核心解析器及容器安全检查，仅解析所选页与必要依赖；关闭 XML 事件清单、节点交叉索引、单成员 XML 哈希、引用证据清单，以及对象属性、祖先链、完整资源目录和完整结构目录的构造。事件仍计数，DTD/实体拒绝、XML/ZIP/引用/字符/节点/展开节点限制保留。

结构导出每节点8192字符的保守成本仅用于真正的结构导出。正文保留数据仍按实际记录计量，不以减字段取消安全限制。合成5000个文字对象的单页默认读取可完整返回一条页文本；显式结构读取仍按原规则触发展开成本上限。这证明解析/构造路径不同，不只是最终删字段。

仍完整读取 OFD 输入字节，并在内存保留所选 XML；没有声称流式解析、最终可见性、自然段恢复或完整 OFD 格式支持。无需新增依赖或分发成员；`packaging/runtime-manifests/ofd.json` 未改变。

## 环境、检查与结果

实际环境：macOS 27.2.1 arm64，Python 3.14.10，Expat 2.7.4，非 root。以下 `python` 指本轮已核实的 `/opt/homebrew/bin/python3.14`；命令从仓库根运行。系统 `python3` 未满足最低3.10要求，未用于业务验证。

| 检查 | 实际结果 |
|---|---|
| `python -I -S -B skills/ofd/scripts/read_ofd.py --check` | 通过，退出0，environment-ok及环境检查范围标记均存在 |
| `python -I -S -B skills/ofd/scripts/read_ofd.py --capabilities` | 通过，退出0，interface_version=2、默认reading/content、content-2 |
| `python -I -S -B -m unittest discover -s skills/ofd/scripts/test -p 'test_*.py'` | 190项通过，退出0 |
| `python -I -S -B -m unittest discover -s tests -p 'test_runtime_packager.py'` | 11项通过，退出0 |
| `python -I -S -B -m unittest discover -s tests -p 'test_skill_runtime_packages.py'` | setUpClass失败，0项执行；已有ET未分类文件 `scripts/wps_ooxml_worksheet.py` 阻塞，未改动ET |
| OFD专项独立包验证（上述190项内） | 临时构建并独立安装，安装只读、PATH为空、隔离启动；默认文本重复调用、detail和full通过，安装文件哈希未变；未覆盖dist包 |
| `python -I -S -B skills/skill-development-standards/scripts/check_skill.py skills/ofd --strict` | 通过 |
| `python -I -S -B tools/check_project_docs.py` | 通过；仅结构/链接/声明检查，不是语义或Mermaid渲染验收 |
| 目标文件 `git diff --check` | 通过 |
| 全仓 `git diff --check` | 已有ET `skills/et/scripts/wps_access.py:125` 末尾空行失败，未更改 |
| `python -m pylint --version` | 退出1，No module named pylint；Pylint未执行，未安装依赖，不声称Error/Fatal为零 |
| 本轮涉及Python的AST、物理行数、行长及多语句定点检查 | 通过；没有超过1000行或200字符行、分号复合语句/单行语句体；不代替Pylint |

190项包括原结构、安全、生成源码回读等回归和新增的默认CLI/API一致性、显式结构迁移、5000碎片合并、解析器无事件/索引保留、模板与重复复合文字、Unicode续读、游标错源/错格式、扫描页、重复诊断、标题歧义、安全计数器及只读安装检查。历史生成源码回归不是恢复对外生成服务。

README、docs索引、OFD说明/架构图、主入口、输出视图、边界和CLI/API契约均已同步。description 为123个Unicode字符，人工核对普通读取、显式结构与混合读取适用，OCR/渲染/写入不适用；新增一条人工选择期望，未做真实模型触发评测。版本采样为 America/New_York `2026-11-11T05:50:25.133057-05:00`，整体加12小时并截断百分之一秒。

## 已有验收样本核对

保留仓库现有 `OFD_Acceptance_20261103.ofd` 和配套验收指南，原件没有复制或改写。该样本是此前生成的两页合成验收文件，不是外部真实OFD格式样本。独立预期来自既有指南：第1页标题、指定中英文/数字句和页脚；第2页标题和页脚。本轮按两页分别读取，这些文字全部匹配，两个响应均 `done=true`、`page_count=2`，并分别保留字形/向量和字形/图片诊断，均为partial。

原文件SHA-256在前后相同：`33bec7c37f4972fa36c3c5a571d11323d6fb73e7e4f882c5f36be0962d59c1db`。同一样本完整结构JSON为100121字符；默认内容两次响应分别721和719字符（均含末尾换行）。这是不同输出范围的字符计数，不是token、耗时、峰值内存或宿主计费测量，也不能替代未输出内容的核对。

## 静态选择与加载证据

仅更新OFD选择指纹和3个OFD加载计划的文件字符/字节/哈希；其他Skill指纹和计划未刷新。更新前OFD子集保存在 [原静态证据](default-content-prior-evidence-20261111.json)，原历史基线不改。

全仓人工选择一致性测试6项中5项通过、1项因并行ET入口哈希已变化而失败；OFD指纹已核对。全仓加载快照回归25项中24项通过、1项因非OFD快照与并行源码不一致而失败；OFD子集另行核对。二者均不是模型选择或宿主usage测量。

## 未验证范围

未取得可用Linux容器入口，目标Linux与Windows均未运行；本机结果不能替代目标平台验收。Pylint不可用，Mermaid仅人工核对节点和调用关系，未运行渲染器。未验证外部真实OFD、客户端视觉/字体显示、OCR、完整XSD、真实宿主模型路由与结果回传。实际CLI由本地subprocess分别捕获stdout/stderr/退出码，当前执行器向模型合并回传摘要；这不证明DeepSeek Harness已按新版本部署或展示。
