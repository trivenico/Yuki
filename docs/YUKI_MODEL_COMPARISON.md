# Model comparison and regression reports

Yuki can compare two completed `EvalReport` objects against the same immutable benchmark revision. The comparison requires exact case-ID coverage and matching task labels, then reports per-case improvements, regressions, unchanged passes, and unchanged failures.

## Boundaries

- The caller supplies the benchmark ID and SHA-256 digest; this module does not independently resolve the registry revision.
- Reports are supplied observations. The comparator validates internal arithmetic and shape, but cannot authenticate the model, runner, timing, benchmark contents, or safety labels.
- A pass-rate delta alone can hide regressions; case-level regression counts are therefore always included.
- No confidence intervals or significance claims are made. Small benchmark suites are descriptive only.
- The recommendation is always `human_review_required`. Comparison never promotes a model, changes provider routing, or triggers training.

Use the same held-out benchmark revision, runner contract, and evaluation settings for meaningful comparisons. Preserve both original reports alongside the comparison.
