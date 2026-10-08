# Change 002 — Conversation Context

## Problem

The API kept a history list per session, but Gemini and OpenRouter only appended messages without limiting growth. DemoAgent ignored that list, so follow-up questions could not refer to earlier results.

## Existing Behavior

`/api/chat` passed each session's history to its agent and `/api/chat/new` cleared it. Provider changes also reset history. Gemini and OpenRouter retained prior provider messages; DemoAgent did not use them.

## Implementation

Added a shared in-place history trimmer and a configurable limit of 12 user turns. Before each request, agents keep at most the newest 11 completed/in-progress user turns; appending the current request makes at most 12 turns. OpenRouter retains its system message. Gemini counts text-bearing user messages and does not count function responses as user turns. Existing provider tool messages remain in the retained turn sequence.

### Provider Behavior

Gemini and OpenRouter continue sending their existing provider-native message formats, tool calls, and tool responses. Their existing system prompts still contain schema metadata rather than raw dataset rows. API keys remain provider client/header configuration and are not added to history or response events.

### DemoAgent Behavior

DemoAgent now records user/assistant turns in the session list. After a highest-region analysis, it stores only the table/column identifiers and winning region name as compact context metadata. A follow-up such as “How much did it sell?” performs a fresh, parameter-safe aggregate query scoped to that region. No dataset rows are added to prompts or copied into history. `/api/chat/new` clears this metadata with the rest of the conversation.

## History-Bounding Strategy

Keep the latest 12 user turns per session, dropping the oldest complete prefix before appending a new user turn. This simple turn-count bound accommodates provider tool exchanges while preventing unbounded conversation growth.

## Tests

Added deterministic tests for DemoAgent first/follow-up questions and reset, session isolation, provider history preservation and bounds with mocked Gemini/OpenRouter clients, and Gemini function-response counting.

## Actual Test Results

- Conversation tests: 5 passed.
- Security: 13 passed.
- API: 9 passed.
- Engine: 28 passed.
- Full suite: 55 passed.
- TestClient emitted the existing Starlette/httpx deprecation warning.

## Limitations

DemoAgent context resolution is intentionally narrow: it supports a “how much” follow-up referring to the most recent highest-region result. Other ambiguous follow-ups remain unsupported in offline mode. Remote provider context is bounded by turn count, not token count.

## Manual Verification Steps

1. Start AskCSV without a provider API key and load a CSV with a region and numeric sales column.
2. Ask “Which region has the highest sales?” and confirm the answer and aggregate SQL.
3. Ask “How much did it sell?” and confirm the SQL filters the selected region and the answer reports its total.
4. Start a new chat and ask the follow-up again; confirm the prior region is not reused.
5. Repeat the two questions in a separate uploaded session and confirm its context remains isolated.
