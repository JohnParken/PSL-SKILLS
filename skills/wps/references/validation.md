# 验证方法、样本证据与未验证范围

## 目录

- [当前验证范围](#当前验证范围)
- [可复现的本地检查](#可复现的本地检查)
- [样本与未验证边界](#样本与未验证边界)
- [历史证据](#历史证据)

## 当前验证范围

当前版本 `20261027.2320.5486` 维护单一默认读取入口、ET/文字任务分流和 partial 消费，不新增格式记录或私有格式支持。文件入口默认 data；strict/legacy 保留声明的支持差异和历史成功契约，共用文件有界读取、CFB预检、加密/歧义流及资源门禁，不在失败后扫描或轮换策略。

[机器可读证据](reading-enhancement-evidence.json) 的 `current` 是唯一当前摘要；`history` 只说明对应旧版，不作为当前版本、测试数量或包状态。本批 Python3.12.12/Expat2.6.3 下WPS400、DOCX86、XLSX274、OFD35及仓库端到端2项，共797项通过、无失败或跳过。仓库验收记录保存具体命令、实际结果、评测提示和包哈希；维护记录不随业务读取强制加载。结构检查、合成回归、独立模型评测和真实文件语义验收分别报告。

## 可复现的本地检查

先完成 [入口自检](../SKILL.md#执行环境约束与首步自检)，替换 `<skill_dir>`，在宿主确认的任务目录运行：

```console
python -I -S -B "<skill_dir>/scripts/read_wps.py" --check
python -I -S -B -m unittest discover -s "<skill_dir>/scripts" -p "test_*.py"
```

自检要求退出 0 和 `environment-ok`，并单独确认 OOXML 所需 Expat 可用；测试要求退出 0，区分通过与跳过。运行 [契约示例](reading-assessment.md#可运行的契约示例) 要求退出 0 和 `contract-examples-ok`。独立包验证检查 ZIP CRC、包内路径、每个文件与源码的 SHA-256、隔离运行及执行前后安装文件不变。

仓库严格检查是维护工具，不是独立运行包的依赖。维护者从仓库根目录使用 Python 3.10+ 运行 `python -I -S -B skills/skill-development-standards/scripts/check_skill.py skills/wps --strict`；普通使用无需获取相邻技能或仓库检查器。


## 样本与未验证边界

- 允许的四份原始ET/ETT有固定来源和SHA；198个保存值与独立冻结观察对照，其中33个公式缓存未重算、不能证明最新。空ET不证明有内容ET兼容；具体范围见[真实ET/ETT验收](et-real-validation.md)及[固定预期](real-value-expectations.json)。
- 新增CodePage声明、普通Unicode头保留位兼容目前主要是合成验证；已有样本没有这些新情形，不据其回归宣称新增能力已获生产验收。真实用户仍失败的无图片ET/ETT与独立预期尚缺。
- 真实`.wps`正文/二进制表格生产来源验收未完成；合成二进制一级行单元格证据不是完整网格、合并或布局还原。真实验收方法见[文档验收](wps-real-validation.md)。
- 不验证页面视觉、完整原生WPS格式、私有流等价、公式重算、宏/外链执行或显示顺序；原值、格式解释、缓存与缺口按[结果契约](reading-assessment.md)分别消费。
- 独立模型选择和本地前向任务不等于真实宿主自动发现/触发；专用命名linter未安装，语法/人工命名审阅及结构检查不能冒充其结果。

## 历史证据

旧版环境、计数、描述字符数和默认策略仅保留在[证据history](reading-enhancement-evidence.json)及仓库维护记录，避免与当前契约混用。历史记录中的依赖工具或验证方法不授权当前标准库流程调用外部软件。业务读取只按当前入口和实际结果处理，不必先阅读维护历史。
