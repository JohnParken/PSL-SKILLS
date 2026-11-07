# DOCX 稳定性治理：OPC 结构、兼容性与验收边界（2026-10-31）

路径占位符说明：`<skill-root>` 是本 DOCX Skill 根目录的文档占位符，不是 Shell 变量。取得宿主本次实际读取主入口 `SKILL.md` 的真实 `file_path`；若为相对路径，先按本次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名，所得目录才是根目录，不能使用参考文档所在目录。读取资源或执行命令前，将 `<skill-root>` 整体替换为该真实目录，保留资源后缀，将完整路径作为一个参数正确引用；嵌入 execute 字符串时按其语法转义。无法确认主入口真实路径时停止依赖操作并报告，禁止猜测路径。示例的工作目录为宿主已确认的任务目录 `./`，任务输入和受控临时文件沿用该目录内相对路径规则；交付输出只传裸文件名，由脚本固定写入任务根目录 `./`，不写入 Skill 安装目录。

路径约定：`<skill-root>` 指本次实际读取 `SKILL.md` 的父目录。使用以下命令前必须由宿主确认该真实路径，并整体替换占位符；任务输入文件路径由实际任务目录提供。普通业务生成不会调用办公软件。

## 本次修复的真实兼容问题

原先的 `create_docx.py` 使用 ElementTree 序列化 `[Content_Types].xml` 和 `.rels`，由于默认命名空间注册为 Content Types，`.rels` 被序列化为 `<ns0:Relationships xmlns:ns0="...">`。**这在 XML 命名空间意义上是有效的**，不能笼统判断为损坏的 OOXML；但当前验收环境的 LibreOffice 导入器不能加载该形式。相同包将所有 `.rels` 的根节点和子节点改为默认命名空间后可成功打开和导出 PDF。因此生成器 `_relationship_xml()` 现在用固定元素词汇和 `xml.sax.saxutils.quoteattr` 安全写入默认命名空间，避免改动 ElementTree 的进程级全局注册表引起并发竞态。

回归测试要求根关系文件与 `word/_rels/document.xml.rels` 默认使用 `xmlns="http://schemas.openxmlformats.org/package/2006/relationships"`。对外部传入的有效前缀关系文件只警告，不将其当作 XML 结构错误；这是一项兼容性风险判断而不是新的格式限制。

## 已有校验与新增稳定性校验

- 原有安全边界：ZIP 成员数/尺寸、路径穿越与别名、ZIP64 限制、CRC、XML 解析预算、DTD 和实体禁用、OPC 内容类型与 Relationship 目标、正文、样式、编号和复杂表格结构。
- 本轮增强：已知 Word 专用部件的实际 Content Type 检查（避免错误降级为通用 `application/xml`）；`basedOn` 样式继承循环；节内 `pPr/sectPr` 必须位于段落属性结尾；节页眉与页脚的关系类型核对；前缀形式 `.rels` 的 LibreOffice 兼容性警告。
- 本轮新增只读 `<skill-root>/scripts/audit_package.py`：集中返回 `structure_valid`、错误和警告；明确 `xsd_validated=false`、`visual_validated=false`。不修改输入文件，不下载、转换或自动修复。默认文本输出，添加 `--json` 则输出 JSON，退出 0 表示已实现的结构检查通过，退出 1 表示失败。警告不等于彻底兼容；调用方必须阅读。

已有 DOCX 的独立检查示例（在任务目录执行）：

```text
python -I -S -B "<skill-root>/scripts/audit_package.py" ./实际文件.docx --json
```

或使用已有结构校验入口：

```text
python -I -S -B "<skill-root>/scripts/office/validate.py" ./实际文件.docx
```

标准生成器 `verify=True` 已集成结构验收，不需要生成成功后再次运行上面的命令；审计入口更适合独立检查输入文件或追查兼容性问题。

## 分层测试矩阵

| 层级 | 目标 | 当前自动检查 | 不能证明的事情 |
|---|---|---|---|
| ZIP 安全 | 有界读取、路径/别名、CRC | `test_docx_io.py` | 完整 ZIP64/非预期压缩支持 |
| OPC 完整性 | 类型、关系、内部目标 | `test_docx_stability.py`、原 `structure.py` 测试 | 完整 ISO 29500 XSD 语义 |
| 样式/节结构 | basedOn、section、heading/footer rel | `test_docx_stability.py` | 最终视觉页码与可用字体 |
| 复杂内容 | 编号、目录字段、跨行列合并 | P1/P2 回归与综合样例 | Word/WPS 实际更新域结果 |
| 独立来源样本 | LibreOffice 生成的对照 DOCX 不被误拒绝 | `<skill-root>/assets/compatibility/libreoffice-control.docx` | 所有第三方生成器文件均兼容 |
| 真正的应用兼容 | 在 Word/WPS 中打开、保存和重开 | **尚无自动化环境** | 不能据此宣称 Word/WPS 已验收 |

## 可重复的办公软件诊断（仅在独立维护环境，不进入 Skill 业务入口）

本次维护在装有 LibreOffice 的受控环境内，用生成器分别生成最小文本、标题/表格以及目录+分节+合并表格组合案例；通过命令行转换 PDF，原先带前缀 `.rels` 的最小样例失败、修复后的三类样例成功。这里**没有把 LibreOffice 引入运行依赖**。

有 Word/WPS 桌面环境时，建议额外核验：打开时无修复提示；中文字体与标点显示正确；跨行列合并和三线表可编辑；纵横节切换、首页/偶数页页眉页脚正确；目录和交叉引用更新域后结果正确；另存为新 DOCX 后可重新打开；比较文字、表格数量及图片。记录产品名称与版本、操作系统、截图或问题复现文件、检查结果。缺少测试时将对应项标为 `not_tested`，不要补造通过结果。

## 边界与参考

- 不实现完整的 XML Schema（XSD）验证，不能用 `xml.etree.ElementTree` 宣称完全满足 ISO/IEC 29500。
- 不尝试用 Python 字符宽度估算获取真实 Word 页数。目录及 REF/PAGEREF 依然需要 Word/WPS 更新字段。
- 本次 `audit_package.py` 是诊断入口，不代替原来 `run_docx_task.py` 的生成、归档和清理事务机制；所有 P0/P1/P2 输入仍兼容。
- OOXML/OPC 结构参考：Microsoft Learn《Structure of a WordprocessingML document》和《How to: Add a new document part that receives a relationship ID to a package》。
