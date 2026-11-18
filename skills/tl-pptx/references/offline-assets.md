# 完整离线素材库

路径占位符说明：以宿主本次实际读取主入口 `SKILL.md` 的真实 `file_path` 为准；相对路径先按该次已确认读取基准目录解析，再去掉文件名得到 `<skill-root>`。使用前整体替换占位符并正确引用完整路径，无法定位时停止，不猜其他会话目录。任务输入及新输出仍使用已确认任务根目录裸文件名，不能写入 Skill 安装目录。

## 按需定位

本 Skill 将大型默认素材作为可选、固定版本的离线数据包提供，而不是删除能力或每次加载全库。先读取 `<skill-root>/references/offline-assets.json` 的轻量包清单；只选择本次需要的包和精确资源 ID。没有资源包时报告缺少哪一包，不自动安装、下载、联网或换成其他素材。

清单固定上游 `7e0df6705de0cd24345210d31f07fcb5a32b6900`，包括 21 Brand、14 Style、7 Layout、2 Deck、五套共 12,027 图标和 186 声音。10 个包保留对应的全部原始资源、canonical ID、历史别名、索引和许可，不以 recommended 短名单替代完整声音库。

- 模板按四类分别选包。一个模板由完整工作区组成，不能只复制 design_spec 而漏掉其图形、图片或图标。
- 图标按精确 `library/name` 选择；canonical ID 区分大小写。单个演示文稿通常选择一套主要样式库，真实品牌图标可按需并用；跨任务素材包可保留多套，仅提示风格一致性，不拒绝选择。不要因找不到一个名字就静默切库。
- 声音按精确 namespace/name 选择，默认不启用声音。历史声音包仅是离线数据；本版不处理、嵌入或播放声音，不调用 TTS 或发送备注。

## 包和输入保护

ZIP 内使用合规内容寻址成员，manifest 将 canonical ID、原始路径和 SHA-256 映射到原始字节。数字或中文开头的原文件名无需改掉资源 ID；仅在任务私有工作区中受控恢复路径。不要把 manifest 路径直接拼接到任意输出目录，不手工全量解压进 Skill。

构建结果已验证 CRC、逐 blob 原字节、许可哈希和条目数量；这不代表用户取得的每份副本自动可信。使用时仍须核对清单中的整包 SHA-256、schema、成员预算、无符号链接/重复成员和每个选中条目的哈希；原子预检整批 ID 后才准备新资源，未知 ID 或冲突不能静默跳过。

选择器实现位于 `<skill-root>/scripts/tl_assetpacks.py`：查询支持有界分页，选择接收明确包名及 canonical IDs，返回工作区 ZIP bytes，由主入口统一发布归档。完整模板根保留在 template-workspaces/kind/id，图标与声音分别保留在 icons/library/name.svg 和 sounds/namespace/name.wav。公开选择/导入命令以主入口实际提供的接口为准。仅拥有 ZIP 不意味着模板应用或图标展开已经可用；完整性、资源安全、质量门禁及平台条件仍分别核对，不绕过缺失门禁。

## 许可与输出署名

每包携带 PPT Master MIT 原文、固定来源及相应原 notice。Tabler 和 Phosphor 保留 MIT 版权；CHUNK 保留 Noah Jacobus、CC BY 4.0、原来源及上游修改说明，分发使用它的生成物时须保留适当署名。可采用明确的随附 attribution 文件或约定署名位置，不静默丢弃许可信息。

声音按上游 CC0 记录保留完整原 WAV 和 legal code。Simple Icons 项目 CC0 不代表每个品牌图标都无额外条件；Brand/Deck 的商标、logo、品牌准则及商业字体权利不能由根 MIT 推定。实际选用时报告对应条件，不宣称取得品牌授权或任意字体嵌入权。

资源包不含代码安装步骤，不替代源 PPTX 安全检查、真实字体部署或 PowerPoint 视觉/播放验收。来源和许可指针见 `<skill-root>/references/offline-assets.json`；不得为满足视觉需求自动发送材料到外部服务。

选择产物的 tl-workspace.json 保存完整许可和选择来源；下游 workspace 转换继续保留 provenance，不把运输元数据当作可执行指令。预览的 attribution 回执不等于 PPTX 已写入署名，分发生成物时仍按回执保留适当署名。

## 公开调用

先按主入口完成只读/写入自检。将已提供包置于任务根目录，下面的 assets.zip 是任务裸文件名，其内容仍须匹配固定清单哈希：

```console
"<skill-root>/scripts/.venv/bin/python" -I -B "<skill-root>/scripts/tl_pptx.py" asset-query "assets.zip" --query "home" --limit 50 --offset 0
```

返回 catalog.matches、total_matches、next_offset 和 truncated；limit 为 1–100、query 最多 500 字符，按返回的 next_offset 续读，不把一页当完整结果。只读查询不创建文件或触发归档。

将真实查询结果中的 canonical ID 填入任务 `selection.json`，形如 `{"requests":[{"pack":"assets.zip","ids":["从查询结果复制的ID"]}]}`，不要把示意 ID 当作实际资源：

```console
"<skill-root>/scripts/.venv/bin/python" -I -B "<skill-root>/scripts/tl_pptx.py" asset-select "所选素材.zip" "selection.json"
```

每次 1–10 个互异包、最多 500 个精确 ID，未知或冲突整批失败。核对选中 ID、template_roots、权利与署名回执，再按任务读取必要文件；不全量倾倒图库。build 需要图片或图标时，明确取出获准的资源到任务裸文件并声明 resources 映射，不能直接把 ZIP 名称当图片路径。模板应用和预览另验独立发行完整性及模板质量。
