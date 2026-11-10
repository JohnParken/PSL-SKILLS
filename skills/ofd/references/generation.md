# 基础 OFD 生成：fixed-layout-1

维护资源说明：文中 `docs/maintenance/ofd/` 与 `skills/ofd/scripts/test/` 均以宿主确认的仓库根目录为基准，仅供源码维护，不随运行包分发；运行任务不读取或执行。没有仓库时跳过维护资料，不按 Skill 安装目录拼接。

路径占位符说明：`<skill-root>` 是 OFD Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 OFD 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

只在用户明确指定 OFD 格式并请求新建时使用；未指定格式的“生成文档”请求不默认选择 OFD，明确Word/PDF时按对应流程处理。不把读取任务或第三方 OFD 修改任务改成重新生成。写入逻辑位于独立模块；安全输入与环境核对复用本包读取入口的只读原语，结构检查器不导入生成器或读取器。读取代码与 CLI 保留。当前版本只生成 OFD 1.0 的一个固定版式子集；读取1.1/1.2不表示生成这两个版本。

## 环境、定位和自检

在harness确认的任务工作目录执行；任务JSON文件（如使用）、自备字体、许可证与图片均为该目录内明确指定的相对输入；显式bundled字体只读取本Skill固定且校验哈希的内部资源，拒绝任何层级符号链接、父目录与绝对数据路径。不扫描系统字体，不自动下载、安装或转换资源。使用Python >=3.12.11、自带zlib和Expat >=2.6.0；零第三方Python包与外部软件运行依赖，保留 `-I -S -B`。安全发布需dir_fd、O_NOFOLLOW、O_DIRECTORY及描述符相对、不跟随链接的link/unlink/stat，缺失时停止。

```python
execute(command="python -I -S -B \"<skill-root>/scripts/generate_ofd.py\" --check")
execute(command="python -I -S -B \"<skill-root>/scripts/generate_ofd.py\" --spec-stdin 'new-document.ofd' <<'OFD_SPEC'\n{\"schema_version\":\"ofd-generate-1\",\"pages\":[{\"width\":210,\"height\":297,\"items\":[{\"kind\":\"rectangle\",\"x\":20,\"y\":20,\"width\":60,\"height\":30}]}]}\nOFD_SPEC\n")
```

以上分别为自检和一次生成，须先确认自检成功。将占位符及输出名替换为已确认的实际值并按 Shell 和工具字符串两层正确编码；quoted heredoc 不展开正文中的 `$()`、反引号和变量，结束标记须独占一行且不与正文整行冲突。简单 JSON 完整闭合、不能截断；复杂规格先用 Python 字典/列表和 `json.dumps(ensure_ascii=False, allow_nan=False)` 序列化，再通过 `subprocess.run([sys.executable, "-I", "-S", "-B", script, "--spec-stdin", output], input=payload, check=False)` 传入 UTF-8 字节，透传 stdout/stderr 和真实退出码，不使用 `shell=True` 或把用户文本当代码。若由动态值构造外层 Shell 命令，逐参数使用 `shlex.quote`，不可直接插值。不能提交完整非空 command 或可靠 stdin 时停止并报告，不猜工具字段。读取或生成不会因工具返回 success 自动成功，按实际退出、截断和 JSON 回执判断。

已有规格文件的旧调用继续可用：`execute(command="python -I -S -B \"<skill-root>/scripts/generate_ofd.py\" './layout.json' 'new-document.ofd'")`。`--spec-stdin` 后仅接一个输出裸文件名，stdin 最多 256 KiB UTF-8，超限、无效 UTF-8、空值、重复键或无效 JSON 在发布前失败，不生成临时 JSON 文件。`-` 不单独表示 stdin，保留原文件路径语义。独立检查仅在需要时执行：`execute(command="python -I -S -B \"<skill-root>/scripts/check_generated_ofd.py\" './new-document.ofd'")`。

