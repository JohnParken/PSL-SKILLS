# tl-docx

本 Skill 属于允许按需使用第三方组件的 `tl-` 系列，不受“仅 Python 标准库”限制；当前实际依赖仍以下方说明及 `requirements.txt` 为准。本次政策调整未新增组件，不扩大已实现能力，路径、安全与归档清理约束不变。

当前 Skill 版本 `20261117.1327.0512`。项目源码在 [Skill 入口](../../skills/tl-docx/SKILL.md)。Python ≥3.10，所有XML解析要求解释器内置 Expat ≥2.6.0，唯一直接依赖 `python-docx==1.2.0`。使用其文档对象及 XML 验证，标准库处理包边界、原始 XML 结构、续读、模板替换、发布和归档。`lxml`、`typing_extensions` 由上游组件传递安装。 [python-docx 1.2.0 依赖声明](https://github.com/python-openxml/python-docx/blob/v1.2.0/pyproject.toml)

## 与 docx 的能力对照

| 任务 | tl-docx 实际入口及覆盖 |
| --- | --- |
| 读取报告 | 普通阅读默认内容优先，聚焦正文、表格与图表缓存，`--full` 获取完整报告。标题支持有界样式继承，保留正文脚注/尾注/批注引用锚点，概要包含标题，章节统计与全篇诊断分列。支持原始 XML、节点树、三个修订视图、精细表头与合并定位、图形偏移、图表点分批、精简表格和重复章节消歧。`-o` 保存 JSON、文本或 XML，归档可显式开启。见 [本轮月报与安装验收](../maintenance/tl-docx/read-write-20261113.md)、[前轮回归](../maintenance/tl-docx/parity-20261113.md) 和 [首版验收](../maintenance/tl-docx/validation.md)。 |
| 创建 | 支持公文、商务、学术主题、四种 Profile、角色样式和局部格式覆盖、中西文字体、多级编号、复杂表格与表头正文格式、毫米页面/分节尺寸、首页和奇偶页眉页脚、分节页码及独立交叉引用块。 |
| 编辑及批注 | `edit` 支持定点文字和 run 格式、同格式纯文本 run 受控合并、段落插入、规则表格行和简单单元格、同类型图片；另有 `format_cell_run` / `replace_cell_run_text`，按物理单元格、段落和 run 精确定位未合并单元格，或显式expected_merge且拓扑证明通过的合并原点中的多段/多 run，保留其他文字与格式。 `format_story_run` / `replace_story_run_text` 另支持单节、显式唯一 default 引用的现存纯文本页眉页脚；拒绝共享、继承、多节、复杂内容及与正文操作混批。`replace` 按预期次数批量填充正文、指定页眉页脚的普通文字。`comments` 支持 DOCX/DOTX 的现代四部件批注查询、增加、一级回复、更新及删除，经典简单批注支持查询、更新、删除。格式参考 [Microsoft Word 扩展规范](https://learn.microsoft.com/en-us/openspecs/office_standards/ms-docx/b839fe1f-e1ca-4fa6-8c26-5954d0abbccd)。 |
| 分页、命名及交付 | `validate --strict-pagination` 可将 12 类静态分页警告作为质量门禁。创建与编辑可自动命名，同名递增，发布时记录原清单。`resume` 只恢复该任务的收尾，`archive` 可按已核对的 SHA-256 重试归档。 |

## 剩余读写差距

对照工作树中 `docx` 版本 `20261113.2302.5108` 的入口、参考与实现。两套 CLI 的参数和 JSON 格式按各自契约使用。

| 方向 | 当前 `docx` 已有而 `tl-docx` 尚缺或更窄 |
| --- | --- |
| 结构与报告兼容 | 节点树用自有 schema，原始部件可按字节取出。缺少旧版 `--start-block`、字符预算、旧结构和精简回执字段的兼容；已提供显式跨次内容缓存及释放，结构视图与旧缓存接口不兼容。单个大 XML 树可显式使用无损分片协议，消费者需先重组完整记录。复杂属性与表格修订保留结构和诊断，实际还原仍有限。 |
| 创建样式与验证 | 已补四种 Profile、角色/局部及表格文字格式、分节毫米尺寸和独立 `reference`。角色已使用原生样式继承，局部覆盖保留直接格式；已提供静态排版审查，不具备完整 schema/视觉渲染及全部旧参数兼容。 |
| 底层编辑 | 已支持单 run 受控文字修订与 JSON 部件包重包；复杂修订和目录式解包仍未提供；只读 `verify-format` 已支持单操作外部候选核验，拒绝多操作/同文件/相同字节，非视觉认证；内置 `format_run` 已自动执行独立逐步差异守卫。通用定点编辑仍限 DOCX，DOTX 写入支持批注和模板替换。 |
| 原清单恢复与交付细节 | 支持本工具原清单收尾。`docx` 的历史 worker 清单、历史 retry 事务、可编排的多文件清单与精简兼容回执仍由其原入口处理。 |

两者都依赖阅读器验证实际页码、断页、目录字段与字体效果。图表的工作簿实时值、图片 OCR、复杂线程和任意无损编辑仍需单独能力。`tl-docx` 已提供带 XML 定位的单元格段落、图片 SHA-256 和行内字段构建。

## 新增高级入口

显式 `read --cache` / `--cache-token` 与 `release-cache` 用于跨次内容分页；默认仅读仍不写文件。`edit` 的 `tracked_replace_run` 生成受控插入/删除修订。`unpack` / `repack` 使用顶层 JSON 部件包保护非目标部件，不展开子目录。`validate --strict-layout` 可对布局警告执行门禁，仍须阅读器视觉验收。

## 构建和使用

从仓库根执行 `python -I -S -B tools/package_skills.py tl-docx` 构建 ZIP。按 [白名单](../../packaging/runtime-manifests/tl-docx.json) 分发，保留必要运行文件，安装后从宿主确认的任务目录使用。所有运行命令显式使用 `"<skill-root>/scripts/.venv/bin/python" -I -B`，不使用裸 Python、不加 `-S`。从本次真实读取 `SKILL.md` 的 file_path 定位并替换 `<skill-root>`，完整路径须引用。部署方预先配置该解释器及依赖，路径缺失、不可执行或自检失败即停止，不回退全局或其他 Skill 环境、不自动安装。虚拟环境不随源码或运行 ZIP 分发；上面的标准库打包工具不是 TL 业务运行入口，保留原启动约束。合成测试、真实附件和维护验收材料各自独立，附件不纳入运行包。

本轮差距补齐、测试结果与验收边界见 [维护记录](../maintenance/tl-docx/gap-fill-20261113.md)。

高级能力补齐与边界见 [后续维护记录](../maintenance/tl-docx/advanced-gap-fill-20261114.md)。


## 内容读取完整性增强

当前按关系及类型定位附加部件；孤立内容、局部非关键 XML 损坏和未处理语义显式诊断。新增可辨 OMML 文字、继承/多级编号、散点/气泡/多层类别缓存及分节引用。大记录可显式无损 JSON 分片，不等于流式解析。保留工作簿实时值、OCR、完整视觉语义和未知扩展的限制，不能宣称任意 DOCX 已完整支持。

读取完整性增强与验收见 [维护记录](../maintenance/tl-docx/read-integrity-20261114.md)。

## 面向内容的读取

普通阅读、总结和问答首批显式使用 `read 文件.docx --max-output-bytes 12000`，结构追溯使用 `--full` 获取完整记录。默认内容视图减少内部结构信息，但不掩盖 partial、遗漏或续读状态；不是视觉渲染结果，也不代表全部 OOXML 内容已支持。

单元格文字保留段落边界和空段，避免不同段落粘连。图表显示包内保存的系列数据，缺失或稀疏缓存不补值，不读取外部工作簿或重新计算。形状与绘图存在未处理内容时明确诊断，视觉渲染及 OCR 未执行。`header.status` 区分 confirmed/candidate/none，confirmed 只说明显式范围或文档标记依据，不等于业务含义核验。表头候选不是用户确认的列含义，跨行或合并不明时不猜测。内容视图只接受 JSON，保留诊断/覆盖和分页；`--details` 可保留记录定位，其他结构与显式表头任务使用完整报告。

默认内容投影 `content-v2` 进一步移除文档属性、字段指令和嵌套内部定位，保留表头确定性、合并关系、保存的图表轴标题与自定义数据标签。诊断与完整记录仍按需使用 `--details` / `--full`；图表数据不代表工作簿实时值或完整视觉渲染。游标 `read-view-v6`、缓存 `tl-report-6` 不与旧版本混用。

本轮精简投影、完整续读及授权真实样本的范围与缺口见 [默认可见内容验收](../maintenance/tl-docx/visible-content-20261116.md)。

## 发布前独立门禁与缓存输出交接

创建先固定规范化输入和全部图片字节，生成器只取得独立副本；发布前从候选 OOXML 核对正文顺序、列表项文字、表格逻辑单元格及合并、图片内容与替代文字、字段占位和书签、分节页眉页脚及请求的元数据。`result.content_verification` 与原有静态结构/布局结果分开，不认证视觉效果、格式保真、编号标签或字段重算。

`format_run` 及新的表格 run 操作在组件加载、每次操作和最终序列化阶段保护解析后的正文树；仅豁免所请求属性和必要新节点。其他部件仍按解压字节核对，其他编辑类型不因此获得同等守卫。重复请求按操作顺序核对，重复属性等歧义拒绝；外部候选可用只读 `verify-format` 单操作核验，详见[专门边界](../../skills/tl-docx/references/external-format-check.md)。

显式读取缓存在完整 stdout write 与 flush 成功后才交接。本轮新建/重建缓存输出失败仅清理身份可核实的原 token；复用缓存保留可重试，其他缓存、未知或变化文件保护并报告。末页仍输出前释放；输出截断或失败不能当完整结果。详见[独立门禁维护记录](../maintenance/tl-docx/verification-gates-20261116.md)。

读取定位在单次只读解析中缓存原始兄弟序号，避免大量同级段落反复扫描；记录路径与内容契约不变。合成 report API 基准见上述维护记录，不承诺端到端或固定 token 降幅。

分页下一步提供结构化 `continuation`，包含实际任务相对源文件名、全部有效视图/预算/游标及当前缓存token，末页不返回。异常新增稳定 `error_code` / `retryable` / `next_action`，保留API1及原有状态/退出码；读取partial仍是可用结果而非失败。分片JSON按键规范化以保证冷读/缓存复用/重建的一致切分，旧分片游标须从首批重新读取，不能混拼；普通非分片游标保持原绑定。

本轮新增解析前XML节点/深度/字符累计预算、report构建与投影/缓存复核，以及内容投影去冗余拷贝和嵌套路径索引。资源限制不是流式解析或进程RSS保证；完整预算与收紧的Expat前提见[读取参考](../../skills/tl-docx/references/reading.md)。本轮验收另见[优化记录](../maintenance/tl-docx/resource-optimization-20261116.md)。

## 有界普通读取与指导文档

推荐命令显式传入 12000 字节预算；无预算 CLI/API 的完整首读默认行为保持兼容。按返回的完整 continuation 参数续读并核对连续覆盖，首批、局部范围和摘要不能冒充全文。预算分页不再先序列化全部剩余记录，但局部读取仍先生成全文报告，不是流式解析、整体加速或 token 成本保证。

读取支持 DOCX/DOTX；创建和通用 edit 输出 DOCX，模板替换与批注按各自契约支持 DOTX。新建参数与已有文档受控编辑分开选路，不把创建样式参数当作任意现存文档格式编辑能力。详见[指导与有界读取验收清单](../maintenance/tl-docx/acceptance-bounded-read-20261117.md)。

## 显式解释器验证

在已确认任务目录运行 `"<skill-root>/scripts/.venv/bin/python" -I -B "<skill-root>/scripts/tl_docx.py" check`；要求退出 0 且 `status=environment-ok`。维护时以同一解释器运行 `-I -B -m unittest discover -s "<skill-root>/scripts/test" -p 'test_*.py' -q`。测试子进程的 `sys.executable` 继承此入口，不另寻解释器。当前路径约定与本机验证限制见[维护记录](../maintenance/tl-docx/explicit-python-20261117.md)。
