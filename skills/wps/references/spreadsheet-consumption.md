# 电子表格结果选择与消费

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 先选择所需结果

先完成主入口共同自检和任务路径确认；仅Python标准库，不安装、转换、重算或执行宏/外链。退出0/3分别要求status为ok/partial且JSON完整；退出1/2停止分析，不把失败当空表。partial保留全部可用数据与issues/reading_assessment，不能当作完整格式兼容或最新缓存。

- 只看结构、逐格查看或主正文分页：有界输出（`<skill-root>/references/bounded-reading.md`）；`wps_read_view_v1`是投影，不能交给只接受原schema的XLSX分析器，也不能把所有页拼成伪造的原完整schema。
- 程序分析ET/ETT：默认read_wps完整提取JSON，接受`biff8_readonly_v1`或`ooxml_spreadsheet_readonly_v1`；下方消费示例保留同一结果。XLSX并非本包依赖，另有对应技能时才按其入口交接，不猜相邻目录。
- WPS文字、API异常、reading_assessment覆盖细节或二进制来源：按任务读结果契约（`<skill-root>/references/reading-assessment.md`），普通ET消费不加载MS-DOC段落证据。
- 未知schema、无效JSON、工具结果截断、status/退出码不符，均停止依赖分析。只有JSON完整并不证明全部单元格、缓存或元数据完整。

## 最小共同契约

JSON CLI：ok/0、partial/3；读取失败为失败JSON/1；参数错误usage写stderr/2且无结果JSON。自检是独立输出，不送入分析。read(path)捕获受控ParseError/OSError返回失败字典；read_bytes可能抛ParseError，不能假定总返回字典。详细API与文字模式仅在需要时读原结果契约。

数字字符串保留精度，文本编号不转数字；numeric_interpretation不是原value替代。公式值只是保存缓存，不重算、不证明最新。分析前选定工作表、区域及缓存政策；源缺口、原issues与reading_assessment随交接保留。结果文件仅是中间数据，按宿主安全写入和本轮所有权管理，不因落盘自动归档为最终交付物。

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

