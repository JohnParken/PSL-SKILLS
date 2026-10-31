# PSL-SKILLS：真实失败样本兼容性分析报告

> 2026-10-31；基于分支 `codex/wps-paragraph-source-cp-20261031`；本报告仅分析已经固定哈希的政府与银行官网公开附件。置信度分级：**已证实**（实际字节/源码/CI）、**规范支持但实施未验**、**尚无依据**。不将部分文本提取、官网发布或双算法一致等价为 WPS Office 完整视觉兼容。

## 一、结论摘要

本次新增的 GitHub Actions Linux Python 3.13 取证作业 `41184963914`，对两份 SHA-256 固定的原始失败文件实际重新下载、检查 CFB 头、对照目标源码的拒绝位置，并在**只读且不改变生产解析器**的条件下核实：

1. **政府 `.wps`**：CFB v3/512B/全文件对齐。拒绝位置 WordDocument 物理偏移 2546（主正文逻辑 CP 249）恰好是 **U+0008 绘图对象标记**，且 FIB 第 40 对 `fcPlcSpaMom/lcbPlcSpaMom` 对应的 `PlcfSpa` 为 64 字节、2 个形状，CP 列表递增、**CP 249 正好命中形状锚点**。现有 reader 仅把 U+0001 当作可选对象占位，故将该标记作为未支持 C0 控制字符拒绝。**优先开发形状锚点的可核实、非渲染型占位读取**。
2. **农行 `.doc`**：CFB v3/512B，文件长 862771 字节，扇区余数 **51**、尾部非零；头保留 6 字节为零、最小尺寸符合规范。首次拒绝来自总长不对齐；**仅在隔离取证内存中检查扇区对齐前缀**时又发生 `FAT allocates sectors beyond physical input`。因此它至少具有**两个结构阻断条件**，不能靠裁掉 51 字节解决，更不能为通过率取消 FAT/扇区边界验证。
3. **工行济南 `.docx`**：ZIP/OOXML 分支返回 `partial`、主正文仅一段、预期“报价”锚点未出现；属于**覆盖缺口**，并非已证实是扫描图片或 OCR 缺陷。
4. **建行六份附件**：TLS `UNSAFE_LEGACY_RENEGOTIATION_DISABLED`，**连接阶段未取得原件**，属于独立的网络可访问性问题，不是解析失败，不应通过降低 SSL/TLS 安全策略绕开。

## 二、证据及故障定位

### 2.1 中山市住建局“佐证材料.wps”

- 官网来源：`https://jsj.zs.gov.cn/gkmlpt/content/2/2656/post_2656565.html`；原始附件 `https://jsj.zs.gov.cn/attachment/0/579/579836/2656565.wps`。
- SHA-256：`3ccedb9e26d89410796508531912169559c0a0258303a085075e69829762375c`；24,576 字节；CFB major=3, minor=0x003E, sectorShift=9（512B），总长对齐；Header CLSID 不为零，但此前 `data` 模式能按已知精确 legacy Word profile 识别。
- 真实解析异常：`unsupported`，`WordDocument` 偏移 2546，`Fields/objects/C0 control codes outside plain-text subset`。
- 原始字节位置经 CLX/Piece Table 映射：piece 0、未压缩、CP=249、U+0008。形状表 `PlcfSpaMom` 64 字节，按 CP 数组+26B SPA 记录的结构推得两条 SPA；CP 数组有序且包含 249。
- 微软 MS-DOC 将 `U+0008` 定义为 drawn object，并要求 `PlcfSpa` 中每个有效形状锚点与 U+0008 / `sprmCFSpec` 对应。规范链接：`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-doc/7022285b-9621-42e9-ad4d-4e02c115ef18`、`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-doc/e98fe93c-d57f-4d9b-a519-30e6acbdd414`。

**可确认原因**：解析器尚未覆盖一个真实存在且规范定义的绘图对象标记分支，不能将其称作任意未知私有乱码。**未确认**：关联字符 run 的有效 `sprmCFSpec`、SPA 全部字段语义、实际绘图资源和用户看到的形状。

**建议实施（P0，可控）**：新增只读 `PlcfSpa` 受限解析及 `U+0008` 锚点确认；将 `sprmCFSpec` 作为原始证据验证而非最终可见性推断；仅在显式 `object_policy=placeholder`、位置/表范围/数量/锚点/字符属性均可信时，以 `U+FFFC` 代表无法渲染的绘图对象，返回 `partial`（退出 3）和精确 CP/流来源诊断。默认 reject 保持不变；异常 CP、重复/倒序锚点、截断 SPA、交叉区域、资源超限仍拒绝。不得宣称图片内容已解码、页面形状已还原。

### 2.2 中国农业银行广西南宁分行授权/承诺附件 .doc

- 官网来源：`https://www.abchina.com/cn/branch/gx/cg/bm/202404/t20240425_2415781.htm`；原件 `https://www.abchina.com/cn/branch/gx/cg/bm/202404/W020240425607944480483.doc`。
- SHA-256：`a3be79ad74fa0a810215ab477116fdba687aac628b0ab9477fbce7936e20ae1a`；长度 862771；CFB v3, minor 0x003E, sectorShift=9（512B）；头 CLSID 为空；6 个保留头字节为零，满足最小文件长度。
- 首个故障：`wps_cfb.CompoundFile.__init__` 的复合校验报告 `Invalid CFB reserved header fields/alignment/minimum size`，独立布尔探针定位至**长度余数 51**（其他两个校验均未触发）。
- 非零 51 字节尾随区域 SHA-256：`e7433e2f9f222b78938aa11c97e88f8c0108f8e2daa88770a01786034cbd6b47`；此哈希只固定了尾部字节，不解释其来源。
- 实验性非生产诊断：对**原件不修改、不写磁盘、仅在内存中**获得的 sector-aligned prefix 调用现有严格 CFB 结构校验；仍因 `FAT allocates sectors beyond physical input` 被拒。该检查**不是**接受裁剪输入的依据。

