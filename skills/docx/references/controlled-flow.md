# 确定性流程与安全下一步

## 新建失败与重试

原create/retry/resume入口及退出码保留。失败摘要增加error_code、next_action；环境失败为environment_failed/fix_environment，无任务写入。请求错误为request_rejected/inspect_request；未知或无法确认状态为task_state_unconfirmed/inspect_task，不根据错误文本猜测可重跑。worker JSON错误在控制器解析，不再当作嵌套JSON字符串供模型解读。

仅生成规格验证阶段、且规格不含任何path资源依赖时，失败标记spec_validation_failed/correct_spec，并记录规范化JSON的SHA；retry在原有锁、回执、输出、指纹和次数门禁后比较相同规格，返回unchanged_invalid_spec，不消耗attempts、不更新配置。相同内容不同键序也视为相同。外部资源校验失败为resource_validation_failed/inspect_inputs，不按相同规格阻止必要的输入修复；其他未知故障保持人工核查。此机制不自动修改业务内容，也不保证所有可修正错误均能分类。

## 五类编辑统一入口

```python
execute(command="python -I -S -B \"<skill_dir>/scripts/run_docx_edit.py\" text ./原件.docx ./修订版.docx <<'JSON'\n{\"replacements\":[{\"old\":\"原文\",\"new\":\"新文\",\"count\":1}]}\nJSON\n")
```

operation为text、structure、tables、image、comments；各自stdin规格与原API相同，image另传 `--replacement ./替换.png`。comments list继续原只读入口，不创建无用临时文件。入口只调用固定的五类已有编辑API，不执行输入脚本。

入口自动必要环境自检，在任务根创建前登记本轮独占平铺spec.json和manifest；保护原件、替换图片及最终文件，拒绝已有最终名称，不认领旧配置。任务根非阻塞锁与生成runner协作；锁内重查输出、调用编辑校验及自动归档，随后封存并清理自己的JSON。成功主文件经收尾前后指纹确认；清理失败保留主成功并报告cleanup_incomplete，不能声称已清理。编辑拒绝、锁忙或状态不明返回结构化失败并保留清单/配置，不自动修改匹配锚点或重跑。

旧五类CLI/API保留；旧入口既有配置仍为输入，不自动删除。读取stdout不受影响。新编辑入口不提供自动retry/resume生成；失败后先检查原manifest及输出，不能重新登记旧文件。已封存的清单可按原cleanup_task clean幂等恢复；未封存且主文件可能已存在时，先明确业务验收和实际归档状态后按原seal/clean处理，不能根据文件存在就声明成功。同权限恶意写者及强杀自动恢复不作保证。

## 读取结果显式交付

```python
execute(command="python -I -S -B \"<skill_dir>/scripts/read_docx.py\" ./原件.docx --format json -o ./提取.json --delivery")
```

仅--delivery改变保存后的行为：要求-o且禁止--check，拒绝既存输出及输入别名，写入成功后调用统一归档并向stdout输出read_exported摘要。提取文件仍按原--format保存；不加--delivery时保持旧文本/JSON stdout和-o行为。没有临时文件就不建立清理清单。归档失败不回滚提取文件；用户成功仅提示“归档完成”，正常主文件链接保留，不展示内部路径、ID或原始日志。

本批只支持既有格式与编辑子集；内容事实、用户意图、修改目标及验收语义仍由调用者负责。新增机器提示不是授权，不自动删除未知文件、输入或最终产物。

## 文件定位与读取规则

全文的“任务工作目录”（简称“任务目录”）均指宿主确认、执行命令及 API 时保持不变的当前目录；`./` 指该目录，不指系统下载目录、输入文件所在目录或 Skill 安装目录。各参考说明统一遵循本节，不为调用脚本或读取子目录文件切换目录。

| 文件或资源 | 路径基准与读取规则 |
|---|---|
| 文档、模板及操作 JSON 输入 | 使用用户或本轮任务明确指定的相对路径，以任务目录为基准，例如 `./原件.docx`。不猜测目录、不从归档补找同名文件。 |
| 新建主文件 | 新建 CLI 和 `create_document_delivery()` 仅接收文件名，保存到任务目录根部，例如 `报告.docx` → `./报告.docx`。输入 JSON 在子目录也不改变输出位置。 |
| 编辑主文件及读取结果文件 | 编辑交付入口的 `destination`、读取命令的 `-o` 按任务目录解析相对路径，可位于明确指定的子目录；按自检要求准备该目录。编辑不会自动输出到源文档所在目录。 |
| 生成或编辑后的读取 | 读取本次成功摘要的实际 `output`，相对任务目录解析，不拼接输入目录或归档目录；低层 API 使用调用方记录的确切目标路径。主文件缺失时报告，不静默改读归档。 |
| 归档副本 | `./output/{questionId}/` 相对任务目录，代码按主文件名归档，不保留编辑输出的子目录层级。默认不扫描或读取文档内容；用户明确授权的指定归档可作为受保护输入。必要定点检查及本轮副本验证见 [归档边界](delivery.md#归档目录使用边界)。 |
| 标准自动入口的图片 | `run_docx_task.py create` 从 stdin 接收规格，正文和表格内的相对图片 `path` 均以任务目录为基准。 |
| 兼容文件输入 CLI 的图片 | 仅 `create_docx.py 文件.json 文件名.docx` 的相对图片以输入 JSON 实际所在目录为基准；与输出目录和脚本位置无关。 |
| 字典 API 的图片 | `create_document_delivery()`、`create_document()`、`preflight_document()` 使用 `base_dir`；相对 `base_dir` 以任务目录解析，省略或为 `None` 时使用任务目录。API 不知道字典来自哪个 JSON 文件，不自动推断其目录。 |
| 替换图片输入 | `replace_image.py` 的替换图片参数及对应 API 的 `replacement` 相对任务目录解析，不相对选择 JSON 或源 DOCX 所在目录。文档内 `word/media/...` 等标识是 ZIP 成员名，不是文件系统路径。 |
| Skill 内部资源 | 脚本、模板和依赖以已确认的 `<skill_dir>` 为基准，可用真实绝对路径；Markdown 引用相对所在 Markdown 文件解析。不得把任务数据写入安装目录，也不把 `<skill_dir>` 用作业务输入的路径基准。 |

任务数据路径及图片解析后的实际位置都须留在任务目录内；禁止用绝对数据路径、父目录或链接绕过边界。以上是 Skill 调用规则，不表示所有底层 API 都自动检查任务目录边界；不要把低层显式路径能力当作放宽权限。目录和文件命名保护继续适用。

