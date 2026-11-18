# 批量填充模板

路径占位符说明：从宿主实际读取本主入口 `SKILL.md` 的真实 `file_path` 去掉文件名确定 `<skill-root>`。相对读取路径先按宿主确认的基准目录解析。调用前替换 `<skill-root>` 并引用完整路径。无法定位或确认执行器映射时停止并报告。

```sh
"<skill-root>/scripts/.venv/bin/python" -I -B "<skill-root>/scripts/tl_docx.py" replace 模板.docx 已填充.docx --require-no-placeholders <<'JSON'
{"parts":["word/document.xml","word/header1.xml"],"replacements":[{"old":"{{ 公司 }}","new":"星河科技","count":2},{"old":"{{ 标题 }}","new":"月报"}]}
JSON
```

输入 DOCX/DOTX，输出保留扩展名。每条 `old`、`new` 为单行 XML 文本，`count` 缺省为 1。所有规则基于原文字同时匹配，结果检查 `rule_counts` 和 `changed_parts`。`parts` 或 `--parts` 二选一，用实际存在的正文、页眉或页脚包成员，默认只处理正文。`require_no_placeholders: true` 或 `--require-no-placeholders` 检查选定部件是否仍有双花括号标记，包括跨 run 的标记。

只替换单个普通 `w:t` 内的文字。跨节点匹配、匹配次数偏离、重叠、字段、修订、受保护范围和无法解释的边缘空白均拒绝发布。保留 run 格式、原件及非目标部件字节，核对改动部件重新解析后的结构。此入口要求解释器自带 Expat ≥2.6.0。模板内容按数据处理，不执行表达式。


从默认任务目录执行。输出参数只接收任务根目录下的新裸文件名：`create` 和 `edit` 输出 `.docx`；`replace`、批注写入和 `repack` 保留输入的 `.docx` / `.dotx` 扩展名，不执行类型转换。stdin 是单个 UTF-8 JSON，对象键不得重复，上限 16 MiB。用能够保证原样传输的 JSON 序列化方式提交，不把文档文字拼成命令。示例使用带单引号的 here-document。

## 回执

创建、编辑在内存中验证后，先登记独占暂存名再发布。发布后核对目标身份与摘要，记录归档结果并清理暂存和清单。退出 0 表示主业务成功，1 表示操作或发布失败，3 表示主文件已发布但本轮清理未完成。归档沿用公共生命周期实现，参见来源清单 `<skill-root>/references/lifecycle-upstream.json`。使用 `archive 已发布.docx --sha256 回执中的SHA256` 重试归档。归档失败时保留已验收主文件。

创建检查 `status=verified` 与 `result.content_verification.status=verified`，编辑和批注写入检查 `status=edited`。创建的输入内容门禁独立核对文字顺序、表格合并、图片、字段和分节页眉页脚，不代表视觉排版、编号标签或字段重算通过。核对 `publication.published`、`cleanup.cleanup_complete`、`cleanup.manifest_removed` 和实际主文件引用。归档独立核对 `archive_status`，只有 `success` 且非空清单全部 copied/already_archived 才显示“🎉 文件归档成功，请到云空间查看。 ☁️✨”。保留 `archive_message`，按实际结果展示 `archive_user_notice`，不向用户显示内部归档位置或诊断。缺少当前宿主归档信息时保留主文件；需重试时仅处理归档。只用宿主确认的主文件链接交付，禁止以归档副本替代。