**可确认原因**：总长对齐不符合当前 CFB v3 子集，且对齐前缀的 FAT 仍与物理范围不一致。**尚不能确认**：是否损坏、外部追加数据、特殊生产器变体或其他打包形态；亦不能确认将其读入后内容可靠。

**建议实施（P0 调查，P1 是否兼容待证）**：先写入不会泄露正文的 FAT/DIFAT 指针取证器，记录前缀越界的具体扇区索引、所属 FAT 记录及目录链；保留原件哈希，寻求 Word/WPS 应用真实打开、重新保存/导出及独立原始内容的证据。未建立确定性修正规则前继续拒绝。不允许默认 `rstrip`、`[:sector_size*k]`、`zero-padding`、重试不同解析模式或在异常后读取不可信文本；应提高错误代码精度（`cfb_unaligned_length`、`cfb_fat_out_of_range`），而不是变更安全策略。

## 三、相关但非同类失败

| 样本 | 观察 | 应归属模块 | 行动 |
|---|---|---|---|
| 工行济南报价 `.docx` | ZIP/OOXML；`partial`；主正文 1 段，报价关键词未命中 | `wps_ooxml.py` | 检查 `document.xml`、表格/嵌入对象/媒体引用及诊断覆盖，必要时给出明确“图片文字不可读”；不推断一定是扫描件 |
| 工行大连 `.doc` | CFB/MS-DOC；`partial`；1 段，另一套文本算法一致 | 二进制表格与有限正文 | 仅可证明存储正文交叉一致，不是业务价格表结构验收 |
| 建行 6 个官方附件 | TLS 旧版重协商失败，0 字节 | 官网下载与维护取证 | 安全连接无法建立，列为 download_blocked；不禁用 TLS 验证或抓未经核实第三方副本 |

## 四、具体开发工作包与验收口径

| 顺序 | 工作包 | 核心修改范围 | 验收 |
|---|---|---|---|
| P0-1 | `U+0008` 绘图对象证据读取 | `read_wps.py` + 新的有界 `PlcfSpa` 模块 + 单测 | 该政府原件在显式 placeholder 策略下返回部分正文和形状占位；默认 reject 仍失败；没有未知字符跳过 |
| P0-2 | CFB 精细错误分层与 FAT 取证 | `wps_cfb.py` / 维护诊断及测试 | 农行原件的对齐余数 51 与 FAT 越界分别可定位；绝不把结构错误伪装成 `partial` |
| P1-1 | 真实二进制表格（PAPX/TAPX） | `wps_binary_tables.py` | 用独立 WPS 软件表格预期验证已证实行/单元格，合并未知仍报告 unknown |
| P1-2 | OOXML 部件内容覆盖 | `wps_ooxml.py` 及附加部件诊断 | 工行报价缺失可定位为文本/表格/图片/对象哪个分支，不把 1 段误报完整 |
| P1-3 | 多来源真实样本验收 | CI、`verify_wps_sample.py` 支持范围内的固定预期 | SHA、制作者证据、独立全文、状态、失效内容、未验证范围逐项记录 |

**公共契约必须保留**：`ok=0`、`partial=3`、失败 `=1`、参数错误 `=2`；每项增量须经过 Python 标准库 `-I -S -B` Linux 回归、`skills/wps` 严格结构检查、真实原件 SHA/字节未变核对、默认行为比较。WPS Skill 不新增网络、办公软件、写入、转换或执行宏的能力。

## 五、结论与未完成事项

**明确可修的真实缺口是 U+0008+PlcfSpa 绘图对象。** 文件已经提供了 CP 249 的形状锚点来源，可以在原有只读/严格策略上做高置信度的非渲染型结构增强。

**农行 CFB 更偏向格式异常调查，而非马上放宽兼容。** 51 字节尾随数据不是唯一障碍，FAT 实际分配问题仍在；先确定原件是否有受支持的安全解释，才能决定要不要支持这种文件。业务上继续按 `invalid_or_unsupported` / failure 处理更稳妥。

历史 443 项 WPS 回归通过，主要证明受支持格式子集没有在自动化测试中回退；不能证明这些失败原件已经修复，也不证明所有 WPS Office 生产来源。当前诊断脚本和固定机器证据均在 Draft PR 的独立分支，生产解析器并未据本报告修改。

### 可复核证据

- 本轮只读诊断：GitHub Actions `41184963914`、job `124489928317`；`tests/wps_real_failure_deep_probe.py`，只输出边界元数据与字符代码，不输出正文。
- 机器结果：`tests/wps_real_failure_forensics_evidence.json`，记录固定 SHA、CFB 头、绘图对象 CP/形状锚点、非对齐尾部及 FAT 取证结论。
- 公开规范：Microsoft `MS-CFB` Header、`MS-DOC` Character Properties、`MS-DOC` PlcfSpa。
