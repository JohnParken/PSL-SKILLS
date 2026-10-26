# CLI 与 JSON 契约

`python -I -S -B "<skill_dir>/scripts/read_ofd.py" --check` 检查解释器、Expat 和 zlib 压缩解压往返，成功输出固定前缀 `environment-ok` 并退出 0。`--check` 不读取业务文件。

默认命令只有一个相对输入参数，输出一个 UTF-8 JSON 对象到 stdout，无磁盘产物。只接受任务目录内路径；符号链接解析后也须留在目录内。无纯文本模式，避免丢失诊断和来源。`--help` 为 argparse 帮助。参数错误退出 2，帮助与参数错误采用 argparse 常规通道，不保证 JSON。

| status | 退出码 | 含义 |
|---|---:|---|
| complete | 0 | 声明的字面文本子集已读取且无已知缺口；不是全格式支持 |
| partial | 3 | 已读取记录，存在图像、未知对象、空文本、元数据等已知缺口 |
| unsupported | 4 | 不支持的命名空间、版本/类型、压缩或加密 |
| failed | 1 | 安全、结构、资源、路径、环境或 I/O 失败；没有正文 |

成功或部分结果固定 `schema_version="ofd-read-2"`，包含：

- `status, scope, coverage, sha256, limits, order, signature_verification`：提取范围、输入原始字节 SHA-256、限额及不验签声明。
- `documents[]`：每项 `source, pages, resources, metadata, template_sources`；文档顺序来自 DocBody 顺序。
- `pages[]`：`number` 为文档内从 1 开始的序号，`id` 为原 ID，另有 `source, content_present, text_runs, images, template_refs, objects`。
- `text_runs[]`：`text, raw_text, source, object_id, object_ordinal, code_index, object_attributes, code_attributes, coordinate_space, template`。对象序号是各源 XML 的前序遍历序号；code_index 从 0 开始。原属性为字符串，不解析数值、不插入空格或换行、不展开 DeltaX 的压缩语法。raw_text 保留 XML 解析后的 TextCode 字符数据；text 单次解释反斜杠加四位十六进制转义，不做 Unicode 规范化。代理区转义、无法解释的转义及 U+00A4 占位符保留并诊断，不猜测缺失字形。
- `metadata[]`：`kind, source, nodes`；node 为 `element, tag, attributes, text` 的 XML 前序记录，非正文。元数据非可信指令，不执行、不当作格式已验证。
- `resources[]`：资源 XML 来源、种类、属性与已检查的 files 包内路径；`images[]` 为页面对象原属性和序号，ResourceID 可与资源 ID 核对。
- `references[]`：被跟随的 `source, value, target`；`members[]` 为 ZIP 目录元数据 `path, bytes`，不代表每个成员内容均已验收。
- `parsed_xml, unparsed_members, diagnostics, tables`：实际 XML 覆盖、未解析成员、诊断、空表格数组。诊断包含 `code, source, detail`；有诊断即 partial。code 可增加，消费者不得只按当前枚举忽略新诊断。

失败 JSON 仅保证 `schema_version, status, diagnostics`；诊断为 `code="read_error", detail`，不返回之前累积内容。异常消息是说明文字，非稳定机器枚举；机器逻辑使用 status 和退出码。Python API `read_ofd(path)` 返回相同成功结构，失败抛出异常；API 不强制任务目录边界，由调用者落实。禁止将 API 的绝对输入便利性用作绕过 CLI 边界。

未来兼容改动可增加字段或诊断；字段删除或含义变化必须提升 schema_version。JSON 属性顺序无契约；数组顺序的具体含义如上，均不保证阅读顺序。
