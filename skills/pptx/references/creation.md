# PPTX 创建与验收

路径占位符说明：`<skill-root>` 是占位符；从本次主入口 `SKILL.md` 的真实 `file_path` 定位，相对路径先按宿主确认的读取基准解析，再去掉文件名。使用前整体替换为真实目录，将完整路径作为一个参数正确引用；无法定位时停止。该根目录不是当前参考文件目录。
所有命令在默认任务工作目录 `./`运行，输入为任务相对路径，输出为尚不存在的裸文件名；安装目录只读。先完成主入口自检及始终适用的安全、结果与归档检查。

创建前读取 `<skill-root>/references/input-contract.md`，按明确字段构造配置和 SVG；首次使用原生表格、图表或公式时按需读取 `<skill-root>/assets/example.json`。转换子集基于 PPT Master v6.8.0，不代表任意 SVG、LaTeX 或旧稿编辑均受支持。字体度量随包提供，实际字体显示仍受客户端影响。

## 创建流程


1. 明确受众、主题、页数和内容依据；已有明确要求时直接采用。先整理逐页标题、正文、数据与备注，再制作 SVG，避免让布局修改改变已确认的事实。
2. 按输入契约构造 `psl.pptx.v1` JSON。普通任务优先通过 stdin 传入；确有复用需要时使用任务目录内已有的配置文件。
   所有页面统一画布，默认 `1280 × 720`。每页仅提供 `svg` 或 `svg_path` 之一；备注为纯文本。首次原生表格、图表、公式输入以四页示例为依据。
3. 选择尚不存在的 `.pptx` 裸文件名，主文件固定写在任务根目录。脚本不覆盖、不自动改名、不创建父目录；遇到同名文件使用另一个明确的文件名，不删除已有产物。
4. 调用创建命令。将 JSON 作为实际进程 stdin 传入 `-`，不要把执行器参数和演示文稿 JSON 混在一起；文件输入示例如下。

```python
execute(command="\"<skill-root>/scripts/.venv/bin/python\" -I -S -B \"<skill-root>/scripts/pptx.py\" create presentation.json report.pptx")
```

文件方式如上；选择 stdin 方式时，使用 `create - report.pptx` 并在同一 command 内通过 quoted here-document 提供完整 JSON。两种方式互斥。示例文件名可按主题替换，执行工作目录始终保持为默认任务目录。
创建、转换与读取在内存完成，不生成业务中间文件，也不写入安装目录；不支持旧稿编辑。若编排时确需生成临时配置、脚本或其他中间文件，先登记本轮尚不存在的任务根目录裸文件名并保护用户输入与最终文件，进程退出后按原清单安全清理；不得在归档目录或其子目录创建临时文件。归档暂存名称在创建前于进程内登记，再在任务根目录独占创建；写入探针同样独占创建并收尾。身份变化或清理失败时保留并报告；进程被强杀时内存登记丢失，不能扫描认领未知残留。身份核对和删除之间无法原子绑定，任务目录须避免不受信任的并发修改。无法控制外部工具的临时位置时，不将其称为已满足本约束。

5. 要求退出码 0、`ok: true`、`structural_validation: "passed"`，核对返回的 `output`、`bytes`、`sha256`、`slide_count`。
   再读取最终文件，核对页数、文字、备注及对象计数是否符合输入；按需核对图表数据与表格内容，不把对象数量视为内容完全正确。

```python
execute(command="\"<skill-root>/scripts/.venv/bin/python\" -I -S -B \"<skill-root>/scripts/pptx.py\" inspect report.pptx")
execute(command="\"<skill-root>/scripts/.venv/bin/python\" -I -S -B \"<skill-root>/scripts/pptx.py\" validate report.pptx")
```

`inspect` 已执行结构验证并默认返回全量受支持内容；仅需结构复核时选 `validate`，无需为同一结论重复运行两条命令。
读取结果与创建回执的 SHA-256 应一致；不一致时停止交付并查明文件变化。修订页面时从原始内容重新创建不同输出文件，不把此过程描述为原 PPTX 原位编辑。

## 维护边界


内部入口为 PPTX CLI（`<skill-root>/scripts/pptx.py`），实现位于 运行与组包模块（`<skill-root>/scripts/pptx_core/`） 和 SVG 转换模块（`<skill-root>/scripts/_engine/`）。
新增能力须同步输入校验、转换行为、独立预期测试与本文；不得内置第三方 Python 库源码、动态安装包，或用外部命令、网络服务代替“仅标准库”。
安装目录保持只读。OOXML 特殊成员名只在内存 ZIP 中使用；若以后需要源码模板，使用合规文件别名并显式映射，映射冲突必须失败，禁止全局替换 XML 引用。
测试证据按通过、失败、跳过及未验证分别记录；结构合成测试不能替代真实业务样本、PowerPoint/WPS 显示或宿主自动触发验收。
