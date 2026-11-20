# document-navigation：上传文档先导航

Skill 版本：`20261120.2054.3020`。正式入口为 [SKILL.md](../../skills/document-navigation/SKILL.md)。[运行包清单](../../packaging/runtime-manifests/document-navigation.json) 只分发这份指引，没有解析器或调度程序。

## 三步流程

1. 用户上传办公文档时优先选择导航，先在当前 `./` 执行 `ls -la`，以本轮附件映射和指定文件名核对目标。上传和待查交付物只查顶层，不从内部归档找替代品；歧义询问，缺失报告。无附件且无需查找已有文件的新建任务不列目录。业务入口仅保留顶层定位的兜底边界。
2. 按实际格式和任务加载专业 Skill，再读取或操作；不用 `read_file` 打开 DOCX、ET、PDF 等文档本体。存在对应 `tl-` 版本的格式统一选用 `tl-` 技能（DOCX/DOTX 选 `tl-docx`，PPTX 选 `tl-pptx`）。PDF 需要宿主另有相应技能；没有专业支持时报告缺口，不猜格式或自动转换。
3. 只上传而没有说明目的时，确认附件后询问用户。不为导航创建任何文件。目录列表不是内容读取，静态路由测试也不是宿主触发测试。

## 部署边界

需让宿主发现该 Skill 并提供只读列目录能力。默认按元信息选择的宿主无法仅靠 Markdown 强制首次加载；如需每次上传都必经，须由宿主的附件路由或预加载配置保证。各格式技能仍可独立使用。

旧版范围与验收仅供追溯：[初版](../maintenance/document-navigation/validation-20261112.md)、[原触发范围](../maintenance/document-navigation/intent-trigger-20261112.md)。
