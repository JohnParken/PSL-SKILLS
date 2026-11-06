# 真实样本测试记录

> 以下为按时间追加的记录，早期“尚未验证”“未下载”等描述仅反映当时状态；当前结论以末尾汇总为准。用户撤回的候选不作为验收证据。

在 macOS / Python 3.12.11，运行 `python -I -S -B -m unittest discover -s tests -p 'test_table_workflow_quality.py' -v`：27 项通过。

其中公开真实 XLSX `tests/xlsx_real_business_20261103/denham_bank_reconciliation.xlsx` SHA-256 为 `3d2177f4b1e51b6c86b2621a77975469bea13198f6d7a410ec5cf7b587679d5c`，来源 https://denhamparishcouncil.gov.uk/assets/Uploads/Denham-PC-Bank-reconciliation-March-31-2020.xlsx 。使用独立 OOXML/Fraction 预期核对；维表与目标模板是合成的，验证关联、统计和生成模板副本，不表示真实模板编辑验收。另一真实 cashbook 的不支持公式按预期拒绝。

DOCX、WPS、ET、OFD、PDF、PPTX 的真实读取及编辑创建尚未验证；重庆和南京附件下载因本机 DNS 解析失败未取得。GitHub Actions 因 gh 命令不存在未核验。记录不代表全格式兼容或客户端视觉验收。

## 附加技能回归

在 macOS / Python 3.12.11 分别运行 `python -I -S -B -m unittest discover -s skills/<skill>/scripts/test -p 'test_*.py' -q`：DOCX 399、ET 453、OFD 149、PPTX 89、XLSX 373 项通过；WPS 进程退出 0，输出被截断，无法可靠记录测试数。上述为技能测试套件结果，包含合成输入，不能称为全是真实文件验收；未执行客户端视觉验证。仓库没有 PDF 专用 Skill。尚未完成上述各格式的独立真实文件编辑和生成验收。

## Edge 下载的黄石 OFD

来源页面：https://www.huangshi.gov.cn/index_2018/sjkgj/gfxsjk/202608/t20260807_1349117.html ；文件：https://www.huangshi.gov.cn/index_2018/sjkgj/gfxsjk/202608/W020260807361177754852.ofd 。通过本机 Edge 下载，原件 92417 字节，SHA-256 `474f3ca818686606b83fc342ccc96581691f65db5b0bac1664b03d9536b28247`。在隔离临时目录复制后，执行 `python -I -S -B <ofd-root>/scripts/read_ofd.py sample.ofd`：退出 3，schema `ofd-read-3`，状态 partial；JSON 字符串中包含来源网页数字 835、770，尚未核实其确属正文 text_runs，不算独立语义验收。未进行 OFD 创建、编辑或客户端视觉验收。

### Edge 官方样本补充核对（macOS，Python 3.12.11）

本轮通过 Edge 下载，仅读取隔离副本；原件未修改。以下 SHA-256 为实际下载文件哈希，来源页面与附件均为公开政府网站。自检通过仅表示运行环境可用。

