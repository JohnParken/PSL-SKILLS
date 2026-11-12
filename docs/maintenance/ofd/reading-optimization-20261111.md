# OFD reading optimization

Date: 2026-11-11. Skill version: `20261111.1542.5900`. Repository paths below are relative to the repository root; this maintenance record is not a Skill runtime resource.

## Delivered behavior

- Preserved the default full `ofd-read-3` result and existing `summary` / `page-text` projections.
- Added `--view reading` for unique text occurrences, source-layer filtering, selected document/page ranges, and resumable slices for long text runs. Cursors bind the input SHA-256, selected range, layer filter, run index, and character offset. Final serialized JSON, diagnostics, and cursor all count against the response budget.
- Scoped reads continue to verify input identity and all ZIP member metadata and limits, while parsing only the selected document and page dependencies. They report parsed and unexamined member counts; the compact view marks that the path inventory is omitted. The range API retains member path lists. Unselected XML payloads are not CRC checked or structurally validated, and the selected page and input bytes remain in memory.
- `page` layer filtering excludes template-origin records only. It does not infer semantic body text, visibility, headers, footers, or occlusion.

## Validation

Platform: macOS 27.2.1 arm64, Python 3.14.10, Expat 2.7.4. Source and synthetic fixtures only.

| Command from repository root | Result |
|---|---|
| `/opt/homebrew/bin/python3.14 -I -S -B skills/ofd/scripts/read_ofd.py --check` | Pass; `environment-ok` |
| `/opt/homebrew/bin/python3.14 -I -S -B -m unittest discover -s skills/ofd/scripts/test -p 'test_*.py' -v` | Pass; 173 tests |
| `/opt/homebrew/bin/python3.14 -I -S -B -m unittest discover -s tests -p 'test_runtime_packager.py' -v` | Pass; 11 tests |
| `/opt/homebrew/bin/python3.14 -I -S -B -m unittest discover -s tests -p 'test_skill_runtime_packages.py' -v` | Pass; 12 tests, including independent package installation checks |
| `/opt/homebrew/bin/python3.14 -I -S -B skills/skill-development-standards/scripts/check_skill.py skills/ofd --strict` | Pass |
| `/opt/homebrew/bin/python3.14 -I -S -B tools/check_project_docs.py` | Pass; structural checks only |
| `git diff --check` | Pass |

The independent runtime regression also builds a temporary OFD package, installs it read-only, and runs the reading CLI against a synthetic OFD. New reading cases cover template filtering, scoped skipping of unselected malformed XML while full reading rejects it, multiple documents, page ranges, Unicode long-run reconstruction, source/scope/cursor rejection, large unexamined-member inventories, and output budgets.

Pylint was not run because it is not installed for the confirmed interpreter; no package was installed. No real external OFD sample or Linux target container was used. Real harness/model output delivery, client rendering, and visual visibility remain unverified; the reader does not offer rendering or OCR.

## 内容视图复核（版本 20261111.1625.0284）

`--reading-format content` 省略成员统计和逐段来源字段，使用同一选页、分层筛选、诊断和续读逻辑。保留可用的文档属性标题，超出标题预算时标记省略。已核对 description 的适用场景和范围，无需更改。

在 macOS arm64、Python 3.14.10 上，从仓库根运行 `python -I -S -B -m unittest discover -s skills/ofd/scripts/test -p 'test_*.py'`，175 项通过。新增两项合成 CLI 回归覆盖标题和字段精简、partial 诊断保留、超长标题省略及 Unicode 长段按偏移完整续读。OFD 严格结构检查、项目文档检查与 `git diff --check` 均通过。

在一次性维护环境运行 Pylint 4.1.3，参数为 `--max-line-length=200 --output-format=json2 --reports=n`，范围为 `skills/ofd/scripts/read_ofd.py` 和 `skills/ofd/scripts/test/test_ofd_reading.py`。退出 28，0 Error、0 Fatal、28 Warning、57 Refactor、44 Convention，无 C0301 或 C0321。警告仍来自同步循环闭包、兼容导入和游标测试访问私有函数。Linux、真实 OFD 和宿主界面展示未验证。

## 内容优先默认路径（版本 20261111.1738.5254）

普通业务读取从 reading 内容视图开始，保留标题（仅唯一纯文字 DocInfo/Title）、文字、页码、状态、必要诊断和页内续读；明确要求结构才使用 full。范围读取不调用完整结构目录，也不解析无关页面、注释和签名 XML；必要资源、所选页面 XML 与容器安全检查仍保留。为了既有程序兼容，CLI 无视图参数和 Python `read_ofd` 的原默认完整结果未改变；这是业务 Skill 路由的默认值，不是 CLI 默认值的改变。内容视图增加 `scope.document_count` 和 `scope.page_count`，供逐文档逐页核对覆盖；不推断正文语义、不执行 OCR。description 已核对，无需更改。

2026-11-11，macOS 27.2.1 arm64、Python 3.14.10、Expat 2.7.4，使用合成样本：OFD 单元测试 179 项通过（包括内容视图扫描诊断、跳过结构目录、跨文档跨页计数、旧 CLI 默认与 full 等价及只读独立包运行）；通用打包安全测试 11 项通过；OFD 严格检查、项目文档检查和目标文件 `git diff --check` 通过。全仓 `test_skill_runtime_packages.py` 在 setUpClass 因已有 ET 未分类文件 `scripts/ps_biff8_drawing.py` 中止，未执行测试；全仓 `git diff --check` 因已有 ET `wps_access.py:125` 文件末尾空行失败。本轮未改这些并行工作。未验证 Linux、真实外部 OFD 样本、真实宿主模型路由及输出回传。未打包发布、提交或部署。
