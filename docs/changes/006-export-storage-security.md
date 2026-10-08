# Change 006 — Export and Storage Security

## Current File Lifecycle

Uploads are streamed in 1 MiB chunks into `UPLOAD_DIR` (`uploads/`) as `{session_id}_{sanitized_table_name}.{ext}`. `DataEngine` parses them into a per-session in-memory DuckDB database; the source upload is retained for the session lifetime. Export directories are created lazily only when a large SQL result is exported. Reports are returned as JSON and do not create report files. Large SQL results are written as CSV under `EXPORT_DIR/{session_id}/` with a random UUID filename.

## Ownership and Export Security

Exports are no longer exposed through a public `StaticFiles` mount. `GET /api/exports/{session_id}/{filename}` requires a live session, accepts only generated UUID `.csv` names, resolves the session directory and file under the configured export root, and streams the file. A request using another session ID, an unknown/expired ID, a guessed filename, or a traversal path receives not found. The frontend static mount remains public; uploads and exports are not mounted there.

Session IDs are random UUIDv4 values with 122 random bits and act as bearer capabilities. There is no separate user authentication or identity binding, so anyone who obtains another user's session ID can act as that session. Cross-session isolation protects requests using the caller's own session ID; it cannot distinguish people who share a session ID.

## Path Safety and Filenames

Upload table names continue to be normalized from the supplied filename and capped at 64 characters; stored upload paths are resolved beneath `UPLOAD_DIR`. Uploads reject existing paths and symlinks and use exclusive file creation, preventing overwrite through name collisions. Export storage validates the session directory, uses random flat filenames, and rejects symlinked session directories and files that resolve outside their owner directory. Upload parse errors no longer return parser exception text or local paths.

## Cleanup, Retention, and Disk Limits

- Failed uploads drop the provisional session and remove its owned files. Session eviction closes its DuckDB connection and removes its upload and export files.
- Sessions expire after 24 hours without activity; expiration and stale-orphan cleanup are checked at session lookup and when creating a session. Startup also removes orphaned session files older than 24 hours.
- The in-memory session limit is 200. Upload defaults are 50 MiB per file, 20 files and 200 MiB total per session. Exports are capped at 20 files and 200 MiB total per session. These values are configurable through environment variables.
- The configured per-session limits bound expected stored uploads and exports to about 78 GiB across 200 fully populated sessions, before filesystem overhead and in-progress requests.

## Bugs Discovered and Fixes

- The export static mount allowed downloads without checking a live owning session; replaced it with a session-scoped route.
- Failed/evicted sessions left export directories and could leave upload files; cleanup now removes owned storage and closes the database.
- Old in-memory sessions and their files could remain indefinitely; added lazy idle expiration and a startup sweep for old orphaned files.
- Session-cap enforcement removed only one entry even if already over the configured limit; it now evicts until below the limit.
- Repeated exports had deterministic names and no file or byte cap; exports now use random names and enforce configurable count and aggregate-size limits.
- Uploads lacked aggregate-size and file-count limits, and collision paths could overwrite earlier files; added configurable limits and exclusive writes.

## Files Changed

`app/config.py`, `app/main.py`, `app/tools.py`, `tests/test_security.py`, `tests/test_storage_security.py`, and this document.

## Tests and Actual Results

Storage/export, security, ingestion, existing export, and agent export-failure regression tests: **36 passed**, 1 existing Starlette/httpx deprecation warning.

Full suite: **115 passed**, 1 existing Starlette/httpx deprecation warning.

Tests use temporary directories and cover owner downloads, cross-session/unknown session denial, traversal variants, invalid/empty exports, repeated-export limits, unsafe upload names, failed ingestion cleanup, session eviction and idle cleanup, startup orphan cleanup, and upload file-count/aggregate-size limits.

## Known Deployment Limitations

Sessions and DuckDB connections are in memory, so they do not survive process restarts. Docker Compose persists only `data/`; uploads and exports live on the container filesystem and are ephemeral there. The deployment is designed for one process: multiple workers would not share the in-memory session map. Use durable shared storage and session coordination if deploying across workers or requiring durable files. Reports remain JSON responses, not downloadable report artifacts.

## Manual Verification

1. Upload a CSV and issue a query returning more rows than `MAX_ROWS_TO_LLM`; open the returned export URL while the session is live.
2. Substitute another session ID, an unknown ID, a guessed UUID filename, and traversal strings in the export URL; each should return 404.
3. Repeat large-result queries until `MAX_EXPORTS_PER_SESSION` is reached; confirm a controlled limit error and no extra file.
4. Set a short `SESSION_STORAGE_RETENTION_HOURS`, leave a session idle, then make a request with its ID; it should return not found and remove its files.
5. Restart with orphan files older than the retention window and confirm startup cleanup removes only those session files.

## Git Checkpoint

Commit: PENDING
Branch: `assignment-final`
