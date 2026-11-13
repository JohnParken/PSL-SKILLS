# 统一结果、失败契约与读取范围

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

失败JSON新增diagnostic_context，辅助区分CFB容器与负载路由层。原status、location、message和退出码不变。

## 目录

- 入口与失败行为 (`<skill-root>/references/reading-assessment.md#入口与失败行为`)
- 结果字段与范围评估 (`<skill-root>/references/reading-assessment.md#结果字段与范围评估`)
- 最大化读取模式 (`<skill-root>/references/reading-assessment.md#最大化读取模式`)
- MS-DOC 段落 CP 来源（可选） (`<skill-root>/references/reading-assessment.md#ms-doc-段落-cp-来源可选`)
- MS-DOC 段落 CHPX/PAPX 原始证据关联 (`<skill-root>/references/reading-assessment.md#ms-doc-段落-chpxpapx-原始证据关联`)
- 可运行的契约示例 (`<skill-root>/references/reading-assessment.md#可运行的契约示例`)

## 入口与失败行为

先完成 环境自检 (`<skill-root>/SKILL.md#执行环境约束与首步自检`)。下表针对统一入口 `<skill-root>/scripts/read_wps.py`，低层流解析器默认仍 strict；文件入口默认 data。

| 入口 | 正常返回 | 读取失败与异常 |
|---|---|---|
| Python `read_bytes(data, **options)` | 返回结果字典；成功、部分结果及已由格式分支转成字典的失败都会附加 `reading_assessment`。 | 不捕获统一预检或二进制解析抛出的 `ParseError`；未知容器即会抛出，异常有 `status`、`location`。不能假定所有失败都是字典，需捕获异常并检查已返回字典的 `status`。 |
| Python `read(path, **options)` | 读取普通文件后调用 `read_bytes`，返回结果字典。 | 捕获 `ParseError`、`OSError`，返回失败字典及 `reading_assessment`；不保证捕获编程错误或所有 Python 异常。 |
| CLI 默认 JSON | stdout 为一个完整 JSON；ok 退出 0、partial 退出 3。 | 读取失败输出失败 JSON，退出 1；包括分支不适用的策略。未知参数、无效枚举、缺输入及 `--include-related --format text` 等参数解析错误退出 2，stderr 为用法/错误文本，无结果 JSON。 |
| CLI `--format text` | stdout 为正文；RTF 全文按块顺序包含表格行，单元格以制表符分隔；partial 退出 3，stderr 另给 status/issues 摘要。 | 读取失败的 JSON 写 stderr，stdout 无正文，退出 1。电子表格不能输出 text。 |

读取失败的 `status` 包括 `unsupported`、`encrypted`、`corrupt`、`resource_limit`、`io_error`、`invalid_or_unsupported`。失败正文为 `text=null`、`partial=false`，不交付部分单元格或文字；`invalid_or_unsupported` 不应被调用方擅自归类为必然损坏。参数解析错误不是上述失败字典。

CLI 默认完整输出阶段还有两个例外：电子表格请求 text 时返回 unsupported；JSON 超过输出预算时返回 resource_limit。这两处重新生成的失败字典没有 `reading_assessment`。显式有界视图另用 `wps_read_view_v1`，输出投影失败可为 `output_view_error`/退出1且不带该摘要；其完整契约见有界输出（`<skill-root>/references/bounded-reading.md`）。CLI 参数错误、自检输出和纯文本正文也不提供该字段；调用方必须先判断退出码、输出模式和字段是否存在。

## 结果字段与范围评估

文档结果有 `format`、`text`、`paragraphs` 和范围/诊断；OOXML 文档可另有 `tables`，显式附加文字另有 `related_stories`。二进制段落和 TAB 不代表完整表格或页面还原；另有 `binary_table_structure` 的有限一级行/单元格证据，见 二进制表格 (`<skill-root>/references/binary-tables.md`)。电子表格（`.et/.ett`）已迁移至专用的 `et` 技能。

`reading_assessment.schema_version` 为 `wps_reading_assessment_v1`，它是附加摘要，不替换正文、单元格或原始 `issues`、`diagnostics`：

