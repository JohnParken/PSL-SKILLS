# Skill 加载评测（仓库维护工具）

这些文件以仓库根为基准，不随任何单独 Skill 分发，不修改 LangChain Deep Agents 或其加载机制。`benchmark_skill_loading.py` 比较固定提交 `4b5a61f51d12d2a9d293898c2f862cbf7271dc8d` 的参考读取计划与工作区；不执行模型，也不声称计划已经被宿主遵守。

## 重现

使用已确认的 Python 3.10+，在仓库根执行：

```console
python -I -S -B tests/benchmark_skill_loading.py
python -I -S -B -m unittest discover -s tests -p 'test_skill_loading_benchmark.py' -v
```

第一条只向 stdout 输出 JSON。需要更新快照时，将其保存至 `tests/runtime_loading_profiles.json`，最后检查逐文件 SHA256 是否仍匹配工作区。报告期间不要并发修改被测文件。默认只需标准库，无需安装依赖。

可选精确 token 计数只使用预先安装的 tiktoken 和已有缓存（维护依赖，不增加 Skill 运行依赖）：

```console
python -I -B tests/benchmark_skill_loading.py --encoding cl100k_base --tokenizer-cache /confirmed/existing/cache
```

此模式不使用 `-S`，以允许读取已经安装的维护依赖。必须将缓存参数替换为宿主确认位置；不会下载、安装或写入缓存，缺包/缺资产/哈希错误返回非零退出。不要把其他 encoding 的 token 计数当作当前 Deep Agents 模型的计费值。默认未测 token 时字段为 null，绝不按字符或字节换算估计值。

## 测量层次

1. 字符数、UTF-8 字节数、文件 SHA256 和读取事件数：可复现的静态代理。按文件完整读取计数，不推测章节加载；每个文件事件单独计数，重复读取不静默去重。
2. 可选 tokenizer：真实编码函数得到的文本 token 数，记录库版本及 encoding。逐文件求和不包括 chat 包装、系统提示、工具 schema、模型输出、provider 缓存计费或多轮完整上下文重放。
3. Deep Agents usage：本工具不测。必须单独使用真实宿主 trace 与 provider usage 才能判断实际路由、重复读取和节省。不能把工具输出大小或本工具结果标为真实 agent benchmark。
4. 正确性：本工具测试只验证评测实现和路由资源存在，不证明生成、编辑、统计或模型选路正确。CLI 回归、独立包验收及 Linux 测试需另行执行与记录。

历史 `wps-default-et`、`wps-selected-cells` 名称为兼容报告保留，before 和 after 均纠正为 `et/SKILL.md`；不会将旧快照中错误 WPS 路由造成的差异冒充优化。WPS 简单标题/正文/基础表格创建在旧入口已有完整示例，generation 链接不是必读，因此 before/after 都仅计入口；只有高级创建才在两侧加载 generation。XLSX 旧入口明确要求创建参考、共同写入契约和任务收尾，因此其简单创建 before 计这三份参考加入口，after 按新入口闭环计；复杂公式、故障、混合格式仍取所需参考并集。复杂公式场景的 partial 是验收要求，并非本脚本已经执行得出的结果。

重复场景分开：独立任务冷读取两次；同一任务中原文完整保留、文件未变时计划读取一次；压缩后指令缺失则重新读取。它们是不同假设，不代表宿主已有缓存，也不允许跨任务复用授权。

## 真实宿主验收清单

在相同模型、Deep Agents 版本、工具 schema、输入文件和业务预期下，分别使用 baseline 与修改版运行普通概况、静态创建、复杂公式 partial、混合格式、故障和重复任务。保存下列信息，保持业务数据脱敏：

- Skill 文件版本/哈希、实际 read_file 的路径/范围/完整或截断标志、重复事件及原因；不要用推测的计划代替 trace。
- 业务结果、退出码、partial/遗漏/清理状态以及独立预期；失败也保留 stdout/stderr。
- provider 原始 input/output/cache token usage 和统计范围，区分每轮合计与最终轮；不要混加相互包含的指标。
- 实际平台、Python/Expat 条件、单独 Skill 部署和 Linux 验证结果。

本轮快照若为 Darwin，仅证明在 macOS 执行静态测量；Linux、真实模型轨迹与 provider usage 未由该快照验证。
