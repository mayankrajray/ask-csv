# Assignment Requirement Matrix

Statuses describe the implementation present in this repository. `PARTIAL` means the named capability has a bounded scope or documented limitation; `NOT IMPLEMENTED` means no such application feature exists.

| Requirement | Implementation | Evidence / test | Status |
|---|---|---|---|
| Upload one or more CSV files | Bounded multipart upload into an isolated session; file/count/aggregate upload-batch size validation | `app/main.py`; `tests/test_security.py`, `tests/test_ingestion.py`, `tests/test_storage_security.py` | PASS |
| Excel ingestion | XLS/XLSX parsing; workbook sheets are loaded as tables where supported | `app/engine.py`; `tests/test_ingestion.py` | PASS |
| Natural-language dataset analysis | Gemini, Groq, OpenRouter, and offline DemoAgent call the ToolBox | `app/agent.py`, `app/groq_agent.py`, `app/openrouter_agent.py`, `app/demo_agent.py`; `tests/test_agent_reliability.py`, `tests/evaluation/` | PASS |
| Business insights and summaries | SQL-backed analysis answers and report/dashboard summaries | `app/tools.py`, `app/analytics.py`; `tests/test_analytics.py`, `tests/evaluation/` | PASS |
| Charts | Chart specifications support implemented chart types and are rendered by frontend Plotly | `app/charts.py`, `frontend/js/app.js`; `tests/test_analytics.py`, evaluation chart case | PASS |
| Generate/use SQL or Pandas | Agent uses guarded read-only SQL through DuckDB; ingestion/analytics use Pandas | `app/engine.py`, `app/tools.py`; `tests/test_engine.py` | PASS |
| Explain reasoning/results | UI shows analysis status, SQL, result data and a natural-language explanation | `/api/chat` flow; `tests/test_api.py`, conversation and evaluation tests | PARTIAL — evidence and explanation are surfaced, but this is not a promise to expose private model chain-of-thought. |
| Detect and explain anomalies | Numeric IQR and z-score rules return flagged values/statistics | `app/anomalies.py`; `tests/test_analytics.py`, `tests/test_engine.py`, evaluation anomaly case | PASS |
| Maintain conversation context | Per-session, bounded provider history; DemoAgent resolves supported follow-ups; new chat clears history | `app/conversation.py`, provider agents; `tests/test_conversation.py`, `tests/test_agent_reliability.py` | PASS |
| Multi-file analysis and joins | Tables are held in one session DuckDB engine and can be joined with SQL | `app/engine.py`; `tests/test_ingestion.py`, `tests/evaluation/test_evaluation.py` | PASS |
| Modular architecture | Separate API, engine, agents, tools, analytics, anomaly, chart, config, and frontend modules | `app/`; [architecture](architecture.md) | PASS |
| Controlled error handling | Input/tool/provider failures return controlled API or tool errors | `app/main.py`, `app/tools.py`; `tests/test_api.py`, `tests/test_agent_reliability.py`, `tests/test_analytics.py` | PASS |
| Documentation and tests | Change records, architecture, matrix, README, automated pytest suite | `docs/`, `tests/` | PASS |
| Docker support | Dockerfile and Compose service are present with app/data configuration | `Dockerfile`, `docker-compose.yml`; this environment’s engine was unavailable for a build (see change 011) | PARTIAL — configuration exists; Docker image build/run could not be verified in this environment. |
| Architecture diagram | Mermaid system and boundary diagrams | [architecture.md](architecture.md) | PASS |
| Screenshots/demo readiness | Existing UI screenshots and a reproducible demo sequence are documented | `screenshot/`; [README demo flow](../README.md#3–5-minute-demo-flow) | PASS |
| Multi-file joins (bonus) | SQL joins operate across session tables | Ingestion and evaluation join tests | PASS |
| Dashboard (bonus) | Per-table counts, metrics, and summaries | `app/analytics.py`; `tests/test_analytics.py` | PASS |
| Data-quality analysis (bonus) | Missing, duplicate, type, uniqueness, and column summaries | `app/analytics.py`; analytics tests and evaluation quality case | PASS |
| Forecasting (bonus) | Linear trend projection with residual-spread band | `app/analytics.py`; forecast tests | PARTIAL — no seasonality or advanced predictive model. |
| Agentic workflow/tool calls (bonus) | Provider agents invoke validated tools with an 8-step budget | `app/agent.py`, `app/openrouter_agent.py`; `tests/test_agent_reliability.py` | PASS |
| Semantic search (bonus) | No embedding/vector retrieval feature | No implementation or test | NOT IMPLEMENTED |
| Caching (bonus) | UI may retain preview state; no server-side query/LLM response cache | Frontend state; no cache service in `app/` | PARTIAL — no application response cache. |
| Authentication (bonus) | No account/login/authentication subsystem; sessions use bearer IDs | API/session model; documented security limitation | NOT IMPLEMENTED |
| Export functionality (bonus) | Session-owned CSV result exports with limits and formula neutralization; reports can be downloaded/printed | `app/tools.py`, `app/main.py`; `tests/test_storage_security.py`, `tests/test_analytics.py` | PASS |
| Streaming (bonus) | Chat response streams Server-Sent Events | `/api/chat`; `tests/test_api.py`, `tests/test_conversation.py` | PASS |
| Observability (bonus) | Per-session SQL execution log/status view | `/api/logs/{sid}`; API and reliability tests | PASS |
| Evaluation / benchmarking (bonus) | 12 deterministic fixed-fixture pytest cases using DemoAgent and real local analytics/tools | `tests/evaluation/`; `docs/changes/010-evaluation-quality-gate.md` | PARTIAL — no live LLM quality scoring or broad benchmark. |
