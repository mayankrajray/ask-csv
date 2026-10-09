# Change 009 — Adversarial Security Review

## Attack surfaces reviewed

Reviewed read-only SQL validation and DuckDB lockdown; upload and export paths; session isolation; dataset/schema prompt framing; tool argument and iteration limits; provider configuration and error handling; CORS; and spreadsheet handling of generated CSV files.

## Tests performed

- Expanded SQL guard tests for dangerous statements and variants (including `PRAGMA`, `INSTALL`, `LOAD`, `IMPORT`, `CALL`, comments, chaining, and file readers), while retaining nested queries, `UNION`, quoted identifiers/literals, `EXPLAIN`, and `DESCRIBE` reads.
- Existing tests exercise path traversal and unsafe names, session-owned export downloads, upload limits and cleanup, session/provider isolation, API-key non-disclosure, prompt-injection framing, provider/tool failures, and tool-loop bounds.
- Added a regression test that exports formula-like text beginning with `=`, `+`, `-`, and `@`, including leading whitespace and a formula-like output header, and checks that typed negative/zero numeric values remain numeric.

## Vulnerabilities discovered and fixes

CSV exports wrote user-controlled text cells directly. Spreadsheet applications can interpret formula-like cells as executable formulas. Exports now prefix formula-like string values and headers with an apostrophe. Typed numeric values, including negative values, are preserved. The regression test reproduces the prior unsafe output and verifies the resulting CSV content.

No demonstrated SQL, storage traversal, session-crossing, or secret-disclosure bypass was found in the reviewed paths.

## Existing protections verified

- SQL is restricted to a single parsed read statement and applies a forbidden-operation and file-reader guard. DuckDB independently has external access disabled and configuration locked after loading.
- Upload filenames are normalized before storage; uploads enforce per-file, per-request aggregate size, and file-count limits. Failed uploads clean partial files and sessions.
- Export paths use generated flat names, are stored under a session directory, and downloads require a live session and validated session/export identifiers.
- Provider configuration and API keys are session-scoped. Configuration/health/log responses do not return keys; provider initialization errors are controlled.
- Dataset schema and result text are framed as untrusted input. This is a prompt-injection mitigation, not a guarantee that a model can never follow hostile data.
- Conversation and tool-call histories are bounded.
- CORS defaults to the local frontend origins, with credentials disabled and methods limited to GET/POST. Deployments can override the allowlist through `ALLOWED_ORIGINS`.

## Resource limits

Current defaults in `app/config.py`: 50 MB per uploaded file; 200 MB aggregate within one upload batch; 20 files per batch; 200 MB generated exports per session; 20 exports per session; 200 in-memory sessions; 20 rows returned to the model; 12 conversation turns; and 8 tool steps per question. These are configuration defaults, not a global disk quota. The session upload aggregate applies to a batch; each upload request creates a new session.

## Known security limitations

- **Session IDs are bearer capabilities.** Anyone who obtains one can use that session's API operations while it remains active. Authentication is outside this chunk.
- **Prompt injection is mitigated, not eliminated.** Hostile dataset content remains untrusted, but no prompt technique proves perfect resistance for every model/provider.
- In-memory sessions and local files are intended for a single local process; they are not shared or durable multi-worker storage.
- CORS is a browser-origin policy, not authentication. Operators must configure `ALLOWED_ORIGINS` for their deployment.
- Formula neutralization targets common spreadsheet formula prefixes. Applications differ in CSV interpretation; users should treat downloaded data as untrusted.

## Exact test results

- Targeted SQL, security, storage, agent-reliability, and lifecycle suites: **102 passed**.
- Full suite: **144 passed**, with one existing Starlette/httpx deprecation warning.
- `git diff --check`: passed.

## Manual security verification

1. Submit `DELETE`, `PRAGMA`, semicolon-chained SQL, and a `read_csv` query through the SQL tool; confirm rejection. Submit a nested `SELECT`/`UNION`; confirm it works.
2. Upload a CSV with formula-like text and a negative numeric value, force an export (including a query alias beginning with `=`), and confirm text/header values are prefixed while the numeric value is unchanged.
3. Try a traversal filename and traversal export URL; confirm storage remains under its configured root and the download returns not found.
4. Create two sessions and try using one session ID to download the other's export; confirm it is denied.
5. Check `/api/config`, `/api/health`, and `/api/logs/{session_id}` after a session-specific provider switch; confirm the key is absent from responses.
6. Inspect deployed `ALLOWED_ORIGINS`; keep it limited to the intended frontend origins.
