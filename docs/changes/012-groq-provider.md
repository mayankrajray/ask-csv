# Change 012 — Groq Provider Integration

## Purpose

Add Groq as a selectable AI provider while retaining Gemini, OpenRouter, and offline DemoAgent behavior.

## Implementation

Groq uses `https://api.groq.com/openai/v1/chat/completions` and the default model `openai/gpt-oss-20b`. The provider reuses AskCSV's OpenAI-compatible tool-call loop and dispatches tool calls through the existing `ToolBox`, including SQL, schema profiling, charting, and anomaly tools. The model identifier is configurable with `GROQ_MODEL`; the credential is read from `GROQ_API_KEY`.

Selection uses the existing `LLM_PROVIDER` setting with value `groq`. If Groq is explicitly selected but its key is absent, AskCSV uses DemoAgent. Provider errors do not switch to Gemini or OpenRouter. Existing provider-specific session keys are not carried across a provider change. The configuration endpoints report whether Groq is configured without returning its key. The frontend settings selector now offers Groq; its layout and visual style remain consistent with the existing selector.

The model ID was checked against Groq's official [model documentation](https://console.groq.com/docs/model/openai/gpt-oss-20b), and the tool-call flow against their [tool-use documentation](https://console.groq.com/docs/tool-use/overview). No live Groq request was made as part of this change.

## Tests

Deterministic mocked tests cover provider selection, missing credentials despite other configured providers, provider switching and session-key isolation, the exact endpoint/model/tool schema, a `run_sql` tool call and follow-up completion, and controlled authentication, rate-limit, HTTP, timeout, and malformed-response failures. Existing Gemini, OpenRouter, API, security, and tool tests remain in the suite.

## Verification Results

Results for this working tree:

- Full suite: **164 passed, 2 skipped**.
- Evaluation suite: **12 passed**.
- `pip check`: **No broken requirements found**.
- `node --check frontend/js/app.js`: passed.
- `git diff --check`: passed.
- One existing Starlette/httpx deprecation warning was emitted during pytest.

Tests mock external provider traffic; no API key was used. The deployed application was not changed or verified for Groq. The live service remains on its existing deployment configuration until the owner adds `GROQ_API_KEY`, chooses `LLM_PROVIDER=groq`, and deploys. `ALLOW_KEY_OVERRIDE=false` should remain set in the hosted environment.

## Limitations

Groq availability, account limits, model access, and usage charges depend on the user's Groq account. There is no automatic Gemini/OpenRouter fallback. A health response identifies the selected/configured mode, but a successful real model request is needed to confirm live provider operation. The local app continues to have its existing unauthenticated, in-memory session and ephemeral local-file limitations.
