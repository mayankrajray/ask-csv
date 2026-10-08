# Change 001 — Security Hardening

## Purpose

Verify and close the existing Chunk 1 security work against the current code and tests.

## Problems Addressed

- Uploads were read without a size bound and partial files could remain after failure.
- Provider and API-key switching changed process-wide configuration.
- Uploaded metadata and query results could be mistaken for agent instructions.

## Implementation

### Bounded Uploads

Uploads are read in 1 MiB chunks, checked against the configured per-file size limit, and partial files are removed on failure. Table names are sanitized and the resolved target path is checked against the upload directory.

### Session-Scoped LLM Configuration

Provider, model, and runtime API-key overrides are stored on the selected in-memory session. The global provider and key settings are not changed. API responses expose configuration status, not the key.
Provider switching requires a valid session so the endpoint cannot report success for a setting it did not persist.

### Prompt Injection / Untrusted Data Boundaries

Gemini and OpenRouter system prompts identify uploaded data as untrusted. The system schema digest contains table/column metadata but not cell values. Model-bound SQL results are capped at `MAX_ROWS_TO_LLM` (20 by default) and framed as untrusted data. Delimiter characters in tool-returned dataset values and metadata are HTML-escaped so values cannot spoof the framing tags.

## Files Changed

- `app/main.py` — bounded upload handling and per-session provider configuration.
- `app/agent.py`, `app/openrouter_agent.py` — session credentials and untrusted-data system instructions.
- `app/tools.py` — untrusted-data formatting and escaping.
- `app/engine.py` — maximum normalized column-header length.
- `tests/test_security.py` — behavior checks for upload bounds/cleanup/path safety, session isolation, key non-disclosure, data boundaries, delimiter escaping, and the LLM row cap.

## Tests

- Security behavior, API-key handling, and data-boundary tests.
- API regression tests.
- Engine regression tests.
- Complete pytest suite.

## Actual Test Results

- Development dependencies installed from `requirements-dev.txt`; pytest version: 9.1.1.
- `tests/test_security.py -q`: 13 passed.
- `tests/test_api.py -q`: 9 passed.
- `tests/test_engine.py -q`: 28 passed.
- `pytest -q`: 50 passed.
- Security/API/full-suite runs emitted one Starlette deprecation warning about using `httpx` with `starlette.testclient`; no test failures.
- `git diff --check`: passed.

## Smoke Test

Offline DemoAgent smoke requests passed: `/api/health` returned 200 in demo mode, `/api/config` returned 200 with demo selected, `/api/sample` loaded the `sales` table, and `/api/chat` returned 200 with SQL, chart, token, and done events.

## Known Limitations

- Sessions and runtime keys remain in memory and are lost on server restart.
- Prompt instructions and data framing reduce injection risk but cannot guarantee model behavior.
- Session history has no pruning strategy; DemoAgent does not use conversation context.
- Upload size is bounded per file, not across all files in a multi-file request.

## Assumptions

- The configured upload limit applies separately to each uploaded file.
- Runtime API-key overrides are allowed only when `ALLOW_KEY_OVERRIDE` is enabled.
- Offline smoke verification uses the existing DemoAgent and does not call a remote LLM provider.

## Git Checkpoint

Commit: PENDING
Branch: `assignment-final`
