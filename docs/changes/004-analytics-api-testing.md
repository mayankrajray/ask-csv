# Change 004 — Analytics and API Test Coverage

## Purpose

Add deterministic coverage for existing SQL analysis, dashboard, data quality, anomaly detection, forecasting, charts, reports, result exports, and representative API failures. Apply only fixes demonstrated by these tests.

## Existing Analytics Architecture

The API resolves a session/table to its in-memory DuckDB `DataEngine`. Analytics functions in `app/analytics.py` read bounded query results and use pandas/numpy for summaries, statistical anomaly checks, and simple linear forecasts. `app/charts.py` creates Plotly JSON specs. `ToolBox` validates and runs read-only SQL, caps rows returned to the LLM, and writes larger query results as session-scoped CSV exports. Dashboard, quality, forecast, and report endpoints return JSON; there is no standalone anomaly endpoint.

## Functionality Tested

- SQL selection, filtering, aggregation, grouping, ordering, limits, empty results, invalid SQL, and write rejection through `ToolBox`.
- Dashboard row/column counts, KPI totals, category charts, and empty-table response.
- Quality nulls, duplicate rows, type reporting, distinct counts, empty/constant columns, mixed values, and empty-table response.
- IQR and z-score anomaly detection for an obvious outlier, stable repeated output, normal values, small/constant samples, missing values, and nonnumeric data. Anomaly coverage uses the helper and `ToolBox` because the API has no anomaly route.
- Forecasts with sufficient observations, missing dates/values, constant values, insufficient observations, nonnumeric target, zero periods, and malformed period query parameters.
- Line, bar, scatter, histogram, pie, and donut chart specs; unsupported type fallback, empty queries, invalid SQL, and invalid chart columns.
- Report content, multiple metrics, and empty datasets.
- Bounded CSV result export, safe hash filename, valid CSV contents, and empty-result handling.
- API missing session/table/dataset and controlled validation/error responses without stack traces.

## Tests Added

Added 11 deterministic tests in `tests/test_analytics.py`. They use temporary CSVs, small fixed datasets, direct engine/tool calls, and FastAPI TestClient; no external LLM or network is used.

## Bugs Discovered

- Zero forecast periods returned success with an empty forecast.
- A named nonnumeric forecast metric raised during numeric conversion and became an API 500.
- Invalid chart columns could reach pandas numeric-column indexing and raise an uncaught `IndexError`.
- Empty quality analysis calculated a 100% health score, and empty report generation returned a successful report with no KPIs.

## Fixes Made

- Forecasting now returns a controlled `{ok: false}` result for periods below one and for explicitly selected nonnumeric metrics.
- `ToolBox.build_chart` returns controlled errors for missing x/y columns, nonnumeric y columns, and queries without numeric measures.
- Empty data quality and report requests now return the same controlled empty-table result used by the dashboard.

## Files Changed

- `app/analytics.py`
- `app/tools.py`
- `tests/test_analytics.py`
- `docs/changes/004-analytics-api-testing.md`

No frontend, provider, or storage changes were needed.

## Exact Test Results

- Analytics: 11 passed.
- Security: 13 passed.
- Conversation: 5 passed.
- Ingestion: 13 passed.
- API: 9 passed.
- Engine: 28 passed.
- Full suite: 79 passed.
- TestClient emitted the existing Starlette/httpx deprecation warning.

## Known Limitations

Forecasting is a simple linear trend projection over observed groups; it does not fill missing calendar periods or provide a full time-series model. Anomaly detection is statistical IQR/z-score screening with a minimum sample size, not machine learning. Unsupported chart types continue to fall back to bar charts. Reports return Markdown summaries rather than a separate HTML/PDF artifact. CSV export is the existing large query-result mechanism; this chunk does not redesign export security or add a general-purpose export API.

## Manual Verification Steps

1. Upload a small sales CSV and open `/api/dashboard/{session_id}/sales` and `/api/quality/{session_id}/sales`; compare row counts, totals, nulls, and duplicates with the CSV.
2. Open `/api/forecast/{session_id}/{table}?date_col=month&metric_col=sales&periods=3`; repeat with `periods=0` and a text metric to see controlled failures.
3. Open `/api/report/{session_id}/{table}` and confirm the KPI and quality summary.
4. Run a chart-producing analysis and confirm the chart JSON uses the selected columns and supported chart type.
5. In a local test or Python shell, use `ToolBox.run_sql` with more than `MAX_ROWS_TO_LLM` rows and confirm the session-scoped CSV export contains the full result.
