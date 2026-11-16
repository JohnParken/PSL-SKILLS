# BIFF5/7 核心保存值只读子集

路径占位符说明：`<skill-root>` 是 ET Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 ET 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

只在 Workbook/Book 流具有 8 字节、版本 0x0500 的 globals BOF 时进入独立 `<skill-root>/scripts/wps_biff5.py`。0x0500 同时用于 Excel 5 与 Excel 95，不能据此分辨产品版本或证明 WPS 来源；不把修改过版本字段的 16 字节 BIFF8 BOF 当旧格式。已由 globals 确定旧版后，8 字节工作表 BOF 可接受 0x0600（Excel 新版另存旧格式的已记录差异），仍按旧版结构读取，保留原始头与 biff5_sheet_bof_version_mismatch/partial；其它版本、错误子流类型及非 8 字节头仍不放行。

## 范围和结果

- 返回独立 `biff5_readonly_v1`、`format=biff5_subset`、`biff_version=5/7`，不会冒充 BIFF8；默认 partial。ET metadata、区域预览和分析显式接受此 schema，其它消费者须显式声明支持，不能改字段绕过拒绝。table-workflow 已通过公开能力协商接受新 schema，但要求 XLSX 提供方声明 `extraction_schemas` 包含它；默认拒绝 partial，显式 `accept_partial` 也不能放行未知值遗漏或缺失/错误缓存，使用保存缓存仍须显式 `cached`。
- 核心读取 Number、RK、MulRK、Blank、MulBlank、BoolErr、Label，以及 Formula 的保存数值/布尔/错误和紧邻 String 文本缓存。保留前导零、Decimal 精度及缓存未验证标记；不转写或执行旧版公式 token，不把未识别缓存当零或空字符串。
- 旧网格上限为 16384 行、256 列；BoundSheet 名称是字节字符串，旧 XF 16 字节仅保留槽位与原始格式编号。Dimensions、格式、图形、名称、合并等未实现元数据只保留原始证据，不套用 BIFF8 结构，不作日期/样式继承推断。
- 字符串仅按唯一、显式支持的 CodePage 严格解码，不用系统区域、猜测编码或替代字符。当前显式编码为 437/850/852/866、932/936/949/950、1250–1258、10000。缺失、冲突、未知或不可解码时省略文字并报告；数值仍可保留。表名不可读时使用带 `name_unavailable` 与 `name_raw_hex` 的结果内标识，不称它为原表名。
- 不恢复旧字符串 Continue，不扫描损坏文本重新同步。公式固定22字节头不全时省略该格；头完整而 token 截断时保留逐项验证的缓存并标记 `tokens_complete=false`/partial。String 不紧邻时省略受影响缓存；不把 BIFF8 的空值 marker 套用旧版。安全拒绝包括 FILEPASS、文件/记录/单元格等硬预算和大于 2080 字节的旧记录。
- 只跟随已验证的 BoundSheet 记录边界；别名/重叠/无效偏移或非工作表不读值。遇嵌套 BOF 停止该表，不将图表/宏子流当工作表；未归属记录与缺 EOF 明确影响覆盖。宏、公式、外链及嵌入对象从不执行或跟随。

## 证据与限制

规范依据为 Microsoft Excel 97–2007 Binary Specification 的旧 BOF，以及 OpenOffice Excel File Format 文档的字节字符串、CodePage、Label、String、Formula、XF、BoundSheet 与网格规则。运行时不联网。

源码维护证据以仓库根目录为基准：`skills/et/scripts/test/test_wps_biff5.py` 是明确合成测试；`skills/et/scripts/test/verify_biff5_public.py --online` 是显式联网的维护探针，下载两个哈希固定的公开 OpenOffice 测试文件到内存，非运行包入口。没有仓库时不执行或猜维护路径。

真实公开 Excel95 样本读取 195 格（含空白），CodePage 1252；同主题 XLSX 年代与布局不同，不能逐坐标当作整表 oracle。已人工确认段落位置后对 25 个指定保存值做独立标准库 XML 对照，包含数值、布尔、错误与公式缓存，全部一致；全坐标 101 项仍有 37 差异，明确不宣称整表相同。此证据不等于全部 BIFF5/7、所有编码、真实 WPS ET 或客户端版式验收。详细来源/hash/命令在仓库维护文档 `docs/maintenance/et/biff5-sst-20261115.md`。
