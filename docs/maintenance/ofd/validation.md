# 来源与验证记录

维护路径说明：本页以宿主确认的 PSL-SKILLS 仓库根目录为基准；`skills/ofd/` 是源码目录，`docs/maintenance/ofd/` 是维护证据目录。先确认仓库位置；不能从独立运行包推断仓库。原记录的测试数量、平台、版本与结论保留历史含义，不代表当前重跑。测试仅在源码树执行，不随精简运行包分发。

测试脚本路径已按当前布局更新至 `skills/ofd/scripts/test/`；下文历史测试数量、平台及结论仍为当时记录，不代表本次重跑。测试只用于维护，生产不得调用。

## 2026-11-09 Pylint 合规维护

从仓库根使用 Pylint 4.1.3、Astroid 4.3.4 和 Python 3.12.12 扫描 `skills/ofd` 下全部 17 个 Python 文件，含运行脚本、结构验收工具及测试。命令为 `python -B -m pylint --max-line-length=200 --output-format=json2 --reports=n`，追加 `rg --files --hidden --no-ignore skills/ofd -g '*.py'` 的文件列表。检查环境可导入 `skills/ofd/scripts` 和 `skills/ofd/scripts/test`，Pylint 仅用于临时维护，运行依赖仍是标准库。

| 诊断 | 初扫 | 复扫 |
|---|---:|---:|
| Error / Fatal | 0 / 0 | 0 / 0 |
| Convention | 342 | 336 |
| Warning | 45 | 25 |
| Refactor | 133 | 133 |

修正压缩异常别名的命名诊断并保留实际异常类型，声明生成器只在处理页面时赋值的状态，移除未使用的验收工具导入和读取器赋值，保留引用校验调用。测试显式设置 `subprocess.run(check=False)` 以继续断言退出码，并去掉冗余括号、绑定字体损坏测试的当轮文件名。全量复扫退出 28，剩余 Warning 为 19 条读取器同步闭包捕获和 6 条兼容导出。保留严格整数判断，以维持拒绝布尔值及整数子类的契约。定点复扫 `--disable=all --enable=E,F,C0103,C0301,C0321,C0325,W1510,W1514 --reports=n --score=n` 退出 0。

macOS arm64 / Expat 2.6.3，Python 3.12.12 与 3.13.12 均通过读写自检，并各自通过 `-I -S -B -m unittest discover -s skills/ofd/scripts/test -p 'test_*.py'` 的 160 项回归。验证了正常及模拟缺失 zlib 时的异常别名和自检行为。OFD 严格结构检查、独立运行包 11 项验收（含随包字体生成及回读）、项目文档、选路证据、加载快照和 `git diff --check` 均通过。人工复核 description 与读写能力相符。Linux、Windows、真实样本和客户端视觉未复验，发布包未更新。

## 规范身份与实现依据

本参考主要记录读取器历史验收。当前基础生成入口、字体许可/度量限制、独立维护工具交叉检查与尚未完成的客户端/视觉验收见 `skills/ofd/references/generation.md`；生成器不继承读取器的版本覆盖或完整性结论。

目标规范是 **GB/T 33190-2016《电子文件存储与交换格式 版式文档》正式版**，不以网上草案代替。以下是可追溯的公开来源：

- 国家密码管理局正式介绍 (`https://www.oscca.gov.cn/sca/ztpd/2017-04/19/content_1012652.shtml`)：2017-04-19，来源为国家电子文件管理部际联席会议办公室，确认标准的范围。
- 国家标准全文公开系统 (`https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=3AF6682D939116B6F5EED53D01A9DB5D`)：确认标准号、2016-10-13 发布、2017-05-01 实施及正式发布单位。
- 江苏档案部门公开的正式标准 PDF (`https://www.dajs.gov.cn/attach/0/88a7620d9d3f4e13b2baa52ab3487854.pdf`)：135 页，公开索引确认封面标准号及发布日期。没有将 PDF 或草案复制入包。

**证据边界：**已确认正式规范身份和公开附件，但没有完成正式全文所有字段的逐条核对。当前字段处理为待其余正式条款与真实样本进一步验收的有限实现，不能称为 GB/T 33190 全面符合性实现。已按独立查看的正式扫描条文核对：§6.2 单一 OFD.xml、§7.1 命名空间、§7.3 ST_Loc 点段与包根、§7.6 显式页序、§7.7 模板内部引用无效、表45 TextCode 四位十六进制转义。尚未逐条核对整个格式，不据此作完整合规认证。下述合成结果不填补这一规范语义证据缺口。

## 当前资源索引缺失恢复验证

