# TL PPTX 离线完整素材包方案（固定快照审计）

本文记录初始实现方案、来源审计及后续本地构建验收，不是获准裁剪列表。目标仍是保留上游全部 canonical ID、原始资源字节、兼容别名和许可材料；运行时仅准备用户明确选择的资源，不每次加载全库或联网。

## 1. 固定基线和实际规模

上游：`hugohe3/ppt-master`，固定提交 `7e0df6705de0cd24345210d31f07fcb5a32b6900`。本次直接读取 Git tree/blob，不把稀疏检出的可见文件当完整库。以下六类共 **12,397 文件，25,190,343 字节原始有效载荷**，对应 12,386 个唯一 Git blob。先前“约 65 MB”不能作为有效载荷数字；小文件的磁盘分配空间与 Git blob 大小不是同一口径。

| 目录 | 文件数 | 原始字节 | 可选资源身份 |
| --- | ---: | ---: | --- |
| brands | 44 | 1,533,208 | 21 Brand |
| styles | 16 | 159,655 | 14 Style |
| layouts | 95 | 254,664 | 7 Layout |
| decks | 22 | 517,494 | 2 Deck |
| icons | 12,029 | 10,181,948 | 12,027 SVG，另 2 说明/声明文件 |
| sounds | 191 | 12,543,374 | 186 WAV，另 5 索引/词汇/许可/说明文件 |

图标库分别为 CHUNK 641、Tabler filled 1,055、Tabler outline 5,138、Phosphor duotone 1,518、Simple Icons 3,675。保留原有历史拼写别名，不把上游当前移除的品牌 ID 自动删除。

四个模板索引的 ID 集与实际工作区目录完全一致，每个 ID 均存在 `templates/design_spec.md`。声音索引的 186 个 ID、文件名、SHA-256 已逐项核对成功。全部 12,397 条文件已重新计算 Git blob 哈希并与固定 tree 一致，另生成逐文件 SHA-256。

全 `templates/` 树另含 charts、tables、schemas、scaffolds 和根参考文件，总计 12,450 文件、25,637,635 字节；其中 schemas/scaffolds、chart/table 索引由本轮其他核心模块迁移。这些不是本六类资源方案默认删除的内容。

## 2. 许可和权利边界

| 内容 | 固定快照内的许可依据 | 打包与使用要求 |
| --- | --- | --- |
| PPT Master 自有代码、规格及模板编排 | Skill 根 MIT，Copyright (c) 2025–2026 Hugo He | 每个可独立分发包保留完整 MIT、来源提交、版权与适配说明；不改称官方原版 |
| Tabler、Phosphor | icons/THIRD_PARTY_NOTICES.md 内完整 MIT 条款、各自版权及固定版本 | 同包携带原声明；保留历史 aliases 和本地归一化差异 |
| CHUNK | 同一声明标注 Noah Jacobus、CC BY 4.0、来源及 PPT Master 修改 | 保留作者、来源、许可链接及修改说明；生成物分发时不能仅因离线取用而漏掉适当署名，可通过随附 attribution 文件或约定的演示文稿署名位置提供 |
| Simple Icons | 项目 CC0 声明，同时明确品牌商标、个别图标许可和品牌使用准则可能另有限制 | 不能把 3,675 个品牌图标全部宣称为无条件 CC0；保留 222 个兼容文件及原免责声明，选用实际品牌时记录对应权利提示 |
| Kenney、BigSoundBank 声音 | sounds/THIRD_PARTY_NOTICES.md、CC0-1.0.txt、sounds_index.json 的逐来源/逐文件证据 | 保留全部 186 个声音，包括不在 recommended 短名单中的长声音；不裁剪、不重新响度处理、不重编码 |
| Brand/Deck 中 logo、品牌外观及提及的字体 | 上游工作区规格和根 MIT；没有发现逐品牌完整独立许可文件 | 可保存用户授权的固定来源快照和事实来源，不据根 MIT 推定商标许可、品牌授权或商业字体嵌入权；公开再分发权未逐品牌核实，实际选用按素材和用途核对 |

