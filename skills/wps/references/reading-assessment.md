# 统一结果、失败契约与读取范围

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 目录

- 入口与失败行为 (`<skill-root>/references/reading-assessment.md#入口与失败行为`)
- 结果字段与范围评估 (`<skill-root>/references/reading-assessment.md#结果字段与范围评估`)
- MS-DOC 段落 CP 来源（可选） (`<skill-root>/references/reading-assessment.md#ms-doc-段落-cp-来源可选`)
- MS-DOC 段落 CHPX/PAPX 原始证据关联 (`<skill-root>/references/reading-assessment.md#ms-doc-段落-chpxpapx-原始证据关联`)
- 消费电子表格 JSON (`<skill-root>/references/reading-assessment.md#消费电子表格-json`)
- 可运行的契约示例 (`<skill-root>/references/reading-assessment.md#可运行的契约示例`)

## 入口与失败行为

先完成 环境自检 (`<skill-root>/SKILL.md#执行环境约束与首步自检`)。下表针对统一入口 `<skill-root>/scripts/read_wps.py`，低层流解析器默认仍 strict；文件入口默认 data，区别见 数据读取策略 (`<skill-root>/references/biff8-readonly.md#default-data-policy`)。

| 入口 | 正常返回 | 读取失败与异常 |
|---|---|---|
| Python `read_bytes(data, **options)` | 返回结果字典；成功、部分结果及已由格式分支转成字典的失败都会附加 `reading_assessment`。 | 不捕获统一预检或二进制解析抛出的 `ParseError`；未知容器即会抛出，异常有 `status`、`location`。不能假定所有失败都是字典，需捕获异常并检查已返回字典的 `status`。 |
| Python `read(path, **options)` | 读取普通文件后调用 `read_bytes`，返回结果字典。 | 捕获 `ParseError`、`OSError`，返回失败字典及 `reading_assessment`；不保证捕获编程错误或所有 Python 异常。 |
| CLI 默认 JSON | stdout 为一个完整 JSON；ok 退出 0、partial 退出 3。 | 读取失败输出失败 JSON，退出 1；包括分支不适用的策略。未知参数、无效枚举、缺输入及 `--include-related --format text` 等参数解析错误退出 2，stderr 为用法/错误文本，无结果 JSON。 |
| CLI `--format text` | stdout 仅正文；partial 退出 3，stderr 另给 status/issues 摘要。 | 读取失败的 JSON 写 stderr，stdout 无正文，退出 1。电子表格不能输出 text。 |

读取失败的 `status` 包括 `unsupported`、`encrypted`、`corrupt`、`resource_limit`、`io_error`、`invalid_or_unsupported`。失败正文为 `text=null`、`partial=false`，不交付部分单元格或文字；`invalid_or_unsupported` 不应被调用方擅自归类为必然损坏。参数解析错误不是上述失败字典。

CLI 输出阶段还有两个例外：电子表格请求 text 时返回 unsupported；JSON 超过输出预算时返回 resource_limit。这两处重新生成的失败字典没有 `reading_assessment`。CLI 参数错误、自检输出和纯文本正文也不提供该字段；调用方必须先判断退出码、输出模式和字段是否存在。

## 结果字段与范围评估

文档结果有 `format`、`text`、`paragraphs` 和范围/诊断；OOXML 文档可另有 `tables`，显式附加文字另有 `related_stories`。二进制段落和 TAB 不代表完整表格或页面还原；另有 `binary_table_structure` 的有限一级行/单元格证据，见 二进制表格 (`<skill-root>/references/binary-tables.md`)。电子表格的 `schema_version` 分别为 `biff8_readonly_v1`、`ooxml_spreadsheet_readonly_v1`，字段见 BIFF 输出 (`<skill-root>/references/biff8-readonly.md#headers-and-output`) 和 OOXML 保存值 (`<skill-root>/references/ooxml-spreadsheet.md`)。不要把文档、失败字典或 OOXML 表格当成 BIFF schema；BIFF 统一结果另保留 `reader_status`；data 可为 ok/partial，显式 strict/legacy 保留历史字符串。新增 cell_value_coverage、omissions_affect_cell_values 及 reading_assessment.cell_values 区分元数据缺口与公式缓存缺失。

