# 结果核验与交付

创建检查 `status=verified` 与 `result.content_verification.status=verified`，编辑和批注写入检查 `status=edited`。创建的输入内容门禁独立核对文字顺序、表格合并、图片、字段和分节页眉页脚，不代表视觉排版、编号标签或字段重算通过。核对 `publication.published`、`cleanup.cleanup_complete`、`cleanup.manifest_removed` 和实际主文件引用。归档独立核对 `archive_status`，只有 `success` 且非空清单全部 copied/already_archived 才显示“🎉 文件归档成功，请到云空间查看。 ☁️✨”。保留 `archive_message`，按实际结果展示 `archive_user_notice`，不向用户显示内部归档位置或诊断。缺少当前宿主归档信息时保留主文件；需重试时仅处理归档。只用宿主确认的主文件链接交付，禁止以归档副本替代。
