# Validate a standard-library output

Follow the [path and first-operation rules](../SKILL.md#执行环境约束与首步自检)
before using this reference; internal resources and task data have different path bases.

Keep these findings separate:

1. **Readable structure:** the ZIP and XML parse, expected sheets/cells exist, values,
   types and style references are appropriate. Parsing is not full OOXML XSD validation.
2. **Change integrity:** only intended cells and required calculation metadata changed.
   Compare untouched ZIP member contents; source file and unrelated data must remain.
3. **Subset calculation:** inspect `status`, `issues`, `calculated_count`, `formula_count`
   and `output_written` from `<skill_dir>/scripts/stdlib_xlsx.py`. See [python-calculation.md](python-calculation.md).
4. **Business accuracy:** independently check representative expected totals, ranges,
   rounding and units. A formula can evaluate successfully and still reference wrong data.
5. **Unverified behavior:** report unknown formulas, unrefreshed chart/pivot caches and
   lack of Excel rendering/recalculation verification. Never claim these checks passed.

Reopen the output using `read` to verify stored formulas and caches. Reading old caches
marks them unverified because an XLSX alone does not prove their freshness. Retain the
calculation report as the evidence of this run. No third-party application is launched.

Exit 0 / calculated_subset means this engine completed its supported calculations; it
is not full Excel compatibility. Exit 2 / incomplete produces an output with unsupported
formula caches removed and must be disclosed as `部分公式未计算`. Exit 1 / failed must
not be delivered as a successfully calculated workbook. Static values-only reports must
be identified as such; inputs will not drive automatic updates.

The legacy `<skill_dir>/scripts/formula_check.py` is heuristic static inspection. Its clean exit is not
calculation evidence, it does not prove business correctness, and it is not required by
the Python 3.9+ standard-library path. No LibreOffice installation or runtime is required.

## Task intermediate files

Use the executable [delivery lifecycle](delivery-lifecycle.md): run_xlsx_task.py owns new stdin JSON configs; xlsx_lifecycle.py provides explicit plan/seal/clean for custom helpers. Existing inputs are protected, never automatically adopted. The following rules remain the boundary, rather than permission for extension scanning.

- Before creating auxiliary files, record the exact task-relative paths and purpose of
  files/directories newly created by this task. Use a new independent subdirectory for
  work copies or unpacked data; never reuse an existing directory as disposable storage.
- After successful validation, remove only recorded intermediates no longer needed,
  such as assistant-generated specifications, temporary scripts and inspection outputs.
  Keep final deliverables, required calculation evidence, user-provided inputs and any
  auxiliary outputs the user requested. Never infer ownership from a filename or extension.
- While retrying, retain material still needed. On final failure, retain original inputs,
  existing outputs and recovery backups; keep only necessary diagnostic intermediates
  and report their locations. Do not deliver an old output as a new successful result.
- Clean recorded paths individually with Python standard-library operations within the
  task directory. Do not follow symlinks or use wildcard/recursive deletion; retain and
  report paths whose ownership or type changed, including symlinks in any parent component. Remove directories only after known
  contents are cleaned and the directory is empty. Do not touch the installed Skill.
- Verify the final deliverables still exist and are nonempty after cleanup. Report any
  cleanup failure without expanding the deletion scope. Cleanup records stay outside
  the workbook. Existing validation can be reused only if the deliverable is unchanged.


## Legacy utilities

The older scripts remain for compatibility and reference. They are not the standard
library workflow: `<skill_dir>/scripts/xlsx_reader.py` needs pandas, `<skill_dir>/scripts/libreoffice_recalc.py` needs external
software, and older helpers may require Python 3.10+. Do not invoke legacy utilities for
the maintained workflow. The `<skill_dir>/scripts/xlsx_insert_row.py` and
`<skill_dir>/scripts/xlsx_shift_rows.py` CLI entrypoints are disabled and exit 1 before any writes. A known
formula-reference corruption in these legacy scripts remains unresolved; disabling the
commands does not repair workbooks they may have changed previously. Do not invoke them
for structural edits. `<skill_dir>/scripts/formula_check.py` is heuristic static inspection only; exit 0
does not prove calculation. Legacy examples are historical interface documentation,
not supported task instructions. In particular, `libreoffice_recalc.py` uses a system
temporary directory and external software; it does not satisfy this workflow's task-directory
and runtime contract. Do not invoke it or treat the main self-check as validating it.


## Development verification

Maintenance only: run from the confirmed repository root, not the business task
directory. `skills/xlsx/scripts` below is repository-relative and is not an
installation path to copy into task commands. Tests use isolated temporary directories;
this does not authorize business workflows to choose a different temporary root.

```console
python3 -I -S -B -m unittest discover -s skills/xlsx/scripts -p 'test_*.py' -v
```
