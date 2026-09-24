# Roadmap

This file contains the living open-work list. Completed execution history,
release evidence, and old implementation plans do not belong here; Git history
and the changelog retain that context.

## 1. Reduce dependencies and review security

Heavyweight Hugging Face tokenizer packages are now optional while the base
install retains `tiktoken` and heuristic fallback. Continue measuring accuracy
before removing exact-token support with demonstrated routing value.

Remaining security work, in priority order:

1. Restrict or explicitly opt in to provider base URLs and remote tokenizer
   repositories outside known HTTPS hosts.
2. Run the production container as a non-root user after defining a
   migration-safe ownership strategy for bind-mounted SQLite data. Existing
   bind mounts may hold root-owned database, WAL, and SHM files; the transition
   needs an explicit one-time ownership repair that works for Linux and Docker
   Desktop upgrades without making the application process root again.

Record decisions here only until they become stable architecture or operator
guidance, then link from [`architecture.md`](./architecture.md) or
[`operations.md`](./operations.md).

## Decision rules

Prefer small, reversible changes that preserve the single-node reliability
model. Routing improvements must remain usable without paid classification or
other mandatory metered services, and must preserve hard request constraints.
