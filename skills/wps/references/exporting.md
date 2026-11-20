# Word 转 WPS 兼容格式

## 自检与操作

先遵循主入口的任务目录、保护和交付边界，输入相对默认任务目录，输出仅传裸文件名并固定写入任务根目录。仅本次导出才加载本参考，不预读所有后端。首次转换前运行所选后端自检，要求退出0、JSON `status=environment-ok` 并核对可用后端；全部OOXML输入另需Expat >=2.6。

```python
execute(command="\"<skill-root>/scripts/.venv/bin/python\" -I -S -B \"<skill-root>/scripts/export_wps.py\" check --backend rtf")
# 仅需要预装 LibreOffice 二进制能力时
execute(command="\"<skill-root>/scripts/.venv/bin/python\" -I -S -B \"<skill-root>/scripts/export_wps.py\" check --backend libreoffice")
```

将已排版的 `.docx` 转换为 WPS 兼容的 `.wps` 文档：

```python
# 零依赖纯 Python RTF 后端（受支持内容映射，不承诺原版式保真）
execute(command="\"<skill-root>/scripts/.venv/bin/python\" -I -S -B \"<skill-root>/scripts/export_wps.py\" convert \"./input.docx\" --output \"output.wps\" --backend rtf")

# 自适应 .wps：优先 LibreOffice；失败可回退 RTF，回执标记 partial 与 fallback
execute(command="\"<skill-root>/scripts/.venv/bin/python\" -I -S -B \"<skill-root>/scripts/export_wps.py\" convert \"./input.docx\" --output \"output.wps\" --backend auto")
```

显式要求二进制 DOC 时将输出设为 `output.doc`；`auto` 与 `libreoffice` 均不向该后缀回退 RTF。输出 `.rtf` 的 `auto` 直接使用 RTF 后端。候选先在独占临时目录中转换并经现有读取器核验，再发布；转换或结构失败保留已有目标。来源和候选的 `partial`、遗漏诊断均保留，不能把主文字回读成功当作版式或全格式验收。

| 导出专项 | 说明与按需参考 |
|---|---|
| 导出四级验收标准 | 转换完成、结构合规（拦截假冒扩展名）、内容回读、WPS实机打开，详见 导出验收标准 (`<skill-root>/references/export-acceptance-criteria.md`)。 |
| 纯 Python RTF 后端机制 | 零外部依赖、中文字体映射表（宋体/仿宋/黑体/楷体）、公文边距与跨页表格控制，详见 RTF 后端规范 (`<skill-root>/references/export-rtf-backend.md`)。 |
| LibreOffice 二进制导出 | 调用系统 soffice 导出 CFB/WordDocument 二进制结构，详见 LibreOffice 后端规范 (`<skill-root>/references/export-libreoffice-backend.md`)。 |

## 输出与结果

省略 `--output` 时使用输入同主名 `.wps`，固定写任务根目录；仍只写未占用的新文件。用户要求RTF内容时显式 `--backend rtf`，不因.wps改用二进制。默认名称和格式说明按主入口；不承诺原版式保真。

转换 success退出0、partial退出3、失败1、参数错误2。`inspect` 校验输出候选，不替代输入读取；格式/后缀不符退出1，有效但部分退出3，完整声明子集退出0。`--verify-read` 通过真实读取器返回 `readback-ok` / `readback-partial`，保留退出0/3和诊断；回读失败不得完整交付。头部魔数或文件存在不足以通过验收。

必须按四级验收区分转换/结构/内容/客户端；具体字段、验收缺口或要求实机打开时读上述验收标准。仅需RTF映射细节才读RTF后端，仅需二进制能力才读LibreOffice后端，不为成功的简单转换预读所有机制。