- `outcome` 对应原始 `status`；`ok` 的 `completeness=within_declared_scope`，不代表完整格式、页面或生产软件认证。
- `partial` 的 completeness 为 partial；返回失败字典的为 `no_content_delivered`。字段排除或隐藏策略继续由原始诊断解释，不统一改成损坏。
- `events` 汇总原有诊断的 code、来源字段和已记录位置；缺失偏移不推测。
- `coverage` 报告 related_content、binary_tables、objects、适用的 binary_images 及显式遗漏代码。状态取决于已记录证据，`unknown` 不表示不存在。每类最多保留 20 个位置并记录截断数。`evidence_count` 统计证据条目。最大模式已恢复二进制表格行时使用 `partially_extracted`，仍需查看表格分组和合并的实际范围。
- `events` 也收集附加故事和工作表的嵌套诊断；最多保留 1000 项，超出数见 `events_truncated`。原始诊断仍是完整依据。
- `omitted_regions` 是 MS-DOC 未请求的非空附加故事范围，不含文字；遇到这些区域时顶层 partial/退出 3。
- `regions` 仅汇总附加区域类型、状态、来源、空状态与引用状态，不复制正文或被排除文字。
- `visibility` 按部件统计已记录 run 的 action/state，保留策略；精确路径见原 `visibility.runs`。计数不是字符覆盖率，未记录 run 不推断可见性。
- `full_format_verified` 始终 false。原始诊断仍是完整依据，摘要不是隐私或安全认证。
- 已有绘图锚点未渲染 `drawn_shape_anchor_not_rendered` 与 `picture_or_data_marker_not_decoded` 同属 `coverage.objects.state=not_decoded`；摘要按已有对象来源位置/CP 计数，允许保留 `shape_index`、`spa_record_offset`、`plcf_offset` 等受控定位字段，不读取、复制或展示对象正文。嵌套附加故事中出现的对象诊断同样参与覆盖统计；这些是未解码证据，不能视为对象渲染能力。

本轮二进制主正文新增保守绘图标记：`--object-policy placeholder` 可以在 `PlcfSpa` CP/存储字符全部经过校验的情况下，将 `U+0008` 替换成 `U+FFFC` 并返回 `drawn_shape_anchor_not_rendered`，保留 CP、WordDocument 偏移与 SPA 源偏移；默认 reject 不变。该诊断不是图像、文字、字体或绘制结果的还原。具体边界见 绘图锚点（`<skill-root>/references/binary-shapes.md`）。

CFB 末扇区的 `cfb_missing_final_sector_padding` 属于兼容诊断。仅当所有声明的流字节和分配元数据实际存在时，data 策略才会在内存补齐未声明的填充，并保留 partial。声明的数据或扇区实际缺失时读取失败。失败原因可见 `cfb_unaligned_physical_length` 或 `cfb_fat_unbacked_allocation`。

内部格式解析器、历史低层 CLI 可能没有此摘要或使用不同状态/退出码。合成回归验证范围限定、部分区域定位和隐藏文字不进入摘要，不构成真实文件语义对照。

选中 `object_policy=placeholder` 或 `field_policy=cached_result` **不代表实际发生文本转换**。当存储故事没有需要替换的对象/字段/注释标记时，原文保持原样，可取得精确 CP 来源；内部不再为无转换内容构造逐字符复制列表。只有实际替换或过滤导致内容变化时才按 `unavailable_after_text_transform` 保守返回。此优化不改变任何输入选项、状态或文档格式支持范围。

## 最大化读取模式

显式 `--reading-mode maximum` 或 `read_bytes(data, reading_mode="maximum")` 只用于“尽量保留可安全取得的存储文字”。它固定采用 `compatibility=data`、二进制 `text_policy=recover`、`field_policy=cached_result`、`object_policy=placeholder`、`hidden_text=include`、`revisions=all` 和附加区域读取；与这些目标冲突的显式策略会在读取前拒绝，不静默覆盖。CLI 只允许完整 JSON，拒绝纯文本、摘要、标题/段落/章节选择和分页视图，因为这些投影会丢失独立区域或来源诊断。

- **MS-DOC/CFB。** `binary_table_structure.tables` 补读可确认的一级表格分组、存储列边界及横向和纵向合并，参见 `<skill-root>/references/binary-tables.md#表格分组与合并`。`drawing_objects` 记录存储形状和锚点。`binary_images` 提供已验证的 PNG 元数据和位置，带 `--include-image-bytes` 或 Python `include_image_bytes=True` 时可取有界原始字节。`binary_charts` 提供已定位的图表分类、系列、缓存值及缺失范围，见 `<skill-root>/references/binary-shapes.md#存储图表缓存`。附加区域见 `<skill-root>/references/ms-doc-stories.md#最大模式文本框`。
- **OOXML Word：** 使用全部存储修订视图并跟随主文显式引用的页眉页脚、脚尾注和批注；另外从已经通过 XML/ZIP 结构检查的 `w:txbxContent` 子树提取文字和有限表格，按独立 `related_stories[type=textboxes]` 返回。绘图锚点、页面位置、Markup Compatibility Choice/Fallback 分支和主文阅读顺序都不推断；同一逻辑文本存在于多个未选择分支时可能分别返回，不能自动去重或并入主正文。
- **RTF：** 仅捕获已闭合且首控制字可证明为页眉、页脚、脚注或批注的目标组，在继承到该组的代码页和 `uc` 设置下隔离解析，并按独立故事返回。节、页面、引用锚点、目标组与主正文的阅读顺序未知；未闭合、超预算或无法可靠解析的区域保留失败/遗漏诊断，不猜测补齐。

