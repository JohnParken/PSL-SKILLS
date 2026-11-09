# 公共生命周期维护验证

以下路径以已确认仓库根目录为基准，历史结果保持原始基线。测试不进入业务运行包。

## 验证边界

从任务目录运行：

```console
python -I -S -B -m unittest discover -s "skills/artifact-lifecycle/scripts/test" -p 'test_*.py' -v
```

测试使用独占临时目录与标准库合成文件，验证配置、归档、输入保护、显式登记、失败重试及幂等清理。不是对真实用户残留文件的清理授权，不证明业务格式正确、真实模型选技能、云端Linux或Windows兼容、断电恢复或恶意并发隔离。发布时核对CRC、逐成员哈希和独立解包测试，保留旧包；本技能不处理技能开发发布ZIP的历史清理，也不上传/分享交付物。


本次参数裁剪维护（2026-10-29，版本20261029.1442.4400）：router_config返回命名字符串值前strip两端空白，原始环境值、内部内容与非字符串类型保持。公共15项合成测试、来源一致性1项及严格结构检查在macOS/Python3.12.14通过；包含CLI实际归档到裁剪后目录、输入及主文件保持的验证。未更新ZIP，未在目标Linux/Windows验证；未运行专用命名linter，命名人工复核。主代理完成全部工作，未调用子代理。

本次上线修复同步DOCX来源与XLSX受追踪副本：配置日志默认关闭、开启也不记录原值；增加任务输入路径验证，公共clean明确仅处理登记项。来源版本和哈希见现行upstream.json；独立包和回归结果由本轮仓库验证记录提供，不沿用历史验证替代当前版本验收。

2026-11-03 同步：归档成功前核对主文件与副本 SHA-256，报告 archive_verified；失败区分跨文件系统、权限和空间问题。来源版本 20261103.1521.2675，公共16项及一致性1项通过，两个 Skill 严格检查通过；macOS 本机合成验收，未验证 Linux/Windows 或生产云存储，未更新发布 ZIP。description 适用范围保持准确，依赖仍为标准库。Main-agent work：实现同步、来源记录与验收；Subagent execution record：未调用子代理。

2026-11-03，版本20261103.1633.1524：同步 archive_user_notice 的状态文案生成，原机器 archive_message 保留；副本与来源哈希一致。公共16项、一致性1项、严格检查通过；DOCX399项覆盖共用文案和CLI回传。description与依赖无变化；macOS合成验证，未进行Linux/Windows或宿主展示验收，未更新ZIP。Main-agent work：同步及审查；Subagent execution record：未调用子代理。