`reading_assessment.schema_version` 为 `wps_reading_assessment_v1`，它是附加摘要，不替换正文、单元格或原始 `issues`、`diagnostics`：

- `outcome` 对应原始 `status`；`ok` 的 `completeness=within_declared_scope`，不代表完整格式、页面或生产软件认证。
- `partial` 的 completeness 为 partial；返回失败字典的为 `no_content_delivered`。字段排除或隐藏策略继续由原始诊断解释，不统一改成损坏。
- `events` 汇总原有诊断的 code、来源字段和已记录位置；缺失偏移不推测。
- `coverage` 按 related_content、binary_tables、objects 及显式遗漏代码报告 extracted/not_requested/not_decoded/unknown；仅依据已记录证据，unknown 不表示不存在。每类最多保留 20 个位置并记录截断数；`evidence_count` 是证据条目数，不是字符覆盖率。部分二进制行已观察时完整结构仍为 not_decoded。
- `events` 也收集附加故事和工作表的嵌套诊断；最多保留 1000 项，超出数见 `events_truncated`。原始诊断仍是完整依据。
- `omitted_regions` 是 MS-DOC 未请求的非空附加故事范围，不含文字；遇到这些区域时顶层 partial/退出 3。
- `regions` 仅汇总附加区域类型、状态、来源、空状态与引用状态，不复制正文或被排除文字。
- `visibility` 按部件统计已记录 run 的 action/state，保留策略；精确路径见原 `visibility.runs`。计数不是字符覆盖率，未记录 run 不推断可见性。
- `full_format_verified` 始终 false。原始诊断仍是完整依据，摘要不是隐私或安全认证。

内部格式解析器、历史低层 CLI 可能没有此摘要或使用不同状态/退出码。合成回归验证范围限定、部分区域定位和隐藏文字不进入摘要，不构成真实文件语义对照。

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

## 消费电子表格 JSON

默认 data 读取从共同自检开始，不需要先了解 BIFF 记录或 legacy 参数。下例使用标准库调用统一 CLI：先将 `<skill-root>` 替换为已确认的真实 Skill 目录，再在任务目录执行；`./input.et` 可换成允许读取的 `.ett`。它不创建文件，退出 0/3 均接收完整 JSON；退出 3 的数据和原诊断一起交给后续处理。退出 1/2、JSON 无效、状态/退出码不符或 schema 不符均停止，不能把文档或失败字典送入表格分析。

```sh
python -I -S -B - "<skill-root>" ./input.et <<'PY'
import json
from pathlib import Path
import subprocess
import sys

skill_dir = Path(sys.argv[1]).resolve()
process = subprocess.run(
    [sys.executable, '-I', '-S', '-B',
     str(skill_dir / 'scripts' / 'read_wps.py'), sys.argv[2]],
    capture_output=True, text=True, encoding='utf-8', check=False,
)
if process.returncode not in (0, 3):
    raise SystemExit(process.stderr or process.stdout or 'Reading failed')
result = json.loads(process.stdout)
expected_status = 'ok' if process.returncode == 0 else 'partial'
if result.get('status') != expected_status:
    raise SystemExit('Unexpected status/exit-code combination')
if result.get('schema_version') not in (
    'biff8_readonly_v1', 'ooxml_spreadsheet_readonly_v1',
):
    raise SystemExit('Spreadsheet schema required')
# Pass this same result to the chosen consumer; do not rebuild a reduced schema.
print(json.dumps(result, ensure_ascii=False, allow_nan=False))
if process.returncode == 3:
    print('Partial data retained; report the original issues and reading_assessment.',
          file=sys.stderr)
PY
```

表格消费者按 `type` 解释 `value`：JSON 中的数字字符串保持保存值精度，不与文本长编号混为一类，不先转换成 float。`numeric_interpretation` 是有限数字格式/日期解释的附加字段，不替换 `value`。有 `formula` 的值属于保存缓存；`cache_status=missing` 不能当空值或零，`unverified` 等状态不能证明缓存最新。使用缓存分析须显式选择 cached 政策，所选区域的缺失或错误缓存仍拒绝，禁止重算。

