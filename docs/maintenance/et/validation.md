# 验证方法、样本证据与未验证范围

维护路径说明：本页以宿主确认的 PSL-SKILLS 仓库根目录为基准；`skills/et/` 是源码目录，`docs/maintenance/et/` 是维护证据目录。先确认仓库位置；不能从独立运行包推断仓库。原记录的测试数量、平台、版本与结论保留历史含义，不代表当前重跑。测试仅在源码树执行，不随精简运行包分发。

## 目录

- 验证范围与方法 (`docs/maintenance/et/validation.md#验证范围与方法`)
- DXF 增量与可读性验证 (`docs/maintenance/et/validation.md#dxf-增量与可读性验证`)
- 真实 ET 样例验证 (`docs/maintenance/et/validation.md#真实-et-样例验证`)
- 可复现的本地检查 (`docs/maintenance/et/validation.md#可复现的本地检查`)
- 样本与未验证边界 (`docs/maintenance/et/validation.md#样本与未验证边界`)
- 历史证据 (`docs/maintenance/et/validation.md#历史证据`)

## 验证范围与方法

本技能面向金山 WPS 兼容电子表格（.et/.ett）及其底层 BIFF8 二进制与 OOXML 电子表格子集，构建了包含格式解析、DXF 样式、单元格精度、公式缓存、合并单元格与分页视图的完备测试矩阵。

## DXF 增量与可读性验证

以合成记录验证所选属性的长度、枚举、UTF-16、分布对齐依赖、原始保留、partial 与分层错误。7 类有界格式问题均保留 18 个单元格和 5 个公式缓存，逐项数据/样式表/合并范围比较通过，退出 3/partial。属性长度问题仅在外层和 XFProp 记录边界完整时降级，不读取越界字节；真正记录损坏、未知 BIFF 数据、加密和资源限制仍拒绝。

## 真实 ET 样例验证

针对真实公开的 ET/ETT 样本，测试已验证保存值、公式缓存和工作表结构，详见 真实 ET/ETT 记录（`docs/maintenance/et/et-real-validation.md`）与 政府公开 ET 验证（`docs/maintenance/et/biff8-gov-real-et-validation.md`）。

## 可复现的本地检查

先完成 入口自检 (`skills/et/SKILL.md#执行环境约束与首步自检`)，替换 `skills/et`，在宿主确认的任务目录运行：

```console
python -I -S -B "skills/et/scripts/read_et.py" --check
python -I -S -B -m unittest discover -s "skills/et/scripts/test" -p "test_*.py"
```

自检要求退出 0 和 `environment-ok`，并单独确认 OOXML 所需 Expat 可用；测试要求全部通过。

## 样本与未验证边界

本技能支持的是明确声明的 BIFF8 与 OOXML 电子表格子集，未知私有宏、VBA 脚本、私有加密格式明确拒绝，不提供公式重算引擎，不将提取等同于完整专有格式认证。

## 历史证据

机器可读证据见 增强证据清单 (`docs/maintenance/et/reading-enhancement-evidence.json`)。
