from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import config
from app import agent as gemini_module
from app import openrouter_agent as openrouter_module
from app.groq_agent import GroqAgent, GROQ_BASE_URL
from app.demo_agent import DemoAgent
from app.engine import DataEngine
from app.main import app, sessions, _new_session, _drop_session, _create_agent
from app.openrouter_agent import OpenRouterAgent
from app.agent import GeminiAgent
from app.tools import ToolBox


SECRET = "sk-test-secret-do-not-disclose"


@pytest.fixture
def engine(tmp_path):
    path = tmp_path / "sales.csv"
    path.write_text("region,sales\nNorth,100\nSouth,200\nWest,300\n", encoding="utf-8")
    result = DataEngine()
    result.load_csv("sales", str(path))
    return result


def _gemini_types(monkeypatch):
    class Part:
        def __init__(self, text=None, function_call=None, function_response=None):
            self.text, self.function_call, self.function_response = text, function_call, function_response

    class Content:
        def __init__(self, role, parts):
            self.role, self.parts = role, parts

    class FunctionResponse:
        def __init__(self, name, response):
            self.name, self.response = name, response

    class Types:
        class GenerateContentConfig:
            def __init__(self, **kwargs): self.kwargs = kwargs
        class Tool:
            def __init__(self, **kwargs): pass
        class FunctionDeclaration:
            def __init__(self, **kwargs): pass

    Types.Part, Types.Content, Types.FunctionResponse = Part, Content, FunctionResponse
    monkeypatch.setattr(gemini_module, "types", Types)
    return Part, Content


def _gemini_agent(monkeypatch, engine, responses, key=SECRET):
    Part, Content = _gemini_types(monkeypatch)
    calls = []

    class Models:
        def generate_content(self, **kwargs):
            calls.append(kwargs)
            response = responses[len(calls) - 1]
            if isinstance(response, Exception):
                raise response
            return response

    class GenAI:
        class Client:
            def __init__(self, api_key):
                assert api_key == key
                self.models = Models()

    monkeypatch.setattr(gemini_module, "genai", GenAI)
    monkeypatch.setattr(gemini_module, "_declarations", lambda: [])
    return GeminiAgent(engine, "gemini-test-session", api_key=key), calls, Part, Content


def _gemini_text(Content, Part, text="answer"):
    return SimpleNamespace(candidates=[SimpleNamespace(content=Content("model", [Part(text=text)]))])


class _OpenRouterResponse:
    def __init__(self, status=200, payload=None, body=""):
        self.status_code, self.payload, self.text = status, payload, body

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def _openrouter_agent(monkeypatch, engine, responses, key=SECRET):
    calls = []

    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, headers, json):
            assert headers["Authorization"] == f"Bearer {key}"
            calls.append(json)
            response = responses[len(calls) - 1]
            if isinstance(response, Exception):
                raise response
            return response

    monkeypatch.setattr(openrouter_module.httpx, "Client", Client)
    return OpenRouterAgent(engine, "openrouter-test-session", api_key=key), calls


def _groq_agent(monkeypatch, engine, responses, key=SECRET):
    calls, urls = [], []

    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, headers, json):
            assert headers["Authorization"] == f"Bearer {key}"
            calls.append(json)
            urls.append(url)
            response = responses[len(calls) - 1]
            if isinstance(response, Exception):
                raise response
            return response

    monkeypatch.setattr(openrouter_module.httpx, "Client", Client)
    return GroqAgent(engine, "groq-test-session", api_key=key), calls, urls


def _or_text(text="answer"):
    return _OpenRouterResponse(payload={"choices": [{"message": {"role": "assistant", "content": text}}]})


