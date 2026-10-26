# MS-DOC 字符属性证据读取

显式 `--hidden-text annotate` 在支持的二进制 MS-DOC 中读取 `PlcBteChpx` 与 `ChpxFkp`，保留全局 CP 范围、物理文本 FC、FKP/属性组偏移和可识别的原始修改操作。CP证据只覆盖已请求的主正文或主正文加附加故事，不包含末尾额外终止CP。默认 `include` 不读取这些额外结构，保持既有行为。`exclude`、accepted/original 修订过滤仍不支持；annotate 不过滤或复制文字。

目前识别 CHPX 内的 `sprmCFVanish`、`sprmCFWebHidden`、字段隐藏及插入/删除修订操作，保留 toggle 原数值和顺序，不把操作数当成最终属性。`0x80/0x81` 与样式有关，Web 隐藏也只适用于对应视图。无 CHPX 或未识别修饰符均不等于文字可见。结果始终为 partial，`effective_visibility/effective_revisions=unknown`。

安全检查：BTE 长度与数量、严格递增 FC、512 字节页范围、页别名、页与 FIB/文本片段交叠、页内 run 数与 FC、BTE/FKP 范围一致、属性指针与长度、非一致属性重叠、PRL 及变长操作数边界和 UTF-16 码元边界。共享完全相同的属性组可保留，部分重叠拒绝。Pn 上十位按规范忽略。非字符 SPRM 不按通用变长规则猜测，剩余组标未解释。最多 4096 页、65536 run/CP 注释、累计属性操作数字节 1 MiB；映射只遍历实际相交片段，不展开每字符矩阵。

这只是字符属性证据，不是有效隐藏/修订过滤。要可靠过滤还需解析段落 PAPX/style、STSH 字符样式、CLX piece PRM 的叠加顺序与状态重置，并处理完整修订语义。缺少这些结构时不能仅凭某个 `vanish=1` 删除文字；exclude 亦不能成为脱敏承诺。诊断不包含被排除文字全文。

新增行为用明确的标准库合成样例验证，未取得真实隐藏/修订内容的独立显示对照。[官方依据](ms-doc-charprops-evidence.json)为开发资料，运行时不联网。
