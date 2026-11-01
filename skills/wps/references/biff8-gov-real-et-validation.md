# 政府官网原始 ET 的 BIFF8 表格样式兼容验证

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 目标与真实来源

本轮仅针对已取得**真实文件**的 BIFF8 保存值读取，分析 MS-XLS 的 TableStyle (0x088F) 与连续 TableStyleElement (0x0890) 记录。它们是工作簿全局的表格**格式**记录；如未知或边界不合法仍拒绝，不因为格式不影响普通数字就跳过任意记录。

官方页面由中山市交通运输局发布：

- `https://jt.zs.gov.cn/xxml/zwgk/czxx/zbggxx/content/post_2632463.html` — 运输车租赁报价表 `.et`
- `https://jt.zs.gov.cn/xxml/zwgk/czxx/zbggxx/content/post_2603613.html` — 自卸汽车采购报价表 `.et`

公开附件直链和原件 SHA-256 见固定机器证据（`<skill-root>/references/biff8-gov-real-et-evidence.json`）。官网发布不等于具体 WPS Office 生产者/版本经证实；不把下载文件提交进 Skill 或部署包。

## 实际 CI 结果

2026-11-01，GitHub Actions Ubuntu Linux / Python 3.13 通过维护脚本（仓库根目录 `tests/wps_biff8_gov_et_discovery.py`）按 HTTPS 官网页面原始链接取得 **2/2 份文件，均为 CFB/OLE，CodePage 均为 1200**：

| 官方原件 | 大小 | 原始结果 | 现有受控子集读取 | 独立源记录值对照 |
|---|---:|---|---|---:|
| 运输车租赁报价表 | 16896 字节 | 原本可 `partial` | `partial`，32 个单元格 | 18/18 一致 |
| 自卸汽车采购报价表 | 21504 字节 | 原先拒绝在 `0x088F`（Workbook 偏移 14984） | `partial`，43 个单元格、两个表格样式组（7/10 个元素） | 19/19 一致 |

全部 37 项源记录值比较由仓库已有独立测试用 `wps_source_oracle.py` 完成，其 CFB 和基础单元格记录算法不同于 WPS Skill 的 BIFF8 保存值解析器。该 oracle 只覆盖能独立观察的纯 SST、LabelSst、Number、RK/MulRk 和数字型 Formula 缓存，不代表 75 个单元格都已获得独立人工逐项确认，也不检验表格样式显示。

全量 WPS 测试 `459/459` 在 Linux Python 3.11 / 3.13 都通过，严格 Skill 检查通过；前述固定 4 份 ET/ETT 的旧版 198 个保存值（33 个未验证时效性的公式缓存）也一致。CI 入口分别为 `wps-biff8-audit.yml`、`wps-biff8-real-source.yml` 和 `wps-biff8-gov-et.yml`。

## 有限实现与保留的拒绝边界

新增的独立模块 `<skill-root>/scripts/wps_biff8_tablestyles.py` 仅在 BIFF8 `data` 模式对有前置 TableStyles 集合及已验证 DXF 记录的 Globals 进行表格样式组验证：

- `0x088F`：固定 FrtHeader (12 字节)、被支持的保留位、声明不超过 28 个元素、1–255 UTF-16 字符样式名且负载完全消费
- `0x0890`：必须紧邻对应 TableStyle，数量精确匹配；每条固定 24 字节 FrtHeader/元素区域/条纹尺寸/已存在 DXF 索引
- 参与记录原始 payload 均有界保留在 `uninterpreted_metadata`；新增 `table_style_sources` 只说明原始名字、数量和 DXF 直接来源，`effective_formatting=unknown`
- 多余孤立元素、分组错位、错误长度、非法类型、未支持 FrtHeader、异常 UTF-16、越界 DXF、超配额仍拒绝；不扫描恢复、不删除或忽略未知记录
- 保留 `partial` 和既有页面格式未知范围；严格 `strict` 与 legacy 原有拒绝不变，Word/MS-DOC 读取器没有修改

`0x088F` / `0x0890` 对保存值不直接编码，但**可能影响最终视觉格式**。本轮不解释是否真实被应用、字体颜色、表格边框、条件格式联动或布局；不能因文字和数字一致就宣称完整格式兼容。两份样本仍是 `partial`。

## 参考依据（维护时可访问，运行时不联网）

- MS-XLS TableStyle：`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/32b77a63-30c5-4885-bc94-434923002d6d`
- MS-XLS TableStyleElement：`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/5c1d12a9-01db-4471-897a-e2d2875980c5`
- MS-XLS FrtHeader：`https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/d56e99f8-a271-4db1-923c-222722d21a37`
- 既有非单元格分类矩阵：`<skill-root>/references/et-noncell-compatibility.md`
