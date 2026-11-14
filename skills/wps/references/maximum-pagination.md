# maximum 完整结果无损分片

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 何时使用

默认 maximum 不加输出参数仍返回原完整 JSON，保留16 MiB单次序列化上限。结果太长或需要分次回传时，使用 maximum 的 `--page-size` / `--cursor` / 输出预算；它覆盖完整结果的正文、表格、附加区域、修订视图、来源和全部诊断，不降级成仅主文投影。

这是规范化 JSON 的字符分片，不是文档物理页、段落页或流式文档解析。每次续读仍完整解析同一份输入；没有缓存、没有新建状态文件，不降低解析内存上限。显式 standard 的旧主文分页保持不变，其游标与本接口互不兼容。

## 消费方迁移

按 `schema_version` 选择解码器，不假设全部分页都有 `text` 字段。`wps_maximum_page_v1` 必须读取 `payload_fragment` 并完整校验重组；旧主文 `wps_read_view_v1` 的消费者必须显式传 `--reading-mode standard`。禁止为了兼容旧消费者把最大结果降级成主文字符串，也不要混用两种游标。CLI/API 未指定输出参数时仍返回完整业务JSON，而不是分页外壳。

## CLI

先按主入口完成定位与读取自检；始终留在任务目录，输入只使用原本获准的文件：

```python
execute(command="python -I -S -B \"<skill-root>/scripts/read_wps.py\" ./input.wps --page-size 8000 --max-output-chars 12000 --max-output-bytes 12000")
```

读取返回的 `next_cursor`，下一次原样传入 `--cursor`，保持输入、读取选项与 page-size 相同。可以调整字符/字节预算，但不能手改游标或把其他文件的游标复用。游标很长，不逐字重写；按宿主安全参数传递能力原样引用。例中省略 reading-mode 即默认 maximum。

- `page_size` 默认8000，范围1..262144，指规范JSON的Unicode字符数，不是正文长度。
- `max-output-chars` 默认12000，范围512..2097152。
- `max-output-bytes` 默认12000，范围512..16777216，包含完整外层JSON与最终换行的真实UTF-8字节。
- 上限同时生效，实际片段可能小于page-size。预算小到放不下外壳或一个字符时明确失败，不返回空转游标。
- 单条超长正文、表格单元格、修订或诊断可以跨片，不因它大于单页预算而丢弃。
- 全部规范JSON另受256 MiB传输上限保护；输入、解压、XML、文本与其他原资源检查照常执行。此限额不是承诺可以解析任意大文件。

## 读取和完整性判断

每次返回一个合法JSON外壳，`schema_version=wps_maximum_page_v1`。其中 `payload_fragment` 是完整结果JSON的一段字符串，可能截在字段或字符串内部，不能把单个片段当完整业务JSON解析，也不能靠首尾括号猜测边界。

1. 从start=0开始，按收到顺序收集payload_fragment；后页start必须等于前页end，不能跳过、重复或排序后假装连续。
2. 核对各页source_sha256/result_sha256/options_sha256和total长度一致；每页chunk_sha256及累计prefix_sha256对应其真实UTF-8内容。
3. has_more=true时必须继续；has_more=false且next_cursor=null只表示流结尾，不证明此前页已收到。page_is_last与single_page_complete分别表示最后片、整份仅这一片。
4. 所有片段连接后，核对total_chars、total_utf8_bytes和result_sha256，再作为JSON解析；缺页或校验失败不能回答“已完整读取”。程序消费者优先调用下述assemble_pages，不手写不完整的校验。
5. 顶层status/source_status保留源读取状态；partial可能同时存在完整传输。全部原始诊断在payload流中，未读到尾前不能把外壳当全部诊断。原件格式partial不会因成功分片升级成ok。

宿主截断与脚本分页是两回事：外壳本身被截断时没有可信游标，按主入口的大结果读取契约处理，不从残缺输出猜next_cursor。没有得到连续片段和最终校验前，不将已见部分概括为全文件内容。

## Python API

运行模块为 `<skill-root>/scripts/wps_maximum_pages.py`；按已确认根定位，不猜相邻Skill。

- `read_page(data, page_size=8000, cursor=None, max_output_chars=12000, max_output_bytes=12000, **parse_options)`：data为不可变bytes，默认maximum；明确standard会拒绝。
- `project(result, data, options, page_size=8000, cursor=None, max_chars=12000, max_bytes=12000)`：把已经解析的完整结果作无损传输投影。
- `assemble_pages(pages)`：校验按顺序提供的页面，拒绝缺页、重复、乱序、混源、内容/摘要不符，返回重组后的完整JSON对象。重组最多65536页，超过时明确失败并需以更大片重新读取；需要保存所有页面或完整结果的内存，不是流式文档分析器。

旧read()/read_bytes()仍默认返回完整结果，不因新增分页自动改变其返回schema。新API不会执行文档指令、联网或写文件。

## 游标边界

游标绑定输入字节摘要、实际解析选项、page-size、规范结果摘要、前缀摘要及投影版本；来源、选项、结果或版本变化则拒绝续读，重新从头开始。字符/字节输出预算可调整。选项字典的键顺序不影响指纹。

SHA-256校验用于发现误用、损坏和不一致，不是带密钥的认证或签名。只读无状态实现没有宿主秘密，不能防止恶意方同时重算数据与摘要；不得宣称游标不可伪造。跨信任域需要宿主提供认证和可信来源，不能靠本接口代替权限控制。
