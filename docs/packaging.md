# 项目结构与精简运行包

本页以已确认的仓库根目录为基准，是项目维护说明，不随 Skill 安装。单个 Skill 内部组织仍遵守 [开发规范](../skills/skill-development-standards/SKILL.md)，没有放宽安全、依赖、输出路径、归档或失败清理契约。

## 项目分层

- `skills/<name>/`：唯一业务源码、运行参考、模板和许可；测试依规范保留 `scripts/test/`。
- `docs/maintenance/<name>/`：历史样本证据、维护审计与测试报告。运行说明保留必要的当前能力/限制，维护文件不变成必需运行依赖。
- `tests/fixtures/docx/`：跨 Skill 使用的七个既有 DOCX 样本，原字节不变。ET 来源清单和固定预期移至 `docs/maintenance/et/`，维护脚本及 CI 同步定位。
- `packaging/runtime-manifests/`：10份审阅后的显式发布文件清单；不自动吸入新文件。
- `tools/`：统一构建、自检和回归工具。DOCX、OFD旧打包命令是兼容入口，复用同一构建实现。
- `dist/runtime/`：当前生成 ZIP 与发布清单；`dist/archive/`：原有12份历史 ZIP，保持原字节。整个 `dist/` 不纳入Git。
- `docs/validation/`：重组基线、历史包指纹和当前验收记录。旧证据保留原日期/版本结论，不伪装为本轮重跑。

## 保留与排除

运行包包含入口、声明依赖、所有现有正式脚本、当前任务参考和必需静态资源。业务 Python 不压缩改写、不删兼容接口、不把运行时移到仓库共享目录。运行时不依赖仓库测试、维护资料或相邻 Skill 的私有模块。

业务包排除源码 `scripts/test/`、缓存和已移出的历史材料。两个有意保留的例外：

- 银行写作全部21个文件都是业务参考，保留完整内容，不为数字缩小删功能。
- 开发规范的业务就是检查和维护 Skill，其流程要求自身回归，因此保留唯一自测文件，4文件整包发布，不篡改规范来消除引用。

OFD内置朱雀字体、许可和来源信息属于既有能力，完整保留；私人 `assets/user_fonts/` 只保留NOTICE，实际字体或符号链接不读取、不发布。PPTX内嵌形状定义、字体度量、引擎许可，XLSX模板与可运行示例，公共生命周期副本及来源追踪都保留。体积缩小不等同 token 减少。

`table-workflow` 仍是显式组合型 Skill，需要宿主提供并验证 XLSX/ET 的公开能力；它不是无依赖解析器。没有提供者时明确拒绝，不能用仓库相邻目录偷偷补齐。

## 构建

使用已确认满足环境要求的 Python，从仓库根执行：

```console
python -I -S -B tools/package_skills.py
python -I -S -B tools/package_skills.py docx xlsx --output-dir dist/runtime/review
python -I -S -B tools/package_docx_runtime.py --output dist/runtime/docx-review.zip
python -I -S -B tools/package_ofd.py dist/runtime/ofd-review.zip
```

输出名为 `<name>-runtime-<Skill version>.zip`，顶层为对应 Skill 名称。目标必须不存在，禁止覆盖旧包；默认统一工具会创建输出目录。兼容OFD命令的输出父目录需已存在。安装时完整解压对应顶层目录，不混入另一个版本或将维护测试复制进去。

构建拒绝未分类源码、缺失清单成员、路径越界、符号链接运行文件、重复成员和私有字体发布；缓存不进入包。输出固定ZIP元数据、排序和压缩级别，同环境同源码可重现；构建后核对CRC、成员清单及源码快照逐字节一致。ZIP压缩字节可能随Python/zlib版本变化，不把跨环境压缩哈希视为业务差异。

构建不执行业务验收，成功不代表所有功能或视觉效果已验证。默认全量构建先检查所有输入/已有输出，再逐包发布；发生磁盘错误时已成功发布的包保留，不谎称整批原子成功。

## 验证与维护

```console
python -I -S -B tools/run_all_tests.py --report-dir docs/validation/new-test-run
python -I -S -B tools/run_skill_tests.py tests -p test_runtime_packager.py
python -I -S -B tools/run_skill_tests.py tests -p test_skill_runtime_packages.py
python -I -S -B tools/run_skill_tests.py tests -p test_docx_runtime_package.py
python -I -S -B tools/check_project_docs.py
```

