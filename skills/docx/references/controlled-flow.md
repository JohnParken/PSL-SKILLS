# 确定性流程与安全下一步

路径占位符说明：`<skill-root>` 是 DOCX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 DOCX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 新建失败与重试

标准新建调用create。业务失败后立即清理本轮临时文件及清单；清理完成返回status=failed、task_status=failed_cleaned、task_manifest=null、next_action=correct_request_and_start_new_task，退出1。按诊断修正后重新create，不对已删除的清单调用retry/resume。清理受阻返回cleanup_incomplete及真实诊断；保持失败退出码，不声称收尾完成。

环境失败为environment_failed/fix_environment；请求错误为request_rejected/inspect_request。仅生成规格验证阶段、且规格不含任何path资源依赖时，error_code为spec_validation_failed；资源校验错误保留对应业务分类。错误码说明原因，不覆盖本轮清理结果。不得根据错误文本猜测可重跑，不自动修改业务内容。

retry/resume仅保留为历史或强杀遗留任务的兼容接口，必须有仍存在且归属已确认的原清单。历史retry在锁、回执、输出、指纹及次数门禁后拒绝相同的无效规格；不据此为新任务保留失败临时文件。主入口与任务清理说明定义当前默认流程。

## 五类编辑统一入口

```python
execute(command="python -I -S -B \"<skill-root>/scripts/run_docx_edit.py\" text ./原件.docx ./修订版.docx <<'JSON'\n{\"replacements\":[{\"old\":\"原文\",\"new\":\"新文\",\"count\":1}]}\nJSON\n")
```

operation为text、structure、tables、image、comments；各自stdin规格与原API相同，image另传 `--replacement ./替换.png`。comments list继续原只读入口，不创建无用临时文件。入口只调用固定的五类已有编辑API，不执行输入脚本。

入口自动必要环境自检，在任务根创建前登记本轮独占平铺spec.json和manifest；保护原件、替换图片及最终文件，已有主文件或归档同名时选择递增版本名，返回实际output，不认领旧配置。任务根非阻塞锁与生成runner协作；锁内重查输出、调用编辑校验及自动归档，主文件已验收后按真实归档结果封存并清理配置JSON；归档failed/skipped也清理，不因此保留配置或清单。成功主文件经收尾前后指纹确认；全部成功后，在同一任务根锁内核对本次清单身份、内容及单链接条件并删除清单，不扫描同前缀文件。摘要返回task_manifest=null、cleanup.manifest_retained=null、cleanup.manifest_removed=true，并在deleted记录清单。清单变化、链接替换或删除失败时保留并报告cleanup_incomplete，不返回已删除标记；清理失败保留主成功并报告cleanup_incomplete，不能声称已清理。编辑拒绝同样自动清理；锁忙、文件变化或删除失败则报告cleanup_incomplete，不自动修改匹配锚点或重跑。

旧五类CLI/API保留；旧入口既有配置仍为输入，不自动删除。读取stdout不受影响。新编辑入口不提供自动retry/resume生成；失败且清理完成后从原始输入重新建立任务；只有返回非空manifest并报告清理受阻时才检查原清单及输出，不能重新登记旧文件。已封存的清单可按原cleanup_task clean幂等恢复登记项，该通用入口在完成后也删除指定清单，不扫描或追认其他历史清单；未封存且主文件可能已存在时，先明确业务验收和实际归档状态后按原seal/clean处理，不能根据文件存在就声明成功。同权限恶意写者及强杀自动恢复不作保证。

## 读取结果显式交付

```python
execute(command="python -I -S -B \"<skill-root>/scripts/read_docx.py\" ./原件.docx --format json -o ./提取.json --delivery")
```

仅--delivery改变保存后的行为：要求-o且禁止--check，拒绝既存输出及输入别名，写入成功后调用统一归档并向stdout输出read_exported摘要。提取文件仍按原--format保存；不加--delivery时保持旧文本/JSON stdout和-o行为。没有临时文件就不建立清理清单。归档失败不回滚提取文件；用户提示按用户展示规则（`<skill-root>/references/delivery.md#用户展示与机器元信息`）映射，正常主文件链接保留，不展示内部路径、ID或原始日志。

本批只支持既有格式与编辑子集；内容事实、用户意图、修改目标及验收语义仍由调用者负责。新增机器提示不是授权，不自动删除未知文件、输入或最终产物。

