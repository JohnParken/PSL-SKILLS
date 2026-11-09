# 版本与兼容性

维护资源说明：文中 `docs/maintenance/ofd/` 与 `skills/ofd/scripts/test/` 均以宿主确认的仓库根目录为基准，仅供源码维护，不随运行包分发；运行任务不读取或执行。没有仓库时跳过维护资料，不按 Skill 安装目录拼接。

本参考覆盖读取器的版本策略；独立生成器目前只输出OFD 1.0固定版式子集，见 `<skill-root>/references/generation.md`。

路径占位符说明：`<skill-root>` 是 OFD Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 OFD 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 版本策略

| 声明 | 默认 known | 显式 compatible |
|---|---|---|
| 1.0、1.1、1.2 / OFD | 相同的 XML 存储结构与文字模型，可能 complete 或 partial | 相同 |
| 其他 ASCII 1.x（小版本最多三位） | unsupported/4 | 试读公共结构，始终 partial |
| OFD-A 且上述版本可接受 | 公共结构读取，partial；不认证长期保存规范 | 相同 |
| 2.x、畸形版本、其他命名空间或未知 DocType | unsupported/4 | 仍拒绝 |
| Version 或 DocType 缺失 | 保留原默认值但明确 partial 诊断 | 相同 |

在宿主确认的任务目录执行：

```console
python -I -S -B "<skill-root>/scripts/read_ofd.py" ./input.ofd
python -I -S -B "<skill-root>/scripts/read_ofd.py" ./input.ofd --version-policy compatible
```

compatible 是用户理解缺口后显式选择的未知小版本存储结构试读，不是完整支持未知版本。不在失败后自动切换策略、伪造版本属性或尝试替换 XML 命名空间。更高版本的安全、结构或资源校验失败仍拒绝，不输出积累中的正文。

## 扩大的解析范围

- PageRes 与公共/文档资源使用相同的安全引用入口；资源 ID 在文档公共作用域及当前页/模板作用域中查找，页级声明不泄漏到其他页。冲突定义失败，同一资源文件重复引用不重复声明。
- PublicRes、DocumentRes、PageRes 索引成员确实缺失时保留其他可读 XML / TextCode，记录 resource_gaps 与 missing_resource_index，结构和全局结果为 partial；文字可能缺少复合资源内容。仍累计引用预算，不扫描同名路径、不合并未知资源。目标为目录、路径越界、现存 XML 损坏、DTD/实体、角色冲突或载荷缺失仍失败；此机制不适用于文档/页面/模板/签名/附件索引。
- Font、MultiMedia、CompositeGraphicUnit、DrawParam、ColorSpace 元数据及字体/媒体包内引用；不解码字体或图像，不应用绘制参数。
- CompositeObject 通过 ResourceID 展开 CompositeGraphicUnit/Content 内的 TextObject；嵌套最多16层，循环失败，共享资源每个实际出现各提取一次，不将重复出现误当作重复数据删除。节点和输出预算继续累计。
- text_runs 中的 occurrence 保存外到内的复合对象引用链，ancestry 保存当前源 XML 中的原始祖先属性。复合资源中的 source 指向资源 XML。原始坐标不做缩放、CTM、裁剪或可见性计算；复合几何缺口明确 partial。
- DocBody/DocInfo 分离列入 documents[].info，不混入页面正文；嵌套 PageBlock 的原始祖先属性同样保留。
- schema ofd-read-3 增加全量已读 XML 结构、存储文字和覆盖维度；具体边界见 `<skill-root>/references/structure.md`。

## 依据与证据界限

正式基准仍是 GB/T 33190-2016；本版未取得可逐条认证的独立 1.1/1.2 全文规范。下列上游作者源码仅作为生产实现证据，不替代国家标准，也不引入 Java 或该项目作为依赖：

- OFDRW 主入口 OFD.java 明确使用 Version 1.2，并识别 OFD-A：`https://github.com/ofdrw/ofdrw/blob/c633a61fb37d300392a51f67b101f46738866c83/ofdrw-core/src/main/java/org/ofdrw/core/basicStructure/ofd/OFD.java`。
- Page.java 的 PageRes 列表与模板约束：`https://github.com/ofdrw/ofdrw/blob/c633a61fb37d300392a51f67b101f46738866c83/ofdrw-core/src/main/java/org/ofdrw/core/basicStructure/pageObj/Page.java`。
- CT_VectorG.java 的 Content 与 CompositeObject.java 的 ResourceID：同一提交的 `ofdrw-core/src/main/java/org/ofdrw/core/compositeObj/CT_VectorG.java` 和 `ofdrw-core/src/main/java/org/ofdrw/core/basicStructure/pageObj/layer/block/CompositeObject.java`。

固定提交的原始 1.1/1.2/OFD-A 1.2 测试原件可读取；结构、命名空间及文字维度分别记录。原始1.1样例自定义 XML 的异常命名空间名称以 raw_names 保留，不能据此称该 XML 合规。新版未取得独立的全功能规范/所有真实文件覆盖证据，不将存储模型完整冒充全格式语义认证。复合对象新增能力目前只有合成验证。真实样本清单与结果见 `docs/maintenance/ofd/real-samples.json`，详细方法与平台缺口见 `docs/maintenance/ofd/validation.md`。
