# TL-DOCX 高级差距补齐

本轮基于上一轮未提交工作树继续维护，原 DOCX 未修改；TL 版本 20261114.1000.1951。没有提交、推送、安装新运行依赖或重打历史 ZIP。

## 已实现

- 原生角色样式：BodyText、Title、Subtitle、Heading1–9、TableBody、TableHeader 基于 Normal；角色格式进入 styles.xml，局部覆盖保留直接属性。保留未管理属性并按 schema 顺序插入，新增继承传播回归。
- 显式内容解析缓存：read --cache / --cache-token 与 release-cache；默认读取不写文件，缓存位于任务目录顶层并绑定源摘要、版本、选项及文件身份。末页释放，新缓存首次分页失败清理。
- 文字修订：tracked_replace_run 生成 w:del/w:ins，保留字体、段落与非目标部件；单 run 精确定位，拒绝复杂或已有修订目标。
- 受控部件包：unpack/repack 使用完整、源绑定的 JSON 部件包，不展开目录树；16 MiB 上限，支持 DOCX/DOTX，禁止增删成员和签名修改，重包复用已有发布与归档。
- 静态布局审查：分节几何、装订线、表格网格/纵向合并、段落属性顺序及制表位、页码和引用；宽度/图片风险为 warning，strict-layout 可作为门禁。

## 回归发现并修复

新审查器查出创建样式合并未管理属性时错误追加 pBdr/numPr 的顺序问题，导致首轮 14 个用例失败。修复生成器使用 schema-aware 插入，而不是降低审查规则，随后通过全部 TL 回归。

## 未完全等同的边界

- 不提供任意自定义样式继承图；列表、图片说明、页眉页脚等仍使用各自直接格式。
- 缓存只支持 JSON 内容/报告 stdout，不用于 XML/结构/文本或报告导出；跨次仍核验源包，不是完全零解析。强杀残留须使用原 token，跨版本旧 token 不复用。
- 不支持任意嵌套修订、移动/格式修订或完整接受/拒绝。
- JSON 部件包不是目录式解压；重包后只保证非目标成员解压内容不变，不保证 ZIP 元数据/整体字节一致。
- 静态布局审查不是完整 schema 或渲染器；浮动重叠、分栏、单元格图片适配、继承页面默认值、字体与视觉断页须另验。

## 验证记录

平台 macOS 27.2.1 arm64，Python 3.12.12，python-docx 1.2.0、Expat 2.6.3。合成 OPC 与 CLI 流程测试，不冒充真实 Office/WPS 或 DeepAgents 验收。

- TL-DOCX 全套 164 项通过；原 DOCX 493 项通过。
- Skill 严格结构、项目文档检查及 git diff --check 通过。
- 各新增实现专项 Pylint 的 E/F、C0301/C0321 为零，其他 W/C/R 保留。最终聚合 Pylint 因已有工具的绝对依赖路径被执行审查阻止；新集成入口和 CLI 测试不能据此宣称聚合 lint 已通过。
- 仓库集成首跑将临时安装置于源码树内，349 项中 24 failures / 1 error 为独立部署的源码访问防护触发。没有降低防护，改在用户工作目录运行与源码隔离的相对临时工作区复核，最终结果另记。
- Linux、Windows、真实 DeepAgents、Office/WPS 视觉及客户端字段更新未验证。

最终复核：与源码隔离的相对临时工作区内，仓库集成 349 项全部通过（78.470 秒），其中独立运行包 17 项通过。所有 TL Python 47 个文件 AST 可解析，最大 935 行，无超过 200 字符的代码行；这些检查不替代聚合 Pylint。

## 授权安装后完成聚合 Pylint

用户明确允许安装后，从官方 `https://pypi.org/simple` 以 `--isolated --only-binary=:all: --no-cache-dir` 安装 Pylint 4.0.4 和 python-docx 1.2.0 到项目已忽略的 `test_workspace/tl-lint-20261114` 虚拟环境。未修改系统 Python、业务 requirements 或已打包 ZIP。传递依赖含 astroid 4.0.4、lxml 6.1.3 等，维护环境不进入分发。

对 TL 全部正式 Python、测试及受影响 `tests/test_skill_runtime_packages.py`，共 48 个文件，执行完整 Pylint（`--persistent=n --max-line-length=200 --output-format=json`）。导入路径为项目相对的 TL 脚本、ET 脚本及测试夹具路径，与既有联合测试一致。

- 完整扫描退出 28：Error 0、Fatal 0、C0301 0、C0321 0。
- 其余诊断为 Convention 251、Refactor 159、Warning 85，不宣称全量零告警。
- 额外 `--errors-only` 专项复核退出 0；没有禁用错误类别。
- 已审阅其余诊断：主要是测试文档字符串/隔离导入顺序、OOXML 私有成员、复杂度、严格排除 bool 的类型检查及既有未使用局部量。读取缓存的循环引用在函数内部延迟导入，并有跨进程缓存测试；不属于初始化时循环依赖失败。公共来源副本的提示未借机改写或静默分叉。
- 新隔离环境中 TL 164 项回归通过。项目内维护环境运行独立包验收时，因其 python-docx 位于源码树内，被既有源码访问隔离防护正确拒绝；不降低防护，独立包验收改用仓库外已有业务解释器。

本轮无需要修复的 E/F 或项目行长/多语句问题，未更改业务代码。README/docs 的能力和版本已核对，无新增能力说明需要修改；本节补全验收证据并解除上文聚合 lint 未完成状态。

仓库外业务解释器最终复核：独立运行包 17 项全部通过（6.188 秒）。
