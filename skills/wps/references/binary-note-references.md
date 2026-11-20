# 二进制脚注与尾注 CP 引用

维护资源说明：文中 `docs/maintenance/wps/` 与 `skills/wps/scripts/test/` 均以宿主确认的仓库根目录为基准，仅供源码维护，不随运行包分发；运行任务不读取或执行。没有仓库时跳过维护资料，不按 Skill 安装目录拼接。

显式 include-related 时，读取第2/3对 footnote Ref/Txt、第46/47对 endnote Ref/Txt。Ref为4(n+1)+2n字节，Txt为4(n+2)字节；两者条目数一致。Ref有效锚点必须严格递增且位于主正文内，最后CP未定义并忽略。Txt从0起、有效CP严格递增，倒数第二CP必须ccpStory−1；最后CP未定义并忽略。每个文字范围尾字符为CR。原表偏移、全局CP、编号原值均保留，原正文输出不变。

只建立已存储的CP对应，不生成页码或编号显示，不验证sprmCFSpec与表深度，因此映射仍partial。自动编号锚点需U+0002；默认正文C0控制策略仍拒绝该字符；仅显式include-related且Ref/Txt完整校验后，精确匹配的自动引用锚点及对应注释起始U+0002替换为U+FFFC并记录位置/partial，不计算编号。其他CP上的U+0002仍拒绝。自定义符号锚点可读取。缺表但有文字沿用unknown/partial，不把缺信息当空成功。只在显式选项下校验这些附表，默认主正文范围兼容。

配额先于条目分配：每表最多100000引用，沿用流/总大小与输出预算。校验表长度、偏移、Ref/Txt/CLX交叠、主/附故事计数与末端。未重复输出每条注释全文，text_range指向已输出故事的原始CP而非规范化字符索引。

批注作者及引用另见批注子集 (`<skill-root>/references/binary-comments.md`)，节与页眉存储槽另见分节范围 (`<skill-root>/references/binary-headers.md`)；本引用映射不计算完整书签或有效页面语义。脚注尾注映射为标准库合成验证，没有真实非空故事的独立内容对照。

官方依据见来源 (`docs/maintenance/wps/binary-note-evidence.json`)，开发研究用途，运行时不联网。
