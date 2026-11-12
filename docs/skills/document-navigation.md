# document-navigation：文档任务导航

Skill 版本：`20261112.1921.3400`。本页以仓库根目录为维护基准，核对日期 2026-11-12；正式入口为 [SKILL.md](../../skills/document-navigation/SKILL.md)。本页和测试记录不随运行包分发。

## 解决的问题与限制

当用户说“看看这个文件”“整理附件”“修改这份文档”“生成一份报告”等，即按文档处理意图选择导航，无需先说明格式、后缀、文件位置或技能名。格式已知、输入已定位时也适用；加载后复用已有信息，同一任务已完整加载导航时不重复读取。普通闲聊、术语解释及无关代码文件维护不因出现关键词触发。

导航只包含一个 Markdown，不解析文档、不生成文件、不配置 DeepAgents，也不让格式 Skill 增加导航依赖。先加载导航与后续选择格式工具是两个阶段；格式尚未知时不预读所有格式 Skill，不要求用户先提供后缀。

DeepAgents 官方说明默认按元信息选择 Skill，再读取正文与所需资源。因此导航能改善被选中后的执行指引，无法保证自己先加载；明确提到名称可以帮助选择，但不构成宿主强制加载。官方提供的 pinning 需要应用传入状态，单独修改本 Skill 不能完成，用户当前也不能修改加载机制。[官方技能文档](https://docs.langchain.com/oss/python/deepagents/skills#how-skills-work)

文件工具通过 backend 访问文件；状态存储、文件系统和执行器具有不同能力。导航不假设读取路径与执行器路径相同，也不把工具根目录或一次 `pwd` 输出直接当成获准任务目录。[官方后端说明](https://docs.langchain.com/oss/python/deepagents/backends)

## 执行流程与边界

1. 文档处理意图匹配后，核对本轮任务目标、附件映射、已有交付回执、任务目录和实际工具 schema；不要求用户先提供格式或重复已知文件名。
2. 本地输入只在宿主确认的任务 `./` 顶层定位，列表截断不能证明唯一或不存在。存在真实宿主预览引用时可直接用于查看；脚本访问另行确认。
3. 按格式和操作选择实际可用技能，只加载必要入口；后缀用于候选选择，格式安全识别由提供方负责。未知或不支持的操作停止该分支，不改后缀或自动转换。
4. 完整读取所选入口后，保留授权、任务目录、来源、入口/版本和缺口，继续提供方自检及操作。不存在导航和格式 Skill 互相必读的循环。
5. 业务验收、裸文件名输出、归档和清理由提供方负责。已有 runner 不重复收尾；结果缺口和 partial 继续传递。

任务目录未知时，仍可读取宿主明确定位的 Skill；脚本执行条件未知时，只停止依赖该条件的操作。缺少附件映射、目录查询能力、列表覆盖或技能入口，导航只能指出缺口，不能创造环境事实。

## 独立部署与验证

通过 [显式清单](../../packaging/runtime-manifests/document-navigation.json) 与统一打包器生成，ZIP 只含 `document-navigation/SKILL.md`。没有 CLI、Python、第三方包、网络或办公软件依赖；宿主读取和目录查询能力按实际任务需要核对。格式 Skill 依赖沿用各自入口，导航不把它们打包到自身。

从已确认的仓库根目录，以满足版本条件的 Python 执行：

```console
python -I -S -B skills/skill-development-standards/scripts/check_skill.py skills/document-navigation --strict
python -I -S -B tools/package_skills.py document-navigation
python -I -S -B tools/run_skill_tests.py tests -p test_runtime_packager.py
python -I -S -B tools/run_skill_tests.py tests -p test_skill_runtime_packages.py
python -I -S -B tools/run_skill_tests.py tests -p test_skill_selection_current.py
python -I -S -B tools/check_project_docs.py
```

独立安装测试要求 Python ≥3.12.11、Expat ≥2.6、非 root 及实际只读权限能力；这些是仓库联合验收条件，不是导航的运行依赖。包测试核对成员、字节、只读安装和重复读取；既有格式执行测试拒绝读取未声明的导航包，以检查反向依赖。人工选路期望与情景走查不证明模型实际选择准确率。

当前触发范围、描述核对及检查结果见 [触发更新](../maintenance/document-navigation/intent-trigger-20261112.md)，前版实测保留于 [初版记录](../maintenance/document-navigation/validation-20261112.md)。真实 DeepAgents 的安装发现、首次选择、附件映射、工具参数、完整读取、后续自检与交付仍须在用户部署中实测；此处不声称 Linux、Windows、视觉效果或 token 节省已通过。

本轮统一归档回复边界：完整/精简回执只供内部核验，正文及链接目标均不得暴露归档路径或任务 ID；保留主文件真实引用与失败/部分结果说明。能力范围、CLI 与内部机器字段未改；本轮核验见 [归档展示复核](../maintenance/artifact-lifecycle/archive-reply-privacy-20261112.md)。
