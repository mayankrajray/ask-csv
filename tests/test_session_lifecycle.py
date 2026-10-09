import time

import pytest
from fastapi.testclient import TestClient

from app import config
from app.demo_agent import DemoAgent
from app.engine import DataEngine
from app.main import _drop_session, _new_session, _session_for, app, sessions


@pytest.fixture
def lifecycle_env(tmp_path, monkeypatch):
    upload_root = tmp_path / "uploads"
    export_root = tmp_path / "exports"
    upload_root.mkdir()
    export_root.mkdir()
    monkeypatch.setattr(config, "UPLOAD_DIR", upload_root)
    monkeypatch.setattr(config, "EXPORT_DIR", export_root)
    monkeypatch.setattr(config, "SESSION_STORAGE_RETENTION_HOURS", 24)
    client = TestClient(app)
    owned = []
    yield client, owned, upload_root, export_root
    for sid in owned:
        _drop_session(sid)


def _make_owned_file(root, sid, name="source.csv"):
    path = root / f"{sid}_{name}" if root.name == "uploads" else root / sid / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("a\n1\n", encoding="utf-8")
    return path


def _assert_closed(engine):
    with pytest.raises(Exception):
        engine.con.execute("SELECT 1")


def test_failed_agent_creation_closes_database_and_never_publishes_session(
        lifecycle_env, monkeypatch):
    import app.main as main_module

    _, _, _, _ = lifecycle_env
    created_engines = []

    def create_engine():
        engine = DataEngine()
        created_engines.append(engine)
        return engine

    before = set(sessions)
    monkeypatch.setattr(main_module, "DataEngine", create_engine)
    monkeypatch.setattr(main_module, "_create_agent",
                        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("init failed")))
    with pytest.raises(RuntimeError, match="init failed"):
        _new_session(provider="demo", api_key="temporary-secret")

    assert set(sessions) == before
    assert len(created_engines) == 1
    _assert_closed(created_engines[0])


def test_failed_sample_creation_removes_session_files_and_database(lifecycle_env, monkeypatch):
    import app.main as main_module

    client, owned, upload_root, export_root = lifecycle_env
    created_engines = []
    original_engine = main_module.DataEngine

    def create_engine():
        engine = original_engine()
        created_engines.append(engine)
        return engine

    monkeypatch.setattr(main_module, "DataEngine", create_engine)
    monkeypatch.setattr(original_engine, "load_csv",
                        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("parse failure")))
    before = set(sessions)
    response = client.post("/api/sample")

    assert response.status_code == 500
    assert "parse failure" not in response.text
    assert set(sessions) == before
    assert not list(upload_root.iterdir())
    assert not list(export_root.iterdir())
    _assert_closed(created_engines[0])


def test_failed_upload_ingestion_drops_provisional_session_and_partial_file(lifecycle_env, monkeypatch):
    import app.main as main_module

    client, _, upload_root, export_root = lifecycle_env
    created_engines = []
    original_engine = main_module.DataEngine

    def create_engine():
        engine = original_engine()
        created_engines.append(engine)
        return engine

    monkeypatch.setattr(main_module, "DataEngine", create_engine)
    monkeypatch.setattr(original_engine, "load_file",
                        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("parse failed")))
    before = set(sessions)
    response = client.post("/api/upload", files=[("files", ("broken.csv", b"a\n1\n", "text/csv"))])

    assert response.status_code == 400
    assert "parse failed" not in response.text
    assert set(sessions) == before
    assert not list(upload_root.iterdir())
    assert not list(export_root.iterdir())
    _assert_closed(created_engines[0])


def test_expiration_releases_engine_files_history_and_credentials(lifecycle_env):
    _, owned, upload_root, export_root = lifecycle_env
    sess = _new_session(provider="demo", model="offline-demo", api_key="temporary-secret")
    sid = sess["id"]
    owned.append(sid)
    engine = sess["engine"]
    upload = _make_owned_file(upload_root, sid)
    export = _make_owned_file(export_root, sid, "result.csv")
    sess["contents"].extend([{"role": "user", "content": "private question"}])
    sess["query_logs"].append({"sql": "SELECT 1"})
    sess["last_seen"] = time.time() - 25 * 3600

    assert _session_for(sid) is None
    assert sid not in sessions
    assert sess["api_key"] is None
    assert sess["provider"] is None and sess["model"] is None
    assert sess["contents"] == [] and sess["query_logs"] == []
    assert sess["agent"] is None and sess["engine"] is None
    assert not upload.exists() and not export.exists()
    _assert_closed(engine)


