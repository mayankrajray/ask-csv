# Change 005 — Agent and Tool Reliability

## Current Agent Architecture

Gemini and OpenRouter agents send model-selected calls to a shared `ToolBox`; `DemoAgent` uses deterministic local SQL and chart tools. All providers share per-session history and the existing tool definitions. The configured tool-call budget is eight calls per user turn.

## Failure Modes and Changes

- Tool dispatch now validates argument objects, required and unexpected fields, types, tables, columns, supported chart types, and anomaly methods before execution. SQL remains subject to the existing engine guard.
- Tool, chart, anomaly, and export failures return controlled results. Useful SQL diagnostics are returned to the model while user-facing events omit internal exception details.
- Gemini and OpenRouter validate response structure, tool names, and arguments. Provider timeout, authentication, rate-limit, HTTP, connection, and malformed-response failures receive concise safe messages; raw provider bodies and exception text are not returned.
- Both model agents enforce the configured limit across all tool calls, including calls grouped in one response. Failed or malformed provider turns roll back their partial history so the session can be retried.
- The API chat stream masks unexpected exception details. DemoAgent tolerates malformed context, validates saved follow-up context against the current schema, and returns deterministic controlled errors for query/chart failures.

## Tests

Added `tests/test_agent_reliability.py` with mocked Gemini/OpenRouter responses and deterministic local fixtures. It covers invalid tool arguments, tool failures, invalid SQL through a provider tool call, bounded loops and batches, malformed provider output, provider failures, DemoAgent context and failures, API error masking, and session recovery. Existing security, conversation, ingestion, API, engine, and analytics suites provide regression coverage.

## Bugs Discovered and Fixes

- Raw provider exception text and OpenRouter error bodies could reach the chat stream; safe provider-specific failure messages now replace them.
- Malformed model responses and tool arguments were not consistently validated and could crash or continue with empty arguments; responses are validated and malformed calls become controlled tool errors.
- The per-round loop bound could be bypassed by multiple calls in one response; the total tool-call count is now bounded.
- Failed provider turns could leave an unmatched partial user/assistant turn in session history; failures now restore the prior history.
- Unexpected API chat exceptions exposed exception names and details; the stream now emits a useful generic recovery message.
- DemoAgent could fail on malformed saved context or tool exceptions; context is checked against the current table schema and tool failures are handled.

## Files Changed

`app/agent.py`, `app/conversation.py`, `app/demo_agent.py`, `app/main.py`, `app/openrouter_agent.py`, `app/provider_errors.py`, `app/tools.py`, `tests/test_agent_reliability.py`, and this document.

## Actual Test Results

- Reliability + conversation + security: **46 passed**, 1 existing Starlette/httpx deprecation warning.
- API + engine + ingestion + analytics: **61 passed**, 1 existing Starlette/httpx deprecation warning.
- Reliability tests alone: **28 passed**, 1 existing Starlette/httpx deprecation warning.
- Full suite: **107 passed**, 1 existing Starlette/httpx deprecation warning.

No external provider calls or credentials were used.

## Security Preservation

This chunk does not change the SQL write guard, DuckDB external-access controls, upload validation, API-key session handling, untrusted-data prompt boundaries, or bounded conversation-history policy. Dataset rows are still supplied through tool results rather than added wholesale to provider system prompts.

## Known Limitations

Provider failures are intentionally summarized and omit provider-specific diagnostic bodies, which can limit diagnosis of unusual provider errors. The eight-call budget is per user turn; it does not impose a separate wall-clock or response-size limit. DemoAgent supports its existing deterministic question patterns only.

## Manual Verification

1. Start the app in offline demo mode and ask a supported question, then a follow-up using its result.
2. Submit an unsupported question and confirm DemoAgent responds with its offline-mode guidance.
3. With mocked provider responses, try malformed tool arguments and a provider timeout; confirm the stream reports a controlled error and the session can answer a subsequent request.
4. Try a tool loop and confirm it stops at the configured `MAX_TOOL_STEPS` limit.

## Git Checkpoint

Commit: `82e554c` — `fix: harden agent and tool execution`
Branch: `assignment-final`