先核对自检退出0、JSON status=environment-ok、artifact_validation=not_run；write_probe=not_run明确没有证明目录可写。生成过程实际独占创建并登记一个任务根目录私有候选，写入、fsync、读回比较与结构验证成功后，才使用硬链接原子建立全新的输出名；失败不覆盖已有名字，也不退回replace/rename覆盖方案。候选写入是本次实际写入能力检查。输出只接收新的 `.ofd` 裸文件名；`./文件名`、子目录、绝对/父目录及 Windows 路径均拒绝，不截取 basename 改写参数；不会创建输出子目录。调用前确认不是技能源码目录。

## 复杂规格的完整调用示例

以下是替代上方生成命令的一次调用，不要两次执行。示例数据仅展示结构；用完整、可信的 Python 数据对象承载实际值，不能将用户文本未经安全编码拼进 Python 源码或 Shell。脚本路径和输出名经 Shell 参数引用，包含特殊字符的动态参数须用 `shlex.quote`；不能仅替换双引号中的原文。模型/宿主需按工具 schema 序列化外层字符串，核对结束行与完整命令；此调用不创建临时脚本或规格文件。

```python
execute(command="python -I -S -B - \"<skill-root>\" \"复杂示例.ofd\" <<'PY_SPEC'\nimport json\nfrom pathlib import Path\nimport subprocess\nimport sys\n\nroot = Path(sys.argv[1])\noutput = sys.argv[2]\nspec = {\n    \"schema_version\": \"ofd-generate-1\",\n    \"title\": '中文 \"引号\" 和字面文本 $(echo 未执行)',\n    \"pages\": [{\"width\": 210, \"height\": 297, \"items\": [\n        {\"kind\": \"rectangle\", \"x\": 20, \"y\": 20, \"width\": 60, \"height\": 30}\n    ]}]\n}\ntry:\n    payload = json.dumps(spec, ensure_ascii=False, allow_nan=False).encode(\"utf-8\")\nexcept (TypeError, ValueError, OverflowError):\n    print(\"spec_serialization_failed\", file=sys.stderr)\n    raise SystemExit(2)\ncommand = [sys.executable, \"-I\", \"-S\", \"-B\", str(root / \"scripts/generate_ofd.py\"), \"--spec-stdin\", output]\ntry:\n    result = subprocess.run(command, input=payload, check=False)\nexcept OSError:\n    print(\"generator_not_started\", file=sys.stderr)\n    raise SystemExit(1)\nraise SystemExit(result.returncode)\nPY_SPEC\n")
```

子进程不捕获或合并输出，返回的进程退出码直接传给外层；工具仍可能合流、截断或隐藏状态，必须依据实际回传判断，不能认为 `print` 或此模板保证回传完整。仅在明确未启动时修正参数；状态不明或目标可能已发布时先核实，勿盲目重跑。

## 输入模型

顶层严格接受schema_version、title、fonts、images、pages；必需schema_version="ofd-generate-1"与非空pages。未知字段、重复JSON键、NaN/Infinity、布尔数值、越界或缺少字段均失败，不忽略用户指定的样式。最小不依赖字体的模型：

```json
{
  "schema_version": "ofd-generate-1",
  "title": "线框示例",
  "pages": [{
    "width": 210, "height": 297, "margins": [10, 10, 10, 10],
    "items": [{"kind": "rectangle", "x": 20, "y": 20, "width": 60, "height": 30}]
  }]
}
```

所有几何单位为毫米。页面width/height为20..2000；margins是[left,top,right,bottom]，默认各10，不能吃掉页面。items按照输入顺序生成；不自动重排、分页或避免用户主动安排的重叠。各页可使用不同尺寸和边距。

| kind | 字段与含义 |
|---|---|
| text | x为首字形原点，y为基线；font引用fonts键；size_mm为0.5..50毫米；text是单行字符串。按已核验cmap给出CGTransform字形ID和hmtx字宽DeltaX，不做塑形、字偶距、自动换行或缩放。 |
| line | x1,y1,x2,y2为端点；stroke_mm默认0.2，范围0.01..5；零长度拒绝。 |
| rectangle | x,y,width,height为线框中心线的外接矩形；stroke_mm同上。无填充，只使用黑色基础绘制。 |
| image | ref引用images键；x,y,width,height为放置框；保持原始像素比例居中contain，不裁剪、不重采样或拉伸。 |
| table | x,y,widths列宽数组、row_height、rows二维字符串数组、font、size_mm；padding_mm默认1，stroke_mm默认0.2；align为left/center/right或逐列数组，valign为top/middle/bottom，默认left/top。仅矩形网格和单行单元格，无合并、跨页、自动列宽、公式或编辑表格语义。 |

