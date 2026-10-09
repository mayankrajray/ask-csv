import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import config
from app.agent import GeminiAgent
from app.main import app, sessions
from app.openrouter_agent import OpenRouterAgent


def _events(response):
    return [json.loads(line[6:]) for line in response.text.split("\n\n")
            if line.startswith("data: ")]


def _upload(client, path):
    return client.post("/api/upload", files=[("files", ("sales.csv", path.read_bytes(), "text/csv"))]).json()["session_id"]


def _mock_engine():
    return SimpleNamespace(profile=lambda *args: [{
        "table": "sales", "rows": 2,
        "columns": [{"name": "region", "type": "VARCHAR"}, {"name": "sales", "type": "INTEGER"}],
    }])


def test_demo_agent_resolves_followup_and_new_chat_resets_and_bounds(tmp_path, monkeypatch):
    import app.demo_agent as demo_module

    monkeypatch.setattr(demo_module, "stream_tokens", lambda text: iter([{"type": "token", "text": text}]))
    client = TestClient(app)
    path = tmp_path / "sales.csv"
    path.write_text("region,sales\nNorth,400000\nSouth,200000\n", encoding="utf-8")
    sid = _upload(client, path)
    try:
        first = _events(client.post("/api/chat", json={"session_id": sid, "provider": "demo",
                                                          "message": "Which region has the highest sales?"}))
        assert any("North" in e.get("text", "") for e in first)
        second = _events(client.post("/api/chat", json={"session_id": sid, "message": "How much did it sell?"}))
        assert any("North" in e.get("text", "") and "4.0 lakh" in e.get("text", "") for e in second)
        query = next(e["sql"] for e in second if e["type"] == "sql")
        assert '"region" = \'North\'' in query

        assert client.post("/api/chat/new", json={"session_id": sid}).json()["ok"]
        reset = _events(client.post("/api/chat", json={"session_id": sid, "message": "How much did it sell?"}))
        assert not any(e.get("type") == "sql" for e in reset)
        assert len(sessions[sid]["contents"]) == 2
        for i in range(15):
            list(sessions[sid]["agent"].chat(sessions[sid]["contents"], f"unknown question {i}"))
        assert sum(item["role"] == "user" for item in sessions[sid]["contents"]) == 12
        assert len(sessions[sid]["contents"]) == 24
    finally:
        sessions.pop(sid, None)


def test_demo_context_is_session_isolated(tmp_path):
    client = TestClient(app)
    path = tmp_path / "sales.csv"
    path.write_text("region,sales\nNorth,400000\nSouth,200000\n", encoding="utf-8")
    first_sid, second_sid = _upload(client, path), _upload(client, path)
    try:
        client.post("/api/chat", json={"session_id": first_sid, "provider": "demo",
                                       "message": "Which region has the highest sales?"})
        response = _events(client.post("/api/chat", json={"session_id": second_sid, "provider": "demo",
                                                              "message": "How much did it sell?"}))
        assert not any(e.get("type") == "sql" for e in response)
        assert not any("North" in e.get("text", "") for e in response)
    finally:
        sessions.pop(first_sid, None)
        sessions.pop(second_sid, None)


def test_history_helpers_bound_turns_and_preserve_system_and_tool_responses(monkeypatch):
    from app.conversation import trim_gemini_contents, trim_messages

    msgs = [{"role": "system", "content": "rules"}]
    for i in range(5):
        msgs.extend([{"role": "user", "content": str(i)}, {"role": "assistant", "content": "ok"}])
    trim_messages(msgs, 3)
    assert msgs[0]["role"] == "system"
    assert [m["content"] for m in msgs if m["role"] == "user"] == ["2", "3", "4"]

    class Part:
        def __init__(self, text=None, function_response=None):
            self.text, self.function_response = text, function_response

    class Content:
        def __init__(self, role, parts):
            self.role, self.parts = role, parts

    contents = []
    for i in range(5):
        contents.extend([Content("user", [Part(text=str(i))]),
                         Content("model", [Part(text="answer")]),
                         Content("user", [Part(function_response={"result": "tool"})])])
    trim_gemini_contents(contents, 3)
    assert sum(any(p.text for p in c.parts) and c.role == "user" for c in contents) == 3
    assert len(contents) == 9


def test_gemini_mock_preserves_recent_turns_and_system_schema(monkeypatch):
    import app.agent as module

    requests = []

    class Part:
        def __init__(self, text=None, function_call=None, function_response=None):
            self.text, self.function_call, self.function_response = text, function_call, function_response

    class Content:
        def __init__(self, role, parts):
            self.role, self.parts = role, parts

    class Types:
        class GenerateContentConfig:
            def __init__(self, **kwargs): self.kwargs = kwargs
        class Tool:
            def __init__(self, **kwargs): pass

    Types.Part = Part
    Types.Content = Content

    class Models:
        def generate_content(self, **kwargs):
            requests.append(kwargs)
            return SimpleNamespace(candidates=[SimpleNamespace(content=Content("model", [Part(text="mock answer")]))])

    class GenAI:
        class Client:
            def __init__(self, api_key):
                assert api_key == "private-test-key"
                self.models = Models()

    monkeypatch.setattr(module, "types", Types)
    monkeypatch.setattr(module, "genai", GenAI)
    monkeypatch.setattr(module, "_declarations", lambda: [])
    monkeypatch.setattr(config, "MAX_CONVERSATION_TURNS", 3)
    agent = GeminiAgent(_mock_engine(), "sid", api_key="private-test-key")
    history = []
    for i in range(5):
        events = list(agent.chat(history, f"question {i}"))
        assert "private-test-key" not in repr(events)
    sent = requests[-1]["contents"]
    assert [c.parts[0].text for c in sent if c.role == "user"] == ["question 2", "question 3", "question 4"]
    assert "private-test-key" not in repr(sent)
    assert "question 0" not in repr(sent)
    system = requests[-1]["config"].kwargs["system_instruction"]
    assert "sales" in system
    assert "North" not in system


def test_openrouter_mock_preserves_recent_turns_and_system(monkeypatch):
    import app.openrouter_agent as module

    payloads = []

    class Response:
        status_code = 200
        text = ""
        def json(self): return {"choices": [{"message": {"role": "assistant", "content": "mock answer"}}]}

    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, headers, json):
            assert headers["Authorization"] == "Bearer private-test-key"
            payloads.append(json)
            return Response()

    monkeypatch.setattr(module.httpx, "Client", Client)
    monkeypatch.setattr(config, "MAX_CONVERSATION_TURNS", 3)
    agent = OpenRouterAgent(_mock_engine(), "sid", api_key="private-test-key")
    history = []
    for i in range(5):
        events = list(agent.chat(history, f"question {i}"))
        assert "private-test-key" not in repr(events)
    sent = payloads[-1]["messages"]
    assert sent[0]["role"] == "system"
    assert [m["content"] for m in sent if m["role"] == "user"] == ["question 2", "question 3", "question 4"]
    assert "private-test-key" not in repr(sent)
    assert "North" not in sent[0]["content"]