全量回归按 Skill 分进程，另跑仓库跨技能套件与PPTX适配器；报告目录必须是新目录。零发现、导入错误和测试失败不得算通过，跳过数单独记录。受影响源码测试仍可用 `tools/run_skill_tests.py skills/<name>/scripts/test` 运行。

独立安装验收从ZIP解压到互不相邻的只读安装，非root实际尝试写入并确认拒绝；业务工作目录另建，Python保留 `-I -S -B`、空PATH。核对源码、ZIP、安装文件前后哈希。直接业务进程使用Python审计钩子拒绝仓库及未声明其他包读取；这不是OS级沙箱，workflow子进程不继承审计钩子，仅获得明确的独立提供者路径。

覆盖生成/读取/编辑、ET保存值和partial语义、OFD字体原始哈希、PPTX原生生成、生命周期归档清理、组合依赖缺失，以及所有包严格路径闭包。真实原生ET、Word/WPS/PPT视觉、真实模型自动选技能、云空间同步和所有平台仍须分别验收。源码ET/WPS已有缓存不在此次授权清理范围内；源码严格检查可受缓存警告影响，所有运行包必须无缓存并通过严格检查。

## 当前结果

本轮验收基于 `e86522e` 加本轮工作树变更，证据生成时尚未提交；后续分批提交与推送状态以 Git 历史和远端 SHA 为准。详细大小与SHA256见 [10包发布记录](validation/runtime-release-20261109.json)，完整回归见 [2368项汇总](validation/restructure-tests-final-20261109/summary.json)，[源码完整性](validation/restructure-integrity.json) 记录255份正式Python未改与原包/样本保护。

- macOS 27.2 arm64，Python3.13.12/Expat2.6.3：全部2368测试通过，0失败、0跳过；包含DOCX421、ET480、OFD152、PPTX100、WPS447、XLSX401、生命周期17、开发规范31、仓库316和PPTX适配器3。
- 最终合集另在Debian13.6/Linux x86_64、非root uid1000、Python3.12.14/Expat2.8.3完成11项隔离验收（0跳过）及5项验收器边界测试，十个内部包和外层合集SHA一致；见 [Linux汇总](validation/runtime-linux-summary-20261109.json)。未把合成ET或预期partial宣称为完整原生格式支持。
- 对最终实际ZIP在Mac另跑SHA校验与11项隔离验收全部通过；十个发布包严格检查全部通过。源码ET/WPS只因原有缓存产生严格模式警告，保留记录，不称源码全量strict通过。
- 第一轮仓库回归发现静态加载快照已过期，旧快照保留在history后刷新，第二轮全部通过；这不是宿主token测量。夹具迁移遗漏也已修复，最终无跳过。
- 修改的Python AST检查通过，超过200字符的一处既有行保持AST等价换行；Pylint未安装，未宣称全量lint通过。
- 合计压缩8,560,938→7,840,957字节，减少8.4%；展开17,284,333→14,827,539字节，减少14.2%。OFD字体占总包主体，完整保留。

| Skill | 旧ZIP字节 | 新ZIP字节 | 缩小 |
| --- | ---: | ---: | ---: |
| artifact-lifecycle | 32,432 | 28,452 | 12.3% |
| bank-writing | 90,082 | 90,082 | 0% |
| docx | 459,195 | 295,752 | 35.6% |
| et | 434,611 | 238,851 | 45.0% |
| ofd | 6,084,411 | 6,042,483 | 0.7% |
| pptx | 528,963 | 494,379 | 6.5% |
| skill-development-standards | 24,903 | 24,903 | 0% |
| table-workflow | 53,304 | 50,817 | 4.7% |
| wps | 380,726 | 217,403 | 42.9% |
| xlsx | 472,311 | 357,835 | 24.2% |

以上比较对象为保留的20261108.0102.2369完整包。旧 [DOCX验收](docx-runtime-contract-validation.json) 保持其原版本，不替代全体新包验收。Windows、真实客户端视觉与宿主集成未验证；Linux结论仅限独立记录中实际运行的范围。
