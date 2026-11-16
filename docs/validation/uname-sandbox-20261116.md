# uname 沙箱兼容维护（2026-11-16）

## 基线与范围

基线为 `8235397` 加当时已有的 WPS、ET、DOCX 等工作区修改。本轮保留这些修改，不提交、不推送、不重打或覆盖历史 ZIP，不读取私人业务样本。涉及 9 个 Skill 的版本统一更新为 `20261116.1353.3706`；采样为 America/New_York 2026-11-16 01:53:37.068957，日历时间加 12 小时后截断到百分之一秒。描述的任务适用范围未变，人工检查后保留原 description。

## 已确认根因

直接文本扫描未发现原有项目源码调用 `os.uname()`，但这不足以排除间接调用。

在 CPython 3.12.11 中，`read_docx.py → docx_read_pages.py → import uuid` 会进入标准库：当 `sys.platform` 不为 win32/darwin 时，旧版 `uuid` 通过 `platform.system() → platform.uname() → os.uname()` 判断平台。因此 Linux 上即使只需要 `uuid4().hex` 随机名，也可能在导入时被沙箱拒绝。3.11 同样具有该路径；检查到的 3.13/3.14 Linux 分支已采用 `sys.platform` 快路径，不能用新版 Python 或 Mac 的成功结果否认旧版故障。

修复前，在 Mac 的 Python 3.12.11 新进程中设置 `sys.platform='linux'`，将 `os.uname` 改为抛 `PermissionError`，执行 DOCX `--check`，取得上述完整导入失败链。修复后同样条件下自检通过。这里是 **Linux 导入分支模拟，不是原生 Linux 操作系统或真实宿主沙箱验收**。

同时确认 `tempfile` 与 `secrets.token_hex(16)` 在该阻断条件下没有调用 uname。没有修改标准库、第三方包或沙箱配置，也没有捕获错误后伪造检测成功。

## 实现与兼容边界

- 33 个 Python 文件的 uuid 相关路径改为无需 uname 的实现。临时文件名、工作区名、缓存与会话 ID 使用 `secrets.token_hex(16)`，继续返回 32 位小写十六进制、使用密码学随机源；独占创建、冲突拒绝、文件身份核对与清理规则不变。
- OFD 文档 DocID 不当作普通随机名处理。`_new_document_id()` 使用 16 个随机字节并设置 UUID v4 版本位及 RFC variant 位，保留原来的 32 位大写十六进制形式。新增随机字节全 0/全 FF 的边界断言。
- 同步 DOCX、公共 artifact-lifecycle 及 XLSX/WPS/ET/OFD/PPTX/TL-DOCX 的适用副本，验证精确复制或已声明的相对导入变换，再更新 7 份来源哈希与来源版本。没有跨 Skill 新增隐式运行依赖。
- 9 个既有脚本中的 `platform.platform/system/machine/python_version` 元数据探测改为解释器已有信息。维护报告的 `platform` 现在是 `sys.platform`（如 `darwin`/`linux`），**不再宣称包含内核版本或硬件信息**；XLSX 写入基准的 `machine` 明确为 `not_probed`，新增 `platform_source=sys.platform`。历史测量文件原样保留，不伪造或回写旧证据。
- PPTX 自检只需系统标签，保留 Linux/Darwin/Windows 常见输出；其他平台回传原 `sys.platform`。安全能力仍按描述符相对操作、Expat 等实际能力校验，不拿标签代替安全检查。

## 回归与扫描方法

新增仓库级 `tests/test_uname_sandbox.py`，不随单 Skill 分发：

1. AST 守卫检查 `skills/`、`tools/`、`tests/` 的 796 份 Python 源码，覆盖直接引用、import 别名、from-import、简单赋值别名、常量 getattr 与旧版 uuid 导入风险；包含正反测试。守卫不会执行扫描内容或读取业务样本。
2. 新进程在导入项目入口前阻断 `os.uname`、`subprocess.Popen` 和 `os.system`，检测“尝试后吞异常”也失败；验证 DOCX/PPTX 的真实自检入口，分别跑原生及 Linux 导入分支模拟，并核对平台标签及 Python 版本。
3. 仅用自行构造的 DOCX，验证全量读取、分页缓存创建/续读/清理、正文拼接相同、原件字节不变。
4. 另人工扫描 Python/Shell/JavaScript/TypeScript/PowerShell 脚本，未发现残留 uname 命令或已知平台探测调用；命中只剩新测试中的阻断和检测用例。历史 JSON 中的执行记录、分发 ZIP、Git 历史及维护虚拟环境第三方代码不改写。

