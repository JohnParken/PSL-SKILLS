# 分页策略治理（2026-10-31，P3 稳定版增量）

路径占位符说明：`<skill-root>` 是本 DOCX Skill 根目录的文档占位符，不是 Shell 变量。取得宿主本次实际读取主入口 `SKILL.md` 的真实 `file_path`；若为相对路径，先按本次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名，所得目录才是根目录，不能使用参考文档所在目录。读取资源或执行命令前，将 `<skill-root>` 整体替换为该真实目录，保留资源后缀，将完整路径作为一个参数正确引用；嵌入 execute 字符串时按其语法转义。无法确认主入口真实路径时停止依赖操作并报告，禁止猜测路径。示例的工作目录为宿主已确认的任务目录 `./`，任务输入、输出和临时文件均在该目录及其子目录内，不写入 Skill 安装目录。

本版维持已通过 Word/WPS 实机验收的 P3 **生成行为不变**，不自动插入、删除或合并分页符、不自动换页、不默认给一级标题增加 `page_break_before`。所有新增风险规则是纯 Python 标准库的 **OOXML 静态检查**；警告不是 Word/WPS 真实页码或空白页事实。原 `page_break` / `section_break` / `format.page_break_before` 输入仍可使用。

## 默认公文自然分页策略（简单任务先执行）

默认 `theme: official`，用户未明确要求强制换页时，不生成任何 `page_break`、`section_break`、`format.page_break_before:true`。即使有多个一级标题，正文也仍由 Word/WPS 自然换页，`Heading1`～`Heading4` 原生样式负责标题与后文尽量同页。**不要为使页面看起来整齐而通过空段落、`\f`、手动分页堆叠模拟版面。**

任务判断顺序：

1. 未明确要求换页（普通通知、说明、总结、含多个标题的报告）→ **不添加**换页控制；标题使用已有样式。
2. 明确要求某标题另起新页 → **仅在该标题**配置 `format.page_break_before:true`。
3. 明确要求封面结束后另起正文页，且不涉及节级设置 → 在两个块之间**仅加入一个**独立 `{"type":"page_break"}`。
4. 需要横向附录、不同页边距/页眉页脚、页码重置 → 使用 `section_break`，其分页式 `break_type` 已包含换页含义，**不额外加** `page_break`。
5. 用户显式提供合法分页配置 → 保留其意图；有冲突时告警或澄清，不静默删除或自动转换。严格模式仅作为可选质量门禁。

**自然分页示例（无强制分页）**：

```json
{"theme":"official","blocks":[{"type":"title","text":"工作总结"},{"type":"heading","level":1,"text":"总体情况"},{"type":"paragraph","text":"第一部分正文。"},{"type":"heading","level":1,"text":"下一阶段工作"},{"type":"paragraph","text":"第二部分正文。"}]}
```

**明确要求章节另起页的示例**：`{"type":"heading","level":1,"text":"下一阶段工作","format":{"page_break_before":true}}`，此标题前不再添加独立 `page_break`。

## 分页对象、使用规则

- **手动分页**：`{"type":"page_break"}` 是独立 block，只允许 `type`，在正文段落中生成 `w:br w:type="page"`。用于明确的封面结束、固定章节边界；不使用 `\f` 控制字符或堆积空段落模拟。
- **段前分页**：`{"type":"heading","level":1,"text":"第二章","format":{"page_break_before":true}}`。该段绑定 `w:pageBreakBefore`，用于确定该标题必须从下一页开始；不在它前面再单独插入 `page_break`。
- **分节**：`{"type":"section_break","break_type":"next_page","page":{"orientation":"landscape"}}`。此处开始**新节**，而非普通分页；适用于横竖页面、独立页边距、不同页眉页脚或重新起算页码。`next_page` / `odd_page` / `even_page` 本身可能换页；不相邻增加普通分页。`continuous` 不保证页面属性改变时阅读器仍然连续排版。
- **标题同页**：默认依赖现有标题样式 `keep_with_next`，正文可设置 `keep_together`、`widow_control`。非强制分页，不保证跨阅读器的最终分页位置。
- **推荐选择**：普通正文自然分页；常规标题仅 `keep_with_next`；确需新页的标题使用 `page_break_before` 或单独的 `page_break`，二者二选一；横向附录等使用 `section_break`；目录、交叉引用页码需 Word/WPS 更新域。

## 只读分页风险审计