线宽、全字体声明包围盒与文字advance均计入边界检查。超出页内容框或单元格即失败，绝不截断、缩小或添加省略号。对齐按声明的保守文字包围盒在单元格内定位，不是复杂塑形或实际像素边缘对齐；padding仍保留，空单元格不生成伪文字。全字体包围盒有意保守，可能拒绝实际上可排下的文本；不冒称精确轮廓或可见性证明。空单元格保留记录而不生成空TextObject；普通文字项不接受空字符串。网格、单元格文本与对象ID的明确绑定保存在结果tables中，是输入模型的实现证明，不是从版面猜测或通用OFD表格标准。

fonts是字典。内置示例为 `{"example":{"bundled":"zhuque-fangsong-v0.212"}}`，来源与完整许可见 `<skill-root>/references/bundled-fonts.md`。普通自备字体最小输入为 `{"main":{"path":"./font.ttf"}}`：将字体放到任务目录后明确此路径即可，不扫描其他目录、不自动发现或回退。字体必须是可读普通文件，不能位于目录外或经符号链接访问。

自备字体的license_path及embedding_permission均不再必填；兼容旧输入，若提供license_path则仍验证UTF-8及大小并完整保留，若提供embedding_permission则只接受旧值confirmed但不把它当法律认证。未提供许可时JSON的permission明确not assessed、license_sha256=null，OFD写入FontRights:not_assessed元数据，不伪造许可证或授权。提供字体文件不等于自动获得嵌入或分发授权；生成器不执行法律许可审查。内置朱雀的两份完整许可、版权及预览声明继续随包保留并嵌入，不因自备字体简化而删除。字体原字节完整嵌入，不安装、不子集化或改名字体内部名称。


只接受独立、静态TrueType/glyf字体，Unicode cmap4/12、hmtx/hhea/head/maxp/loca、OS/2及可解释PostScript名称。只允许fsType=0、8、0x0100或0x0108；8表示Editable embedding，非限制嵌入；含0x0100者要求完整嵌入，生成器始终保留原始字体全部字节并报告embedding_mode=full_font_unmodified，不通过改写权限位取得成功。拒绝TTC、CFF、变量/彩色/SVG字体、限制/预览打印/仅位图及未知权限位组合。对每个字符核对非零、未越界的映射和advance；非空白字符还需有声明范围内的非空字形包围盒。允许ASCII、BMP汉字和受限中西文标点、全角字符；拒绝组合符、控制字符、双向/阿拉伯文、emoji、补充平面及需要塑形的其他文字。即使有cmap，也不能证明字体绘出的图案与语言意义一致；字体应来自可信、已获准使用的来源。字体程序不执行、不栅格化，也不宣称完成字体安全净化。

images是字典，每项只含path。PNG限非交错8-bit灰度/RGB及可选alpha，验证全部块CRC、受限解压后的扫描线长度和滤镜编号；不做视觉解码。拒绝调色板、APNG、EXIF及不支持的关键块。JPEG限单扫描8-bit灰度/三分量baseline，有基础量化/Huffman表和扫描头；拒绝progressive、APP1/EXIF/XMP、算术编码及其他扩展。标准库不含JPEG像素解码器，因此只检查标记、尺寸和扫描轮廓，结果images[].validation明确说明未解码熵数据，不承诺任何通过头部检查的JPEG都可显示。需要像素级或视觉保证而无独立客户端时，该项验收未完成。图片原字节直接嵌入，不净化未知元数据。

## 输出与验收契约