@pytest.mark.parametrize(("name", "args", "expected"), [
    ("run_sql", {}, "requires a non-empty"),
    ("run_sql", {"query": 5}, "requires a non-empty"),
    ("profile_schema", {"table": "missing"}, "Unknown table"),
    ("profile_schema", {"table": []}, "must be a string"),
    ("detect_anomalies", {"table": "sales", "column": "missing"}, "Unknown column"),
    ("detect_anomalies", {"table": "sales", "method": "other"}, "must be 'iqr'"),
    ("build_chart", {"query": "SELECT sales FROM sales", "chart_type": "invalid", "x": "sales", "y": "sales"},
     "Unsupported chart type"),
    ("build_chart", {"query": "SELECT sales FROM sales", "chart_type": "bar", "x": "missing", "y": "sales"},
     "not in the query result"),
    ("build_chart", {"query": "SELECT region FROM sales", "chart_type": "bar", "x": "region", "y": "region"},
     "must be numeric"),
    ("run_sql", {"query": "SELECT 1", "unexpected": True}, "Unexpected argument"),
])
def test_tool_dispatch_validates_arguments_and_returns_controlled_errors(engine, name, args, expected):
    text, events = ToolBox(engine, "dispatch-test").dispatch(name, args)
    assert expected in text
    assert events
    assert events[0]["type"] in ("error", "sql_error")
    assert SECRET not in repr(events)


def test_invalid_sql_tool_error_is_safe_but_informative(engine):
    text, events = ToolBox(engine, "sql-failure-test").dispatch("run_sql", {"query": "SELECT absent FROM sales"})
    assert "SQL ERROR" in text
    assert "absent" in text
    assert events == [{"type": "sql_error", "error":
                       "The query could not run. Check its syntax, table names, and columns."}]


def test_invalid_sql_completes_mocked_natural_language_tool_flow(engine, monkeypatch):
    call = _OpenRouterResponse(payload={"choices": [{"message": {"role": "assistant", "tool_calls": [
        {"id": "call-invalid-sql", "function": {
            "name": "run_sql", "arguments": '{"query":"SELECT absent FROM sales"}'}}]}}]})
    agent, requests = _openrouter_agent(monkeypatch, engine, [call, _or_text("The query could not run.")])
    events = list(agent.chat([], "Show sales by missing column"))
    error_event = next(event for event in events if event.get("type") == "sql_error")
    assert "check its syntax" in error_event["error"].lower()
    assert any("The query could not run." in event.get("text", "") for event in events)
    tool_result = next(message for message in requests[1]["messages"] if message.get("role") == "tool")
    assert "absent" in tool_result["content"]
    assert SECRET not in repr(events)


def test_provider_agents_contain_unexpected_tool_dispatch_exceptions(engine, monkeypatch):
    Part, Content = _gemini_types(monkeypatch)
    tool_response = SimpleNamespace(candidates=[SimpleNamespace(content=Content("model", [Part(
        function_call=SimpleNamespace(name="run_sql", args={"query": "SELECT 1"}))]))])
    agent, _, _, _ = _gemini_agent(monkeypatch, engine, [tool_response, _gemini_text(Content, Part, "recovered")])
    monkeypatch.setattr(agent.tb, "dispatch", lambda *a: (_ for _ in ()).throw(RuntimeError(SECRET)))
    events = list(agent.chat([], "question"))
    assert any(e.get("type") == "error" and "tool failed" in e.get("detail", "") for e in events)
    assert any("recovered" in e.get("text", "") for e in events)
    assert SECRET not in repr(events)

    tool_call = _OpenRouterResponse(payload={"choices": [{"message": {"role": "assistant", "tool_calls": [
        {"id": "call", "function": {"name": "run_sql", "arguments": '{"query":"SELECT 1"}'}}]}}]})
    agent, _ = _openrouter_agent(monkeypatch, engine, [tool_call, _or_text("recovered")])
    monkeypatch.setattr(agent.tb, "dispatch", lambda *a: (_ for _ in ()).throw(RuntimeError(SECRET)))
    events = list(agent.chat([], "question"))
    assert any(e.get("type") == "error" and "tool failed" in e.get("detail", "") for e in events)
    assert any("recovered" in e.get("text", "") for e in events)
    assert SECRET not in repr(events)


