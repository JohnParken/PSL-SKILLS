# 验证方法、样本证据与未验证范围

路径占位符说明：`<skill-root>` 是 ET Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 ET 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 目录

- 验证范围与方法 (`<skill-root>/references/validation.md#验证范围与方法`)
- DXF 增量与可读性验证 (`<skill-root>/references/validation.md#dxf-增量与可读性验证`)
- 真实 ET 样例验证 (`<skill-root>/references/validation.md#真实-et-样例验证`)
- 可复现的本地检查 (`<skill-root>/references/validation.md#可复现的本地检查`)
- 样本与未验证边界 (`<skill-root>/references/validation.md#样本与未验证边界`)
- 历史证据 (`<skill-root>/references/validation.md#历史证据`)

## 验证范围与方法

本技能面向金山 WPS 兼容电子表格（.et/.ett）及其底层 BIFF8 二进制与 OOXML 电子表格子集，构建了包含格式解析、DXF 样式、单元格精度、公式缓存、合并单元格与分页视图的完备测试矩阵。

## DXF 增量与可读性验证

以合成记录验证所选属性的长度、枚举、UTF-16、分布对齐依赖、原始保留、partial 与分层错误。7 类有界格式问题均保留 18 个单元格和 5 个公式缓存，逐项数据/样式表/合并范围比较通过，退出 3/partial。属性长度问题仅在外层和 XFProp 记录边界完整时降级，不读取越界字节；真正记录损坏、未知 BIFF 数据、加密和资源限制仍拒绝。

## 真实 ET 样例验证

针对真实公开的 ET/ETT 样本，测试已验证保存值、公式缓存和工作表结构，详见 真实 ET/ETT 记录（`<skill-root>/references/et-real-validation.md`）与 政府公开 ET 验证（`<skill-root>/references/biff8-gov-real-et-validation.md`）。

## 可复现的本地检查

先完成 入口自检 (`<skill-root>/SKILL.md#执行环境约束与首步自检`)，替换 `<skill-root>`，在宿主确认的任务目录运行：

```console
python -I -S -B "<skill-root>/scripts/read_et.py" --check
python -I -S -B -m unittest discover -s "<skill-root>/scripts/test" -p "test_*.py"
```

自检要求退出 0 和 `environment-ok`，并单独确认 OOXML 所需 Expat 可用；测试要求全部通过。

## 样本与未验证边界

本技能支持的是明确声明的 BIFF8 与 OOXML 电子表格子集，未知私有宏、VBA 脚本、私有加密格式明确拒绝，不提供公式重算引擎，不将提取等同于完整专有格式认证。

## 历史证据

机器可读证据见 增强证据清单 (`<skill-root>/references/reading-enhancement-evidence.json`)。
