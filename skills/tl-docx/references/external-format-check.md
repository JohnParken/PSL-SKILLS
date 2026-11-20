# 外部候选的单操作格式核验

此入口只读原件与外部候选，不启动外部软件，不生成、覆盖、归档或交付候选。先按主入口运行读取自检，无需写入探针。仅支持 DOCX；签名包、越界路径、链接及不安全包按既有门禁拒绝。

```sh
"<skill-root>/scripts/.venv/bin/python" -I -B "<skill-root>/scripts/tl_docx.py" verify-format 原件.docx 外部候选.docx <<'JSON'
{"operations":[{"type":"format_run","paragraph_index":0,"expected_text":"待核对文字","run_index":0,"expected_run_text":"待核对文字","format":{"bold":true}}]}
JSON
```

JSON沿用编辑协议，但仅允许一个 `format_run`、`format_cell_run` 或 `format_story_run`，不得传文字替换或其他操作。表格、页眉页脚的定位和安全范围见 `<skill-root>/references/writing.md`。普通正文也限目标段落为普通文本，不接受字段、修订或复杂锚点。所有索引与段/run预期文字必须匹配，格式值按公开属性类型和范围独立检查，不调用格式setter构造“预期”。

两输入必须是任务内不同的普通文件。通过安全打开描述符获取设备号/文件号，不以先stat后open代替身份核对；同物理文件（包括硬链接）返回 `format_check_inputs_not_distinct`。不同文件但内容字节完全相同返回 `format_check_no_change`，表示没有核验外部格式改变，不表示文档损坏。即使所需格式原已满足，也不把相同候选称为一次新变更。

候选须保持相同包成员集合与主部件。除明确目标部件外，其余部件的解压字节逐项相等；目标部件独立核对只允许该run请求的属性变化及必要属性容器。正文文字、非目标run、节点/属性/顺序和未请求格式均受保护。外部工具顺手重写样式、文档属性或关系会被拒绝，这是保守边界，不是任意外部Word另存的兼容承诺。

成功退出0，返回 `status=format_checked`、`verification=passed`、两份输入SHA256、`read_only=true`、`scope=captured_input_bytes`。只证明本次安全读取的字节快照；文件随后被其他进程替换或修改不在保证内，使用候选前仍须核对回执摘要。失败退出非零并返回稳定错误码，内容/请求不匹配通常为 `format_check_failed`；路径/权限和资源错误保留相应类型。没有输出文件或归档成功标记。

此检查不是完整OOXML schema验证、外部工具背书、视觉验收或任意无损证明。明确未检查：视觉布局、字段重算、编号显示、修改部件的XML词法表示及ZIP元数据。不得把“格式核验通过”说成Office/WPS显示已经验证。
