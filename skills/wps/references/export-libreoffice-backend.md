# LibreOffice 后端导出规范

路径占位符说明：`<skill-root>` 是本 Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 导出原理与机制

可选后端调用系统已有的 LibreOffice `MS Word 97` 过滤器，要求输出真正的 MS-DOC/CFB 字节。不是金山原生 WPS 生成器，也不保证任意文档转换保真。任务输入输出位于宿主确认的任务工作目录；建议使用带候选校验的公开入口：

```console
python -I -S -B "<skill-root>/scripts/export_wps.py" convert "./input.docx" --output "./output.doc" --backend libreoffice --verify-read
```

脚本使用 `--headless --convert-to "doc:MS Word 97"`，为每次调用创建独立输出目录及 UserInstallation 配置目录；取得 `.doc` 候选后通过现有有界 CFB/MS-DOC 读取器核验，再原子发布到 `.doc` 或兼容 `.wps`。仅换后缀、检查 CFB 魔数或发现旧目标文件均不能证明转换成功。

## 依赖与失败处理

1. 依次检查明确指定的可执行路径、SOFFICE_PATH／LIBREOFFICE_PATH、PATH 和已实现的常见安装位置。不得安装软件或联网补依赖。
2. 自检检查 Python 与后端可用性；发现可执行文件和版本信息不代表转换或客户端验收通过。
3. 保留转换进程的实际 stdout、stderr 与退出码；超时不伪造退出码。候选缺失、格式不符或结构失败时不发布，保留已有目标。临时目录仅由本次调用清理。
4. `.doc` 不允许 RTF 回退；显式 `libreoffice` 失败直接返回失败。仅 `.wps` 的 `auto` 允许改用 RTF，并返回 `status=partial`、`fallback_used=true` 与原失败原因；`output_format=rtf`、`binary_doc=false` 不得解释为二进制成功。`.rtf` 的 `auto` 直接使用纯 Python。

## 回执与验证边界

真实 MS-DOC 候选的 `output_format=ms_doc_subset`、`binary_doc=true` 只表示实际字节格式与支持的读取子集已核验。来源或候选返回 partial 时导出也返回 partial／退出3，保留 source_readback、readback 和遗漏诊断；不能用回读到的主文字证明未解析区域完整。

`layout_verification=not_run` 与 `full_format_verified=false` 保持明确。实际字体、分页、复杂表格、图形、域和 WPS/Word 客户端打开及再保存均需另行验收。模拟转换器测试验证调用、候选隔离、失败保护与状态传播，不等于 LibreOffice 实际生成验收。
