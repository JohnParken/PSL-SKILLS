# 复杂JSON的Python调用模板

路径占位符说明：`<skill-root>` 是 XLSX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 XLSX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 选择输入方式

简单JSON（短文本、少量字段、浅层结构）可直接写；复杂JSON（长正文、多层对象或数组、表格批量数据、大量引号或反斜杠）必须用Python构造dict/list，再用json.dumps(..., ensure_ascii=False, allow_nan=False)序列化，不手工拼接JSON。Python语法与业务字段仍需核对，序列化成功不等于业务验收通过。

先按主入口完成适用的环境确认；XLSX先按主入口执行check并核对成功标记。工作目录保持宿主确认的`./`。模板只构造规格并调用现有runner，不修改生成器，不安装依赖。以下示例内容仅用于说明；执行前换成本次已确认的完整内容与文件名，不为演示生成额外文件。

## Python源码

以下源码通过Python stdin执行；不是新增安装脚本，也不要求保存为.py。仅修改数据和已授权输出，不将用户材料当代码执行；不使用eval、exec、Shell插值或字符串拼接组装JSON。单元格数据须符合XLSX规格，不使用DOCX的blocks字段。

```python
import json
from pathlib import Path
import subprocess
import sys

root = Path(sys.argv[1])
output = sys.argv[2]
spec = {
    "sheets": [{"name": "Data", "cells": {
        "A1": {"value": '说明：客户说"已确认"；字面文本 C:\\资料 与 $(echo 未执行)。'},
        "B1": {"value": 42},
    }}]
}
request = {"script": "stdlib_xlsx.py", "arguments": ["create", "@spec", output],
           "output": output, "configs": {"spec": spec}}
command = [sys.executable, "-I", "-S", "-B", str(root / "scripts/run_xlsx_task.py")]

try:
    payload = json.dumps(request, ensure_ascii=False, allow_nan=False).encode("utf-8")
except (TypeError, ValueError, OverflowError):
    print("spec_serialization_failed: correct the Python data structure", file=sys.stderr)
    raise SystemExit(2)
try:
    result = subprocess.run(command, input=payload, check=False)
except OSError:
    print("runner_not_started: check the confirmed interpreter and Skill path", file=sys.stderr)
    raise SystemExit(1)
raise SystemExit(result.returncode)
```

## 完整调用表示

下例与上述源码是同一个模板，只执行一次。将`<skill-root>`替换为实际读取主入口确定的真实目录，使用宿主确认的Python解释器；输出参数按实际名称替换。quoted heredoc结束行必须独立且不能与源码完整行冲突。它承载Python源码，不直接承载手写复杂JSON。外层工具参数仍须按真实schema完整编码，不能传空command或把execute表示式嵌套进command。

```python
execute(command="python -I -S -B - \"<skill-root>\" \"报告.xlsx\" <<'PY_SPEC'\nimport json\nfrom pathlib import Path\nimport subprocess\nimport sys\n\nroot = Path(sys.argv[1])\noutput = sys.argv[2]\nspec = {\n    \"sheets\": [{\"name\": \"Data\", \"cells\": {\n        \"A1\": {\"value\": '说明：客户说\"已确认\"；字面文本 C:\\\\资料 与 $(echo 未执行)。'},\n        \"B1\": {\"value\": 42},\n    }}]\n}\nrequest = {\"script\": \"stdlib_xlsx.py\", \"arguments\": [\"create\", \"@spec\", output],\n           \"output\": output, \"configs\": {\"spec\": spec}}\ncommand = [sys.executable, \"-I\", \"-S\", \"-B\", str(root / \"scripts/run_xlsx_task.py\")]\n\ntry:\n    payload = json.dumps(request, ensure_ascii=False, allow_nan=False).encode(\"utf-8\")\nexcept (TypeError, ValueError, OverflowError):\n    print(\"spec_serialization_failed: correct the Python data structure\", file=sys.stderr)\n    raise SystemExit(2)\ntry:\n    result = subprocess.run(command, input=payload, check=False)\nexcept OSError:\n    print(\"runner_not_started: check the confirmed interpreter and Skill path\", file=sys.stderr)\n    raise SystemExit(1)\nraise SystemExit(result.returncode)\nPY_SPEC\n")
```

Python通过sys.executable启动现有runner，subprocess参数使用列表，未启用shell=True、不设置cwd，也不另设更短的外层超时。JSON只通过input字节传stdin，不写临时JSON、不打印正文调试信息。子进程stdout/stderr直接透传，真实退出码原样返回，包括1、2、3；不要用check=True异常掩盖返回摘要。宿主仍须完整回传两个通道，截断或未知执行状态不能当成功。

## 成功与失败清理

默认模板不创建临时Python文件或JSON文件；序列化失败时runner尚未启动，无新增辅助文件。业务成功或失败时，现有runner清理自己的已登记临时项和清单；清理受阻须保留cleanup_incomplete及真实退出码，不能报告全部完成。用户原件、当前与历史交付物不删除。强杀、宿主崩溃及权限失败不能保证即时清理，按原清单核实恢复，不扫描历史文件。

若已确认的宿主确需把Python源码写成临时文件，必须由外层执行者在创建前用本Skill清理入口plan登记一个尚不存在的`./`下独占文件名，保护用户输入和预期输出，再独占写入。源码执行完毕后，不论退出0、业务失败、序列化失败或Python语法错误，外层均执行seal/clean；不能让临时脚本删除自身，不能把清理放在只在成功时执行的&&链上。业务成功使用真实验收和归档状态封存，失败用failed；执行状态未知先确认子进程已退出，未退出不能删。外层清理摘要单独回传，不混入runner的单个JSON stdout，不覆盖原业务退出码。完整流程与真实入口见下节。

## 保存脚本时的清理入口

交付生命周期（`<skill-root>/references/delivery-lifecycle.md`），CLI为`<skill-root>/scripts/xlsx_lifecycle.py`。 只登记额外保存的调用脚本，不重复登记runner内部临时项。输入规格无论简单或复杂均由原生成器校验；模板不是宿主参数校验器，也不解决工具文件系统隔离。`/large_tool_results/`必须由read_file读取，不能作为本模板的Path输入。
