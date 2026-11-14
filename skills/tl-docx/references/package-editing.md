# 受控部件解包与重包

路径占位符说明：从宿主实际读取主入口 `SKILL.md` 的真实 `file_path` 去掉文件名确定 `<skill-root>`；相对读取路径按宿主确认的基准目录解析。使用前替换并引用完整路径，无法定位则停止。

本能力用于明确要求的部件级维护，不作为日常改字或改字体的默认方式。使用已确认任务目录的 `execute`，不切换目录、不向安装目录写入。

```console
python -I -B "<skill-root>/scripts/tl_docx.py" unpack 原件.docx -o 部件包.json
python -I -B "<skill-root>/scripts/tl_docx.py" repack 原件.docx 副本.docx < 部件包.json
```

`unpack` 返回顶层 JSON 部件包，不展开目录树。UTF-8 XML 为可编辑文本，其他内容为 base64；保留 source_sha256、extension、每个部件的 member 和原 sha256。只修改明确目标的 content，不修改源摘要或原摘要，不增删部件。包总 JSON 限 16 MiB，超限使用定点读取/编辑，不输出半份包。无 `-o` 时 stdout 即完整包；导出默认维护材料，不归档，明确需要交付才加 `--delivery`。

`repack` 从 stdin 读取完整包，核对源身份、完整成员集合、原摘要、XML 和关系；保留 DOCX/DOTX 扩展名。数字签名文档不允许改包，不访问外链。原件不变，未改部件的解压字节保持不变；改过的 ZIP 不承诺整体字节或元数据一致。无修改时保持原 ZIP 字节。

新文件先静态验证，再通过现有发布、归档和清理流程交付；验收 `status=edited`、publication、cleanup 和独立 archive_status。归档失败保留主文件，只重试归档。禁止覆盖原件。任意部件修改仍须理解 OOXML，静态检查不替代完整 schema、关系语义或阅读器视觉验收。
