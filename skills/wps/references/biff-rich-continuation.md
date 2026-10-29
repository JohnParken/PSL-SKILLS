# BIFF8 富文本 Continue 子集

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

支持 SST 的字符和 FormatRun 续段，不解释字体效果，不恢复样式。每个字符串的固定头（cch、flags、可选 cRun、可选有符号 cbExtRst）必须在同一记录；字符阶段续段带 0/1 编码字节，可切换压缩低字节/UTF-16。UTF-16 不得在单个 code unit 内断开，代理对可跨记录并在逻辑拼接后校验。字符读完后切换为原始格式 run 阶段，续段不消费编码字节；严格按 cRun×4 收集，支持 run 字节跨记录边界。下一个字符串从实际游标开始，不能把其头吞入 run。

记录原字符串起始流偏移与 payload 偏移，保留每个 SST 字符串的一份原始 run 字节。run 索引递增且不得越出字符串；末尾哨兵的 font 按规范忽略。其他 font 必须是已存在的有效索引。返回 rich_string_formatting_not_applied 诊断，仍为 partial。

cch≤32767，继续沿用记录数和文件/流预算；在收集正文或元数据前检查 run 与 ExtRst 合并字节预算及共享条目数预算。缺 Continue、空续段、非法编码、截断、奇数字节断点及不完整固定头明确拒绝。SST 的 fExtSt/ExtRst 注音扩展按声明长度原样读取，续段不消费字符编码字节；返回 `shared_string_phonetics` 及 partial 诊断，不把扩展字节误当正文或格式 run。不解释注音结构，负长度与截断仍拒绝。公式 String 富格式和注音扩展仍不支持。

依据：Microsoft MS-XLS XLUnicodeRichExtendedString（2.5.293）、FormatRun（2.5.132）、Continue（2.4.58）；继续使用原有字符循环。MS-XLS 对 variable fields 续段的概括文字没有单独解释 run 阶段编码 byte，因此交叉检查 Apache POI 自有 UnicodeString writer：字符与 run 独立输出，run 续段没有编码 byte。未复制 POI 代码，未安装或执行 Java/POI。对 run 内切分采用长度限定的原始数组拼接；不猜测私有变体。

规范链接（开发依据，运行时不访问）：

- https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/173d9f51-e5d3-43da-8de2-be7f22e119b9
- https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/24d30c9e-350e-4d55-a1c3-f4404cc41f99
- https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/999fae21-d3d9-42e8-8290-639782460c67
- https://raw.githubusercontent.com/apache/poi/trunk/poi/src/main/java/org/apache/poi/hssf/record/common/UnicodeString.java

验证：test_wps_rich_strings.py 为标准库构造的合成规范样例，包含中文/ASCII/跨记录代理对、编码切换、长字符串、run 各字节分界、后接字符串、固定头/长度/编码/预算错误及固定种子结构扰动。当前已有合法 ET/ETT 样本未观察到富文本，不能称这些新行为已通过真实富文本语义验收。

新增注音验证见 `<skill-root>/scripts/test_wps_phonetic.py`：字符编码切换、代理对、原始扩展的每个切分点、后接字符串、零长度扩展、组合预算及结构化失败位置。仍为合成证据，详见解析增强边界 (`<skill-root>/references/et-parser-stability.md`)。

纯文字 `Label` 内联单元格复用字符续段规则：从六字节单元格前缀之后读取完整字符串头，只接受 flags 0/1，不接受富格式或注音字段，末尾必须恰好消费完。`<skill-root>/scripts/test_wps_biff_reading.py` 覆盖编码切换、跨记录代理对及失败边界；不扩大 SST 元数据或公式缓存的既有支持范围。详见内联文字与兼容元数据 (`<skill-root>/references/biff8-readonly.md#inline-text-and-passive-metadata`)。