- 黄石政策 OFD：[来源页面](https://www.huangshi.gov.cn/index_2018/sjkgj/gfxsjk/202608/t20260807_1349117.html)，`W020260807361177754852.ofd`，SHA-256 `474f3ca818686606b83fc342ccc96581691f65db5b0bac1664b03d9536b28247`。读取退出 3、partial：诊断包括 CustomTags 仅元数据、图像未读取、字形映射及矢量未解释。输出的 text_runs 实际含 835、770、8580、7080，与来源网页对应金额一致；仅核对这些数值，不代表全文或版面完整。
- 同页面 WPS：`W020260807361177849194.wps`，SHA-256 `780c6a1d0bc03c8063ab28c49eaa5d6039ad0c688fdeee90e7a2f01d68788aa1`，CFB；`read_wps.py` 退出 3、partial，提取标题与上述四个标准数值；不代表完整读取。
- [重庆司法局 DOCX](https://www.cqtn.gov.cn/bm/qsfj/zwgk_25192/zfxxgkml_bm/jczwgk/ggflfwly/gkxx_sfj/cxfw/202411/t20241128_13839332.html)：`P020241128355851494048.docx`，SHA-256 `4ba5fcfb6106dacce040bef964031541977783f95c9e39e9bc275f7be4bcc758`，读取退出 0，输出“法律援助申请表”等文本；ZIP CRC 检查通过，未作全文对照。
- [南京统计局 XLSX](https://tjj.nanjing.gov.cn/njstjj/202502/t20250219_5077756.html)：`P020250219356392777243.xlsx`，SHA-256 `80664a8d6fed6a6ce38bd0aee00cd6c17dc9ddc03daa32cf802f2899d271240f`，读取退出 0，Sheet0 A1 为“南京市市级项目预算绩效目标表”；ZIP CRC 检查通过，未核对全部单元格。
- [深圳人社 ET 附件 1](https://hrss.sz.gov.cn/xxgk/qtxx/zdjsxm/content/post_12753063.html)：`12753063.et`，SHA-256 `4f7e219556345742a6cbba5a32786b0015c079116ffb22a9519fb421578ed315`，CFB；`et_cli.py run` 退出 3、partial，声明工作表内单元格值覆盖 complete，尚未做独立逐格核对，不能据此证明数据完全正确。
- [深圳电梯培训 PPTX](https://www.sz.gov.cn/szzt2010/wgkzl/jcgk/jcygk/zdzcjc/content/post_12677378.html)：`12677378.pptx`，SHA-256 `77b9953a642640a0513ea33b399b7baf6f57d044a229c622445c2c2a22585e4b`。ZIP CRC 检查通过，但 `pptx.py inspect` 退出 2，报 `PPTX: invalid PNG/JPEG image`；原因未定位，不称读取通过。

命令均以隔离任务目录为 cwd、样本副本为相对路径，分别调用 `python -I -S -B <skill-root>/scripts/read_wps.py 文件`、`et_cli.py run 文件`、`read_docx.py 文件`、`stdlib_xlsx.py read 文件` 和 `pptx.py inspect 文件`。除 XLSX 外，以上入口的对应自检本次均退出 0 并出现成功标记；XLSX 本次未单独重跑自检。新下载样本上的创建、编辑与独立视觉验收尚未执行，ET 的独立数值对照及 PPTX 失败根因尚待核查；不能用既有合成回归替代。

### 进一步诊断与隔离写入测试

- PPTX：对上述官方文件逐项调用图片检查，`ppt/media/image1.png`、`image2.png`、`image3.png`、`image5.png` 分别在 IEND 后保留 46、15、14、7 个零字节。独立标准库逐块解析显示这些 PNG 块 CRC 均正确且均存在 IEND；现有图片检查拒绝尾随数据并由上层统一报 `invalid PNG/JPEG image`。这定位了拒绝条件，但不证明全部图片可正常解码或全部幻灯片可用。
- ET：issues 共 57 项：55 项 `non_cell_metadata_not_decoded`，1 项 `nonstandard_header_clsid`，1 项 `other_container_streams_not_decoded`。A1“项目名称”和 B1“深圳市人力资源和社会保障局值班室公共用品清洗\n服务需求明细表”对应的 UTF-16LE 字节可在原文件找到，并与来源页所列服务需求附件相符；这是有限独立字节/上下文核验，不能证明 SST 索引、单元格坐标和全部数值均正确。独立表格读取器未安装，未执行逐格对照。
- 隔离目录中，DOCX 官方样本通过 `edit_docx.py src.docx replace.json edited.docx` 将一次标题替换为测试标题，退出 0；直接检查 OOXML 中新标题存在且 ZIP CRC 正常。XLSX 官方样本通过 `stdlib_xlsx.py edit src.xlsx patch.json edited.xlsx` 修改 Sheet0!A1，退出 0；直接检查 XML 中新标题存在且 ZIP CRC 正常。这些仅核验目标内容与容器完整性，不证明所有非目标属性及视觉布局完整。
- WPS 官方样本通过 `run_wps_edit.py text src.wps edited.wps --find '835元/月' --replace '836元/月' --allow-lossy-reflow` 返回退出 3，输出已发布且回执标记校验范围；属于显式有损重排、partial，未做独立内容或视觉完整性验收，原样本保留。
- 独立新建：DOCX `run_docx_task.py create`、WPS `run_wps_task.py create`、XLSX `stdlib_xlsx.py create`、OFD `generate_ofd.py` 和 PPTX `pptx.py create` 均退出 0。DOCX/XLSX/OFD/PPTX 输出 ZIP CRC 正常；WPS 内容以 RTF 头开头。OFD 随包独立结构检查退出 0；PPTX `inspect` 回读退出 0、4 页。这是基本结构验证，非真实客户端显示验收。缺少归档配置的回执如实报告 skipped 或 handoff_required。
- ET 仅支持读取与分析；OFD 不支持编辑第三方原件；PPTX 不提供任意现有文件的编辑。所有写入均在临时目录进行，未修改原件、未运行宏；Linux 与 WPS/Word/PowerPoint 客户端视觉验收未执行。

### 银行及监管公开样本补充（Edge，macOS，Python 3.12.11）

以下 DOCX 下载后以隔离副本调用 `python -I -S -B <skill-root>/scripts/read_docx.py 文件名`，均退出 0，独立 ZIP CRC 检查通过；只核对提取标题与来源所示主题，不等于全文或视觉验收。原件不纳入提交。

- 农行：[附件](https://www.abchina.com/cn/branch/tj/CG/BM/202408/W020240826552576636302.docx)，[来源页](https://www.abchina.com/cn/branch/tj/CG/BM/202408/t20240826_2429959.htm)，SHA-256 `35933b3a8fa61b7d13fd4e022ca60bbf00f3b79fa505be37a7a97d7f7f14a1cc`；读取法人代表资格证明书。
- 建行：[附件](https://www.ccb.com/chn/attachDir/2026/07/2026072311045721102.docx)，[来源页](https://www.ccb.com/chn/2026-07/23/article_2026072310594014684.shtml)，SHA-256 `d92cc48d6774e8b2886e3d41f003286010638e592e2642966b0596945e43f7ef`；读取财政预算单位公务卡领用协议。
- 邮储：[附件](https://www.psbc.com/cn/fhpd/hddq/jssfh/zxgg_1143/202611/P020261102638151717676.docx)，SHA-256 `e2ae926c48feb64961bf0e28375d20ceab981eeb1a39c2609b87304d170fcbf6`；读取金融许可证公告。
- 中行：[附件](https://www.boc.cn/aboutboc/bi6/202004/P020200318647000619686.docx)，SHA-256 `ab9c2d19b8fde43fcd95d8c5cfbdf2b7c165da97f90a6b1994415d1d8806a0bd`；读取投标人联系信息表。
- 国家金融监督管理总局：[附件](https://www.nfra.gov.cn/chinese/docfile/2023/3811a9f5f5e7458d9aeec66c5d850d91.docx)，SHA-256 `fd9643c704b7d51e7554f396931dd62b6f87fc48064d7e0e26700e186a610ff1`；读取信用风险内部评级体系监管要求。

工行、交行及人民银行样本本轮尚未完成下载验证；以上样本本轮未做编辑及创建复核。PNG 规范核对：W3C PNG 第三版规定 IEND 后不得再有内容（https://www.w3.org/TR/png-3/），故前述尾随零是非标准数据，兼容读取须明确诊断，不能宣称标准 PNG。本轮尚未完成 PPTX 修复和回归。

### PNG 受限兼容与央行样本（2026-11-06）

PPTX 增加显式 `inspect 输入.pptx --allow-png-zero-padding`：IEND 后仅允许 1–1024 字节全零；前缀仍完整检查 CRC、分块及解压边界。发现非标准尾部时每个部件报告路径与字节数，`structural_validation=partial`、退出 3；非零、超限、损坏仍拒绝，默认 inspect、validate 与创建保持严格。深圳真实样本以兼容参数读取到 19 页，报告 4 个非标准图片；仅取得结构和文字，不代表视觉验收。Python 3.12.11/macOS 全部 PPTX 测试 91 项通过，strict 通过；包含 1/46/1024 字节、1025 字节、非零、CRC 损坏、截断、CLI 默认拒绝及 partial 回执。

人民银行 [采购公告](https://jzcg.pbc.gov.cn/freecms/site/rmyh/ggxx/info/2026/f63d2e535613475fb56c185b7b3a635b.html) 经 Edge 下载两个附件：DOCX `4aa72c34b5f1418fb2839ce6c78b9737.docx`，SHA-256 `79d35f4dab1e189c96865143e013cc0d944843528297db4a85ebfaf7ee637f35`，读取退出 0，标题为政府采购项目采购需求；XLSX `7030038e87b44e0f898e127e9fd63df5.xlsx`，SHA-256 `73f85396abaf5344a3c168d55eecc729b3cdd8f28e441469082afcf05968dd01`，读取退出 0，工作表“意见反馈表”，A1“附件2”。两个文件 ZIP CRC 正常；未完成全部内容独立对照。

农行和建行 DOCX 官方样本的副本使用 `edit_docx.py` 各替换一处标题，退出 0；检查输出 OOXML 新文字存在且 CRC 正常，未验证全部非目标内容。中行样本同样替换标题退出 1：目标文字跨 run，编辑器保护性拒绝；不称编辑通过。工商、交通样本本阶段尚未取得；银保监原机构、ET/WPS/OFD 银行与监管官方样本仍待查。未提交推送。

### 撤回的候选

用户要求不再访问或使用一份候选 OFD；该次结果已撤回，不作为验收依据。

### 交通银行及既有工行样本复验

- [交通银行采购结果公告](https://www.bankcomm.com/BankCommSite/shtml/jyjr/cn/7804/2600473/2600509/2631875.shtml?channelId=7804) 的官网附件通过 Edge 下载，DOCX 41115 字节，SHA-256 `c6f7171ef795e0b3867c2379d81ba9a3a88bfbd93eb4a8d70d8b12d5a47f6deb`。隔离副本经 `read_docx.py` 读取退出 0、ZIP CRC 正常，抽查包含官网所列 596.33 和 650；未做全文逐项核验。以 `edit_docx.py` 替换标题退出 1，诊断为目标跨 run，按既有保护规则拒绝，未生成编辑结果。
- 此前已合法取得的工行 WPS 价格表原样本（本轮未重新下载）：SHA-256 `64e7b718ce257ca594db3ff1d17d8a753bcb28d382f184895ab761d6c782dbf9`。以当前 `read_wps.py` 读取隔离副本退出 3、partial，输出设备、型号及价格等文字；诊断表格结构不完整及非标准容器头 CLSID，未作逐格独立对照。工行样本仅本地测试，未经许可不再分发。

## 当前覆盖与验证结论

- 工行：此前合法取得的 WPS，9 行已恢复、未知合并/布局，partial；非标准头 CLSID 已诊断。行界可读不证明网格、合并或样式准确；目前缺少足以安全补全这些语义的独立预期，不移除 partial 或猜测网格。
- 农行、建行：各一份官网 DOCX 读取及副本定点编辑通过；中行、交行：各一份官网 DOCX 读取通过，所选标题跨 run 编辑保护性拒绝；邮储：官网 DOCX 读取通过。只核对指定文字/价格与 ZIP CRC，不代表全文、非目标内容及视觉全部正确。
- 人民银行：官网 DOCX、XLSX 读取通过，有限核对标题、工作表和单元格；国家金融监督管理总局：官网 DOCX 读取通过。原银保监会独立官网样本、六大行与监管官网的 ET/OFD 本轮未完成覆盖。
- 深圳政府 ET：Sheet1 78 个保存单元格、Sheet2/3 空，partial 来自 55 条非单元格元数据、非标准 CLSID 和其他流；输出声称已声明工作表内的值覆盖，但独立证据仅抽查两项文本的原始字节，尚不能证明逐格坐标正确。忽略非单元格数据或私有流不能提升为完整格式支持。
- 黄石政府 OFD：partial 包含 116 个未解释字形映射、4 个矢量对象、2 张图像、2 个外来命名空间及 1 项仅元数据；已抽查四个正文数值与网页一致，图像、字形映射及矢量缺少独立解释，保留 partial。
- 新建：DOCX、WPS（RTF 内容）、XLSX、OFD、PPTX 各有隔离基础创建和结构检查；WPS/DOCX/XLSX 有有限编辑验证，ET 无写入，OFD 不编辑第三方文件，PPTX 不支持任意现有文件编辑。实际客户端视觉与目标 Linux 容器均未验收。
- 修改范围仅 PPTX 的显式非标准 PNG 零填充检查及文档版本；本轮 Python 3.12.11/macOS 复跑 PPTX 91 测试通过，strict 检查 PPTX、OFD 通过；WPS 和 ET strict 因既存 `scripts/__pycache__` 文件告警而失败，未删除未知来源缓存。`git diff --check` 通过。历史其他技能测试数是此前基线，不称本轮重跑。未提交推送。

## WPS 初始单元格属性增强与剩余限制

依据微软 MS-DOC 的 TDefTableOperand、TC80、TCGRF，本轮在 `initial_definition.initial_cell_properties` 返回完整 20 字节 TC80 记录中的原始 TCGRF、初始横/纵向合并位和首选宽度原值，记录缺少完整 TC80 的列数和残余字节；不改变最终网格未知、partial 和安全拒绝。工行价格表复验仍为 9 行、退出 3；首行十个 TC80 初始合并位均为零，但每行有 28–33 个后续未解释表格修饰符，因此不能据此断定最终不合并或页面布局。独立的最终布局/客户端对照未进行。

新增合成测试覆盖初始合并值、短记录和超额记录。macOS/Python 3.12.11 下 WPS 全量 436 项通过；原工作树中已有缓存会使 strict 警告，在不复制缓存的临时源码快照中 strict 通过，原缓存保留。`git diff --check` 通过。深圳 ET 的 78 个单元格仍未通过独立逐格坐标和值核验；黄石 OFD 中的字形映射、图像与矢量仍未解释，保持 partial，没有扩大解析或弱化限制。未访问撤回或受限的 OFD。

## ET、OFD 及 WPS 深入核验

- 深圳政府 ET：用独立 Python `struct` BIFF 记录步行实现解析单段 SST 及 LABELSST、RK、MULRK、MULBLANK，与现有读取器逐坐标比对：78/78 一致（30 文本、21 数字、27 空白）；该方法共用 Skill 的 CFB 流提取，数字对比使用 Python 数值，不能声称独立验证 CFB 分配或全格式精度。其 partial 原因仍为元数据/其他流/非标准头；未删除诊断。
- 黄石政府 OFD：独立 ZIP/XML 遍历确认 116 个 TextObject 的 CGTransform/Glyphs；两页共两个 ImageObject 均指向资源 4，四个 PathObject 含 AbbreviatedData。增加页面对象 `storage_node` 和 TextObject `glyph_transform_nodes` 指向结构目录中的原始 XML 节点；真实样本 116/116 索引指向正确源节点和父对象。未解释字形到 Unicode 的映射、图片内容或矢量几何，不把节点关联说成渲染或 OCR，仍为 partial。
- 工行 WPS：增加每行有界的未解释表格修饰符 ID/次数。样本共 9 行，`0xd635` 64 次、`0xd634` 36 次；MS-DOC 分别定义为单元格首选宽度和默认内边距（https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-doc/b39a6648-501c-4361-8366-4f042f579469）。目前未解析其操作数及样式级联，不能据此推断最终宽度、合并或页面布局，保持 partial。
- 新增合成回归验证 OFD 的真实结构索引、partial 状态和 WPS 修饰符计数；macOS/Python 3.12.11 下 OFD 150 项、WPS 436 项通过。OFD strict 通过；WPS 排除既有缓存的隔离源码快照 strict 通过。两个 Skill 单独复制到临时目录后自检通过；不代表 Linux、客户端视觉或独立包全部任务均已验证。保留所有已有改动，无提交推送。

## 局部表格属性与字形障碍核验

WPS `local_width_operands` 在有界定界后按 MS-DOC 解析 0xD635（5 字节、单元格范围/单位/首选宽度）及 0xD634（6 字节、范围/边掩码/单位/内边距），保留流偏移、原始字节及未支持/无效状态，每行最多 128 项并报告超出数；不应用样式层叠，不把局部值当最终布局。工行价格表 64 条 0xD635 和 36 条 0xD634 均可解析，如首行前四条内边距分别作用于四边、单位 twips、原值 15；原文件仍为 partial、9 行。无效、短记录、越界及合成 PAPX 集成测试通过。macOS/Python 3.12.11 WPS 全量 437 项通过，排除既有缓存的临时快照 strict 通过；工作树缓存未删除。

黄石 OFD 三个内嵌字体的 sfnt 表目录均含 glyf 等表，但均无 cmap 表；且实际 CGTransform 映射 116 处。现有文字已取自 TextCode，字形 ID 到 Unicode 不具备可可靠反向映射的 cmap；当前生成器字体检查也因缺 OS/2 拒绝将这些子集字体用于生成。未据 glyph ID 反猜文字，不将部分字体/图形声明为完整。图像仍需 OCR 才可能获得图片中文字，矢量轮廓不能直接当自然语言。

ET 的 55 条聚合元数据问题涵盖 XFExt、StyleExt、Theme、打印/页面与计算属性等；其中 XFExt 44 条、StyleExt 42 条，且另有原始私有 ET 扩展流未解码。78 个保存单元格的此前独立 BIFF 对照不验证这些显示、打印或私有语义；现有结果已保留原始记录分类与来源，未将其假装已应用或为消除 partial 删除诊断。本轮未改 ET 或 OFD 解析行为。

## 修改后使用 Skill 公开入口回读（macOS / Python 3.12.11）

所有输入在独立临时任务目录内复制；源文件未改。六类 Skill 的相应读取/创建环境自检均退出 0 并返回成功标记，未进行客户端视觉操作。Linux 容器工具 docker、podman、limactl 在本机未发现，目标 Linux 环境本次未验证。

| Skill / 来源 | 操作及公开回读 | 实际结果与独立核对 |
|---|---|---|
| WPS / 黄石政府 WPS，来源和 SHA 见上 | `run_wps_edit.py text before.wps after.wps --find '835元/月' --replace '836元/月' --allow-lossy-reflow`；`read_wps.py after.wps` | 编辑退出 3，源 partial 且明确有损重排；回读退出 0 含 836 及未改的 770，源文件 SHA 不变。独立文本差异显示插入有损副本警示、删除若干换行；段落由 29 变 27，不能称原格式、分段或表格保真。回读 ok 不抵消编辑源 partial。 |
| DOCX / 重庆司法局申请表 | `edit_docx.py before.docx replace.json after.docx` 将一次标题追加“（测试）”；`read_docx.py after.docx` | 编辑和回读均退出 0，目标标题及“申请日期”可见；独立比较 document.xml 所有 w:t 拼接，仅为目标一次替换。该检查不证明所有非文字内容及视觉保真。 |
| XLSX / 南京统计局预算表 | `stdlib_xlsx.py edit before.xlsx patch.json after.xlsx` 修改 Sheet0!A1；`stdlib_xlsx.py read after.xlsx` | 编辑和回读均退出 0，A1 为测试标题；所有工作表名称和其他单元格读取对象一致。独立 ZIP 比较只有 `xl/worksheets/sheet1.xml` 成员字节变化，其他成员不变、CRC 通过；未验视觉与重算等客户端行为。 |
| ET / 深圳人社服务需求表 | `et_cli.py run source.et` | 退出 3、partial，A1 为“项目名称”；该 Skill 不支持编辑或创建 ET，未将其他格式改后缀冒充 ET。此前 78 格 BIFF 对照的独立性限制见上。 |
| OFD / 按黄石政府文件数值构造新模型 | `generate_ofd.py ofd.json derived.ofd`，原 835 改为测试值 836，保留另一数值 770；`read_ofd.py derived.ofd` | 新建退出 0，回读退出 3、partial，两个测试文字均读取；`check_generated_ofd.py` 独立结构检查退出 0。是根据真实来源构造的新文件，不是对原 OFD 的编辑或保真改写；字形/图形与视觉仍未完整解释。 |
| PPTX / 根据深圳政府电梯培训文本构造新幻灯片 | `pptx.py create ppt.json derived.pptx` 将标题加“（测试）”，保留“拆除前准备”；`pptx.py inspect derived.pptx` | 新建、回读均退出 0；独立检查 slide1.xml 包含两条文字。是重新构造单页，不是编辑原 19 页演示稿；未验布局及桌面客户端。 |

本节为真实来源副本及新建文件的实际 CLI 操作，不是客户端编辑或完整保真验收。未访问已撤回或受限 OFD；未提交、推送。

## ET `wps` 前缀文件盘点

本轮静态分析 `skills/et/scripts/` 的 26 个 `wps*.py`：25 个被其他生产模块以普通或函数内导入引用；唯一未被生产模块静态导入的 `wps_biff8_audit.py` 是 `references/biff8-stored-audit.md` 和 `advanced-spreadsheet-routing.md` 明确提供的独立审计 CLI，其 `wps_biff8_audit_sources.py` 是它的依赖。分别覆盖 CFB/BIFF8、OOXML、数字与日期、格式、批注、元数据和结果等实际能力；前缀保留历史命名，不代表调用相邻 WPS Skill。未发现能证明无用的源码，未删除。既存 `__pycache__` 为未知来源缓存，未删除。

macOS/Python 3.12.11：ET 453 项测试通过；将 ET 独立复制到无相邻 Skill 的临时目录并排除既存缓存，自检退出 0，深圳真实 ET 读取返回预期 partial/3，该快照 strict 通过。此验证不覆盖 Linux/Windows 和每种格式，当前 ET 源码未修改。
