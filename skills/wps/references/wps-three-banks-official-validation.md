# 中国银行、建设银行、工商银行官网公开文档验证

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

本页记录维护任务对三家银行官网公开附件的真实下载与内容格式检测，**不属于普通 WPS 读取任务指令**。维护脚本位于仓库根目录 `tests/wps_boc_ccb_icbc_probe.py`；工作流位于仓库 `.github/workflows/wps-boc-ccb-icbc.yml`，只在维护 CI 中访问网络。WPS Skill 本身继续只读、纯 Python 标准库、离线，不执行办公软件，也不向分发包纳入第三方公开附件。

## 固定来源、实测概况

2026-10-31 GitHub Actions Ubuntu Linux / Python 3.13 运行 `41183896639`（job `124486751731`），从 3 家银行的**官方页面中直接引用的附件 URL**选取 10 份候选，限制 32 MiB、HTTPS，拒绝非预设银行域名和跳转。**4 份下载成功，6 份连接阶段失败；不能把下载失败记作解析失败。**

| 银行与官方页面 | 后缀与真实签名 | 读取结果及有限证据 |
|---|---|---|
| 中国银行 2025 年企业年金增选项目确认函 | `.docx` / ZIP-OOXML | `ok`，16 段，参评/确认关键词出现 |
| 中国银行 2025 年广东玖号职业年金选聘确认函 | `.docx` / ZIP-OOXML | `ok`，14 段，报名/确认关键词出现 |
| 工商银行山东济南分行采购最终报价表 | `.docx` / ZIP-OOXML | `partial`，提取 1 段，预期“报价”锚点未匹配，**不得记为完整文本验收** |
| 工商银行大连分行采购价格附件 | `.doc` / CFB-OLE、MS-DOC | `partial`，提取 1 段；“采购”出现，“价格”未匹配；另一套独立 MS-DOC 正文算法的全文和段落数组与读取器一致 |
| 建设银行厦门分行 2026 年两份采购公告，合计 6 个附件 | `.doc` 与 `.docx`（**未成功取得字节**） | `www1.ccb.com` 和 `www.ccb.com` 均遇 TLS `UNSAFE_LEGACY_RENEGOTIATION_DISABLED`；这是网络握手失败，不证明文件损坏、格式不支持或解析器拒绝 |

详细每份直接下载 URL、官网公告、SHA-256（仅下载成功的文件）、文件大小、标题锚点与完整错误分类在机器清单（`<skill-root>/references/wps-three-banks-official-evidence.json`）。该清单把`download`、`magic`和`read_status`分离，避免将未取得字节的文件算入分母。

### 来源页面

- 中国银行：`https://www.bankofchina.com/cbservice/bi2/202510/t20251009_25521263.html` 与 `https://www.bankofchina.com/cbservice/bi2/202511/t20251114_25569711.html`
- 建设银行：`https://www1.ccb.com/chn/2026-04/16/article_2026041608483476352.shtml` 与 `https://www.ccb.com/chn/2026-03/05/article_2026030510470824252.shtml`
- 工商银行：`https://www.sh.icbc.com.cn/shandong/page/1008706621704302592.html` 与 `https://www.sh.icbc.com.cn/dalian/page/887367093941452800.html`

## 兼容性解释与后续要求

此次 4 份能够读取的办公附件都不是经确认的原生 `.wps`：两份中行和一份工行是 OOXML，另一份工行是 CFB/MS-DOC。**不能把 4/4 可读表述为 WPS 专有格式成功率或生产来源认证。** 工行报价表的单段读取/关键词缺失反映显著语义缺口，可能存在扫描内容或对象，当前未做视觉/OCR 检查，不推断具体原因。标题关键词只证明有限内容匹配；与格式完整还原不同。

建设银行连接失败应通过网站 TLS 协议配置或经授权的其他**可信官方源**解决；禁止为了获取样本降低 TLS 校验、启用不安全重协商或转向第三方未经确认副本。保留已验证官方 URL 与错误分类。需要进一步验证 `.wps` 时，仍须提供实际 WPS Office 来源证据与独立全文/表格预期；详情参见真实原生 WPS 验收规则（`<skill-root>/references/wps-real-validation.md`）。

本次只扩展**维护验证工具与证据**，没有改变原始 `read_wps.py` 的安全门禁，`main` 未改动。实际结果使用 Linux 维护 CI，与生产云端 harness、Word/WPS 软件视觉验收区分记录。
