# Change 003 — Ingestion and Multi-File Test Coverage

## Purpose

Add deterministic coverage for CSV/Excel ingestion, multiple uploaded files, joins, malformed uploads, schema normalization, and session isolation. Fix only the confirmed silent table-collision issue.

## Current Ingestion Architecture

Uploads are streamed to session-named files under `UPLOAD_DIR`, checked against the configured size limit and resolved path boundary, then parsed by pandas into a per-session in-memory DuckDB `DataEngine`. The engine creates physical tables from pandas dataframes and disables DuckDB external access after loading. Each upload request creates one session; all files in that multipart request are loaded into that session.

## Test Scenarios

The new tests cover CSV headers and inferred types, nullable values, date casting, unusual and duplicate headers, XLSX workbooks, malformed/empty/unsupported/oversized uploads, joins, table collisions, and independent session schemas.

## Multi-File Behavior

Multiple files sent in one upload request share one session and each produce a table. File stems are normalized to table names. Previously, identical normalized names silently replaced earlier tables; ingestion now rejects duplicate names before changing data, including collisions among sanitized Excel sheet names. Failed multi-file uploads remove partial files and the session.

## Join Behavior

The engine can query across loaded tables with ordinary DuckDB joins. Tests verify a successful customers/sales join, a valid join with zero matching rows, a missing-key failure, and a join failure when corresponding key columns have incompatible inferred types.

## Excel Behavior

`.xlsx` upload is tested with one sheet and multiple sheets. A single-sheet workbook uses the file stem as its table name. A multi-sheet workbook creates one table per sheet named `<file-stem>_<normalized-sheet-name>`. Empty sheets are skipped; a workbook with no populated sheets is rejected. The legacy `.xls` parser is selected by extension and uses pandas/xlrd, but this chunk does not create a legacy `.xls` fixture.

## Malformed-Input Behavior

Malformed CSV, an empty CSV, malformed XLSX, unsupported extensions, and oversized uploads return client errors (oversized input returns 413). Tests confirm responses contain no traceback and upload files are cleaned up. CSV parsing retains its existing fallback that skips ragged malformed lines when the default parser fails.

## Session-Isolation Behavior

Separate upload requests create separate sessions and separate in-memory DuckDB engines. Tests verify each session lists and queries only its own tables and that schema endpoints return 404 for another session's table. Existing Chunk 1 path, size, SQL, and external-access security tests continue to pass.

## Bugs Discovered

Loading a table name that already existed used `CREATE OR REPLACE TABLE`, silently discarding the previously loaded file. Different filenames can normalize to the same table name, and Excel sheet names can normalize to the same table name as well.

## Fixes Made

`DataEngine` now checks incoming table names case-insensitively before loading CSV or Excel data and raises a clear `ValueError` on collisions. The upload endpoint returns its existing 400 parse/load error response and cleans up all files and the partially created session.

## Files Changed

- `app/engine.py`
- `tests/test_ingestion.py`
- `docs/changes/003-ingestion-multifile-testing.md`

No frontend or storage architecture changes were needed.

## Tests Added

13 deterministic tests use temporary CSV/XLSX files and the FastAPI test client. They require no network or LLM credentials.

## Exact Test Results

- New ingestion tests: 13 passed.
- Security: 13 passed.
- API: 9 passed.
- Engine: 28 passed.
- Full suite: 68 passed.
- TestClient emitted the existing Starlette/httpx deprecation warning.

## Known Limitations

CSV type inference follows pandas behavior; date strings are not automatically converted to a date type, though DuckDB `TRY_CAST` can read them as dates. Ragged CSV input may be accepted with malformed rows skipped by the existing fallback. Excel coverage creates `.xlsx` workbooks; legacy `.xls` is supported through its parser path but was not fixture-tested here. Duplicate table names are rejected rather than automatically renamed.

## Manual Verification Instructions

1. Upload `customers.csv` and `sales.csv` together; confirm both tables appear in one session.
2. Query a join such as `SELECT c.name, SUM(s.amount) FROM customers c JOIN sales s USING (customer_id) GROUP BY c.name`.
3. Upload an `.xlsx` with multiple sheets and confirm each sheet appears as a separate prefixed table.
4. Upload two files whose names normalize to the same table name; confirm the request returns 400 and leaves no partial session/files.
5. Upload a file into a second session and confirm the first session cannot access its table or schema.
