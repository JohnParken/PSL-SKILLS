# 分批提交前核验（2026-11-12）

仓库维护记录，不随 Skill 运行包分发。用户要求将当前改动按主题提交；本轮从分支 `codex/et-reader-improvements`、源码 `fb6cdff0161a137ebb0e64ebc64d09d11534d6d2` 开始，工作区已有137个已跟踪改动和49个未跟踪文件，暂存区为空。

## 边界与整合修正

- 保留已有实现、用户文件及历史验收记录，不推送、合并、部署、改写历史或重打 dist 发布包。
- 分四批提交：ET 解析、输出预算、相关回归与维护证据；共同结果读取和归档展示说明；文档导航与整合索引/证据；银行月报执行证据。检查针对合并后的工作区执行，不宣称逐个中间提交均已独立通过全仓库验收。
- 两份旧 OFD 验收交付物不是源码运行资源，原位保留且不提交；无删除或移动。
- 新导航使入口数从10变为11；修正 `tests/test_host_result_reading_contract.py` 的旧数量断言，并分别核验导航简版说明与原有完整说明，保留缺少安全片段时应失败的检查。
- 人工复核64项选择期望、58项提供方读取计划及 description 的实际边界；不修改提示、期望、理由、加载路径或业务 Skill 本身。当前快照已过期，因此先逐字保留两个 `*_before_batch_commit_20261112.json`，再刷新入口哈希和当前加载量证据；历史日期记录不刷新。字符/字节计数不代表 token 或真实宿主行为。
- 已检查 README 与 docs，同步当前源码基线和本记录入口；原有技能版本保持不变，原有历史测试数量与平台结果不改写。

## 实际检查

环境：macOS 27.2.1 arm64，Python 3.14.10，Expat 2.7.4，非 root。以下 `python` 指本轮确认的 Python 3.14.10，命令从仓库根运行。系统默认 Python 3.9.6 不满足开发规范，因此未用它执行验收；合格解释器的 `environment-ok` 与检查器 `--help` 均通过。

| 检查 | 结果 |
| --- | --- |
| `python -I -S -B skills/et/scripts/et_cli.py run --check` | 退出0，environment-ok，ooxml-sheet=available |
| `python -I -S -B -m unittest discover -s skills/et/scripts/test -p 'test_*.py' -q` | 643项通过，退出0 |
| `python -I -S -B -m unittest discover -s tests -p 'test_table_workflow*.py' -q` | 177项通过，退出0 |
| `python -I -S -B -m unittest discover -s tests -p 'test_runtime_packager.py' -q` | 11项通过，退出0 |
| `python -I -S -B -m unittest discover -s tests -p 'test_skill_runtime_packages.py' -q` | 13项通过，退出0；含11包隔离安装、只读目录、原件指纹及合成业务流程 |
| `python -I -S -B -m unittest discover -s tests -p 'test_host_result_reading_contract.py' -q` | 修正后2项通过；此前因11与10不符而失败，未计为通过 |
| 全部11个当前 Skill 的严格结构检查 | 全部OK，退出0 |
| `python -I -S -B -m unittest discover -s tests -p 'test_*.py' -q` | 运行337项，退出1；同一个既有DOCX文案断言在源码/独立安装两个子测试失败，其余未报告失败，无跳过，详见下方 |
| 当前选择期望及加载快照单测 | 已包含于上述337项，分别6项、25项，修正快照后无失败 |
| `python -I -S -B tests/benchmark_skill_loading.py --check-snapshot tests/runtime_loading_profiles.json` | fresh，issues为空，退出0 |
| `python -I -S -B -m unittest discover -s skills/xlsx/scripts/test -p 'test_xlsx_delivery.py' -q` | 30项通过，退出0 |
| `python -I -S -B -m unittest discover -s skills/xlsx/scripts/test -p '*wps*' -q` | 13项ET/WPS消费回归通过，退出0 |
| 银行月报维护JSON | 10项ID及必需字段通过；cases与起始HEAD逐项一致，仅补充执行来源 |
| 项目文档结构检查 | 通过，退出0；不替代语义核对、外部链接或Mermaid渲染 |
| `git diff --check` / `git diff --cached --check` | 初次已跟踪差异通过；暂存新文件后，ET批次发现3处既有文件末尾空行，退出2，保留并报告；其余批次通过 |

初次选择案例测试因入口哈希过期失败，初次加载快照测试因旧字节/字符/指纹失败；两者是提交前发现的整合问题，历史失败结果不伪装为通过。

仓库级失败为 `tests/test_docx_runtime_package.py` 中 `test_font_edit_routing_is_preserved_in_runtime_documentation`：测试要求逐字出现“`structure` 模式”，入口实际写“的 `structure` 和 `format_run`”。已核对起始 `fb6cdff`：该测试文件与当前字节相同、字体路由段落与当前逐字相同，且同样缺少要求的确切短语。因此这是原有文案断言不一致，本轮未改动DOCX编辑行为或该测试来消除失败。

ET暂存空白诊断分别为 `skills/et/scripts/wps_biff8_drawing.py:218`、`skills/et/scripts/wps_biff8_result.py:303`、`skills/et/scripts/wps_ooxml_text.py:214` 的 `new blank line at EOF`。三份用户已有未跟踪源码保持原字节；它们通过语法、结构及ET业务回归，但空白检查并非全通过。

## 未验证与限制

- Pylint 未执行：当前 Python 3.14.10 下没有 pylint；历史报告所记隔离解释器也已不存在。未安装工具，未宣称当前 E/F 为零；历史报告仅代表原记录基线。
- 未执行 Linux 容器、Windows、WPS/Office视觉、真实宿主选路/结果回传与云空间显示验收。没有可用 Docker/Podman 入口，本机结果不替代 Linux 目标环境。
- 没有重新下载真实 ET/ETT 原件；既有真实样本、Pylint及模型执行资料按历史证据保留，不作为本轮重新运行的结果。
- 文件检查覆盖所有变更路径及可解码文本，常见密钥/令牌模式未命中；维护证据中的本机路径属于环境来源记录，不是凭据。此检查不构成完整历史秘密审计。
- Git 无锁、合并、变基或拣选状态；可见相关 Codex 任务空闲。进程列表读取被环境禁止，未据此宣称系统中绝无其他写入者；提交前另核对文件指纹和暂存区。
