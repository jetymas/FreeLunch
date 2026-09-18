# Roadmap

This file contains the living open-work list. Completed execution history,
release evidence, and old implementation plans do not belong here; Git history
and the changelog retain that context.

## 1. Optional pre-routing task classification

Evaluate TypeSafe AI's Jev as an optional hosted classifier for prompt-task
classification before model selection. The vendor describes Jev as a fast,
low-cost classifier exposed through System One. The launch material's figures of
**$0.042 per million input tokens** and **70–500 ms latency** are vendor claims,
not FreeLunch measurements; verify them locally before using them in an
engineering decision. Start with the official sources:

- [Jev and System One launch](https://typesafe.ai/blog/introducing-system-one-models-and-jev)
- [TypeSafe introduction](https://docs.typesafe.ai/introduction)
- [API reference](https://docs.typesafe.ai/api)
- [Choice, score, and Noul question primitives](https://docs.typesafe.ai/primitives)
- [Confidence](https://docs.typesafe.ai/confidence)
- [Python SDK](https://github.com/typesafe-ai/typesafe-sdk-python)

The current integration hypothesis is `POST https://api.typesafe.ai/v1/systemone`
with model `jev-latest`. Confirm request/response details against the official
API before implementation. No SDK dependency is required initially; the
existing `httpx` dependency is sufficient for a small adapter.

### Design constraints

- Define a separate `TaskClassifier` interface and a TypeSafe adapter. Do not
  place intent classification in `tokens.py`, provider adapters, or ranking
  internals.
- Run it after hard capability filtering and before soft score/rerank selection.
  It must never override tools, vision, streaming, structured-output, context,
  or other hard request constraints.
- Make the feature explicitly disabled by default and configurable as an
  opt-in. Bound timeout, input size, retries, and cost.
- Fail open when disabled, unavailable, timed out, malformed, or below the
  configured confidence threshold; fall back to the existing ranking path.
- Send the minimum prompt data needed. Do not persist raw prompts or classifier
  payloads in runtime logs or SQLite request telemetry; record only redacted
  labels, confidence, latency, and decision metadata where needed.
- Version the classifier schema, labels, thresholds, and routing policy. Keep
  an evaluation set and compare baseline versus classifier-assisted routing
  before enabling it broadly.
- Canary the feature with an explicit rollback switch and monitor quality,
  latency, error rate, privacy exposure, and provider-cost impact.

### Acceptance criteria

1. A disabled classifier adds no network call or routing behavior change.
2. Unit tests cover hard-constraint preservation, confidence thresholds,
   timeouts, malformed responses, and fail-open fallback.
3. Evaluation compares model-selection quality and gateway latency against the
   current baseline on representative tasks.
4. Logs and telemetry contain no raw prompt text or secret classifier data.
5. The adapter contract and response schema are versioned and documented once.

## 2. Make native deployment first-class

Docker remains a supported packaging and CI/release path, but application
behavior should not depend on Docker. Document equivalent native and Docker
startup, persistence, upgrade, backup, and uninstall behavior. Remove
Docker-specific assumptions from runtime lifecycle code where practical.

Acceptance criteria:

- a clean native install can start, pass readiness, and serve a request;
- Docker is optional in user-facing prerequisites;
- both modes use the same config and security defaults;
- CI or a documented smoke check covers each supported deployment path.

## 3. Reduce dependencies and review security

Heavyweight Hugging Face tokenizer packages are now optional while the base
install retains `tiktoken` and heuristic fallback. Continue measuring accuracy
before removing exact-token support with demonstrated routing value.

Remaining security work, in priority order:

1. Add request/body, upstream response, and SSE event size limits plus streaming
   idle/total deadlines.
2. Add rate limits for failed gateway authentication and vault unlock attempts.
3. Restrict or explicitly opt in to provider base URLs and remote tokenizer
   repositories outside known HTTPS hosts.
4. Run the production container as a non-root user after defining a
   migration-safe ownership strategy for bind-mounted SQLite data.
5. Pin CI actions to immutable commits and add dependency/image audit,
   provenance, and SBOM checks.
6. Add production controls for API documentation and browser security headers.

Record decisions here only until they become stable architecture or operator
guidance, then link from [`architecture.md`](./architecture.md) or
[`operations.md`](./operations.md).

## Decision rules

Prefer small, reversible changes that preserve the single-node reliability
model. New routing intelligence must be optional, observable, bounded in cost
and latency, and unable to weaken hard request constraints.