原图标 notice 以链接指向 CC BY、Simple Icons 的额外许可/免责声明；不能声称这些外链全文已随上游包含。构建完整离线许可补充包时可另外从所标官方固定版本/法律文本获取并记录哈希，作为补充文件，不替换原 notice 字节。本轮没有进行公开发布、网络素材下载或逐品牌法律清查。

## 3. 建议的独立 assetpack 布局

保留主 Skill 已有执行所需 schema、preset、字体度量及小型资源目录。大型可选素材不直接复制进 `skills/tl-pptx/`，也不修改仓库规范。建议一个发行 catalog 加 **10 个离线子包**：4 个模板种类包、5 个图标库包、1 个完整声音包。一次安装可选择全部，运行时只读取选中子包和条目；这属于部署分层，不是删能力。

每个 ZIP 使用合规的内容寻址成员：

```text
assetpack.json
licenses/LICENSE-PPT-MASTER.txt
licenses/original_notice.txt
blobs/b_<sha256>
```

`assetpack.json` 至少记录 schema、包 ID、上游完整 SHA、canonical ID、原始 UTF-8 相对路径、内容成员名、字节数、Git blob、SHA-256、媒体类型、许可/notice 引用和兼容别名。说明与许可也在清单中映射到原始来源路径；所有原文件字节原样保留。二进制 blob 由类型元数据恢复格式，不根据不可信扩展名猜执行方式。

156 个原文件名不符合源码首字符规则。内容寻址映射保留 `bigsoundbank/0572`、`simple-icons/1001tracklists` 等 canonical ID，选入本次任务私有工作区时才还原原资源路径。ID 与实体路径分离，不把规范改名变成资源 ID 改名；提取前检查大小写/Unicode 等价碰撞，拒绝歧义而不是覆盖。

不要把 12k 单体 ZIP 直接交给现有任务 workspace 解包器：其 10,000 成员上限用于任务安全，不应为素材库放宽。建议子包的可信发行清单固定包哈希，每个子包仍有独立成员/字节/压缩后与解压后预算；只解出实际选择的资源闭包。清单哈希用于识别发行版本，不把用户自述哈希当来源授权。

## 4. 实际消费 API 与适配路径

| 上游实际消费点 | 当前行为 | 独立资源方案 |
| --- | --- | --- |
| register_template 的 KIND_CONFIG/get_library_paths | 内置目录及 4 个种类索引；支持显式 templates_dir | 主 Skill 默认已映射 assets/templates；离线选择器读取外置 index，选择后还原单个完整工作区，注册只写本次私有输出库 |
| apply_templates | 明确 root 列表，安装 spec、SVG、images/icons，冲突预检及回执 | 输入为已验证任务 bundle；不扫描用户目录，不改安装 Skill，不把全库复制进每个项目；继续保留完整性与质量门禁 |
| icon_sync.sync_icons(project, ids, global_dir=...) | 依赖精确 library/name，复制到项目 icons/library/name.svg | 资源选择器先解析 catalog 精确 ID，只物化选中的图标并预检全部目标；保留一主风格库加品牌图标的选择约束；不得调用原函数绕过覆盖/路径预检 |
| svg_finalize/embed_icons、use_expander | 只消费项目本地 icons，不应直接搜索全库 | 为选择后的 bundle 图标闭包进行安全展开；展开前节点/字节预算，图标文件也接受 SVG 安全检查；不能只解除 data-icon 拒绝 |
| sound_sync.list_sounds/sync_sounds(..., global_dir=...) | sounds_index.json 驱动，校验 ID/文件/SHA，项目 sounds/ 路径 | 读取离线声音索引；选择后仅复制对应 WAV，回执附许可证据；未知 ID 整批失败，已有异内容文件拒绝 |
| narration / motion builder | 消费项目相对音频路径，不需要全局声音库 | 继续只读已物化且验证过的任务资源；默认静音仍合法，素材安装不自动启用声音 |

新资源入口建议为查询与选择两类：查询返回有界结果/游标及许可摘要；选择输入明确 canonical ID 列表，输出与当前 tl-workspace.json 协议兼容的任务 bundle bytes+报告，再由主 CLI 统一发布/归档。既不联网补缺，也不直接接受任意全局目录。缺包、错版本、缺条目、哈希不符、许可提示和资源冲突分别报告；不以空结果冒充完整库。

