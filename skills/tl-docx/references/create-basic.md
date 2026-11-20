# 简单新建

先运行前述 `check --write`，再将以下完整命令作为 `execute` 的 command 执行。将示例文字替换为任务内容；通过安全 JSON 序列化提交，不把文档文字拼成 Shell 命令。stdin 接收一个完整 JSON，输出只传新文件裸文件名。

```sh
"<skill-root>/scripts/.venv/bin/python" -I -B "<skill-root>/scripts/tl_docx.py" create "报告.docx" <<'JSON'
{"blocks":[{"type":"title","text":"工作报告"},{"type":"heading","level":1,"text":"工作概况"},{"type":"paragraph","text":"在这里填写正文。"},{"type":"table","header":true,"rows":[["项目","结果"],["示例项目","已完成"]]}]}
JSON
```

`blocks` 按顺序组成文档；普通段落使用 `paragraph`，小节标题使用 `heading` 和 1–9 的 `level`，规则表格使用等长字符串行及 `header`。不需要表格时删除该块。默认公文样式和自然分页，不自动添加分页块；其他格式需求按路由读取一份相关参考。用户给出名称时优先使用，未给名称时可省略输出名由标题拟定；同名自动递增，明确要求精确同名才用 `--exact-name`，不能覆盖原件。完成后按下方回执核验交付，不再重复生成或归档。

创建检查 `status=verified` 与 `result.content_verification.status=verified`，编辑和批注写入检查 `status=edited`。创建的输入内容门禁独立核对文字顺序、表格合并、图片、字段和分节页眉页脚，不代表视觉排版、编号标签或字段重算通过。核对 `publication.published`、`cleanup.cleanup_complete`、`cleanup.manifest_removed` 和实际主文件引用。归档独立核对 `archive_status`，只有 `success` 且非空清单全部 copied/already_archived 才显示“🎉 文件归档成功，请到云空间查看。 ☁️✨”。保留 `archive_message`，按实际结果展示 `archive_user_notice`，不向用户显示内部归档位置或诊断。缺少当前宿主归档信息时保留主文件；需重试时仅处理归档。只用宿主确认的主文件链接交付，禁止以归档副本替代。