```bash
python -I -S -B "<skill-root>/scripts/validate_layout.py" 文件.docx
# 仅在 CI/实机验收门禁中按需使用：有分页警告退出 2（结构错误退出 1）
python -I -S -B "<skill-root>/scripts/validate_layout.py" 文件.docx --strict-pagination
python -I -S -B "<skill-root>/scripts/validate_layout.py" 文件.docx -o 分页诊断.json
```

结果为 JSON：`structure_valid` 仅代表当前已覆盖的 OPC 与静态布局规则，无论是否含分页警告均不因此变为 false；`issues` 数组显示 `{level:"warning", code, location, message}`。`location` 中 `body[N]` 表示文档 `w:body` 下的 **0 基序号**，不等于原始 `blocks[N]`（某些 block 可以生成多段落）。默认 CLI 退出码 0 表示结构检查成功，**不会把分页警告当成生成失败**。指定 `--strict-pagination` 后，只要出现分页风险或标题孤立风险就退出 2；结构错误仍退出 1。审计完全只读，不改变源 DOCX。

| 风险码 | 风险情形 | 推荐处理 |
|---|---|---|
| `LEADING_PAGE_BREAK` | 文档第一个 block 就是手动分页 | 检查是否确需空白首页 |
| `REPEATED_PAGE_BREAK` | 两个相邻手动分页段落 | 删除冗余分页 |
| `TRAILING_PAGE_BREAK` | 正文最后一个 block 是手动分页 | 删除文末分页以降低尾页空白风险 |
| `MULTIPLE_PAGE_BREAKS_IN_PARAGRAPH` | 同一段中多个手动分页 | 拆成结构化块并审查 |
| `INLINE_PAGE_BREAK_RISK` | 分页符与正文文字混在同一段 | 单独分页 block 或段前分页，避免重排歧义 |
| `PAGE_BREAK_SECTION_CONFLICT` | 手动分页与非连续分节相邻 | 依需求仅保留分节 |
| `REDUNDANT_HEADING_PAGE_BREAK` | 手动分页后标题已 `page_break_before` | 二选一，优先段前分页 |
| `REDUNDANT_PARAGRAPH_PAGE_BREAK` | 手动分页后普通段落已 `page_break_before` | 二选一 |
| `SECTION_HEADING_PAGE_BREAK` | 新分页节的首段又配置 `page_break_before` | 通常移除段前分页 |
| `HEADING_SEPARATED_FROM_CONTENT` | 标题后立即出现分页/分节 | 移动分页位置，使标题与下文同页 |
| `HEADING_WITHOUT_CONTENT` | 文档在标题后直接结束 | 检查是否遗漏正文 |
| `HEADING_ORPHAN_RISK` | 标题未启用有效的 `keepNext` | 给标题样式设置 `keep_with_next:true` |

检查的是**实际 OOXML 块序列**及标题 `pStyle`/`outlineLvl` 的样式继承和显式段落覆盖；不把 `w:lastRenderedPageBreak` 误判为用户设置的分页符；不把表格内部的段落当作文档顶层分页位置。输入来自 Word/WPS 的多种非标准嵌套结构未全部涵盖，风险不能等同于真实空白页。

## 兼容性约束与测试

标准 runner 与 `create_document_delivery()` 将已检出的分页警告回传到 `warnings`，使用 `layout-warning:` 前缀并保留代码、位置和说明；完整及精简回执均保留。最多回传 100 条，超出时明确说明省略数量；只在需要完整诊断时再运行独立审计。检查报告 `-o` 只允许任务目录内的新文件，拒绝原件别名、已有文件及符号链接；安全保存条件与限制见 `<skill-root>/references/typography.md#静态排版检查`。

- 不扩展输入 JSON Schema；不改变当前 `theme/profile` 或 `blocks` 语义，不改变 `.docx` 生成的 OOXML 和默认文件内容。
- `create_document(...,verify=True)` 仍将 **错误**作为发布阻断，但分页 **警告不阻断**；不更改既有交付、归档、清理流程。
- `test_docx_pagination_governance.py` 覆盖正常分页、重复/首尾分页、分节冲突、段前分页重复、标题孤立、继承样式、外部对照文件、只读行为与 CLI 严格退出码。
- 不依赖第三方库/办公室软件作为运行条件；Word/WPS 真实视觉分页、页面数量、空白页是否实际出现必须人工或外部渲染再验证。
