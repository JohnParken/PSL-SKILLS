# 交付输出：只传文件名

本轮输出契约源码已分四批发布至 [`9fcf9fc`](https://github.com/JohnParken/PSL-SKILLS/commit/9fcf9fca339dd0f6491f7e64e4777f9dc57e61a7)，原开发基线为 `6e317cf`。所有业务交付输出由脚本固定写入宿主确认的任务工作目录根部，模型只指定文件名。不得为适配旧参数切换到输入目录或 Skill 安装目录。

## 接口与迁移

- 接受：`报告.docx`、`报告.xlsx`、`工作 汇报.wps` 等符合目标格式的裸文件名。
- 拒绝：`./报告.xlsx`、`sub/报告.xlsx`、`/tmp/报告.xlsx`、`../报告.xlsx`、`sub/../报告.xlsx`、Windows 盘符/反斜杠/UNC 路径。非法参数失败，不用 basename 或 resolve 抹去路径信息。
- CLI、stdin JSON、声明 output 与实际命令参数分别校验原始字符串；先校验再转 Path。Python 调用方已经构造的 `Path('./a.xlsx')` 会在进入 API 前丢失 `./`，接口不能恢复该拼写，此时其语义是裸文件名 `a.xlsx`，仍只能写任务根目录。
- 输入、图片、配置读取位置及 Skill 内部脚本定位保持原契约。内部归档 `output/{questionId}`、受控私有候选和明确解包检查/编辑工作区不是交付输出目录，不向用户展示其内部路径或临时文件。
- 多产物分别传不同裸文件名；主文件与审计文件不构成跨文件原子事务，按各自真实状态报告。公开交付清单只接受根目录主文件，不再接收归档子目录路径作为主文件。

## 全部 Skill 盘点

| Skill | 本轮处理 |
| --- | --- |
| docx | 新建、编辑、报告保存、批注、run 合并、pack/rezip、runner 与显式归档均校验裸文件名；解包和内部 XML 操作为工作区能力 |
| xlsx | Workbook.save、CSV/TSV与分析导出、标准CLI、runner、publish/delivery及legacy报告出口统一校验；禁用legacy编辑工具未重新启用 |
| wps | 创建、受控编辑、RTF/DOCX兼容与可选转换导出、builder.save、job均校验；省略导出名时取输入主名，固定写任务根目录 |
| pptx | create CLI/API校验原始输出字符串；读取、验证与组包内存输出不新增文件出口 |
| ofd | generate/publish校验原串；读取/检查仅stdout；维护分发ZIP工具不属于业务交付入口 |
| table-workflow | apply的XLSX与audit JSON均限制裸文件名；调用前确认XLSX输出能力契约 |
| artifact-lifecycle | 最终文件清单、选择交付名及归档入口校验裸文件名；内部归档和原清单恢复仍沿用安全机制 |
| et | 只读stdout，无新增写文件API；另存或跨Skill交接只给根目录裸文件名 |
| bank-writing | 纯文本，无文件写入脚本；授权DOCX交接只指定文件名 |
| skill-development-standards | 检查器stdout；统一业务输出规则说明，不改维护目标目录或检查器路径参数 |

## 跨 Skill 兼容

XLSX capabilities 改为 `interface_version=2`，声明 `delivery_filename_only=true`。table-workflow 要求该版本与标志，旧 XLSX v1 或缺失标志在业务执行前明确拒绝。ET只读能力仍是v1；类型化传输协议 `psl_capabilities_v1` 不变。升级时成套更新XLSX与workflow，旧带路径输出的调用改为已确认任务cwd内裸文件名；不保留允许目录的回退通道。

## 同名与安全边界

本轮不将所有旧接口强改成相同覆盖策略。DOCX/XLSX标准runner保持递增命名；XLSX、PPTX、OFD、workflow和WPS正常CLI独占新建。WPS builder.save改为独占验证后发布。DOCX部分旧低层/直接CLI仍原子替换非输入的普通同名文件，WPS部分旧直接API也保留已有替换语义；不能把“仅裸文件名”宣传成“所有API均自动版本化或拒绝任何同名”。优先使用标准runner并遵守每个入口的授权与覆盖契约。

符号链接、硬链接输入别名、原件及图片输入保护继续执行，内部安全文件操作未降级。路径校验不是隔离同权限恶意进程的操作系统沙箱；宿主仍须分配可信、独占的任务目录。

归档、业务和清理状态分别核对。完整归档成功继续提示“🎉 文件归档成功，请到云空间查看。 ☁️✨”；失败、部分、跳过和未知不冒报成功，不声称验证了云端上传或展示时效。

## 验证

本轮最终源码回归、独立严格检查、实际平台与未验证范围见 [本轮验证记录](output-filenames-validation.json)。历史日期化记录、旧发布ZIP及原有未知文件保留，不因本轮维护重写其历史结论。模型选路期望只人工复核所有权不变；加载快照是字符/字节与哈希，不是真实token或宿主行为测量。

验证 JSON 的 publication 字段保留采样时尚未提交/推送的状态，不是当前发布状态。提交前全量复跑及发布结果见 [仓库发布记录](../README.md#裸文件名输出分批发布2026-11-07)；后续仅补正文档示例，不改变该记录对应的业务源码。