候选验收之后，发布成功还须重新打开最终文件核对身份、长度、字节及读取期间变化。回执分别给出 `output_written`、`publication_target`、`publication_outcome`、`publication_verification`、`cleanup_complete`、`cleanup_issues`；原有generated/failed和退出0/1不变。`published_verification_failed` 表示已写出但核验失败，保留目标供检查，不交付为合格文件、不自动删除或覆盖；`published_verified` 但清理失败仍返回failed/1，主文件保留并如实报告清理未完成。`publication_uncertain`／`output_written=null` 时先检查，不盲目重试。核验只证明检查时的状态，不能防御同权限进程任意后续修改；仍需宿主隔离。

成功stdout为一个UTF-8 JSON、退出0、schema_version="ofd-generation-result-1"、status="generated"；只表示新文件已发布且通过本生成子集结构检查。报告含output、sha256、bytes、doc_id、max_unit_id、pages、fonts、images、tables、structural_validation，以及visual_validation和independent_client_validation。后两项默认not_performed，生成成功不能覆盖它们。字体权利状态、图片检查范围和缺口随报告保留。宿主保存需要交付的报告时使用独占新文件，不覆盖原件。

失败stdout为同schema、status="failed"、退出1、stage和diagnostics；参数usage退出2写stderr。不返回伪造的空文件成功。publication_outcome为not_attempted或not_published_existing_name时没有本次最终文件；inspect_before_retry表示应先核查目标实际存在性与身份，不能自动重跑或删除可能已经成功发布的文件。原子建立名字不等于断电持久性或针对特权并发写者的完整沙箱。正常异常会核对候选inode再删除，仅处理本次登记项；强杀、权限变更或身份被替换时可能留候选，按原记录报告cleanup_incomplete，不扫描历史文件。

`<skill-root>/scripts/check_generated_ofd.py`是独立实现的标准库ZIP/XML子集检查工具，不导入生成器或读取器；它检查DocRoot/Page/资源引用、全局ID与MaxUnitID、页面/对象边界、文字/字形计数及许可证保留。它不验证整个XSD、字体绘制结果或OFD可打开性。常规调用已在私有候选上自动执行，并逐页比较输入的原文字序列、表格行列地址、文字对象绑定和单元格内容边界；需要独立核对、排错或维护验收时才单独调用，不为省略诊断重复读取所有结构。现有读取器可另外回读检查原文字与页序；CGTransform、图片和线框仍如实partial，不为生成器放宽读取判定。

有业务文件交付时，先核对实际新文件、哈希、文字、页序、表格绑定及全部未验证项；用户要求视觉一致时还需可用OFD客户端验收，当前无此保证。之后由宿主按已确认公共生命周期流程归档与清理；本包不会猜公共包位置、复制归档脚本、自动联网或上传。取得真实归档结果后，归档成功即提示“🎉 文件归档成功，请到云空间查看。 ☁️✨”，无需等待宿主云空间映射确认，不宣称已验证上传完成或展示时效。失败、部分成功、跳过、待交接和未知分别如实报告，不套用全部成功；业务与清理状态独立，主业务失败不称生成成功。隐藏归档路径、目录、ID及原始日志，保留真实主文件链接，不虚构下载。若公共归档能力不可用，保留已验收主文件并如实报告归档未完成，不虚构云空间或下载链接。输入与最终文件受保护；候选由生成函数登记和收尾，不重复认领或删除。

## 规范依据与当前证据

结构以GB/T 33190-2016的OFD/DocBody/DocInfo/DocRoot、Document/CommonData/MaxUnitID/PageArea/DocumentRes/Pages、Page/Area/Content/Layer、Res/Font/FontFile/MultiMedia/MediaFile及文字/路径/图像基本模型为目标。标准身份及已核对条款见 `<skill-root>/references/validation.md`；没有完成正式XSD逐条验证，不能称完整国标符合性。字体字段按Microsoft OpenType官方head、cmap、hmtx、OS/2文档核对：`https://learn.microsoft.com/en-us/typography/opentype/spec/hmtx`、`https://learn.microsoft.com/en-us/typography/opentype/spec/os2`。不以第三方草案代替正式标准。