最大模式增加 `reading_mode=maximum` 与 `maximum_reading`。域及图表缓存以存储值返回。核对图表系列的 `status`、`points` 和 `missing_ranges`，这些字段的范围限于已定位的缓存。顶层 `status` 由全部诊断决定。加密、未知容器及安全和资源门禁按失败处理。WPS Office 12.1.0.26899 生产者和客户端视觉尚未验收。

## MS-DOC 段落 CP 来源（可选）

默认读取返回值、状态和退出码**保持不变**。仅实际识别为 CFB + MS-DOC 的文档，显式使用 `--binary-paragraph-sources` 或 Python 的 `read_bytes(data, binary_paragraph_sources=True)` 才会尝试增加下列字段；CLI 必须使用默认 JSON 输出，`--format text` 与此来源选项组合会以参数错误退出 2，防止悄悄丢失来源字段；对 OOXML 或 BIFF8 使用此二进制专用参数会拒绝，而不是根据 `.wps` 后缀猜测格式。

- `paragraph_sources`：与**未转换的存储段落**一一对应的数组。每项仅包含 `index`、`cp_start`、`cp_end`、`text_cp_end`；其中 `[cp_start, cp_end)` 包含原存储段落结束符 CR（若有），`text_cp_end` 不包含该 CR。CP 坐标按 UTF-16 码元计数，补充平面字符占两个 CP，不是 Python 字符索引、物理字节偏移或页码。
- `paragraph_source_status=exact_stored_cp`：表示当前故事的最终文本与存储文字相同（即使选择了但没有实际触发的转换策略），段落范围可以按存储段落顺序关联；这仍不证明页面布局、字符格式或生产软件来源。
- `paragraph_source_status=unavailable_after_text_transform`：如果文本转换**实际改变存储文字**，`paragraph_sources=[]`，同时返回 `binary_paragraph_source_alignment_unavailable` 诊断并标记 partial。**不得把空数组当成没有段落**；原始 `text`、`paragraphs` 和其他诊断仍按既有契约返回。
- 显式 `--include-related` 时，附加故事自身的 `related_stories[].paragraph_sources` 以对应故事起始 CP 为基准，各故事独立编号；不能把页眉、脚注和主正文来源混成一条扁平页序列。
- 每个故事最多 100000 个段落来源、8 Mi UTF-16 单位；仍受总体文本及 16 MiB JSON 输出预算约束。请求超出预算时按既有资源失败契约返回，不偷偷截断来源。

示例：存储文字 `甲😀\r乙\r` 在 `cp_start=5` 时，分别对应 `[5,9)` 与 `[9,11)`。这一示例是独立的 UTF-16 CP 算术，不是 WPS 真机输出或已验证的文档位置。

## MS-DOC 段落 CHPX/PAPX 原始证据关联

显式使用 `--binary-paragraph-sources` 时，在已取得准确存储 CP 来源的 MS-DOC 故事上，额外提供 `paragraph_evidence`（`schema_version=ms_doc_paragraph_evidence_v1`），将**已有的直接读取证据**按 CP 位置与段落关联。它不解析新的私有格式，不能据此声称有效样式、可见性、修订后文字、页码、完整表格结构或 Word/WPS 原生兼容。

- `character_status`：默认 `not_requested`。同时显式指定 `--hidden-text annotate`，且 CHPX 读取器产生合法且无相互重叠的 CP 段时，才给出 `direct_chpx_overlaps_only` 和 `character_links`。每条链接只保存 `paragraph_index`、原始 `character_properties.runs` 中的 `run_index`、交集 `cp_start/cp_end`；原始 run 的属性、操作数和 `effective_visibility=unknown` 仍需从源结果查询。CP 段歧义时返回 `ambiguous_source_ranges`、不输出链接，不猜测哪个格式生效。
- `papx_row_status`：仅在二进制表格读取器已经观察到明确的一级行结束标记时提供 `direct_row_end_markers_only`，`papx_row_end_links` 指明结束符 `row_mark_cp` 位于哪个已确认的段落。**这不是普通段落 PAPX 全量属性、行合并网格、列格式或表格分组恢复**。来源缺失、坐标不一致时保持空链接或标记歧义。
- `effective_formatting` 与 `table_grid` 固定为 `unknown`。`character_status=no_observed_runs`、`papx_row_status=no_observed_row_ends` 只说明没有在本次声明的受控范围中观察到对应证据，不等于文件中不存在相应格式。
- 已经过字段存储结果过滤或对象标记替换的故事，如果无法保持存储 CP 精确对齐，则不生成 `paragraph_evidence`，沿用 `paragraph_source_status=unavailable_after_text_transform`。需要关联 CHPX 时，必须显式组合 `--binary-paragraph-sources --hidden-text annotate`，二者均不会执行隐藏文字过滤。
- 输出链接最多 100000 条/类型；超出时使用 `resource_limited` 并清空相应链接，不截断后声称完整。依然受 16 MiB JSON 输出预算。即使运行成功，也只说明“带来源的原始证据关联”，不表示最终样式已生效。