def test_tool_execution_chart_anomaly_and_export_failures_are_controlled(engine, monkeypatch, tmp_path):
    import app.tools as tools_module
    tb = ToolBox(engine, "tool-failure-test")
    execute = engine.execute
    monkeypatch.setattr(tb, "run_sql", lambda query: (_ for _ in ()).throw(RuntimeError(f"{SECRET} C:\\private\\db")))
    text, events = tb.dispatch("run_sql", {"query": "SELECT 1"})
    assert "RuntimeError" in text
    assert SECRET not in text and "C:\\private" not in repr(events)
    assert events[0]["type"] == "error"

    monkeypatch.setattr(tb, "run_sql", ToolBox(engine, "replacement").run_sql)
    monkeypatch.setattr(tools_module.charts, "build_spec", lambda *a, **k: (_ for _ in ()).throw(RuntimeError(SECRET)))
    chart = tb.build_chart("SELECT region, sales FROM sales", "bar", "region", "sales")
    assert chart["ok"] is False
    assert SECRET not in repr(chart)

    text, events = tb.dispatch("build_chart", {"query": "SELECT missing FROM sales", "chart_type": "bar",
                                               "x": "missing", "y": "missing"})
    assert "not found" in text
    assert SECRET not in repr(events)
    assert "not found" not in repr(events)

    monkeypatch.setattr(tools_module, "analyse_dataframe", lambda *a, **k: (_ for _ in ()).throw(RuntimeError(SECRET)))
    text, events = tb.dispatch("detect_anomalies", {"table": "sales"})
    assert "RuntimeError" in text
    assert SECRET not in repr(events)

    monkeypatch.setattr(engine, "execute", lambda *a, **k: (_ for _ in ()).throw(RuntimeError(f"{SECRET} C:\\db")))
    text, events = tb.dispatch("detect_anomalies", {"table": "sales"})
    assert SECRET not in repr(events) and "C:\\db" not in repr(events)
    assert "Anomaly detection could not complete" in events[0]["detail"]

    monkeypatch.setattr(engine, "execute", execute)
    monkeypatch.setattr(config, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(config, "MAX_ROWS_TO_LLM", 1)
    export_tb = ToolBox(engine, "export-failure-test")
    monkeypatch.setattr(Path, "write_text", lambda *a, **k: (_ for _ in ()).throw(OSError(f"{SECRET} C:\\private")))
    failed = export_tb.run_sql("SELECT * FROM sales")
    assert failed["ok"] is False
    assert SECRET not in repr(failed)
    assert "C:\\private" not in repr(failed)


def test_gemini_handles_timeout_malformed_and_missing_tool_name(engine, monkeypatch):
    Part, Content = _gemini_types(monkeypatch)
    agent, _, _, _ = _gemini_agent(monkeypatch, engine, [TimeoutError(SECRET)])
    events = list(agent.chat([], "question"))
    assert events[-1]["type"] == "error"
    assert "timed out" in events[-1]["detail"]
    assert SECRET not in repr(events)

    agent, _, _, _ = _gemini_agent(monkeypatch, engine, [SimpleNamespace()])
    malformed = list(agent.chat([], "question"))
    assert malformed[-1]["type"] == "error"
    assert "malformed" in malformed[-1]["detail"]

    bad_call = SimpleNamespace(candidates=[SimpleNamespace(content=Content("model", [
        Part(function_call=SimpleNamespace(args={"query": "SELECT 1"}))]))])
    agent, _, _, _ = _gemini_agent(monkeypatch, engine, [bad_call])
    missing_name = list(agent.chat([], "question"))
    assert missing_name[-1]["type"] == "error"
    assert "function name" in missing_name[-1]["detail"]


@pytest.mark.parametrize(("response", "phrase"), [
    (_OpenRouterResponse(401, {"error": {"message": f"{SECRET} C:\\private"}}), "authentication failed"),
    (_OpenRouterResponse(429, {"error": {"message": SECRET}}), "rate limit"),
    (_OpenRouterResponse(503, {"error": {"message": SECRET}}), "HTTP 503"),
    (_OpenRouterResponse(payload=ValueError(SECRET)), "malformed JSON"),
    (_OpenRouterResponse(payload={"choices": []}), "no usable response choices"),
    (_OpenRouterResponse(payload={"choices": [{"message": {"content": ["unexpected"]}}]}), "no usable text"),
])
def test_openrouter_provider_failures_are_sanitized(engine, monkeypatch, response, phrase):
    agent, _ = _openrouter_agent(monkeypatch, engine, [response])
    events = list(agent.chat([], "question"))
    assert events[-1]["type"] == "error"
    assert phrase in events[-1]["detail"]
    assert SECRET not in repr(events)
    assert "C:\\private" not in repr(events)


def test_openrouter_timeout_malformed_tool_arguments_and_missing_tool_name(engine, monkeypatch):
    agent, _ = _openrouter_agent(monkeypatch, engine, [TimeoutError(SECRET)])
    events = list(agent.chat([], "question"))
    assert "timed out" in events[-1]["detail"]
    assert SECRET not in repr(events)

    malformed_args = _OpenRouterResponse(payload={"choices": [{"message": {"role": "assistant", "tool_calls": [
        {"id": "call-1", "function": {"name": "run_sql", "arguments": "not-json"}}]}}]})
    agent, calls = _openrouter_agent(monkeypatch, engine, [malformed_args, _or_text("recovered")])
    events = list(agent.chat([], "question"))
    assert any(e.get("type") == "error" and "malformed arguments" in e.get("detail", "") for e in events)
    assert any("recovered" in e.get("text", "") for e in events)
    assert len(calls) == 2

    missing_name = _OpenRouterResponse(payload={"choices": [{"message": {"role": "assistant", "tool_calls": [
        {"id": "call-1", "function": {"arguments": "{}"}}]}}]})
    agent, _ = _openrouter_agent(monkeypatch, engine, [missing_name])
    events = list(agent.chat([], "question"))
    assert events[-1]["type"] == "error"
    assert "valid name or ID" in events[-1]["detail"]

    agent, _ = _openrouter_agent(monkeypatch, engine, [ConnectionError(SECRET)])
    unavailable = list(agent.chat([], "question"))
    assert "unavailable" in unavailable[-1]["detail"]
    assert SECRET not in repr(unavailable)


def test_provider_failure_rolls_back_partial_turn_before_retry(engine, monkeypatch):
    agent, calls = _openrouter_agent(monkeypatch, engine, [
        _OpenRouterResponse(401, {"error": {"message": SECRET}}), _or_text("retry succeeded")])
    history = []
    failed = list(agent.chat(history, "first question"))
    assert "authentication failed" in failed[-1]["detail"]
    assert [message["role"] for message in history] == ["system"]
    recovered = list(agent.chat(history, "second question"))
    assert any("retry succeeded" in event.get("text", "") for event in recovered)
    assert [message["content"] for message in calls[-1]["messages"] if message["role"] == "user"] == ["second question"]

    Part, Content = _gemini_types(monkeypatch)
    success = _gemini_text(Content, Part, "retry succeeded")
    agent, _, _, _ = _gemini_agent(monkeypatch, engine, [TimeoutError(SECRET), success])
    history = []
    failed = list(agent.chat(history, "first question"))
    assert "timed out" in failed[-1]["detail"]
    assert history == []
    recovered = list(agent.chat(history, "second question"))
    assert any("retry succeeded" in event.get("text", "") for event in recovered)
    assert [part.text for item in history if getattr(item, "role", None) == "user"
            for part in item.parts if getattr(part, "text", None)] == ["second question"]


def test_provider_tool_loops_stop_at_configured_limit(engine, monkeypatch):
    monkeypatch.setattr(config, "MAX_TOOL_STEPS", 3)
    Part, Content = _gemini_types(monkeypatch)
    repeated = SimpleNamespace(candidates=[SimpleNamespace(content=Content("model", [Part(
        function_call=SimpleNamespace(name="run_sql", args={"query": "SELECT 1"}))]))])
    agent, gemini_calls, _, _ = _gemini_agent(monkeypatch, engine, [repeated] * 3)
    monkeypatch.setattr(agent.tb, "dispatch", lambda *a: ("ok", []))
    events = list(agent.chat([], "question"))
    assert len(gemini_calls) == 3
    assert "budget" in events[-1]["detail"]

    tool_call = _OpenRouterResponse(payload={"choices": [{"message": {"role": "assistant", "tool_calls": [
        {"id": "call", "function": {"name": "run_sql", "arguments": '{"query":"SELECT 1"}'}}]}}]})
    agent, or_calls = _openrouter_agent(monkeypatch, engine, [tool_call] * 3)
    monkeypatch.setattr(agent.tb, "dispatch", lambda *a: ("ok", []))
    events = list(agent.chat([], "question"))
    assert len(or_calls) == 3
    assert "budget" in events[-1]["detail"]

    gemini_batch = SimpleNamespace(candidates=[SimpleNamespace(content=Content("model", [
        Part(function_call=SimpleNamespace(name="run_sql", args={"query": "SELECT 1"})) for _ in range(4)]))])
    agent, gemini_calls, _, _ = _gemini_agent(monkeypatch, engine, [gemini_batch])
    dispatched = []
    monkeypatch.setattr(agent.tb, "dispatch", lambda *a: (dispatched.append(a) or ("ok", [])))
    events = list(agent.chat([], "question"))
    assert len(gemini_calls) == 1 and dispatched == []
    assert "budget" in events[-1]["detail"]

    openrouter_batch = _OpenRouterResponse(payload={"choices": [{"message": {"role": "assistant", "tool_calls": [
        {"id": f"call-{i}", "function": {"name": "run_sql", "arguments": '{"query":"SELECT 1"}'}}
        for i in range(4)]}}]})
    agent, or_calls = _openrouter_agent(monkeypatch, engine, [openrouter_batch])
    dispatched = []
    monkeypatch.setattr(agent.tb, "dispatch", lambda *a: (dispatched.append(a) or ("ok", [])))
    events = list(agent.chat([], "question"))
    assert len(or_calls) == 1 and dispatched == []
    assert "budget" in events[-1]["detail"]


def test_demo_agent_unknown_malformed_context_empty_data_and_tool_failure(engine, tmp_path, monkeypatch):
    demo = DemoAgent(engine, "demo-reliability-test")
    unknown = list(demo.chat([], "can you do something unsupported"))
    assert any("offline demo mode" in e.get("text", "").lower() for e in unknown)

    history = [{"role": "assistant", "content": "", "demo_context": {
        "kind": "region_total", "table": "missing", "group_column": "region",
        "metric_column": "sales", "entity": "North"}}, "malformed-entry"]
    invalid_context = list(demo.chat(history, "How much did it sell?"))
    assert any("offline demo mode" in e.get("text", "").lower() for e in invalid_context)
    assert SECRET not in repr(invalid_context)
    wrong_column = [{"role": "assistant", "demo_context": {
        "kind": "region_total", "table": "sales", "group_column": "missing",
        "metric_column": "sales", "entity": "North"}}]
    recovered_context = list(demo.chat(wrong_column, "How much did it sell?"))
    assert any("offline demo mode" in e.get("text", "").lower() for e in recovered_context)

    empty_path = tmp_path / "empty.csv"
    empty_path.write_text("region,sales\n", encoding="utf-8")
    empty_engine = DataEngine()
    empty_engine.load_csv("empty", str(empty_path))
    empty_demo = DemoAgent(empty_engine, "demo-empty-test")
    empty_events = list(empty_demo.chat([], "Which region has the highest sales?"))
    assert any(e.get("type") == "error" for e in empty_events)

    monkeypatch.setattr(demo.tb, "run_sql", lambda *a, **k: (_ for _ in ()).throw(RuntimeError(SECRET)))
    failed = list(demo.chat([], "Which region has the highest sales?"))
    assert any(e.get("type") == "error" for e in failed)
    assert SECRET not in repr(failed)
    recovered = list(demo.chat([], "unknown question"))
    assert any("offline demo mode" in e.get("text", "").lower() for e in recovered)

    context = [{"role": "assistant", "demo_context": {
        "kind": "region_total", "table": "sales", "group_column": "region",
        "metric_column": "sales", "entity": "North"}}]
    monkeypatch.setattr(demo.tb, "run_sql", lambda *a, **k: {"ok": False, "error": f"{SECRET} C:\\db"})
    failed_followup = list(demo.chat(context, "How much did it sell?"))
    assert SECRET not in repr(failed_followup)
    assert "C:\\db" not in repr(failed_followup)


def test_chat_api_hides_unexpected_exception_and_session_stays_usable(engine):
    client = TestClient(app)
    session = _new_session(provider="demo")
    sid = session["id"]
    session["engine"] = engine
    class FlakyAgent:
        attempts = 0
        def chat(self, contents, message):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError(f"{SECRET} C:\\private\\engine")
            yield {"type": "token", "text": "Recovered"}
    session["agent"] = FlakyAgent()
    try:
        failed = client.post("/api/chat", json={"session_id": sid, "message": "question"})
        assert failed.status_code == 200
        assert SECRET not in failed.text and "C:\\private" not in failed.text
        assert "failed unexpectedly" in failed.text
        recovered = client.post("/api/chat", json={"session_id": sid, "message": "try again"})
        assert recovered.status_code == 200
        assert "Recovered" in recovered.text
    finally:
        sessions.pop(sid, None)


def test_provider_failure_sse_is_controlled_and_session_recovers(engine, monkeypatch):
    client = TestClient(app)
    session = _new_session(provider="demo")
    sid = session["id"]
    session["engine"] = engine
    provider_agent, _ = _openrouter_agent(monkeypatch, engine, [
        _OpenRouterResponse(401, {"error": {"message": f"{SECRET} C:\\private\\key"}})])
    session["agent"] = provider_agent
    try:
        response = client.post("/api/chat", json={"session_id": sid, "message": "question"})
        assert response.status_code == 200
        assert "authentication failed" in response.text.lower()
        assert SECRET not in response.text and "C:\\private" not in response.text
        assert "Traceback" not in response.text
        assert sid in sessions

        session["agent"] = DemoAgent(engine, sid)
        recovered = client.post("/api/chat", json={"session_id": sid, "message": "unsupported question"})
        assert recovered.status_code == 200
        assert "offline demo mode" in recovered.text.lower()
    finally:
        sessions.pop(sid, None)


def test_report_endpoint_hides_internal_failure(engine, monkeypatch):
    import app.main as main_module
    client = TestClient(app)
    session = _new_session(provider="demo")
    sid = session["id"]
    session["engine"] = engine
    monkeypatch.setattr(main_module.analytics, "generate_report",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError(f"{SECRET} C:\\private")))
    try:
        response = client.get(f"/api/report/{sid}/sales")
        assert response.status_code == 500
        assert SECRET not in response.text
        assert "C:\\private" not in response.text
        assert "Traceback" not in response.text
        assert "Report generation failed" in response.text

        monkeypatch.setattr(main_module.analytics, "forecast_metric",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError(f"{SECRET} C:\\private")))
        forecast = client.get(f"/api/forecast/{sid}/sales")
        assert forecast.status_code == 500
        assert SECRET not in forecast.text and "C:\\private" not in forecast.text
        assert "Traceback" not in forecast.text
        assert "Forecasting failed" in forecast.text
    finally:
        sessions.pop(sid, None)