2026-11-03，macOS arm64 / Python 3.12.11 / Expat 2.6.3，标准库隔离启动：维护前92项回归通过，增加11项回归后共103项通过。缺失 PublicRes、DocumentRes、PageRes 索引在1.0/1.1/1.2均保留独立可读文字并返回partial；仍拒绝路径越界、外部路径、目录、现存畸形XML、DTD/实体、资源载荷缺失及必需文档/页面/模板缺失，引用预算继续生效。full、summary、page-text均保留诊断和partial/3。新能力不改变description声明的XML存储与文字读取范围，未扩大到OCR、渲染或完整规范认证。

重新读取固定提交的四个公开 OFDRW 原件并核对既有SHA-256：Page5曾因缺失PublicRes整体failed，现保留五页、七个XML部件，明确missing_resource_index和partial/3；原件五页没有TextCode，目标文字登记为empty，未把图形解释成文字。修改解析器前，用独立标准库ElementTree逐页提取TextObject/TextCode并冻结预期，仅验证原始存储文字，不是可见文字或版式预期。reader HelloWorld按既有上游固定文字断言通过；1.1 keyword2和1.2 areaholder的文字哈希与维护前一致，后两项只是回归保留证据，没有新的独立全文验收。来源、原始哈希和当前结果见 `docs/maintenance/ofd/real-samples.json` 的 resource_index_recovery_run；完整维护记录位于仓库 `tests/ofd_resource_recovery_20261103.json`，不随包分发。

没有在Linux或Windows运行当前修复；本机Python 3.12.11通过不能代替目标Linux验收。未执行本轮独立代理审查或模型触发评测；此前独立审查仅作为历史证据。当前静态检查仍使用标准库AST/tokenize和严格结构检查器，未安装第三方linter。

## 2026-11-02 结构扩展历史验证

2026-11-02 使用 Python 标准库直接读取 OFDRW 固定提交 `c633a61fb37d300392a51f67b101f46738866c83` 的7个未修改公开测试原件，不运行上游 Java/JS，不使用 OCR、PDF 转换器或外部解析器。每个原件的源 URL、SHA-256、大小、Version、实际状态及退出码见 `docs/maintenance/ofd/real-samples.json`。

- 两个 reader HelloWorld（其中一个含 PageBlock）按上游 ContentExtractorTest.java 的独立固定文字预期核对，均单页22字符、complete/0。预期在运行目标解析器前冻结。原有固定历史样例记录保留，不按相同字节的不同提交重复计数为新的语义样本。
- 真实 Version 1.1 keyword2 原件：13个 XML 部件的结构目录完成，一页63段541字符。其 CustomTag.xml 带异常命名空间名称，保持原始名称和内容、namespace_resolution=partial，主 OFD/页面未放宽。全局仍 partial/3，不能把“结构已存储完整”冒充“命名空间、所有文字及显示语义均完整”。没有独立全文预期，不能据数量推断无漏字或显示一致。
- 真实 Version 1.2 areaholder_fields 原件：5个 XML 部件、一页2段4字符；真实 OFD-A 1.2 原件：4个 XML 部件、一页1段11字符。两者结构目录均 complete，全局仍因向量/附加部件或归档规范未认证而 partial/3；没有独立全文或归档规范验收。
- converter HelloWorld 作为额外结构烟雾验证，complete/0；当时另一 Page5 原件引用不存在的 PublicRes.xml，failed/1。当前资源索引缺失恢复结果见上一节，历史结果保留而不改写。
- 页资源、嵌套复合对象新增行为仍只有合成验证；没有新增政务/商业原件全文、表格或视觉验收。

本轮实际平台为 macOS、Python 3.13.12 / Expat 2.6.3，保留 `-I -S -B`，运行隔离测试时 PATH 为空。未取得目标 Linux 容器执行入口，Linux 及 Windows 未验证；本机通过不等于目标平台验收或跨平台保证。旧 Python 3.12 基线仅为历史记录。自检对缺少安全文件操作原语的宿主仍明确拒绝，不降级。

## 合成与安全回归

84 项测试通过：前版54项继续通过（契约断言显式升级为schema 3、三个已知版本同等判定），新增30项结构目录测试。覆盖所有逻辑元素/属性/text/tail与独立标准库 XML 读取的逐项比较、XML声明/注释/PI/命名空间事件、存储文字与页面出现分离、批注/自定义数据、版本存储、角色隔离、未知数据保留、异常业务 XML 原名模式，以及名称/URI展开资源上限。原有 ZIP/XML、路径竞态、DTD/实体、CRC、压缩大小伪造、预算与部分读取契约继续通过。

从宿主确认的仓库根目录运行；python 替换为已确认的兼容解释器：