## 5. 后续验收与当前未完成项

1. 每个发行包逐条对照固定来源的路径、字节、哈希、ID 及 notice，确认所有 12,397 原文件均有去向；通过脚本构建确定性 ZIP，保持旧包不覆盖。
2. 分别测 ASCII、中文、数字起始 ID、历史别名、缺失/重复/未知 ID、恶意路径、Unicode/大小写碰撞、ZIP bomb、坏哈希、缺许可、只读安装及新文件独占发布。
3. 抽取代表性 Brand/Style/Layout/Deck 验证索引→选择→应用→预览；每个图标库至少一个真实原生输出、声音三个来源均验证音频关系与时长，另保留全库 SVG/XML 与 WAV 结构检查。
4. 当前 template-import 会生成 imported data-icon；注册/预览 SVG 外层 guard 和 use_expander 尚未完成受控本地图标闭环。已明确报告，这不是永久裁剪决定；资源包方案不能掩盖运行时闭环缺口。
5. 原上游完整性门禁目前仍 fail-closed，替换等效 TL 门禁等待明确授权。不能以直接调用业务 API 或跳过校验假装完整应用成功。
6. 首次审计阶段只完成 Git 对象/索引/哈希/许可来源审计及布局方案，尚未生成 assetpack；后续本地构建见下节。未复制 12k 文件到 Skill、未修改规范、未提交或推送。Linux/Office/真实宿主验证未执行。

完整逐文件审计清单保存在本轮维护临时记录中，供构建时合并来源清单；它不是 Skill 必需运行资源。本报告路径以 PSL-SKILLS 仓库根目录为基准。

## 6. 固定来源

- [六类模板和资源根](https://github.com/hugohe3/ppt-master/tree/7e0df6705de0cd24345210d31f07fcb5a32b6900/skills/ppt-master/templates)
- [图标第三方声明](https://github.com/hugohe3/ppt-master/blob/7e0df6705de0cd24345210d31f07fcb5a32b6900/skills/ppt-master/templates/icons/THIRD_PARTY_NOTICES.md)
- [声音第三方声明](https://github.com/hugohe3/ppt-master/blob/7e0df6705de0cd24345210d31f07fcb5a32b6900/skills/ppt-master/templates/sounds/THIRD_PARTY_NOTICES.md)
- [声音索引](https://github.com/hugohe3/ppt-master/blob/7e0df6705de0cd24345210d31f07fcb5a32b6900/skills/ppt-master/templates/sounds/sounds_index.json)

## 7. 后续本地构建与选择器验收

获准后已构建 10 个新命名包到仓库 `dist/runtime/`，总 ZIP 18,228,470 字节，最大单包 5,140 成员。既存包没有被覆盖。六类 12,397 原文件全部有映射去向；每包 CRC、逐 blob 原字节及 SHA、许可哈希、canonical ID 计数和 10,000 成员上限验证通过。主 Skill 仅新增 6,222 字节 `references/offline-assets.json` 目录和使用说明，不包含 dist 包或 5 MB 审计明细。

新增 `scripts/tl_assetpacks.py`：`query_asset_pack` 按包、查询词与游标有界返回；`select_asset_packs` 接受明确的包文件名和 canonical IDs，仅接收轻量清单中已知整包 SHA。输出标准 task workspace ZIP bytes，不安装、不联网、不自动发布。模板保留独立 `template-workspaces/<kind>/<id>/` 完整根；图标和声音保留 canonical namespace。混合主要图标风格只提示视觉建议，不拒绝多任务资源选择。

选择产物的 `tl-workspace.json.asset_selection` 保存来源、选择条目和完整许可 notice；`preserve_asset_attributions` 将其跨 workspace 转换保留为 provenance。模板适配识别这些根并只在私有副本处理；预览报告返回所需 attribution 信息，不声称已经写入 PPTX 署名页或 notes。后续发布生成物仍须保留适当署名。

真实 10 包及选择器 7 测试通过；模板适配 9 测试通过（含 import 51 页在转换前拒绝、returned SVG 实际物化图标且原输入不变），模板核心 12 测试通过。完整性门禁仍不绕过，注册/应用/预览公开成功尚不能宣称。独立 Linux/Office/DeepAgents 验收未执行。