def test_groq_tool_call_executes_through_existing_toolbox(engine, monkeypatch):
    tool_call = _OpenRouterResponse(payload={"choices": [{"message": {
        "role": "assistant", "tool_calls": [{"id": "groq-call-1", "type": "function",
            "function": {"name": "run_sql", "arguments":
                         '{"query":"SELECT SUM(sales) AS total_sales FROM sales"}'}}]}}]})
    agent, requests, urls = _groq_agent(monkeypatch, engine, [tool_call, _or_text("Total sales are 600.")])

    events = list(agent.chat([], "What are total sales?"))

    assert urls == [f"{GROQ_BASE_URL}/chat/completions"] * 2
    assert requests[0]["model"] == config.GROQ_MODEL
    assert {tool["function"]["name"] for tool in requests[0]["tools"]} == {
        "profile_schema", "run_sql", "detect_anomalies", "build_chart"}
    assert any(event["type"] == "sql" for event in events)
    assert any("600" in event.get("text", "") for event in events)
    tool_result = next(message for message in requests[1]["messages"] if message.get("role") == "tool")
    assert "600" in tool_result["content"]
    assert SECRET not in repr(events)


@pytest.mark.parametrize(("response", "message"), [
    (_OpenRouterResponse(401, {"error": {"message": SECRET}}), "Groq authentication failed"),
    (_OpenRouterResponse(429, {"error": {"message": SECRET}}), "Groq rate limit reached"),
    (_OpenRouterResponse(503, {"error": {"message": SECRET}}), "Groq request failed with HTTP 503"),
    (TimeoutError(SECRET), "Groq request timed out"),
])
def test_groq_provider_failures_are_controlled_and_do_not_fallback(engine, monkeypatch, response, message):
    agent, requests, _ = _groq_agent(monkeypatch, engine, [response])
    events = list(agent.chat([], "question"))
    assert any(e.get("type") == "error" and message in e.get("detail", "") for e in events)
    assert len(requests) == 1
    assert SECRET not in repr(events)