```console
python -I -S -B skills/ofd/scripts/read_ofd.py --check
python -I -S -B -m unittest discover -s skills/ofd/scripts/test -p 'test_ofd*.py' -v
python -I -S -B skills/skill-development-standards/scripts/check_skill.py skills/ofd --strict
```

独立包使用 `python -I -S -B -m unittest discover -s "skills/ofd/scripts/test" -p 'test_ofd*.py' -v`。结构检查器是仓库维护资源，不随包分发。整个只读解包副本重复测试、执行前后源码及包成员哈希核对分别记录在仓库维护文件 `tests/ofd_structure_validation.json`；开发 ZIP 不是业务文件清理流程，不引入运行时归档依赖。

CLI 实际 stdout/stderr/退出码通过 subprocess 捕获；模型收到执行器返回的测试与结果摘要，不据此声明已完成所有宿主的完整 JSON 回传集成验收。业务任务仍须保留非零退出时的完整诊断，不分析被截断的 JSON。

## 独立审查与静态检查

独立审查实际复现了共享页输出放大、异常位置 TextCode 静默遗漏、外来命名空间文字混入三个问题；已修复并新增永久回归。节点预算与字符成本预算分别限制解析缓存复用导致的展开。后续复核还发现并修复 Content 外对象、公共数据/资源命名空间误识别、zlib 自检缺口、STORED 大小伪造、检查与打开分离的输入竞态，以及重复单值容器遗漏后 complete；均有固定回归。本轮独立初审另复现了部件顺序依赖、XML/载荷角色冲突及未生效 XML 伪声明载荷导致遗漏三个问题；主代理按类型化生效路径约束、提前声明及冲突拒绝修复，固化回归。修复后的原第二次独立补跑因平台内容过滤未完成，保留为历史限制；不能用主代理回归替代。2026-11-02由另一维护线程完成修复后复审，以独立构造的小型合成包验证三项原问题及正确索引、层级、ZIP顺序的正反例：6项测试覆盖15个变体通过，未复现原三项缺陷；本次复审不是完整格式或特权并发隔离认证。回归已迁入 `skills/ofd/scripts/test/test_ofd_postfix_review.py` 并随独立包分发；证据 `tests/ofd_postfix_review_verification.json` 仍以仓库根为基准，不随包分发。受限输入检查不承诺对特权并发写者的完整隔离。最终回归在修复后执行，不以跳过测试掩盖边界。

结构检查使用仓库自带的 Python 标准库检查器，覆盖元信息、文件命名、引用和 Python 语法；不代替安全或格式语义验收。人工核对常量与局部变量命名、外部 XML 标签未被改名，运行模块不跨 Skill 导入。本轮未执行 pylint：该开发工具不可用，未安装；不能宣称其诊断已清零。使用标准库 AST/tokenize 核对行长≤200、无分号复合语句、标准库导入及语法；这是定点检查，不等价于完整 Pylint。前版既有测试的纯排版部分比较去掉位置属性后的 AST 一致；本轮新增行为和契约变化均运行实际测试。description 人工核对了“三版本 XML 结构、批注/资源文字、存储覆盖核对”适用与“OCR、转 PDF、验签、生成”不适用请求；历史四技能20项选择模拟保留为历史证据；本轮人工核对新版描述的适用与不适用场景，未重跑模型触发评测。这是有限任务选择模拟，没有运行真实宿主自动触发验收。评测记录属于仓库维护资源 `tests/skill_selection_cases.json`，不在独立包内。

## 依赖审计

技能入口仅导入 Python 标准库模块；无第三方 Python 包、外部命令解析器、OCR、Office、PDF 工具或转换器，不安装任何组件。解释器须自带所需 zlib 与安全版本的 Expat，CLI 另需 dir_fd / O_NOFOLLOW / O_DIRECTORY；缺失时停止，不要求另装系统软件。测试中的 subprocess 只启动同一 Python 解释器，空 PATH 隔离测试无需外部可执行程序。读取公开样例的 urllib 下载代码属于维护验证，不进入运行包；运行脚本不含网络客户端。读取公开的上游文字断言只是来源证据，不引入或执行外部解析器；独立分发包不含样本下载功能。

## 发布验收

版本化分发包位于仓库 `skills/ofd-<version>.zip`，与其他技能的现有位置一致。分发 ZIP 应仅包含 `ofd/` 下实际源码、引用和依赖声明；成员名与源目录一致，不含任务原件、临时产物或缓存。发布时重新读取 ZIP、比较每个成员 SHA-256、从解包副本运行测试，再交付包的实际路径与哈希。源资源不需要特殊文件名映射，也不在执行时改名。ZIP 的 SHA-256 在外部交付记录给出，不写入包内造成自引用。