这是可复现的有限回归，不是任意动态代码/第三方依赖的完备调用图证明，也不保证所有脚本每条运行路径都在禁止 uname 的条件下动态执行过。

## 验证结果

以下按实际执行结果分别记录；命令均从仓库根执行，测试临时目录由本轮进程建立并清理。未安装依赖。

- Python 3.12.11 / Expat 2.6.3 和 Python 3.14.10 / Expat 2.7.4：最终新增 4 项沙箱回归分别全部通过；Mac 原生与 Linux 分支模拟都通过。
- Python 3.14.10 / Expat 2.7.4：公共副本与相关流程 79 项定点回归通过；9 个涉及 Skill 的严格结构检查通过。
- TL-DOCX 使用已有维护解释器 Python 3.12.12、python-docx 1.2.0，按 `-I -B` 运行完整套件，232 项通过，无跳过。通用 `run_all_tests.py` 固定 `-S` 会屏蔽该组件，原命令的 TL 导入失败不能记成通过；本轮不改该无关 runner 策略。
- Python 3.14.10 完整业务套件：artifact-lifecycle 21、DOCX 500、ET 747、OFD 200、PPTX 126、WPS 772、XLSX 418、开发规范 44、tests/pptx 3 项通过，均无跳过。
- 独立运行包：通用 Python 环境下 17 项通过，TL-DOCX 1 项因 python-docx 不可见而跳过；使用已有维护虚拟环境补跑时，仍是 17 项通过，TL-DOCX 在真正运行前被源码访问守卫拒绝，因为该组件安装在仓库 `test_workspace` 内。没有放宽守卫、复制组件规避边界或安装新依赖；这项独立包验收仍受阻，不能用 TL 自身 232 项通过替代。
- 仓库级最终复跑：395 项，退出 0，394 通过、1 跳过（上述 TL-DOCX 独立包组件条件）；最初两个当前快照 stale 失败已解决。最终 `check_project_docs.py` 和 `git diff --check` 均通过。
- 当前选择和加载快照最初因 9 个版本指纹及基准脚本变化报 stale；人工核对入口改动仅为版本、原有任务选择边界不变，保留全部 79 条 cases。刷新前的两个当前文件逐字保存在 `tests/*_before_uname_20261116.json`；既有 `_before_content_reading_20261116.json` 与历史快照未改。当前加载报告重新测量，非真实 token 或宿主测量。
- Pylint 4.0.4 / astroid 4.0.4：检查本轮 42 个 Python 文件，按实际 `scripts`、测试目录、PPTX `_engine` 和 workflow XLSX 提供方配置导入路径。未安装可选维护依赖 tiktoken，`benchmark_skill_loading.py` 的两个 E0401 保留为组件缺失，不能宣称全量 Pylint 通过。最终汇总为 Error 2、Warning 36、Convention 592、Refactor 195、Fatal 0。其余 Error/Fatal 在正确导入路径下为零；范围外既有风格/复杂度与受控副本重复提示未批量重构。

## 未验证范围

没有原生 Linux/Windows 系统验收，没有真实 DeepAgents 宿主 sandbox 回传验收，没有 Office/WPS 视觉验收，没有私人样本复跑。TL-DOCX 的第三方组件完整调用链不由项目 AST 扫描兜底。若实际部署仍被拒绝，应核对部署代码/解释器版本与真实拒绝堆栈，不能把本轮有限验证当作所有环境的保证。

## 复现命令

```console
python -I -S -B tools/run_skill_tests.py tests -p test_uname_sandbox.py
python -I -S -B tools/run_all_tests.py
python -I -B tools/run_skill_tests.py skills/tl-docx/scripts/test
python -I -S -B tools/check_project_docs.py
git diff --check
```

第三条必须选择已具备声明组件的解释器。第二条的 TL 隔离参数不合适及独立包 TL 验收缺口按上文记录，不将其非零状态改写成全量成功。
