# MS-DOC 存储子文档分区

维护资源说明：文中 `docs/maintenance/wps/` 与 `skills/wps/scripts/test/` 均以宿主确认的仓库根目录为基准，仅供源码维护，不随运行包分发；运行任务不读取或执行。没有仓库时跳过维护资料，不按 Skill 安装目录拼接。

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

`--include-related` 为二进制 MS-DOC 增加 `related_stories`；附加区域必须使用JSON：`--include-related --format text`明确参数失败（退出2），避免纯文本只输出正文而静默漏掉所请求区域。默认 maximum 已自动请求这些区域；显式 standard 的顶层 `text`、`paragraphs`、`main_cp_units` 保持原范围。附加文字只出现在各区域，不并入主正文，也不推测页码。按FIB长度分区，并不等于完整解析子文档内部各条注释、页眉或文本框。

## 范围与结果

FIB的ccpText之后依次为ccpFtn、ccpHdd、ccpAtn、ccpEdn、ccpTxbx、ccpHdrTxbx，对应脚注、页眉页脚合并区、批注、尾注、正文文本框、页眉文本框。每区是全局CP半开区间，保存WordDocument来源与FIB计数字段偏移。字符数量为UTF16码元，不是Python字符个数。

只要六个附加计数中任意非零，PlcPcd最后CP必须为ccpText加所有附加计数再加1；否则必须等于ccpText。额外最后CP不是任何输出区域文字，不混入末区。所有片段仍先检查CP递增、物理偏移/重叠/长度和配额，再按逻辑区间裁剪；不允许用下一故事的低代理修复当前故事的高代理。加密、版本和CFB安全边界不改变。

零长度区域明确`empty=true`、空文字/空段落、`status=ok`、`anchor_status=not_applicable`。非空区域`empty=false`；未能映射的区域附加`story_references_not_decoded`诊断、`status=partial`、`anchor_status=unknown`、`references=[]`。空引用数组不代表无引用，而是未解释引用表。

脚注PlcffndTxt/Ref和尾注PlcfendTxt/Ref的限定映射见引用表 (`<skill-root>/references/binary-note-references.md`)；PlcfandTxt/Ref的作者和锚点见批注子集 (`<skill-root>/references/binary-comments.md`)，PlcfHdd及分节的存储槽见页眉映射 (`<skill-root>/references/binary-headers.md`)。页眉页脚合并故事可能含脚注/尾注分隔符及 guard 段落。文本框文字默认按存储顺序输出。最大模式另提供下述文本框范围。

附加区域沿用字段/对象策略，默认遇未支持控制符即unsupported，不将它们默认为普通文字或空结果。cached_result及placeholder同样显式partial，位置CP仍为全局；字段不能跨区域闭合。默认自动注释C0仍拒绝；仅显式include-related并完成Ref/Txt验证时，在精确映射CP将U+0002变为U+FFFC并诊断partial，不计算编号。其他CP或未支持C0仍拒绝。非正文区不统一强加正文终止CR规则；已解码引用表或页眉槽按各自规则验证终止和guard，未解释区域仍partial。所有区域合用字符、段落、字段及对象诊断预算，先于分配；完整JSON仍有输出配额。

## 依据和验证

公开规范仅开发资料，运行时不联网，原始URL见故事依据 (`docs/maintenance/wps/ms-doc-stories-evidence.json`)。FibRgLw97定义计数；MS-DOC2.3.2至2.3.7明确相邻顺序及引用表；PlcPcd定义最后额外CP规则。Header段落说明guard段落及页眉页脚合并范围。

新test_wps_stories.py仅标准库合成规范样例：正文加全部六区、空区、混合压缩/Unicode、物理逆序、单片段跨区、两种Table、代理对跨区拒绝、字段/对象与组合预算、损坏计数及统一CFB入口。真实样本若无附加区域，不算本能力真实验收；尚无多故事独立语义对照。

## 显式附表映射

脚注/尾注以外，现有批注作者与锚点 (`<skill-root>/references/binary-comments.md`)及节/页眉存储槽 (`<skill-root>/references/binary-headers.md`)。精确匹配的批注U+0005在include-related下才占位并partial；不解释书签定义的非零长度批注范围。页眉槽是存储范围，不代表页面选择或最终继承。所有已解Table Stream范围统一检查与CLX、CHPX BTE等冲突；SEPX与FKP/文字范围交叠拒绝。原故事文字只存一份，映射元数据不重复计入主正文。

## 最大模式文本框

`--reading-mode maximum` 校验正文及页眉文本框的 `PlcftxbxTxt` 或 `PlcfHdrtxbxTxt`。每个实际文本框在对应故事的 `references` 中提供 `textbox_index`、`shape_id`、全局半开 `text_range` 和记录来源。跳过备用条目及最后一个保护条目。保护条目的末端只检查递增，不用来取文字，实际文本框范围仍必须在故事内并以段落结束符结尾。形状编号唯一匹配且该锚点可输出时提供 `anchor_cp`。组内形状还需 `lTxid` 匹配文本框索引及唯一可输出的组合锚点，成功时返回 `matched_officeart_group_shape_id`、`anchor_shape_id`、`group_shape_path` 及 `officeart_source`。匹配不到或存在歧义时，仍保留已校验的文本框范围。

小故事（至多 65536 个 UTF-16 码元）的各条引用包含 `text`。大故事通过 `story_text_utf16_range` 从合并故事文字定位，避免复制整段输出。故事经字段过滤后只提供存储 CP 和合并文字，`textbox_text_status=combined_story_only_after_field_filtering` 表明不能用存储范围切分输出文字。

缺附表时保留合并故事和引用未解码诊断。记录语义不一致时附加 `textbox_metadata_invalid` 并保留合并文字，流越界、结构重叠或超出 4096 条记录的资源检查按失败处理。链接文本框之间的流向、图形显示顺序和有效页面仍待核对，返回 partial。开发依据为 MS-DOC 的 PlcftxbxTxt、PlcfHdrtxbxTxt 和 FTXBXS 章节。

最大化读取的局部恢复：仅 reading-mode maximum 对已分类的页眉槽 CP/guard、注释映射语义错误保留可安全定界的存储文字，报告 binary_story_mapping_unresolved；不伪造锚点。独立区域 UTF-16 或域边界错误仅将该区域标 partial/text=null，正文及其他可读区域保留。标准 include-related 仍严格。SEPX/共享数据范围、重叠、安全与资源错误及未分类异常继续失败；恢复不等于全格式支持。
