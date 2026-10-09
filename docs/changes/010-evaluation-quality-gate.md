# Change 010 — Evaluation and Quality Gate

## Purpose and architecture

Add a small offline pytest quality gate using the real `DataEngine`, `ToolBox`, analytics functions, and `DemoAgent`. The suite has fixed fixtures and explicit expected results. It does not call Gemini/OpenRouter and reports individual pytest case names when run verbosely.

## Datasets

Fixtures under `tests/evaluation/fixtures/` include a five-row sales dataset, small customers/transactions tables for a join, a three-region conversation dataset, a numeric outlier sample, a three-point linear series, and a dataset with one duplicate row and one missing value. They contain no credentials or personal data.

## Evaluation cases and actual results

The evaluation command completed with **12 passed, 0 failed**.

| Area | Cases | Result |
|---|---:|---|
| Aggregation | 1 | PASS |
| Grouping | 1 | PASS |
| Filtering | 1 | PASS |
| Sorting / top-N | 1 | PASS |
| Multi-file join | 1 | PASS |
| Conversation context | 1 | PASS |
| IQR anomaly | 1 | PASS |
| Linear forecast | 1 | PASS |
| Data quality | 1 | PASS |
| Chart data | 1 | PASS |
| Invalid SQL / continued usability | 1 | PASS |
| Unsupported DemoAgent question | 1 | PASS |
| **Total** | **12** | **12 passed** |

Representative expected values: total sales **6600**; highest region **South**; product A units **38**; top two regions **South (2600), North (2000)**; joined totals **North 150 / South 70**; conversation follow-up resolves North at **4.0 lakh**; anomaly sample contains **1000**; the forecast is upward with **2 finite points**; quality reports **1 missing value and 1 duplicate row**.

## What is measured

These tests measure exact deterministic outputs or conditions against local fixtures. The conversation case uses DemoAgent's actual bounded history and SQL tool path. The error case sends forbidden SQL through `ToolBox.dispatch`, checks for a controlled event, then verifies a valid query still works.

Provider integration behavior is covered elsewhere in the test suite with mocks. Those tests verify request/response handling without external calls. This evaluation does not measure Gemini/OpenRouter answer quality or live model accuracy; no LLM accuracy percentage is claimed.

## Tests and command

Run with visible case names and a pass/fail summary:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/evaluation -v
```

The requested quiet command was also run: `.\.venv\Scripts\python.exe -m pytest tests/evaluation -q` reported **12 passed**. The complete suite reported **156 passed**, with one existing Starlette/httpx deprecation warning.

## Known limitations

This is a compact regression gate, not a broad benchmark. It tests fixed input examples and does not generalize to arbitrary schemas or language. DemoAgent recognizes a fixed set of question patterns; deterministic ToolBox/analytics cases are not evidence of provider model quality. The multi-file case checks a SQL join and aggregation, while advanced join reasoning remains unmeasured.

## Files added

- `tests/evaluation/test_evaluation.py`
- `tests/evaluation/fixtures/*.csv`
- `docs/changes/010-evaluation-quality-gate.md`
