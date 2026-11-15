# BIFF5/7 公开提取结果跨表交接（2026-11-15）

## 变更及边界

原 ET 已提供独立 `biff5_readonly_v1`，但 workflow metadata 与 XLSX extract 仍拒绝。现在 XLSX 的 JSON 适配器显式接受新schema，严格使用旧网格16384×256，保留原schema、Decimal精度、源issues/coverage/cache状态；不解析二进制、不伪装BIFF8。XLSX capabilities新增 `extraction_schemas`，版本仍为2。

workflow仍只通过ET read/metadata及XLSX extract/region公开能力调用。BIFF5计算来源必须先核对提供方的schema声明；旧提供方缺声明明确拒绝，不影响原BIFF8/OOXML路径。原partial默认拒绝、accept_partial的完整格覆盖要求、未知日期拒绝、公式cached显式选择及缺失/错误缓存拒绝均未放宽。names仅名称发现，不代表计算准入。

## 验证

macOS / Homebrew Python3.14.10，标准库。XLSX与workflow自检通过。新增：

- `skills/xlsx/scripts/test/test_xlsx_biff5_result.py` 3项：独立schema/精度/旧网格边界、非法覆盖/日期/解析状态拒绝、未知覆盖保留。
- `tests/test_table_workflow_biff5.py` 8项：真实ET→XLSX子进程能力链路，metadata、原schema/前导零/保存值；partial默认拒绝与明确接受；未知覆盖、缺失及错误缓存拒绝；缓存明确选择；缺schema能力拒绝；完整计划结果及审计/原模板保护；独立manifest复制且运行文件只读的提供方调用。

最终命令及结果：

```console
python -I -S -B -m unittest discover -s skills/xlsx/scripts/test -p 'test_*.py'
# 417 passed
python -I -S -B -m unittest discover -s tests -p 'test_table_workflow*.py'
# 188 passed
python -I -S -B skills/skill-development-standards/scripts/check_skill.py skills/xlsx skills/table-workflow --strict
# both passed
git diff --check
# passed
```

新BIFF5场景全部为合成原件；既有workflow套件另含已登记公开来源XLSX回归，不是私人样本。Pylint未安装、未执行；未安装新依赖。未执行Linux/Windows或真实WPS BIFF5业务填报、客户端视觉、模型宿主选路。最终全仓库整合验收与快照由整合任务另记。未提交推送，不覆盖已有发布ZIP。

## 独立审查修正

能力协商先严格验证extraction_schemas为list[str]，再精确匹配；字符串、字典、null、tuple和混入非字符串均明确拒绝，不把substring或dict键当支持声明。BIFF5除了存储格校验，公开region查询也拒绝超16384行/256列的空位范围，不静默返回blank。新增2项真实CLI链路回归及1项XLSX边界回归（包括合法IV16384），累计workflow BIFF5 10项、XLSX BIFF5 4项。最终全量验收由整合任务复跑。
