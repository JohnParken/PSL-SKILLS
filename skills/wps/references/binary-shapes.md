# 二进制 MS-DOC 的绘图对象 U+0008 锚点（只读）

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 能力范围

实际文件内容识别为 CFB + MS-DOC 时，**默认严格读取不变**：不将未知控制字符当普通正文。仅明确选择 `--object-policy placeholder` 时，按规范读取主正文形状表 `FibRgFcLcb97.fcPlcSpaMom/lcbPlcSpaMom`，验证在主故事中的 `U+0008` 锚点与 `PlcfSpa` 记录来源，再以 `U+FFFC` 表示无法渲染的对象。返回 `partial` 并保留源流偏移、原始 CP、形状索引与 SPA 原始所在偏移。不输出画面、内容、图形类型或坐标值。

`PlcfSpa` 布局为 `(n+1)*4+n*26` 字节；n 是主正文形状数。各真实锚点 CP 必须递增、落在主正文范围内且在 Piece Table 对应的存储字符为 `U+0008`。**最后一个 CP 是不应解释的终止值**，只要求大于其前一个 CP，不将它误判为正文锚点，也不要求终止值小于主正文长度。读取必须检查 Table Stream 区间、与已解释 CLX/其他区域不重叠、形状数最多 1000。无锚点或非 `U+0008` 的存储位置不得强制占位；即使存在合法 `PlcfSpa`，其他未支持控制字符仍按原安全策略拒绝。

## 不支持和诊断

- `drawn_shape_anchor_not_rendered`：仅表示已验证主正文的 `PlcfSpa` CP 指向存储的 `U+0008`，及占位已经输出；不能据此声称绘图资源可读取或页面显示等价。
- 本阶段**尚未建立有效的 `sprmCFSpec` / 字符样式级联验证**，未解读 26 字节的 SPA 字段语义、图形数据或流中的实际绘图文件。不借用有效属性的推断去做脱敏、隐藏判断或渲染保证。
- 模式 `reject`（默认）仍拒绝 `U+0008`，模式 `placeholder` 仍拒绝无可验证 CP 锚点、非法/重叠表结构、超限形状、未识别控制符及损坏的正文 Piece Table；没有通用容错扫描或自动尝试其他模式。
- 显式使用 `--binary-paragraph-sources` 时，如果对象占位实际修改正文，沿用 `paragraph_source_status=unavailable_after_text_transform`，不保留伪精确的段落 CP 范围。
- 目前仅处理主正文 `fcPlcSpaMom`。页眉/页脚 `fcPlcSpaHdr` 和其他故事中的绘图锚点不属于本阶段支持范围。

官方依据：Microsoft MS-DOC `PlcfSpa`、`FibRgFcLcb97` 和 `Character Properties`，维护记录以固定官网样本的哈希和 GitHub Actions 结果为准。真实中山市政府“佐证材料.wps”默认拒绝；显式占位后返回主正文 **36 段、partial**。这仍不是 WPS Office 实际图形显示的语义验收，也不证明原件的创建软件版本。

## 对照的 CFB 风险

农行广西南宁分行 CFB `.doc` 长度不对齐 512 字节扇区，实际余数 51，且检查对齐前缀时 FAT 仍宣告越界分配。已补充明确的 CFB 诊断原因码：`cfb_unaligned_physical_length` 及 `cfb_fat_unbacked_allocation`。这属于拒绝原因的**定位修复**，没有提供可以安全读取的兼容策略。不得对原件自动裁剪、填零或放宽 FAT 约束；需要先取得可靠生产/软件验证及更完整的 FAT/链证据。
