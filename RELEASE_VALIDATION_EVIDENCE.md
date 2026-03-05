# Release Validation Evidence (March 5, 2026)

This document records execution evidence for `RELEASE_VALIDATION_MATRIX.md`.

## Environment

- Host: Windows (PowerShell), Docker Desktop daemon available
- Also executed Linux-path installer flow via `bash` + Docker integration
- Repository state validated after fixes with:
  - `python -m ruff check src tests` (pass)
  - `python -m mypy src` (pass)
  - `python -m pytest tests -q --basetemp .pytest_tmp_matrix_full -p no:cacheprovider` (`418 passed`)

## Matrix Results

| ID | Status | Evidence |
|---|---|---|
| M1 (Linux install/uninstall happy path) | PASS | Ran `install.sh` through `bash` with Docker daemon (`FREELUNCH_INSTALL_DIR=/mnt/c/.../freelunch-matrix-linux3`, `FREELUNCH_IMAGE=freelunch:test`, `FREELUNCH_SKIP_PULL=true`, port `18113`), validated auth + readiness + tiny non-stream/stream chat, then ran `uninstall.sh` and verified directory removal (`M1 uninstall PASS`). |
| M2 (Windows install/uninstall happy path) | PASS | Ran `install.ps1` with temp install dir and local image (`freelunch:test`, `FREELUNCH_SKIP_PULL=true`, port `18111`), validated auth + readiness + tiny non-stream/stream chat, then ran `uninstall.ps1` with `FREELUNCH_AUTO_CONFIRM=yes` and verified directory removal (`M2 uninstall PASS`). |
| M3 (macOS install/uninstall happy path) | WAIVED (accepted blocker) | No macOS host in this execution environment. On March 5, 2026, project owner explicitly accepted this blocker for current release. |
| M4 (auth enabled checks) | PASS | Verified `401` on missing token, `401` on wrong token, `200` with valid token for `/v1/models` in Windows and Linux-path runs. |
| M5 (tiny non-stream chat) | PASS | `POST /v1/chat/completions` with `stream=false`, `max_tokens=1` returned `200` and valid completion payload. |
| M6 (tiny stream chat) | PASS | `POST /v1/chat/completions` with `stream=true`, `max_tokens=1` returned stream with data chunk(s) and terminal `[DONE]`. |
| M7 (restricted egress simulation) | PASS | Forced `providers.openrouter.api_base` to `http://127.0.0.1:9`, dev-stub disabled, dummy key set. App remained alive (`/healthz` `200`), `/readyz` remained responsive, chat returned explicit failure (`502 {"detail":"provider transport error"}`). |
| M8 (low-budget live-provider smoke) | PASS | Used local `.testkey`, validated tiny non-stream + stream live calls against discovered streaming-capable free model (`openrouter/stepfun/step-3.5-flash:free`) with `max_tokens=1`. |

## Issues Found During Matrix Execution

1. Startup crash under provider transport failure during discovery.
- Symptom: app exited during startup in restricted-egress scenario.
- Fix: `src/main.py` now degrades startup (logs `app.startup_pipeline_failed`) instead of exiting process.
- Regression test: `test_startup_discovery_failure_degrades_but_keeps_gateway_alive`.

2. OpenRouter stream capability under-detection.
- Symptom: live streaming requests could be blocked because `supported_parameters` omitted `"stream"` even when streaming worked.
- Fix: `src/providers/openrouter.py` now treats OpenRouter models as stream-capable by default.
- Regression test: `test_discover_models_treats_streaming_as_supported_when_parameter_list_omits_stream`.

## Waiver Record

- March 5, 2026: Project owner accepted M3 (macOS host path) as blocked/waived due lack of macOS access.
- Remaining matrix items are complete for current release sign-off.
