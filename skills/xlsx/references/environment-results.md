# 自检与宿主回传

Self-check reporting: `check` keeps its single JSON stdout and original `ready`/`failed`/`unsupported` statuses and exit codes; it adds `check_kind: environment` and `artifact_validation: not_run` on handled outcomes. It does not create or accept a workbook. Creation/calculation reports keep their existing contract, and `ready` must never be treated as `output_written`.

The harness must return this invocation's stdout, stderr and actual exit code together with an invocation identifier, including nonzero exits and empty channels. A library return or captured subprocess result is not a model-visible tool result until the harness returns it. Missing execution, timeout or signal termination must be explicit, with no invented normal exit code; truncation must identify the channel and known length. Missing or truncated evidence is unconfirmed, not success. Preserve JSON stdout without added prose; result acceptance still follows validation (`<skill-root>/references/validate.md`).

