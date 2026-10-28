# 统一结果、失败契约与读取范围

## 目录

- [入口与失败行为](#入口与失败行为)
- [结果字段与范围评估](#结果字段与范围评估)
- [消费电子表格 JSON](#消费电子表格-json)
- [可运行的契约示例](#可运行的契约示例)

## 入口与失败行为

先完成 [环境自检](../SKILL.md#执行环境约束与首步自检)。下表针对 `scripts/read_wps.py` 统一入口，低层流解析器默认仍 strict；文件入口默认 data，区别见 [数据读取策略](biff8-readonly.md#default-data-policy)。

| 入口 | 正常返回 | 读取失败与异常 |
|---|---|---|
| Python `read_bytes(data, **options)` | 返回结果字典；成功、部分结果及已由格式分支转成字典的失败都会附加 `reading_assessment`。 | 不捕获统一预检或二进制解析抛出的 `ParseError`；未知容器即会抛出，异常有 `status`、`location`。不能假定所有失败都是字典，需捕获异常并检查已返回字典的 `status`。 |
| Python `read(path, **options)` | 读取普通文件后调用 `read_bytes`，返回结果字典。 | 捕获 `ParseError`、`OSError`，返回失败字典及 `reading_assessment`；不保证捕获编程错误或所有 Python 异常。 |
| CLI 默认 JSON | stdout 为一个完整 JSON；ok 退出 0、partial 退出 3。 | 读取失败输出失败 JSON，退出 1；包括分支不适用的策略。未知参数、无效枚举、缺输入及 `--include-related --format text` 等参数解析错误退出 2，stderr 为用法/错误文本，无结果 JSON。 |
| CLI `--format text` | stdout 仅正文；partial 退出 3，stderr 另给 status/issues 摘要。 | 读取失败的 JSON 写 stderr，stdout 无正文，退出 1。电子表格不能输出 text。 |

读取失败的 `status` 包括 `unsupported`、`encrypted`、`corrupt`、`resource_limit`、`io_error`、`invalid_or_unsupported`。失败正文为 `text=null`、`partial=false`，不交付部分单元格或文字；`invalid_or_unsupported` 不应被调用方擅自归类为必然损坏。参数解析错误不是上述失败字典。

CLI 输出阶段还有两个例外：电子表格请求 text 时返回 unsupported；JSON 超过输出预算时返回 resource_limit。这两处重新生成的失败字典没有 `reading_assessment`。CLI 参数错误、自检输出和纯文本正文也不提供该字段；调用方必须先判断退出码、输出模式和字段是否存在。

## 结果字段与范围评估

文档结果有 `format`、`text`、`paragraphs` 和范围/诊断；OOXML 文档可另有 `tables`，显式附加文字另有 `related_stories`。二进制段落和 TAB 不代表完整表格或页面还原；另有 `binary_table_structure` 的有限一级行/单元格证据，见 [二进制表格](binary-tables.md)。电子表格的 `schema_version` 分别为 `biff8_readonly_v1`、`ooxml_spreadsheet_readonly_v1`，字段见 [BIFF 输出](biff8-readonly.md#headers-and-output) 和 [OOXML 保存值](ooxml-spreadsheet.md)。不要把文档、失败字典或 OOXML 表格当成 BIFF schema；BIFF 统一结果另保留 `reader_status`；data 可为 ok/partial，显式 strict/legacy 保留历史字符串。新增 cell_value_coverage、omissions_affect_cell_values 及 reading_assessment.cell_values 区分元数据缺口与公式缓存缺失。

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

## 消费电子表格 JSON

默认 data 读取从共同自检开始，不需要先了解 BIFF 记录或 legacy 参数。下例使用标准库调用统一 CLI：先将 `<skill_dir>` 替换为已确认的真实 Skill 目录，再在任务目录执行；`./input.et` 可换成允许读取的 `.ett`。它不创建文件，退出 0/3 均接收完整 JSON；退出 3 的数据和原诊断一起交给后续处理。退出 1/2、JSON 无效、状态/退出码不符或 schema 不符均停止，不能把文档或失败字典送入表格分析。

```sh
python -I -S -B - "<skill_dir>" ./input.et <<'PY'
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

BIFF `cell_value_coverage=complete_within_declared_sheets` 只表示已声明工作表的受支持单元格/缓存范围；`missing_formula_caches` 和 `omissions_affect_cell_values=true` 指出缓存缺口。它们不取代 `issues`，不证明私有流、有效格式或完整 ET 支持；OOXML 按其自身 schema/诊断解释，不假定具有全部 BIFF 字段。保留顶层 `reading_assessment` 和原始诊断，分析接入另见 [表头与输出](biff8-readonly.md#headers-and-output)。

## 可运行的契约示例

普通使用从任务目录执行 `python -I -S -B "<skill_dir>/scripts/read_wps.py" ./input.wps`，按上述退出码消费结果。以下自包含示例不需要真实文件：先替换 `<skill_dir>`，使用已自检且 Expat >=2.6 的解释器，在任务目录运行。它用标准库生成明确标注的合成 OOXML 字节，以独占临时子目录验证 API 和 CLI 成功、partial、读取失败与参数错误；结束时只清理自己创建的子目录。生成的 `.wps` 后缀仅测试内容路由，不是真实 WPS 来源证据。

```sh
python -I -S -B - "<skill_dir>" <<'PY'
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
    command = [sys.executable, '-I', '-S', '-B', str(skill_dir / 'scripts/read_wps.py')]
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
