# 复用、来源与验证

## 接口与部署

业务技能先负责内容生成及验收，本技能负责清单归档和安全收尾。推荐调用已实际读取定位的 `lifecycle.py` CLI；每次使用保持任务目录不变，保留完整JSON。不要为执行脚本切换到技能源码目录，也不要扫描其他任务文件。

Python集成可从本独立包的scripts导入：

| 模块 | 接口与职责 |
|---|---|
| router_config | read_environment_parameter(name, environ=...)、get_common_parameter(raw,name)，返回原值、不拼路径 |
| output_location | resolve_output_location(raw)，questionId校验、相对归档位置及fallback原因 |
| archive_deliverables | archive_outputs(outputs,protected_inputs)、finalize_delivery、archive_user_message |
| artifact_files | 既有内部路径检查和单文件原子复制原语，公开调用优先archive_outputs |
| controlled_cleanup | TaskFiles、plan_cleanup、seal_cleanup、cleanup_task |
| cleanup_task | 保留DOCX来源的兼容CLI；公共默认使用lifecycle.py |

独立ZIP包含所有同级依赖，不从DOCX或仓库公共目录运行时导入。调用者安装本技能后通过harness确定其位置；未安装或无法定位时报告缺少能力，不猜相邻路径、不自动安装。若将来要求业务技能单包自包含，需要显式引入受追踪副本及同步测试，不在本轮批量迁移。

## 与DOCX的关系

本批经用户授权从已验证DOCX提取分发副本，文件来源及哈希见[来源清单](upstream.json)。DOCX保持现有代码、标准runner和CLI。router_config、output_location、artifact_files、controlled_cleanup、cleanup_task与DOCX逐字节一致；archive_deliverables只保留main之前的共享实现，移除DOCX专属CLI自检依赖。旧docx_cleanup_v1 schema及内部docx临时前缀保留兼容，不意味着公共技能只处理DOCX。

仓库维护回归 `tests/test_artifact_lifecycle_parity.py` 同时比较DOCX来源、公共副本和来源清单哈希。任一边改变时必须先审阅语义差异、同步适用副本与清单、重跑双方回归再更新对应版本包；不能仅更新哈希把未知差异视为通过。该仓库测试不随公共技能包发布；独立包用自身合成测试，不要求DOCX存在。此机制可检测分叉，不自动同步代码，也不宣称已经完成共享源码迁移。未来可评估单一构建源生成各独立包，本轮不改变DOCX行为。

## 验证边界

从任务目录运行：

```console
python -I -S -B -m unittest discover -s "<skill_dir>/scripts" -p 'test_*.py' -v
```

测试使用独占临时目录与标准库合成文件，验证配置、归档、输入保护、显式登记、失败重试及幂等清理。不是对真实用户残留文件的清理授权，不证明业务格式正确、真实模型选技能、云端Linux或Windows兼容、断电恢复或恶意并发隔离。发布时核对CRC、逐成员哈希和独立解包测试，保留旧包；本技能不处理技能开发发布ZIP的历史清理，也不上传/分享交付物。