2026-11-03在macOS/Python3.12.11/Expat2.6.3进行标准库单元与负例测试。真实字体验收使用Google Fonts官方MaShanZheng-Regular.ttf原件及完整OFL，SHA-256分别为6d2546bb189c732a8ca29af9e22457b152387d158aa459e4ac2ce1e51788b7fb和d7bdb1cee215b689e23c2f95672a6084c790542170648267a55114103d756a08；该历史字体及图片不随技能包提供。两页生成样例有固定独立中文预期、PNG/JPEG和2×2网格；独立维护工具使用本机已有JDK24的ZIP/XML、TrueType GlyphVector与ImageIO，核对28字符字形映射、22项间距并实际解码两张图片。该JDK仅为维护交叉验收工具，非Python技能运行依赖，未安装新软件；它没有渲染OFD页面。维护工具源位于仓库tests/OfdGenerationCrossCheck.java，完整来源、哈希、退出码及剩余范围在tests/ofd_generation_validation_20261103.json，不是技能内部路径。

合成字体只用于边界/契约测试，不作为真实中文字形证据；真实样例的原始输入在运行目标前冻结预期，未使用任何先前拒绝的政务文件。未完成Linux、Windows、独立OFD客户端或视觉验收；不能据本机结构、JDK字体/图片交叉检查称“所有客户端可打开、任意中文正确排版”。此为历史验证，不代表当前分发状态。

后续增量：表格可显式水平/垂直对齐，逐列设置水平模式；默认left/top保持原位置。结构验收逐单元格核对请求的对齐位置，不静默忽略未知模式。No subsetting字体兼容依据上述OpenType官方OS/2第8位；仅在原始全字体嵌入时放行0x0100，不要求自备字体提交许可材料，法律授权不由技术旗标证明。对齐包含真实中文字体与合成边界验证；非零No subsetting旗标的新增兼容仅有合成回归，没有真实旗标字体或独立阅读器视觉验收。

当前单字体上限10 MiB（原8 MiB）；图片仍8 MiB、总输入资产24 MiB、产物32 MiB。完整朱雀仿宋原件为8,824,084字节，保持完整而非裁剪；固定资源有长度和SHA-256校验，失败停止。2026-11-04在macOS/Python3.12.11使用该原版生成两页84字符并逐页回读一致，独立JDK核对74项字宽和字形；不等于OFD页面视觉验收。当前分发包含该字体及两份完整OFL许可。

## Skill 内的自备字体

仅部署前管理可预置字体于 `<skill-root>/assets/user_fonts/`，之后配置 `{"main":{"skill_font":"MyFont.ttf"}}`。只接受单个ASCII文件名及.ttf扩展名，不接受路径、子目录、符号链接或与bundled/path混用；按名称精确读取，不遍历寻找字体。字体目录基于本次真实Skill位置，不是任务目录。运行只读，不复制、移动或修改字体；安装目录不可写时使用原任务目录path流程，勿降级权限保护。读取OFD仍不依赖此目录。

普通任务不向 Skill 目录写入，使用任务目录内明确的 `path`；此目录仅供部署前管理预置私人字体，不要求许可证文件或逐次确认，法律授权状态仍为not assessed；fsType、静态glyf、大小与缺字检查不变。字体文件不会自动进入git：仓库根.gitignore仅忽略本目录用户文件，保留NOTICE.txt；不要强制git add。分发必须使用仓库根维护入口 tools/package_skills.py 或兼容入口 `tools/package_ofd.py`，它排除整个私人fonts目录中的用户文件，仅保留NOTICE.txt；不能用递归压缩整个Skill的方法发布。git忽略不等于通用ZIP工具会排除，手工复制安装目录仍可能泄露字体。内置朱雀位于assets而非私人目录，完整原版及许可继续分发。

维护时在宿主确认的仓库根目录运行 `python -I -S -B "tools/package_ofd.py" ./new-ofd.zip`，目标必须为新文件，禁止覆盖既有包；不自动上传或提交。文件放入Skill私人目录后不必每次再复制到任务目录，但搬迁或升级安装时需自行保留私人文件，不将其混入公共发行包。
