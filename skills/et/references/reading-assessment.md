# 电子表格结果与失败契约

路径占位符说明：`<skill-root>` 是 ET Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 ET 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 目录

- 入口与失败行为 (`<skill-root>/references/reading-assessment.md#入口与失败行为`)
- 结果字段与范围评估 (`<skill-root>/references/reading-assessment.md#结果字段与范围评估`)
- 消费电子表格 JSON (`<skill-root>/references/reading-assessment.md#消费电子表格-json`)
- 可运行的契约示例 (`<skill-root>/references/reading-assessment.md#可运行的契约示例`)

## 入口与失败行为

先完成 环境自检 (`<skill-root>/SKILL.md#执行环境约束与首步自检`)。下表针对统一入口 `<skill-root>/scripts/read_et.py`，低层流解析器默认仍 strict；文件入口默认 data，区别见 数据读取策略 (`<skill-root>/references/biff8-readonly.md#default-data-policy`)。

| 入口 | 正常返回 | 读取失败与异常 |
|---|---|---|
| Python `read_bytes(data, **options)` | 返回结果字典；成功、部分结果及已由格式分支转成字典的失败都会附加 `reading_assessment`。 | 不捕获统一预检或二进制解析抛出的 `ParseError`；未知容器即会抛出，异常有 `status`、`location`。不能假定所有失败都是字典，需捕获异常并检查已返回字典的 `status`。 |
| Python `read(path, **options)` | 读取普通文件后调用 `read_bytes`，返回结果字典。 | 捕获 `ParseError`、`OSError`，返回失败字典及 `reading_assessment`；不保证捕获编程错误或所有 Python 异常。 |
| CLI 默认 JSON | stdout 为一个完整 JSON；ok 退出 0、partial 退出 3。 | 读取失败输出失败 JSON，退出 1；包括分支不适用的策略。未知参数、无效枚举、缺输入等参数解析错误退出 2，stderr 为用法/错误文本，无结果 JSON。 |

读取失败的 `status` 包括 `unsupported`、`encrypted`、`corrupt`、`resource_limit`、`io_error`、`invalid_or_unsupported`。失败正文为 `text=null`、`partial=false`，不交付部分单元格或文字；`invalid_or_unsupported` 不应被调用方擅自归类为必然损坏。参数解析错误不是上述失败字典。

CLI 默认完整输出阶段还有两个例外：电子表格请求 text 时返回 unsupported；JSON 超过输出预算时返回 resource_limit。这两处重新生成的失败字典没有 `reading_assessment`。显式有界视图另用 `wps_read_view_v1`，输出投影失败可为 `output_view_error`/退出1且不带该摘要；其完整契约见有界输出（`<skill-root>/references/bounded-reading.md`）。CLI 参数错误、自检输出也不提供该字段；调用方必须先判断退出码、输出模式和字段是否存在。

## 结果字段与范围评估

电子表格的 `schema_version` 分别为 `biff8_readonly_v1`、`ooxml_spreadsheet_readonly_v1`，字段见 BIFF 输出 (`<skill-root>/references/biff8-readonly.md#headers-and-output`) 和 OOXML 保存值 (`<skill-root>/references/ooxml-spreadsheet.md`)。BIFF 统一结果另保留 `reader_status`；data 可为 ok/partial，显式 strict/legacy 保留历史字符串。新增 cell_value_coverage、omissions_affect_cell_values 及 reading_assessment.cell_values 区分元数据缺口与公式缓存缺失。

`reading_assessment.schema_version` 为 `wps_reading_assessment_v1`，它是附加摘要，不替换单元格或原始 `issues`、`diagnostics`：

- `outcome` 对应原始 `status`；`ok` 的 `completeness=within_declared_scope`，不代表完整格式、页面或生产软件认证。
- `partial` 的 completeness 为 partial；返回失败字典的为 `no_content_delivered`。
- `events` 汇总原有诊断的 code、来源字段和已记录位置；缺失偏移不推测。
- `full_format_verified` 始终 false。原始诊断仍是完整依据，摘要不是隐私或安全认证。

CFB 结构错误保持失败：若文件长度非扇区整数倍，可从失败 `location.reason_code=cfb_unaligned_physical_length` 区分；FAT 宣告分配未具备物理存储扇区时为 `cfb_fat_unbacked_allocation`。两类错误均属于 `invalid_or_unsupported`，不补齐缺失扇区，不忽略非零尾随数据。错误分层并非损坏文件读取成功。

## 消费电子表格 JSON

消费方代码应按 电子表格消费说明 (`<skill-root>/references/spreadsheet-consumption.md`) 标准交接，保留源 partial 与诊断。

## 可运行的契约示例

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
from read_et import read, read_bytes, check_environment

# 环境自检
assert check_environment() == 0

# 读取示例
result = read('./sample.et')
if result['status'] in ('ok', 'partial'):
    print(f"提取成功，工作表数量: {len(result.get('sheets', []))}")
    if result['status'] == 'partial':
        print(f"已知缺口: {len(result.get('issues', []))}")
```
