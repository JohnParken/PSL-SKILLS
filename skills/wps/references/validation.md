# 验证方法、样本证据与未验证范围

验证基线版本 `20261025.2240.5053`，日期 2026-10-25。该基线仅维护入口、契约说明和参考路由，不改变解析代码或 API 行为。运行流程仅用 Python 标准库，不安装包、转换文件、调用办公软件或执行公式、宏、外链。

## 可复现的本地检查

先完成 [入口自检](../SKILL.md#执行环境约束与首步自检)，替换 `<skill_dir>`，在宿主确认的任务目录运行：

```console
python -I -S -B "<skill_dir>/scripts/read_wps.py" --check
python -I -S -B -m unittest discover -s "<skill_dir>/scripts" -p "test_*.py"
```

自检要求退出 0 和 `environment-ok`，并单独确认 OOXML 所需 Expat 可用；测试要求退出 0，区分通过与跳过。运行 [契约示例](reading-assessment.md#可运行的契约示例) 要求退出 0 和 `contract-examples-ok`。独立包验证检查 ZIP CRC、包内路径、每个文件与源码的 SHA-256、隔离运行及执行前后安装文件不变。

| 基线版本实际检查 | 环境与结果 |
|---|---|
| 基础自检及 WPS 回归 | Python 3.12.12 / Expat 2.6.3：自检通过，292 项测试通过 |
| 旧 Expat 环境回归 | Python 3.9.6 / Expat 2.2.8：自检声明 OOXML 不可用，200 项通过、92 项跳过；跳过不算 OOXML 验证 |
| 文档中的 API/CLI 契约示例 | Python 3.12.12：成功、partial、读取失败、参数错误及字节 API 异常断言通过 |
| 输出例外及 BIFF 表头示例 | text 失败通道、不适用策略、参数组合和输出预算失败契约通过；合成 Data 表的显式表头命令通过，错误单元格表头按约拒绝 |
| 静态检查 | 仓库 strict 通过，本地 Markdown 文件与锚点核对通过；未安装或运行专用 Python 命名 linter |
| 独立任务评估 | 隔离目录中实际读取正文/附注/页眉、隐藏策略、保存缓存、显式 Word 兼容、BIFF 共享组和未知容器，输出与限制说明一致；仅合成输入 |

仓库严格检查是维护工具，不是独立运行包的依赖。维护者从仓库根目录使用 Python 3.10+ 运行 `python -I -S -B skills/skill-development-standards/scripts/check_skill.py skills/wps --strict`；普通使用无需获取相邻技能或仓库检查器。

## 合成行为覆盖与准确边界

- **正文与结构**：MS-DOC 的 FIB/CLX、片段顺序与计数；Strict/Transitional OOXML 的正文、逻辑表格、关系与命名空间。通过不代表页面布局或完整格式认证，见 [格式矩阵](format.md)。
- **保存值与公式**：BIFF8/OOXML 单元格类型、原始数字和日期系统；缓存保持未验证或缺失状态，不重算。共享/数组组、富字符串和续段的精确范围见 [公式组](formula-groups.md) 与 [字符串续段](biff-rich-continuation.md)。
- **数字与样式来源**：有限格式条件、区域指令原值、行列来源等价、两层数字格式及会计显示的合成预期；不等于完整本地化或有效样式渲染。详见 [数字解释](numeric-interpretation.md) 及其按需参考。
- **附加区域**：脚注/尾注 CP 引用，批注作者和引用，节与页眉/页脚存储槽；默认正文和显式附加区域保持分开。未计算编号、完整书签或有效页眉继承，见 [存储区域](ms-doc-stories.md)。
- **字符属性与可见性**：有界 BTE/CHPX、CP/FC 来源及原始修改操作；二进制有效隐藏/修订仍 unknown，annotate 不过滤文字，见 [字符属性证据](ms-doc-charprops.md)。OOXML 的隐藏及修订策略另见 [可见性](word-visibility.md)。
- **拒绝与预算**：未知/外部关系、活动内容、加密、结构截断、范围交叠、跨表/SEPX/FKP 归属冲突，以及输入、节点、文字、属性和输出配额。拒绝合法但超出子集的文件不等于文件必然损坏。

## 已记录的真实样本证据

以下保留既有原件记录，不表示此文档维护重新获取或重跑了真实文件：

- 四份 ET/ETT 原件在显式 legacy_excel_clsid + opaque 下为 partial，单元格数量依次为 newfile.et 0、Balance_Sheet.ett 717、Schedule.ett 343、Telephone_List.ett 3976；strict 拒绝，Schedule 默认图表策略拒绝。来源、SHA-256、预期与离线复核命令见 [样本清单](et-real-samples.json) 和 [真实样本验证](et-real-validation.md)。
- Balance 的格式 181 曾保留 28 项会计附加解释，原数字及公式缓存不变；原格式字节和合成数值预期不能替代独立真实显示对照。
- 公开 sample.wps 的 SHA-256 为 `59ea15f76da2ae5a9cf9592a2c7623918c8e385dc9f587233a44ce166b2e5e8d`，608 主正文码元、24 段，六个附加区为空；既有显式 annotate 记录为 81 段 CHPX CP 证据、partial，正文未改变。哈希及精确兼容策略见 [格式依据](format-evidence.json)；该清单未记录此原件的下载定位，不能仅凭哈希复现获取。这不能证明隐藏/修订最终显示正确，也不能验收非空批注或页眉映射。

真实二进制原件不随技能分发；复核需另有获准使用、字节哈希一致的原件。运行时不下载样本。

## 尚未验证或未实现

真实非空批注、脚注/尾注、页眉及隐藏/修订语义，以及新 OOXML 容器、复杂格式显示、VML 锚点等，需要合法原件与不依赖本解析器的内容或显示预期。合成样例改后缀不算真实 WPS 验收，测试数量不代表兼容率。

批注非零长度书签和扩展时间、有效页眉选择、二进制有效隐藏/修订筛选尚未实现；所需结构和边界分别见 [批注](binary-comments.md)、[分节](binary-headers.md)、[字符属性](ms-doc-charprops.md)。私有格式无可靠依据时继续 unsupported。真实宿主自动触发、生产软件显示对照和完整格式认证均不在上述本地检查结论内。
