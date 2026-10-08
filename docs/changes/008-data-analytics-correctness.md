# Change 008 — Data and Analytics Correctness

## Areas reviewed

Reviewed SQL aggregation through `ToolBox`/DuckDB, dashboard and quality summaries, IQR/z-score anomaly detection, linear forecasting, chart specifications, reports, and joined-table analysis. Existing coverage already exercised basic SQL, quality, anomalies, forecasts, chart types, reports, and ingestion joins.

## Correctness fixes

- Flat forecast values could acquire a tiny positive slope from floating-point fitting and be labeled “Upward.” Slopes numerically equal to zero are now reported as “Flat.”
- An explicitly requested unknown forecast date or metric column previously fell back to a different column. These inputs now return a controlled `{ok: false}` result.
- Chart specs previously converted missing numeric values to zero and missing x values to the string `"nan"`. Missing chart values now remain JSON nulls so they are not drawn as real zeros or categories.
- Forecast chart traces labeled a residual-standard-deviation spread as a “95% Confidence” interval. The labels now describe the 1.96× residual standard deviation band without claiming inferential confidence coverage.

## Methods and edge cases

- **Anomalies:** statistical IQR fences or a 3-standard-deviation z-score, with a minimum sample/variation requirement. This is not machine learning.
- **Forecasting:** a linear projection over the observed ordered groups, with a residual-spread band and a zero floor when all history is nonnegative. It does not fill missing calendar dates or model seasonality. The residual band is descriptive, not a calibrated confidence interval.
- **Charts:** existing deterministic Plotly JSON specifications for supported chart types; null axis values are preserved.
- **Reports:** existing Markdown executive summary composed from dashboard and quality output.
- Numeric tests include negative, zero, and decimal values; SQL filtering and grouped aggregation after joins; duplicated dimension keys; unmatched keys; flat/decreasing forecasts; and missing chart data.

## Bugs and fixes

The four issues above were reproduced with deterministic tests and fixed with small changes in `app/analytics.py` and `app/charts.py`. No ingestion architecture, analytics framework, or report format was changed.

## Files changed

- `app/analytics.py`
- `app/charts.py`
- `tests/test_analytics.py`
- `docs/changes/008-data-analytics-correctness.md`

## Tests and actual results

Four tests were added in `tests/test_analytics.py` for numeric/join aggregation, flat/decreasing forecast direction, invalid explicit forecast columns, and missing chart values.

- Analytics, ingestion, and engine suites: **56 passed**.
- Full suite: **127 passed**, with one existing Starlette/httpx deprecation warning.
- `git diff --check`: passed.

## Known limitations

Forecasting is a simple linear projection over the ordering of grouped values. Irregular or missing dates are not interpolated, and seasonal patterns are not modeled. The residual-spread band is descriptive. IQR/z-score checks are basic statistical screens. Dashboard summaries operate on bounded samples and categorical groupings may omit null-category groups. Reports are summaries, not exhaustive data validation.

## Manual verification

1. Upload a CSV with a flat `date,value` series and request `/api/forecast/{session_id}/{table}?date_col=date&metric_col=value`; confirm the trend is `Flat` and chart traces call the envelope a residual spread band.
2. Repeat with a declining series and verify the trend is `Downward`.
3. Request a forecast with an unknown explicit date or metric column; confirm it returns a controlled error instead of forecasting another column.
4. Build a chart from rows containing missing x/y values; confirm the JSON contains nulls rather than the text `"nan"` or numeric zero.
5. Use `ToolBox.run_sql` for a grouped join with duplicate and unmatched keys and compare the aggregate against the input rows.
