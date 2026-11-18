# 结构化续读与稳定错误

路径占位符说明：从宿主实际读取本主入口 `SKILL.md` 的真实 `file_path` 去掉文件名确定 `<skill-root>`。相对读取路径先按宿主确认的基准目录解析。调用前替换 `<skill-root>` 并引用完整路径。无法定位或确认执行器映射时停止并报告。

`has_more=true` 时提供 `continuation={action:"read",source:实际任务相对源文件名,parameters:{...}}`。`parameters` 包含有效 `max_output_bytes`、`cursor` 和全部当前视图选项；显式缓存只提供本批当前 `cache_token`，不再次传 `cache`。未列选项沿用CLI默认，末页无continuation；提示本身计入字节预算，不用它绕过partial、分片或缓存身份检查。输出不含内部缓存/归档/安装路径，不带output或delivery，不自动覆盖先前导出。

把请求作为参数数据重放，禁止拼接成未转义Shell。键名下划线转为CLI连字符；true布尔键对应开关，`include_related=false` 特别映射为 `--body-only`；数组逐项重复 `--flag=value`，其他非布尔值使用 `--flag=value`（保护前导短横值）。最后加 `--` 后再传source，保护空格、引号和前导短横文件名。保留所有返回参数，不凭记忆补默认视图。逐批核对 `start` 等于前批 `end`、`total` 一致及源/选项未变化；`has_more=false` 才表示所选视图的记录收齐，仍须保留 `coverage`、`document_coverage` 和 partial 缺口。游标不是 offset，禁止自行解码修改或跳过失败批次。若宿主截断 JSON，先报告未收齐，不能从不完整字段猜下一页。

分片内容现在按规范化JSON键序编码，摘要针对实际拼合的完整UTF-8字节，列表与业务内容不变；这修复了初次解析与排序缓存重读之间分片边界不一致的缺陷。分片游标新增continuation绑定版本及源文件名，旧分片游标会返回 `cursor_mismatch`，须丢弃旧片段并不带cursor从首批重新读；预算变化也须重启。普通非分片游标绑定保持，API仍为1。

失败新增 `error_code`、`retryable`、`next_action`，保留status及业务/发布/归档/清理事实，不解析英文错误来决定动作。partial不加错误分类。参数错误为完整JSON stdout及stderr提示、退出2；其他原退出码不变。常规异常诊断保留，超长异常封装限512字节并标记 `error_truncated`，非法UTF-8文字替换标记 `error_encoding_replaced`。文件系统异常不回显路径。

| error_code | 后续处理 |
| --- | --- |
| `invalid_arguments` | `check_help`，核对CLI参数。 |
| `dependency_unavailable`、`input_unavailable`、`access_denied` | 分别 `repair_environment`、`verify_task_input`、`verify_access`。不自动安装或更改权限。 |
| `invalid_package`、`invalid_input` | 分别核对输入文件、按具体诊断检查输入。 |
| `invalid_cursor`、`cursor_out_of_range` | `restart_read`。 |
| `cursor_mismatch` | `restore_original_read_options`；旧分片游标按上方迁移重读。 |
| `output_budget_exceeded` | 增大预算或从首批启用分片；分片预算错误必须从首批重启。 |
| `io_error` | 只在read的EAGAIN/EINTR/EBUSY时提示retry_read；其他情形先inspect_operation_state。 |
| `operation_failed` | 先inspect_operation_state，不推断重新生成安全。 |

`retryable=true` 只表示按提示修正条件后可重试，不允许自动重跑任何写入或忽略已发布文件。stdout短写/flush失败后不追加JSON污染已有片段，只向stderr发结构化 `output_delivery_failed` / `inspect_received_bytes_before_retry`；缓存清理异常用 `cache_cleanup_incomplete` / `retain_unverified_files`。两者均不可自动重试，未知文件保留。
