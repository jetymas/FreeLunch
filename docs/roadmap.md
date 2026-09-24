# Roadmap

This file contains the living open-work list. Completed execution history,
release evidence, and old implementation plans do not belong here; Git history
and the changelog retain that context.

## 1. Measure token-estimation value before changing dependencies

Heavyweight Hugging Face tokenizer packages are now optional while the base
install retains `tiktoken` and heuristic fallback. Keep optional exact-token
support until its routing value can be assessed with real evidence. The local
checkout has no production request database; test databases cannot establish
live accuracy.

1. Add a privacy-preserving estimator-kind field to request telemetry so
   Hugging Face exact counts can be distinguished from `tiktoken` and heuristic
   estimates. Keep request content out of logs.
2. Collect enough live samples per tokenizer family to meet the threshold in
   `/admin/health`'s `token_estimation_review`, then compare estimates with
   provider-reported prompt usage and context failures.
3. Compare exact and heuristic counts on the same prompt-redacted corpus;
   provider usage can include serialization overhead and is not a direct
   tokenizer ground truth. Remove optional exact support only if these
   comparisons show no material routing benefit.

Record decisions here only until they become stable architecture or operator
guidance, then link from [`architecture.md`](./architecture.md) or
[`operations.md`](./operations.md).

## Decision rules

Prefer small, reversible changes that preserve the single-node reliability
model. Routing improvements must remain usable without paid classification or
other mandatory metered services, and must preserve hard request constraints.