BIFF `cell_value_coverage=complete_within_declared_sheets` 只表示已声明工作表的受支持单元格/缓存范围；`missing_formula_caches` 和 `omissions_affect_cell_values=true` 指出缓存缺口。它们不取代 `issues`，不证明私有流、有效格式或完整 ET 支持；OOXML 按其自身 schema/诊断解释，不假定具有全部 BIFF 字段。保留顶层 `reading_assessment` 和原始诊断，分析接入另见 表头与输出 (`<skill-root>/references/biff8-readonly.md#headers-and-output`)。

## 可运行的契约示例

普通使用从任务目录执行 `python -I -S -B "<skill-root>/scripts/read_wps.py" ./input.wps`，按上述退出码消费结果。以下自包含示例不需要真实文件：先替换 `<skill-root>`，使用已自检且 Expat >=2.6 的解释器，在任务目录运行。它用标准库生成明确标注的合成 OOXML 字节，以独占临时子目录验证 API 和 CLI 成功、partial、读取失败与参数错误；结束时只清理自己创建的子目录。生成的 `.wps` 后缀仅测试内容路由，不是真实 WPS 来源证据。

```sh
python -I -S -B - "<skill-root>" <<'PY'
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from xml.parsers import expat
import zipfile

assert expat.version_info >= (2, 6, 0), 'Expat >=2.6 required for this example'
skill_dir = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(skill_dir / 'scripts'))
from read_wps import ParseError, read, read_bytes

files = {
    '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
    '_rels/.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
    'word/document.xml': '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>示例</w:t></w:r></w:p></w:body></w:document>',
}

def package(parts):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, text in parts.items():
            archive.writestr(name, text)
    return output.getvalue()

payload = package(files)
result = read_bytes(payload)
assert result['status'] == 'ok' and result['text'] == '示例'
assert result['reading_assessment']['completeness'] == 'within_declared_scope'
try:
    read_bytes(b'unknown container')
except ParseError as error:
    assert error.status == 'unsupported'
else:
    raise AssertionError('Expected ParseError for unknown bytes')

with tempfile.TemporaryDirectory(prefix='wps-contract-', dir='.') as directory:
    good = Path(directory) / 'synthetic.wps'
    bad = Path(directory) / 'unknown.wps'
    partial = Path(directory) / 'partial.wps'
    good.write_bytes(payload)
    bad.write_bytes(b'unknown container')
    parts = dict(files)
    parts['word/document.xml'] = parts['word/document.xml'].replace('</w:p>', '<w:drawing/></w:p>')
    partial.write_bytes(package(parts))
    assert read(good)['status'] == 'ok'
    failure = read(bad)
    assert failure['status'] == 'unsupported' and failure['text'] is None
    assert failure['reading_assessment']['completeness'] == 'no_content_delivered'
    command = [sys.executable, '-I', '-S', '-B', str(skill_dir / 'scripts' / 'read_wps.py')]
    for path, code, status in ((good, 0, 'ok'), (partial, 3, 'partial'), (bad, 1, 'unsupported')):
        process = subprocess.run(command + [str(path)], capture_output=True, text=True)
        assert process.returncode == code, process.stderr
        assert json.loads(process.stdout)['status'] == status
    process = subprocess.run(command + [str(good), '--format', 'invalid'], capture_output=True, text=True)
    assert process.returncode == 2 and not process.stdout and process.stderr
print('contract-examples-ok')
PY
```

要求示例退出 0 且输出 `contract-examples-ok`；这验证调用和失败契约，不是文档生产、转换能力或真实 WPS 兼容验收。

默认 data 的 `non_cell_metadata_not_decoded` 聚合事件保留首次 `scope`，并以 `scopes` 列出全部受影响的工作表或 globals；`records` 是这些范围的总记录数。具体记录偏移与原始字节仍在 `uninterpreted_metadata` 中。此附加诊断不改变原始单元格值或 strict 策略。