def test_eviction_removes_oldest_session_and_enforces_capacity(lifecycle_env, monkeypatch):
    _, owned, upload_root, export_root = lifecycle_env
    original_limit = len(sessions) + 2
    monkeypatch.setattr(config, "SESSION_LIMIT", original_limit)
    old = _new_session(provider="demo", api_key="old-session-secret")
    other = _new_session(provider="demo", api_key="other-session-secret")
    owned.extend([old["id"], other["id"]])
    old_engine = old["engine"]
    old_contents = old["contents"]
    upload = _make_owned_file(upload_root, old["id"])
    export = _make_owned_file(export_root, old["id"], "result.csv")
    old_contents.append({"role": "user", "content": "old history"})
    old["created"] = 0

    monkeypatch.setattr(config, "SESSION_LIMIT", len(sessions))
    newest = _new_session(provider="demo")
    owned.append(newest["id"])

    assert old["id"] not in sessions
    assert other["id"] in sessions
    assert newest["id"] in sessions
    assert len(sessions) <= config.SESSION_LIMIT
    assert old["api_key"] is None and old["contents"] == [] and old["agent"] is None
    assert not upload.exists() and not export.exists()
    _assert_closed(old_engine)


def test_chat_new_clears_context_but_keeps_dataset_and_session_config(lifecycle_env):
    client, owned, upload_root, export_root = lifecycle_env
    response = client.post("/api/upload", files=[("files", ("sales.csv",
        b"region,sales\nNorth,400000\nSouth,200000\n", "text/csv"))])
    assert response.status_code == 200
    sid = response.json()["session_id"]
    owned.append(sid)
    sess = sessions[sid]
    sess["agent"] = DemoAgent(sess["engine"], sid)
    sess["provider"] = "demo"
    sess["model"] = "offline-demo"
    sess["api_key"] = "session-scoped-test-key"
    sess["contents"].append({"role": "assistant", "content": "prior context",
                              "demo_context": {"kind": "region_total"}})
    sess["agent"].last_sql = "SELECT prior_context"
    agent = sess["agent"]
    upload = next(upload_root.glob(f"{sid}_*"))

    reset = client.post("/api/chat/new", json={"session_id": sid})
    assert reset.status_code == 200 and reset.json()["ok"] is True
    assert sess["contents"] == []
    assert sess["agent"] is agent and agent.last_sql is None
    assert sess["engine"].tables == ["sales"]
    assert sess["engine"].execute("SELECT COUNT(*) AS n FROM sales").iloc[0, 0] == 2
    assert sess["provider"] == "demo" and sess["model"] == "offline-demo"
    assert sess["api_key"] == "session-scoped-test-key"
    assert upload.exists()
    assert client.get(f"/api/preview/{sid}/sales").status_code == 200
    assert not list(export_root.iterdir())


def test_provider_config_switch_failure_preserves_existing_session_state(lifecycle_env, monkeypatch):
    import app.main as main_module

    client, owned, _, _ = lifecycle_env
    sess = _new_session(provider="demo", model="offline-demo", api_key="do-not-return")
    sid = sess["id"]
    owned.append(sid)
    history = [{"role": "user", "content": "keep this"}]
    sess["contents"].extend(history)
    previous_agent = sess["agent"]
    monkeypatch.setattr(config, "GEMINI_API_KEY", "mock-configured-key")
    monkeypatch.setattr(main_module, "_create_agent",
                        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("secret failure")))

    response = client.post("/api/config/switch", json={
        "provider": "gemini", "model": "new-model", "session_id": sid})

    assert response.status_code == 503
    assert "secret failure" not in response.text and "do-not-return" not in response.text
    assert sess["provider"] == "demo" and sess["model"] == "offline-demo"
    assert sess["api_key"] == "do-not-return"
    assert sess["contents"] == history and sess["agent"] is previous_agent


def test_repeated_create_drop_cycles_do_not_retain_sessions_or_engines(lifecycle_env):
    _, _, upload_root, export_root = lifecycle_env
    engines = []
    for index in range(8):
        sess = _new_session(provider="demo", api_key=f"key-{index}")
        engines.append(sess["engine"])
        sid = sess["id"]
        sess["contents"].append({"role": "user", "content": f"turn-{index}"})
        _make_owned_file(upload_root, sid)
        _make_owned_file(export_root, sid, "result.csv")
        _drop_session(sid)
        assert sid not in sessions
        assert not list(upload_root.iterdir())
        assert not list(export_root.iterdir())

    for engine in engines:
        _assert_closed(engine)
