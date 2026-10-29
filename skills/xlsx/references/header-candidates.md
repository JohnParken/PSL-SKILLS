# Suggest header ranges for review

路径占位符说明：`<skill-root>` 是 XLSX Skill 根目录的文档占位符，不是实际目录或 Shell 变量。取得宿主本次实际读取 XLSX 主入口 `SKILL.md` 的真实 `file_path`，若为相对路径，先按该次读取时宿主已确认的基准目录解析为绝对路径，再去掉末尾文件名；所得目录才是 `<skill-root>`，不是当前参考文档所在目录。读取资源或执行命令前，将路径中的 `<skill-root>` 整体替换为该真实目录，保留其后的资源路径；命令中将替换后的完整路径作为一个参数正确引用，嵌入 execute 字符串时按其语法转义。不要将占位符原样交给文件工具或执行器，不按当前工作目录、其他会话路径、环境变量或宿主掩码猜测。无法取得主入口的真实读取路径时，停止依赖操作并报告无法定位。

Follow the path and first-operation rules (`<skill-root>/SKILL.md#执行环境约束与首步自检`)
before using this reference; internal resources and task data have different path bases.

Run the entrypoint self-check (`<skill-root>/SKILL.md#执行环境约束与首步自检`) first, require exit 0
and `status: ready`, and confirm task directory/input readability. Resolve `<skill-root>`
from the actual SKILL.md path, replace it in commands and use task-relative input paths.
Python 3.9+, standard library only. Inputs are not changed; JSON is written to stdout.

```console
python3 -I -S -B "<skill-root>/scripts/xlsx_header_candidates.py" report.xlsx --sheet Sales
python3 -I -S -B "<skill-root>/scripts/xlsx_header_candidates.py" report.xlsx --sheet Sales --scan-rows 64 --scan-columns 32 --max-depth 4
```

The default scan is A1 through at most row 32/column 32. Allowed bounds are 2..64 rows,
1..64 columns, and 1..4 header rows per candidate. The existing workbook loader's package
and cell limits still apply; only candidate evaluation, not whole-file loading, is limited
to the scan rectangle. `scan_clipped` warns about stored cells outside it. At most 256
row-range combinations are examined and the top eight successful candidates are returned.

Evidence includes complete horizontal/vertical merge structures, text-rich rows and a
following increase in numeric-value share. Full-width single-row merged headings are
excluded from candidates to avoid using report titles as all-column parents. Unmerged
notes and titles cannot be reliably identified: they may appear among weak alternatives.
Blank rows split candidate blocks; a candidate never crosses a blank row. Multiple blocks
might be separate tables, notes or intentional gaps, so the result reports ambiguity.
Repeated paths at different row starts are also reported. Headerless numeric tables,
numeric labels and all-text data may be indistinguishable and remain ambiguous.

Scores are heuristic ordering rules, not calibrated probabilities or accuracy guarantees.
`strong_evidence` means one of the documented structural/type rules fired. It is not proof
that the suggested rows are headers. Formulas and Excel errors are not interpreted as
header evidence; reports list uncertain coordinates. No cache freshness is assumed.
Candidates whose merge geometry is malformed, clipped, overlapping or crosses the
candidate boundary are rejected using the explicit-header safety checks.

`status: suggested` means one range has a sufficiently separated rule score with no
additional ambiguity. `status: ambiguous` means review is needed or evidence is lacking.
Both always set `requires_confirmation: true` and `automatic_boundary_applied: false`.
The command never sends a suggestion into profile, grouping or row processing. After
review, explicitly pass `--header-rows first:last` to the normal workflow; manual ranges
remain available regardless of candidate ranking. See Explicit multi-level headers (`<skill-root>/references/multi-level-headers.md`).

Exit 0 means diagnosis completed, including ambiguous/empty results. Exit 1 returns JSON
`status: failed` for input/runtime errors. No real Excel/WPS application testing or
reliable automatic interpretation of arbitrary layouts is implied by these suggestions.

Merge structure is parsed once and reused across candidate ranges; all per-candidate
geometry checks remain active. No suggested boundary is applied automatically.
