# DOCX 复杂报告读取验收

本轮源码为 `08fe335` 加工作区改动，DOCX Skill 版本为 `20261113.1512.5446`。正式运行入口、返回字段和限制见 [复杂报告读取](../../../skills/docx/references/inspection.md#复杂报告读取)。下列路径和命令以仓库根为基准，真实附件保留在原任务位置。

## 真实附件

使用用户提供的 文档 DOCX 原件。独立基准在运行新入口前从 ZIP 内部部件取得，包括正文和各表的存储文字、兼容分支中的文本框、纵向合并起止范围、两张原生饼图的类别和数值缓存。没有将附件、数据明细或外链路径加入仓库。

| 项目 | 独立预期与实际结果 |
|---|---|
| 正文与表格 | 83 个直属段落含 18 个标题。14 张表、197 行及 855 个物理单元格的文字与独立读取逐项相符。 |
| 合并与表头 | 3 张表中的 11 个合并起点和 18 个续行已关联。未标记的 13 张表使用候选首行。全表 11 行均标记跨页重复的排名表也保留候选首行及诊断。 |
| 文本框与图片 | 26 处兼容分支均选择存储的 DrawingML 分支。27 个文本框的段落内容逐框相符。11 次图片引用定位到 6 个包内资源。 |
| 图表 | 两张饼图各验证 6 对类别和数值，数值保留原字符串。另 3 个系列保留类别和缺值诊断。两张图的外部工作簿关系已识别。 |
| 编号及附加部件 | 8 个显式单级十进制编号已读取。此样本无常规附加文字部件，相关内容的正例见合成测试。 |
| 分页及输入保护 | 默认 12,000 字节预算共 12 次读取。369 块逐项等于单次大预算结果，单次结果 116,871 字节。各次 CLI 均退出 0，stdout 可完整解析，stderr 为空。源文件 SHA-256 前后一致。 |

同一附件还从临时构建的 87 文件只读运行包中读取。禁止加载第三方包，清空可执行文件搜索路径。结果返回 27 个文本框、2 张图、12 个缓存值和 18 个合并续行，原件及安装文件均保持字节一致。验收包位于独占临时目录，运行后已清理。

## 自动验证

平台为 macOS 27.2.1 arm64，Python 3.14.10，Expat 2.7.4。以下命令中的 `python` 为已确认的解释器。合成回归与上面的真实附件验证各自独立。

| 检查 | 结果 |
|---|---|
| `python -I -S -B -m unittest discover -s skills/docx/scripts/test -p 'test_*.py' -q` | 469 项通过，退出 0，无跳过。 |
| `python -I -S -B -m unittest discover -s tests -p 'test_docx_runtime_package.py' -q` | 27 项通过，退出 0，无跳过。 |
| `python -I -S -B -m unittest discover -s tests -p 'test_runtime_packager.py' -q` | 11 项通过，退出 0，无跳过。 |
| `python -I -S -B -m unittest discover -s tests -p 'test_skill_runtime_packages.py' -q` | 最终 13 项通过，退出 0，无跳过。先前运行遇到并行维护中的 WPS 分发清单未同步及源文件变化，待工作树稳定后复跑。 |
| `python -I -S -B skills/docx/scripts/read_docx.py --check` | 退出 0，返回 `read-check-ok`。 |
| `python -I -S -B skills/skill-development-standards/scripts/check_skill.py skills/docx --strict`、`git diff --check` | 均通过，退出 0。 |
| `python -I -S -B tools/check_project_docs.py` | 退出 1，剩余项为 ET、OFD、table-workflow、XLSX 的文档版本不同步，基线检查已出现。DOCX 版本和链接通过核对。 |
| Pylint 4.1.3 | 对本轮涉及的 10 个 Python 文件运行 `--max-line-length=200`，定位导入时使用项目的 DOCX 脚本和 ET 测试路径。全量退出 28，保留复杂度、文档字符串和测试风格等提示，未见 C0301/C0321。相同范围 `--errors-only` 退出 0。 |

人工核对描述覆盖复杂报告文字和指标提取，并保留原生格式路由与编辑保护约定。目标 Linux 容器、Windows、真实宿主选用、Word/WPS 的视觉页面与字体结果、外部工作簿的源值未验收。图片本次仅核对资源引用，OCR、实际页序及一般样式继承超出此读取入口的范围。

## 默认一次读取

版本 `20261113.1526.2905` 将仅传文件名的读取入口改为完整要素 JSON。正文、合并表格、文本框、图表缓存、图片引用和相关部件共用原报告解析与输入校验。旧纯文本 CLI 调用加 `--format text`。已核对现有描述覆盖新默认能力，格式路由与能力范围沿用上面的验收口径。

再次使用相同文档原件，CLI 仅传文件名即返回 **116,871 个 UTF-8 字节、369 个块**，退出 0，stderr 为空。`coverage.selected_view_complete=true`、`has_more=false`、`next_cursor=null`。保留 14 张表的 197 行、27 个文本框、两张图的 12 个数值及 6 个图片资源引用。3 个系列缺少数值缓存，结果仍标记为 `partial`。按 12,000 字节分 12 批收集的块与默认一次读取逐项一致，原件 SHA-256 前后一致。用临时构建的 87 文件只读运行包复跑文件名入口，返回相同的块数和字节数，安装和原件字节均保持一致。

macOS 27.2.1 arm64、Python 3.14.10、Expat 2.7.4 下，DOCX 源码回归 **472 项**、DOCX 独立安装 **27 项**、跨 Skill 独立安装 **13 项**、自检契约 **9 项**、归档提示契约 **3 项**通过，均退出 0，无跳过。上述用例使用 `python -I -S -B -m unittest discover`，对应路径为 `skills/docx/scripts/test` 的 `test_*.py`，以及 `tests` 下的 `test_docx_runtime_package.py`、`test_skill_runtime_packages.py`、`test_self_check_contracts.py` 和 `test_archive_notice_contract.py`。严格 Skill 检查、读取自检和 `git diff --check` 通过。Pylint 4.1.3 检查本轮改动的 8 个 Python 文件，`--max-line-length=200` 退出 28，保留文档字符串、复杂度与测试风格提示，同范围 `--errors-only` 退出 0。全仓文档检查仍报告上述四项 Skill 的既有版本不同步。

真实文档验收使用本地子进程分别核对 stdout、stderr 和退出码。Linux 运行及真实宿主的模型回传、截断行为未验证。

## 列映射、缺值定位与续读复用

版本 `20261113.1842.3507` 保留只传文件名的默认入口。输出新增 `header.columns`、`merged_groups`、图表引用范围及可定位的缺值单元格。description 已核对并更新读取范围，运行仍只用 Python 标准库。

以文档原件的字节一致副本在独占任务目录执行 `read_docx.py`。用标准库独立读取表格单元格、合并标记和两张图的引用与缓存，作为字段验收基准。两轮分别使用源码和新构建的 88 文件只读包。都在空 `PATH` 和 `-I -S -B` 下运行，退出 0，stderr 为空。原件、任务副本和安装文件的 SHA-256 前后一致。

| 核验 | 实际结果 |
|---|---|
| 表头与数值 | 14 张表、62 个逻辑列名、197 行、855 个物理单元格逐项对照。13 张表缺表头标记，另 1 张整表标为重复表头，均保留首行候选状态。 |
| 合并行 | 29 行返回分组，涵盖 11 个起点和 18 个续行。起点坐标和类别逐项与 XML 对照。 |
| 图表 | 12 个保存值逐项对照。图表 A的计数系列为 `$B$2:$B$7`，图表 B前两个系列为 `$B$2:$B$7` 和 `$C$2:$C$7`。18 个缺值仍为空值，均带具体单元格和可回查的工作表范围。 |
| 分页 | 全量 369 块、123,219 个 UTF-8 字节。按每页 12,000 字节分 12 页，首存 1 次、续读命中 11 次。拼接块与一次读取逐项相等，读完无缓存残留。另有回归用阻断 ZIP/XML 解析的方式确认命中页走复用路径。 |

维护命令均从仓库根执行。源码回归使用 `python -I -S -B -m unittest discover -s skills/docx/scripts/test -p 'test_*.py' -q`，489 项通过。对 `tests` 分别运行 `test_docx_runtime_package.py`、`test_runtime_packager.py`、`test_self_check_contracts.py` 和 `test_archive_notice_contract.py`，28、11、9、3 项通过。macOS 27.2.1 arm64，Python 3.14.10，Expat 2.7.4，均退出 0，无跳过。跨 Skill 独立安装另用带 `python-docx 1.2.0` 的宿主 Python 3.12.14 运行 `test_skill_runtime_packages.py`，16 项通过，无跳过。第三方包仅用于该测试覆盖其他 Skill，自包含的 DOCX 读取子进程仍以 `-I -S -B` 启动。

DOCX 严格检查、`read_docx.py --check` 和 `git diff --check` 通过。Pylint 4.1.3 对本轮涉及的 9 个文件执行 `--max-line-length=200`，退出 28，保留命名、文档字符串、复杂度和测试风格提示。无 E/F 或 C0301/C0321，`--errors-only` 退出 0。全仓文档检查仍只有本轮前已存在的 ET、OFD、table-workflow、XLSX 版本差异。Linux、Windows 和真实宿主的长结果回传未验证。

## 按章节完整读取

版本 `20261113.1916.2318` 将 `--section` 默认接入报告视图。用 `--section '标题' --format text` 取得原有纯文本。章节和出现序号参与续读绑定，章节输出沿用正文、表格和图表的原始编号。章节范围覆盖下级标题和正文锚定对象。页眉、页脚及其他关联部件正文由全量视图返回，正文中的关联引用仍随章节返回。主入口和 description 已核对，运行依赖仍为 Python 标准库。

在独占任务目录使用文档字节一致副本。先用标准库直接核对正文标题、图表锚点、关系、饼图类型及数值缓存，再分别用源码和临时构建的 88 文件只读包验收。两次均在 `-I -S -B` 和空 `PATH` 环境下运行，真实退出码为 0，stderr 为空。

| 章节 | 实际输出 | 12,000 字节分页 |
|---|---|---|
| 文档章节 A | 40 个块。2 张表、15 行、1 个文本框、0 次图片引用，读出「图表 A」及 6 个缓存值。6 个缺值带单元格定位。 | 2 页，续页命中缓存 1 次。 |
| 文档章节 B | 91 个块。2 张表、44 行、5 个文本框、2 次图片引用，读出「图表 B」及 6 个缓存值。12 个缺值带单元格定位。 | 3 页，续页命中缓存 2 次。 |

两章的拼接块均与单次选读和全量相应区间逐项一致。上级章节同时读出两张饼图。每轮末页清理缓存，文档原件和安装文件字节一致。只传文件名的 stdout 与修改前的 123,219 字节结果逐字节一致。两张图均保留外部工作簿缺值诊断。

macOS 27.2.1 arm64、Python 3.14.10、Expat 2.7.4。仓库根目录用 `python -I -S -B -m unittest discover -s skills/docx/scripts/test -p 'test_*.py' -q` 跑过 493 项。对 `tests` 单独发现 `test_docx_runtime_package.py` 和 `test_self_check_contracts.py`，分别通过 28、9 项。无跳过。严格检查、`read_docx.py --check` 和 `git diff --check` 通过。Pylint 4.1.3 对本轮 6 个 Python 文件运行 `--max-line-length=200`，退出 28，含命名、文档字符串、复杂度和测试风格提示。无 E/F 或 C0301/C0321，`--errors-only` 退出 0。全仓文档检查仍报告原有四个非 DOCX 版本差异。Linux、Windows 和真实宿主的大结果回传未验证。
