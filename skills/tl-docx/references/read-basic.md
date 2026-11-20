# 普通读取

```console
"<skill-root>/scripts/.venv/bin/python" -I -B "<skill-root>/scripts/tl_docx.py" read "文档.docx" --max-output-bytes 12000
```

默认返回内容优先 JSON，聚焦正文、表格文字和图表保存值，隐藏纯结构定义、文档属性、嵌套来源定位、字段指令及重复表示。保留编号、表格合并关系与表头确定性，图表包括保存的标题、轴标题、自定义标签和系列数值；不将保存值冒充实时值或完整渲染标签。看 `status`、`issues`、`coverage`、`has_more`：partial 时先提供可靠内容并明确遗漏，不把未知形状、图片未做 OCR、图表缓存缺失当作已完整读取。诊断默认按代码汇总，`--details` 可查看完整定位和诊断；原完整记录用 `--full`，不作为普通阅读前置步骤。显式选择 `--summary`、`--report` 或其他输出格式会切换专项视图，不同时传 `--content` / `--full`。

普通任务从首批显式使用 12,000 字节预算（含 JSON 和末尾换行），不是先取全文再判断大小；底层无预算调用仍保留原兼容行为。`has_more=true` 时按 `<skill-root>/references/continuation.md` 将 `continuation.action`、`source` 和全部 `parameters` 安全映射到 CLI；它们是数据而非 Shell 命令。不要只复制游标而漏掉视图、范围或缓存参数。逐批核对 `start` 接前批 `end`、源及选项不变，直到 `has_more=false`；这仅证明所选视图收齐，仍须检查 coverage/partial。续读提示计入预算，末页不再返回 `continuation`。失败看 `error_code`、`retryable` 和 `next_action`；允许按提示修正后重试不等于允许自动重复写入。不能拼接被宿主截断的 JSON 或以首批代替全文。单记录超限、导出、其他格式和专项报告按读取参考处理。该视图调整不等于流式解析，也不承诺固定 token 降幅。

对用户回答文档内容与具体缺口，不堆砌部件路径、XML 或格式内部信息；需要来源追溯时再取详细视图。局部非关键部件损坏可以保留其他可读内容，DTD、危险关系、加密及资源安全失败仍阻断。
