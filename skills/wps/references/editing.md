# WPS 文档受控编辑指南

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 概述

`run_wps_edit.py` 为现存金山 WPS 文档（`.wps`）提供受控的外科手术式定点修改能力，支持定点文字替换、结构段落增删改、表格单元格数据微调以及批量批处理。

## 常用操作与命令

### 1. 定点文字查找替换（Text Replacement）

在全篇段落、标题与表格中执行精准文字替换：

```console
python -I -S -B "<skill-root>/scripts/run_wps_edit.py" text "./原文档.wps" "./修订后.wps" \
  --find "旧项目组" --replace "新综合运营中心"
```

### 2. 段落结构修改与追加（Structure Modification）

- **替换指定段落**：按 1-based 序号替换段落内容：
  ```console
  python -I -S -B "<skill-root>/scripts/run_wps_edit.py" structure "./原文档.wps" "./修订后.wps" \
    --replace-paragraph 2 --text "第二段修改后的全新正文内容。"
  ```
- **删除指定段落**：按 1-based 序号移除过时段落：
  ```console
  python -I -S -B "<skill-root>/scripts/run_wps_edit.py" structure "./原文档.wps" "./修订后.wps" \
    --delete-paragraph 3
  ```
- **末尾追加段落**：
  ```console
  python -I -S -B "<skill-root>/scripts/run_wps_edit.py" structure "./原文档.wps" "./修订后.wps" \
    --text "补充说明：本通知自发布之日起施行。"
  ```

### 3. 表格单元格修改（Table Cell Update）

按表格索引、行号与列号（均为 0-based）更新特定单元格数据：

```console
python -I -S -B "<skill-root>/scripts/run_wps_edit.py" table "./原文档.wps" "./修订后.wps" \
  --table-index 0 --row 1 --col 2 --text "已完成"
```

### 4. 批量操作（Batch Editing）

通过 JSON 规范一次性执行多个复合修改操作：

```console
python -I -S -B "<skill-root>/scripts/run_wps_edit.py" batch "./原文档.wps" "./修订后.wps" <<'JSON'
{
  "operations": [
    {"operation": "replace_text", "find": "旧计划", "replace": "新战略"},
    {"operation": "update_cell", "table_index": 0, "row": 1, "col": 1, "text": "通过"},
    {"operation": "append_blocks", "blocks": [{"type": "paragraph", "text": "附件：推进时间表"}]}
  ]
}
JSON
```

## 质量与安全性保障

1. **输入解析与解耦**：基于只读引擎完整读取目标文件，避免直接文本正则替换导致的二进制或转义符损坏。
2. **原子写入与回滚**：编辑产物使用 `.tmp_wps_edit_` 前缀临时写入，经检验有效后原子替换。
3. **回读核验**：保存前完成 `read_bytes` 结构核对，确保目标文件结构合法，无损坏弹窗风险。