## 可运行的契约示例

普通使用从任务目录执行 `execute(command="python -I -S -B \"<skill-root>/scripts/read_wps.py\" ./input.wps")`，按上述退出码消费结果。以下自包含示例不需要真实文件：先替换 `<skill-root>`，使用已自检且 Expat >=2.6 的解释器，在任务目录运行。它用标准库生成明确标注的合成 OOXML 字节，以独占临时子目录验证 API 和 CLI 成功、partial、读取失败与参数错误；结束时只清理自己创建的子目录。生成的 `.wps` 后缀仅测试内容路由，不是真实 WPS 来源证据。

```python
execute(command="python -I -S -B - \"<skill-root>\" <<'PY'\nimport io\nimport json\nfrom pathlib import Path\nimport subprocess\nimport sys\nimport tempfile\nfrom xml.parsers import expat\nimport zipfile\n\nassert expat.version_info >= (2, 6, 0), 'Expat >=2.6 required for this example'\nskill_dir = Path(sys.argv[1]).resolve()\nsys.path.insert(0, str(skill_dir / 'scripts'))\nfrom read_wps import ParseError, read, read_bytes\n\nfiles = {\n    '[Content_Types].xml': '<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\"><Override PartName=\"/word/document.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/></Types>',\n    '_rels/.rels': '<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\"><Relationship Id=\"r1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"word/document.xml\"/></Relationships>',\n    'word/document.xml': '<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\"><w:body><w:p><w:r><w:t>示例</w:t></w:r></w:p></w:body></w:document>',\n}\n\ndef package(parts):\n    output = io.BytesIO()\n    with zipfile.ZipFile(output, 'w') as archive:\n        for name, text in parts.items():\n            archive.writestr(name, text)\n    return output.getvalue()\n\npayload = package(files)\nresult = read_bytes(payload)\nassert result['status'] == 'ok' and result['text'] == '示例'\nassert result['reading_assessment']['completeness'] == 'within_declared_scope'\ntry:\n    read_bytes(b'unknown container')\nexcept ParseError as error:\n    assert error.status == 'unsupported'\nelse:\n    raise AssertionError('Expected ParseError for unknown bytes')\n\nwith tempfile.TemporaryDirectory(prefix='wps-contract-', dir='.') as directory:\n    good = Path(directory) / 'synthetic.wps'\n    bad = Path(directory) / 'unknown.wps'\n    partial = Path(directory) / 'partial.wps'\n    good.write_bytes(payload)\n    bad.write_bytes(b'unknown container')\n    parts = dict(files)\n    parts['word/document.xml'] = parts['word/document.xml'].replace('</w:p>', '<w:drawing/></w:p>')\n    partial.write_bytes(package(parts))\n    assert read(good)['status'] == 'ok'\n    failure = read(bad)\n    assert failure['status'] == 'unsupported' and failure['text'] is None\n    assert failure['reading_assessment']['completeness'] == 'no_content_delivered'\n    command = [sys.executable, '-I', '-S', '-B', str(skill_dir / 'scripts' / 'read_wps.py')]\n    for path, code, status in ((good, 0, 'ok'), (partial, 3, 'partial'), (bad, 1, 'unsupported')):\n        process = subprocess.run(command + [str(path)], capture_output=True, text=True)\n        assert process.returncode == code, process.stderr\n        assert json.loads(process.stdout)['status'] == status\n    process = subprocess.run(command + [str(good), '--format', 'invalid'], capture_output=True, text=True)\n    assert process.returncode == 2 and not process.stdout and process.stderr\nprint('contract-examples-ok')\nPY\n")
```

要求示例退出 0 且输出 `contract-examples-ok`；这验证调用和失败契约，不是文档生产、转换能力或真实 WPS 兼容验收。

默认 data 的 `non_cell_metadata_not_decoded` 聚合事件保留首次 `scope`，并以 `scopes` 列出全部受影响的工作表或 globals；`records` 是这些范围的总记录数。具体记录偏移与原始字节仍在 `uninterpreted_metadata` 中。此附加诊断不改变原始单元格值或 strict 策略。
