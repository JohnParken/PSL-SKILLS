# MS-DOC 存储子文档分区

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

`--include-related` 为二进制 MS-DOC 增加 `related_stories`；附加区域必须使用JSON：`--include-related --format text`明确参数失败（退出2），避免纯文本只输出正文而静默漏掉所请求区域。默认行为及顶层 `text`、`paragraphs`、`main_cp_units` 不变。附加文字只出现在各区域，不并入主正文，也不推测页码。按FIB长度分区，并不等于完整解析子文档内部各条注释、页眉或文本框。

## 范围与结果

FIB的ccpText之后依次为ccpFtn、ccpHdd、ccpAtn、ccpEdn、ccpTxbx、ccpHdrTxbx，对应脚注、页眉页脚合并区、批注、尾注、正文文本框、页眉文本框。每区是全局CP半开区间，保存WordDocument来源与FIB计数字段偏移。字符数量为UTF16码元，不是Python字符个数。

只要六个附加计数中任意非零，PlcPcd最后CP必须为ccpText加所有附加计数再加1；否则必须等于ccpText。额外最后CP不是任何输出区域文字，不混入末区。所有片段仍先检查CP递增、物理偏移/重叠/长度和配额，再按逻辑区间裁剪；不允许用下一故事的低代理修复当前故事的高代理。加密、版本和CFB安全边界不改变。

零长度区域明确`empty=true`、空文字/空段落、`status=ok`、`anchor_status=not_applicable`。非空区域`empty=false`；未能映射的区域附加`story_references_not_decoded`诊断、`status=partial`、`anchor_status=unknown`、`references=[]`。空引用数组不代表无引用，而是未解释引用表。

脚注PlcffndTxt/Ref和尾注PlcfendTxt/Ref的限定映射见引用表 (`<skill-root>/references/binary-note-references.md`)；PlcfandTxt/Ref的作者和锚点见批注子集 (`<skill-root>/references/binary-comments.md`)，PlcfHdd及分节的存储槽见页眉映射 (`<skill-root>/references/binary-headers.md`)。形状文本框关系仍未解释。页眉页脚合并故事可能含脚注/尾注分隔符及guard段落，存储槽映射不代表某页的有效页眉；文本框文字不是锚点阅读顺序。各已支持附表验证配对、数量、范围与交叠，具体有效语义和未支持结构以相应参考为准。

附加区域沿用字段/对象策略，默认遇未支持控制符即unsupported，不将它们默认为普通文字或空结果。cached_result及placeholder同样显式partial，位置CP仍为全局；字段不能跨区域闭合。默认自动注释C0仍拒绝；仅显式include-related并完成Ref/Txt验证时，在精确映射CP将U+0002变为U+FFFC并诊断partial，不计算编号。其他CP或未支持C0仍拒绝。非正文区不统一强加正文终止CR规则；已解码引用表或页眉槽按各自规则验证终止和guard，未解释区域仍partial。所有区域合用字符、段落、字段及对象诊断预算，先于分配；完整JSON仍有输出配额。

## 依据和验证

公开规范仅开发资料，运行时不联网，原始URL见故事依据 (`<skill-root>/references/ms-doc-stories-evidence.json`)。FibRgLw97定义计数；MS-DOC2.3.2至2.3.7明确相邻顺序及引用表；PlcPcd定义最后额外CP规则。Header段落说明guard段落及页眉页脚合并范围。

新test_wps_stories.py仅标准库合成规范样例：正文加全部六区、空区、混合压缩/Unicode、物理逆序、单片段跨区、两种Table、代理对跨区拒绝、字段/对象与组合预算、损坏计数及统一CFB入口。真实样本若无附加区域，不算本能力真实验收；尚无多故事独立语义对照。

## 显式附表映射

脚注/尾注以外，现有批注作者与锚点 (`<skill-root>/references/binary-comments.md`)及节/页眉存储槽 (`<skill-root>/references/binary-headers.md`)。精确匹配的批注U+0005在include-related下才占位并partial；不解释书签定义的非零长度批注范围。页眉槽是存储范围，不代表页面选择或最终继承。所有已解Table Stream范围统一检查与CLX、CHPX BTE等冲突；SEPX与FKP/文字范围交叠拒绝。原故事文字只存一份，映射元数据不重复计入主正文。