## 文件定位与读取规则

全文的“任务工作目录”（简称“任务目录”）均指宿主确认、执行命令及 API 时保持不变的当前目录；`./` 指该目录，不指系统下载目录、输入文件所在目录或 Skill 安装目录。各参考说明统一遵循本节，不为调用脚本或读取子目录文件切换目录。

| 文件或资源 | 路径基准与读取规则 |
|---|---|
| 文档、模板及操作 JSON 输入 | 使用用户或本轮任务明确指定的相对路径，以任务目录为基准，例如 `./原件.docx`。不猜测目录、不从归档补找同名文件。 |
| 新建主文件 | 新建 CLI 和 `create_document_delivery()` 仅接收文件名，保存到任务目录根部，例如 `报告.docx` → `./报告.docx`。输入 JSON 在子目录也不改变输出位置。 |
| 编辑主文件及读取结果文件 | 编辑交付入口的 `destination`、读取命令的 `-o` 按任务目录解析相对路径，可位于明确指定的子目录；按自检要求准备该目录。编辑不会自动输出到源文档所在目录。 |
| 生成或编辑后的读取 | 读取本次成功摘要的实际 `output`，相对任务目录解析，不拼接输入目录或归档目录；低层 API 使用调用方记录的确切目标路径。主文件缺失时报告，不静默改读归档。 |
| 归档副本 | `./output/{questionId}/` 相对任务目录，代码按主文件名归档，不保留编辑输出的子目录层级。默认不扫描或读取文档内容；用户明确授权的指定归档可作为受保护输入。必要定点检查及本轮副本验证见 归档边界（`<skill-root>/references/delivery.md#归档目录使用边界`）。 |
| 标准自动入口的图片 | `"<skill-root>/scripts/run_docx_task.py" create` 从 stdin 接收规格，正文和表格内的相对图片 `path` 均以任务目录为基准。 |
| 兼容文件输入 CLI 的图片 | 仅 `"<skill-root>/scripts/create_docx.py" 文件.json 文件名.docx` 的相对图片以输入 JSON 实际所在目录为基准；与输出目录和脚本位置无关。 |
| 字典 API 的图片 | `create_document_delivery()`、`create_document()`、`preflight_document()` 使用 `base_dir`；相对 `base_dir` 以任务目录解析，省略或为 `None` 时使用任务目录。API 不知道字典来自哪个 JSON 文件，不自动推断其目录。 |
| 替换图片输入 | `<skill-root>/scripts/replace_image.py` 的替换图片参数及对应 API 的 `replacement` 相对任务目录解析，不相对选择 JSON 或源 DOCX 所在目录。文档内 `word/media/...` 等标识是 ZIP 成员名，不是文件系统路径。 |
| Skill 内部资源 | 脚本、模板和依赖以已确认的 `<skill-root>` 为基准，可用真实绝对路径；内部代码路径引用以已确认的 `<skill-root>` 为基准。不得把任务数据写入安装目录，也不把 `<skill-root>` 用作业务输入的路径基准。 |

任务数据路径及图片解析后的实际位置都须留在任务目录内；禁止用绝对数据路径、父目录或链接绕过边界。以上是 Skill 调用规则，不表示所有底层 API 都自动检查任务目录边界；不要把低层显式路径能力当作放宽权限。目录和文件命名保护继续适用。


## 归档受阻后的恢复责任

归档失败或跳过不阻止已验收任务清理；后续如需重试归档，只使用已交付主文件，不重新生成配置或编辑文档。对以前版本遗留或清理失败的原清单，先确认本轮归属、工作进程已退出及主文件状态，再按真实结果seal/clean；最终clean也删除清单。新任务失败立即用failed清理全部登记中间项；重试重新创建任务。归属不明或权限失败属于收尾未完成，不能把恢复材料永久保留解释为成功。

公开入口的代码边界：read_docx（含未启用delivery的-o）、create_docx兼容CLI及五类编辑CLI在读取任务数据前检查输入路径；输出继续保护输入。标准runner和create_document_delivery在读取图片前按任务目录检查资源路径，兼容JSON文件入口仍以JSON父目录作为图片基准。低层create_document/preflight_document等库接口保留显式路径能力，不应当作绕过任务边界的回退。路径检查不能隔离同权限恶意并发目录修改，宿主仍负责独占任务目录。
