# TL 显式 Python 解释器约定（2026-11-17）

本轮扫描 skills/tl-*，当前只有 tl-docx，唯一直接依赖 python-docx==1.2.0，传递依赖仍由部署环境提供。未新增依赖、安装组件、生成虚拟环境、修改生产逻辑、重打 ZIP、提交或推送。保留既有未提交的 TL/ET 代码与文档。

## 范围与适用性

- 入口及 5 份含运行命令的参考显式使用 `<skill-root>/scripts/.venv/bin/python`，保留 `-I -B`，不使用 `-S`。缺失即停止，不依赖 shell 激活、不回退全局解释器。
- 更新维护基准脚本的调用说明、README、docs 索引及技能说明；AGENTS.md 规则适用于所有实际依赖第三方组件的 tl- Skill。
- 虚拟环境是部署生成资源，不是源码或运行包静态文件。未修改白名单或构建边界；标准库仓库工具和非 TL 命令不机械替换。
- 已检查 Python 调用点：测试及基准中的子进程使用 sys.executable，继承已选解释器；无生产裸 Python 子进程，不改业务逻辑。独立包测试可选择维护解释器验证组件行为，但不是新部署路径验收。
- 历史验收记录保留实际旧命令/环境，不伪装为新约定下的结果。description 的任务、格式和能力范围不变；人工复核 9 项相关选择预期并仅刷新 TL 入口指纹。

## 验证

验证平台为 macOS，维护解释器 Python 3.14.10。以下结果为本轮检查，不代表 Linux/Windows、真实 DeepAgents、Word/WPS 视觉或全业务验收。

| 检查 | 实际结果 |
| --- | --- |
| 新增 `test_tl_python_commands.py` | 5 项通过，退出 0；只检查命令/说明契约，不证明部署就绪。 |
| `tools/check_project_docs.py` | PASS，退出 0。 |
| `tests/test_project_docs.py` | 15 项通过，退出 0；首次只读沙箱中 14 项因无法建临时夹具报错，获准执行后全量重跑通过。 |
| `tests/test_skill_selection_current.py` | 6 项通过，退出 0；人工预期，不是模型选择测试。 |
| 加载快照 `benchmark_skill_loading.py --check-snapshot tests/runtime_loading_profiles.json` | fresh，退出 0；原快照未涵盖本轮 TL 文件，无需重写其他技能证据。 |
| `git diff --check` | 通过。 |
| TL 严格结构检查 | 退出 1，22 条 broken-link 均指向新声明的部署生成解释器。旧检查器把所有字面根路径视为随包静态资源，与虚拟环境部署约定冲突；未修改检查器、未创建伪文件或拆分路径规避。 |
| TL 指定解释器与业务自检/全量回归 | 本机 `scripts/.venv/bin/python` 不存在；未执行业务自检及全量业务测试，不回退其他解释器冒充新路径验证。 |
| Pylint | 维护解释器报告 No module named pylint，未执行；未安装，不宣称 Error 为零。 |

完整路径命令保留用户要求。实际业务验证仍需部署方按授权提供指定环境，再运行入口 check（写入另加 --write）及全量 TL 回归。维护规范检查器若要区分部署生成解释器与静态资源，须另获明确维护规范授权；本轮仅记录冲突，不放宽其他路径/链接安全边界。未验证 Linux、Windows、真实宿主模型执行或视觉呈现。
