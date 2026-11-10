# WPS 文档受控编辑指南

路径占位符说明：`<skill-root>` 是 WPS Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 WPS 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

## 概述

`<skill-root>/scripts/run_wps_edit.py` 为可读取文档提供明确操作下的内容重排：支持文字替换、段落增删改、表格单元格更新和批量操作。它全文重建RTF，不是保留式或无损编辑，原格式、域、对象及分页不保留，原件受保护。

## 常用操作与命令

### 1. 定点文字查找替换（Text Replacement）

在全篇段落、标题与表格中执行精准文字替换：

```python
execute(command="python -I -S -B \"<skill-root>/scripts/run_wps_edit.py\" text \"./原文档.wps\" \"修订后.wps\" \\\n  --find \"旧项目组\" --replace \"新综合运营中心\"")
```

### 2. 段落结构修改与追加（Structure Modification）

- **替换指定段落**：按 1-based 序号替换段落内容：
  ```python
  execute(command="python -I -S -B \"<skill-root>/scripts/run_wps_edit.py\" structure \"./原文档.wps\" \"修订后.wps\" \\\n  --replace-paragraph 2 --text \"第二段修改后的全新正文内容。\"")
  ```
- **删除指定段落**：按 1-based 序号移除过时段落：
  ```python
  execute(command="python -I -S -B \"<skill-root>/scripts/run_wps_edit.py\" structure \"./原文档.wps\" \"修订后.wps\" \\\n  --delete-paragraph 3")
  ```
- **末尾追加段落**：
  ```python
  execute(command="python -I -S -B \"<skill-root>/scripts/run_wps_edit.py\" structure \"./原文档.wps\" \"修订后.wps\" \\\n  --text \"补充说明：本通知自发布之日起施行。\"")
  ```

### 3. 表格单元格修改（Table Cell Update）

按表格索引、行号与列号（均为 0-based）更新特定单元格数据：

```python
execute(command="python -I -S -B \"<skill-root>/scripts/run_wps_edit.py\" table \"./原文档.wps\" \"修订后.wps\" \\\n  --table-index 0 --row 1 --col 2 --text \"已完成\"")
```

### 4. 批量操作（Batch Editing）

通过 JSON 规范一次性执行多个复合修改操作：

```python
execute(command="python -I -S -B \"<skill-root>/scripts/run_wps_edit.py\" batch \"./原文档.wps\" \"修订后.wps\" <<'JSON'\n{\n  \"operations\": [\n    {\"operation\": \"replace_text\", \"find\": \"旧计划\", \"replace\": \"新战略\"},\n    {\"operation\": \"update_cell\", \"table_index\": 0, \"row\": 1, \"col\": 1, \"text\": \"通过\"},\n    {\"operation\": \"append_blocks\", \"blocks\": [{\"type\": \"paragraph\", \"text\": \"附件：推进时间表\"}]}\n  ]\n}\nJSON\n")
```

## 质量与安全性保障

1. **输入解析与解耦**：基于只读引擎完整读取目标文件，避免直接文本正则替换导致的二进制或转义符损坏。
2. **原子写入与回滚**：编辑产物使用 `.tmp_wps_edit_` 前缀临时写入，经检验有效后发布到未占用的新文件，CLI 不覆盖已有目标。
3. **回读核验**：发布前完成 `read_bytes` 结构核对及内容账本比较，不保证客户端打开、原版式或视觉分页。

## 发布前内容账本

生成和重排使用 `<skill-root>/scripts/wps_content_validation.py`，在候选回读与发布之间对照规范化文档树的主文字、块顺序、物理单元格和已支持合并标记；不一致保留原目标并失败，不先发布再检查。`content_validation` 回执列出比较范围与未验证项。段落/单元格首尾空白按读取器规则规范化，相邻表格只比较连续物理行，空段落和相邻表格分界另列未验证，不因此无必要拒绝。

这不是原始输入规格到所有排版的完整认证：规范化阶段、样式、附加区域、域新鲜度、非文字换页和客户端布局不在此账本认证范围。partial及显式有损重排仍保留原缺口；账本验证的是已授权重排计划的可读内容，不证明原件未读内容已恢复。

输入继续按内容识别受支持的 RTF、MS-DOC 和 OOXML（包括 `.doc`）；输出只允许 `.wps/.rtf`，不能原格式写回 DOC。CLI 与 execute_edit_task API 均在写入前校验后缀，回执 `output_format=rtf`、`binary_doc=false`。partial 输入的既有显式有损重排要求保持不变。

## 部分输入与回执

首次编辑前完成主入口的读取与生成环境自检。默认拒绝partial输入或无法确认表格顺序的重排；用户明确接受损失后才给操作添加 `--allow-lossy-reflow`，输出仍为未占用的新文件。保留已读内容及原issues/diagnostics/omitted_regions/assessment；未知顺序采用正文段落后接表格，不宣称原位置已恢复。副本正文含部分内容重排提示，回执partial=true、losses列损失、退出3；不绕过安全或资源检查。

编辑无 `--compact-receipt` 参数；正常退出0仍检查partial、issues及spec_warnings，不以success代替完整性或视觉验收。生成/编辑均自动归档，分别核对发布、delivery和清理；异常或需要落盘批量配置才读取 `<skill-root>/references/delivery.md`。
