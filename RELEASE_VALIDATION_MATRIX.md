# Release Validation Matrix

This runbook defines the manual release-validation evidence required before public release.

Execution results are tracked in `RELEASE_VALIDATION_EVIDENCE.md`.

Use with:

- `OPERATIONS.md` for runtime troubleshooting and endpoint semantics
- `TESTING.md` for overall testing strategy and CI relationship
- `TASKS.md` for open/closed validation work items

## 1. Evidence Rules

- capture command + timestamp + platform for each check
- capture pass/fail plus short notes for anomalies
- attach logs/screenshots only for failures or non-obvious passes
- keep API spend low; live-provider checks are tiny-request only
- before a release tag is pushed, the local pre-push gate should already be green (`ruff`, `mypy`, full pytest, coverage, and installer syntax checks)

## 2. Matrix

| ID | Platform | Scenario | Expected Result | Evidence |
|---|---|---|---|---|
| M1 | Linux (Docker daemon) | `install.sh` + `uninstall.sh` happy path | install succeeds, gateway responds, uninstall removes install dir | shell transcript + endpoint outputs |
| M2 | Windows (Docker Desktop) | `install.ps1` + `uninstall.ps1` happy path | install succeeds, gateway responds, uninstall removes install dir | PowerShell transcript + endpoint outputs |
| M3 | macOS (Docker Desktop) | `install.sh` + `uninstall.sh` happy path | install succeeds, gateway responds, uninstall removes install dir | terminal transcript + endpoint outputs |
| M4 | Linux | Auth enabled (`GATEWAY_API_KEY`) | `/v1/models` returns `401` for missing/wrong token and `200` for valid token | three HTTP responses |
| M5 | Linux | Tiny non-stream chat | `200`, non-empty assistant response | request + response excerpt |
| M6 | Linux | Tiny stream chat | at least one `data:` chunk and terminal `[DONE]` | stream output excerpt |
| M7 | Linux/Windows/macOS | Restricted egress simulation (block provider egress) | app remains up; readiness/behavior aligns with policy and errors are explicit | policy notes + app logs |
| M8 | Linux | Low-budget live-provider smoke (optional key) | tiny non-stream + stream request(s) succeed within budget ceiling | spend log + responses |

## 3. Command Templates

### Linux/macOS install

```bash
OPENROUTER_API_KEY=... GATEWAY_API_KEY=release-smoke-key FREELUNCH_PORT=8000 sh ./install.sh
```

### Windows install

```powershell
$env:OPENROUTER_API_KEY="..."
$env:GATEWAY_API_KEY="release-smoke-key"
$env:FREELUNCH_PORT="8000"
.\install.ps1
```

### Auth checks

```bash
curl -i http://127.0.0.1:8000/v1/models
curl -i -H "Authorization: Bearer wrong" http://127.0.0.1:8000/v1/models
curl -i -H "Authorization: Bearer release-smoke-key" http://127.0.0.1:8000/v1/models
```

### Tiny non-stream chat

```bash
curl -sS -H "Authorization: Bearer release-smoke-key" -H "Content-Type: application/json" \
  -d '{"model":"auto","stream":false,"max_tokens":1,"messages":[{"role":"user","content":"hi"}]}' \
  http://127.0.0.1:8000/v1/chat/completions
```

### Tiny stream chat

```bash
curl -N -H "Authorization: Bearer release-smoke-key" -H "Content-Type: application/json" \
  -d '{"model":"auto","stream":true,"max_tokens":1,"messages":[{"role":"user","content":"hi"}]}' \
  http://127.0.0.1:8000/v1/chat/completions
```

## 4. Sign-Off

Release sign-off requires:

1. M1-M6 pass evidence attached.
2. M7 documented with expected behavior observed.
3. M8 executed or explicitly waived with rationale.
4. Any host-unavailable leg (for example M3 on missing macOS access) must be explicitly accepted/waived by project owner and recorded in `RELEASE_VALIDATION_EVIDENCE.md`.
5. The release tag is pushed only after the corresponding `main` commit has already passed CI.
