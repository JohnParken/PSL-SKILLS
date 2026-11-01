# DOCX P3 读取增强版与 main 比对记录（未完成源码上传）

候选包：docx-skill-p3-reading-fastpath-20261101.zip；116 个文件；已在本次本地工作环境解压。
基准：main @ b47b1ba1b39d060421c16679ab92a64d8fd22f88。

关键对比（Git blob SHA）：
- scripts/run_docx_task.py：候选包与 main 均为 7783794a160f0225879cc89d1e06968ed7e9bbc6（完全一致）。
- scripts/create_docx.py：候选包 6397e335bce3216dc2d2ebb3d2d814a1ea99c723；main 5a5d602ac1af0546a911f356f05eb830198706d2（不同）。
- scripts/read_docx.py：候选包 97d5b40c60b5f2728f88edf67afced875febb439；main b53ff49e9e455c082ad4d1bf67805e4468cf50db（不同）。
- SKILL.md：候选包 bc3d8367348d204713ed63d591580148c083f06e；main abc6b2e83e21ee2c9696b34c28d2128bc7987ad2（不同）。

候选版新增：轻量读取参数与读取回归、OOXML 格式模块、分页治理、按需 references 路由与审计等。主干具有独立银行排版、生命周期治理及仓库级严格规范，需保留。

结论：建议择优合并读取 Fast Path 与测试，但不宜将 ZIP 整体覆盖 main。必须先完成逐文件迁移及双套测试。

**注意：本分支目前只包含本比对记录，不包含候选 Skill 源码。** 当前 GitHub 连接不支持直接导入本地 ZIP 文件树，因此未声称已提交源码或完成合并。