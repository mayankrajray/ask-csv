# AskCSV Architecture

## Request and analysis flow

```mermaid
flowchart TD
  subgraph Client[Browser]
    UI[Static HTML / CSS / JavaScript]
  end
  UI <-->|REST + Server-Sent Events| API[FastAPI application]
  API --> Sessions[In-memory session manager]
  Sessions --> State[Session: DuckDB engine · conversation · provider overrides · file metadata]
  API --> Agent[Agent selection]
  Agent --> Gemini[Gemini agent]
  Agent --> OpenRouter[OpenRouter agent]
  Agent --> Demo[Offline DemoAgent]
  Agent --> Tools[ToolBox]
  Tools --> SQL[Read-only SQL guard]
  SQL --> DB[(Session DuckDB tables)]
  DB --> Data[Parsed CSV / XLS / XLSX datasets]
  Tools --> Views[SQL · chart specs · anomaly detection]
  API --> Analytics[Dashboard · data quality · forecast · report]
  Analytics --> DB
  Tools --> Export[Session-owned CSV export]
```

The frontend is served by FastAPI from `frontend/`; the repository has no npm build pipeline. An external static frontend can set `window.ASKCSV_API_BASE` or the supported browser setting to point at the API, with its origin added to `ALLOWED_ORIGINS`.

## Agent providers

The configured provider selects Gemini or OpenRouter when a usable key is present. If neither provider is configured, the application runs with DemoAgent, which uses deterministic patterns and local tools without an external API. Provider configuration overrides belong to the in-memory session. Tool calls flow through `ToolBox`; model-provided SQL is checked before DuckDB executes it.

## Analytics and tools

ToolBox exposes SQL execution, chart construction, anomaly detection, schema profiling, and CSV export. The API exposes dashboard summaries, data-quality analysis, linear forecasting, reports, schema and preview routes. CSV/XLS/XLSX inputs are parsed as dataframes and loaded into the session’s DuckDB engine; join queries can reference multiple session tables.

## Security and lifecycle boundaries

```mermaid
flowchart LR
  Untrusted[Browser requests + filenames + uploaded cells] --> Validate[Request validation · bounded upload · safe paths]
  Validate --> Session[Session ownership checks]
  Session --> Tools[Tool arguments + bounded tool steps]
  Tools --> SQLGuard[Single read-only SQL validation]
  SQLGuard --> Duck[DuckDB with external access disabled]
  Session --> History[Bounded conversation; data and tool results framed as untrusted]
  Session --> Export[Export ownership + size/count limits + CSV formula neutralization]
  Config[Environment defaults + session-scoped provider overrides] --> Session
```

The controls reduce exposure but do not provide user authentication. Session IDs are bearer capabilities. Sessions and provider overrides live in process memory; uploads and exports are local files with retention cleanup and are not shared durable storage for multi-worker deployments. Prompt-injection handling is a mitigation, not a guarantee.

## Runtime configuration

Environment variables and defaults are listed in [README.md](../README.md#environment-configuration) and `.env.example`. Docker Compose forwards the provider settings and configurable limits. Do not bake `.env` credentials into an image.