def test_groq_malformed_response_is_controlled_and_does_not_leak_key(engine, monkeypatch):
    agent, requests, _ = _groq_agent(monkeypatch, engine, [
        _OpenRouterResponse(payload=ValueError(SECRET))])
    events = list(agent.chat([], "question"))
    assert any(e.get("type") == "error" and "malformed JSON" in e.get("detail", "") for e in events)
    assert len(requests) == 1
    assert SECRET not in repr(events)


def test_groq_missing_key_and_explicit_selection_do_not_use_other_provider(engine, monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "configured-gemini-test-key")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "configured-openrouter-test-key")
    monkeypatch.setattr(config, "LLM_PROVIDER", "groq")

    assert config.mode() == "demo"
    with pytest.raises(RuntimeError, match="GROQ_API_KEY is not configured"):
        GroqAgent(engine, "groq-no-key")
    assert isinstance(_create_agent(engine, "groq-no-key", provider="groq"), DemoAgent)


def test_groq_provider_switch_uses_its_key_without_reusing_previous_provider_key(engine, monkeypatch):
    old_key = "openrouter-session-test-key"
    groq_key = "groq-configured-test-key"
    monkeypatch.setattr(config, "GROQ_API_KEY", groq_key)
    monkeypatch.setattr(config, "ALLOW_KEY_OVERRIDE", False)
    sess = _new_session(provider="openrouter", api_key=old_key)
    try:
        response = TestClient(app).post("/api/config/switch", json={
            "provider": "groq", "session_id": sess["id"]})
        assert response.status_code == 200
        assert response.json()["mode"] == "groq"
        assert response.json()["model"] == config.GROQ_MODEL
        assert response.json()["groq_configured"] is True
        assert isinstance(sess["agent"], GroqAgent)
        assert sess["agent"].api_key == groq_key
        assert sess["api_key"] is None
        assert old_key not in response.text
        assert groq_key not in response.text
    finally:
        _drop_session(sess["id"])


def test_chat_provider_change_does_not_pass_previous_session_key_to_groq(engine, monkeypatch):
    old_key = "previous-session-provider-test-key"
    groq_key = "groq-environment-test-key"
    monkeypatch.setattr(config, "GROQ_API_KEY", groq_key)
    sess = _new_session(provider="demo", api_key=old_key)
    sess["engine"] = engine
    sess["agent"] = DemoAgent(engine, sess["id"])
    monkeypatch.setattr(GroqAgent, "chat", lambda self, contents, message: iter([
        {"type": "token", "text": "mock answer"}, {"type": "done"}]))
    try:
        response = TestClient(app).post("/api/chat", json={
            "session_id": sess["id"], "message": "question", "provider": "groq"})
        assert response.status_code == 200
        assert isinstance(sess["agent"], GroqAgent)
        assert sess["agent"].api_key == groq_key
        assert sess["api_key"] is None
        assert old_key not in response.text and groq_key not in response.text
        assert "mock answer" in response.text
    finally:
        _drop_session(sess["id"])
