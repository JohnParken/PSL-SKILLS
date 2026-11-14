# WPS 默认最大化读取与修订隔离验收

版本：`20261114.1747.4416`。本轮基于此前同日关联内容恢复修改继续维护，未提交或推送，未更新历史 ZIP。

## 默认与兼容迁移

- read_wps.py CLI、公开 read()/read_bytes() 默认 maximum；显式 reading_mode=standard 始终传到核心，不能因省略默认选项而失效。
- 标准模式仍可用于旧主文、摘要、段落/章节、分页、纯文本及特定过滤策略。maximum 不接受这些可能遗漏附加区域的投影，不暗中降级。
- 创建、编辑、导出、转换及发布验证内部10处API和1处子进程固定 standard，避免读范围扩大和修订改变影响产物。未扩大写入授权或重排范围。
- 既有标准模式测试迁移为显式 standard，原断言保留；默认行为由单独的 test_wps_default_maximum.py 验证。旧外部调用者若依赖原模式须显式迁移，这不是无行为变化的默认更新。
- 底层具体格式解析器保持原参数契约，不作为另一个默认maximum公开入口。

## 修订与可靠内容

OOXML/RTF 默认 text 为受支持 accepted 当前正文；第二次有界解析得到 all 存储视图，不同内容单独置于 revision_content.content，保留表格、块顺序与关联区域，不把删除文字拼进当前正文。这是两种视图，不是逐项变更清单，也不声明全修订语义。显式 revisions=all 的结果始终标为非当前视图且 partial。

MS-DOC 未实现可靠当前修订过滤，保留存储文字、current_view_available=false、content_view=stored_text_not_verified_current、partial。RTF 已分类的样式/修订筛选不确定性也可返回明确标识的存储文字；显式要求accepted但无法实现时不伪造。第二视图失败保留已读正文，revision_content报告不可用及诊断。资源、加密、实体、路径和共享结构门禁不放宽。

最大模式仍使用完整JSON的16 MiB序列化上限，超限明确失败，不输出截断JSON，也不自动丢弃关联区域。此次未实现跨正文/表格/附加区域的统一分页；两次解析不是流式读取，不宣称降低解析内存。用户仅需主文切片时可明确选择standard，不能据此回答全文覆盖问题。

## 实际验收

环境：macOS 27.2.1 arm64、Python 3.14.10、Expat 2.7.4。Pylint用既有维护隔离环境Python3.12，不是运行依赖。

- WPS全套694项通过，无跳过；新增默认模式专项10项，包含CLI/API默认、显式standard、删除/插入分离、显式all警告、二进制当前视图未知、RTF样式未知恢复、关联损坏恢复、修订补读失败及输出限制。
- WPS全部scripts（含测试）Pylint --errors-only退出0；未声明所有Warning/Refactor清零。
- 独立只读运行包6项通过：原4项创建/编辑/图表/图片/合并验收，加默认修订视图和默认损坏页眉恢复2项临时子类验收。
- 通用打包安全11项通过。新增wps_maximum_view.py列入显式manifest，测试不随包。
- Skill严格检查、项目文档检查和git diff --check通过；Python文件不超1000行。

全部为合成预期和本机独立包验收，不代表真实WPS生产者、全部格式、视觉排版、Linux/Windows或宿主模型输出接收验证。当前视图不等于授权/隐私过滤；隐藏文字仍保留，不能当脱敏工具。没有修改其他Skill既有工作。
