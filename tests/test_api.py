import json

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app

client = TestClient(app)
SAMPLE = "data/sales.csv"

demo_only = pytest.mark.skipif(
    config.mode() != "demo",
    reason="these assertions assume the offline demo agent",
)


def _events(text):
    return [json.loads(l[6:]) for l in text.split("\n\n") if l.startswith("data: ")]


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["mode"] in ("demo", "gemini", "openrouter")


def test_upload_rejects_non_csv():
    r = client.post("/api/upload", files=[("files", ("x.txt", b"hello", "text/plain"))])
    assert r.status_code == 400


def test_upload_and_quality(tmp_path):
    with open(SAMPLE, "rb") as f:
        r = client.post("/api/upload", files=[("files", ("sales.csv", f.read(), "text/csv"))])
    assert r.status_code == 200
    body = r.json()
    table = body["tables"][0]
    assert table["table"] == "sales"
    assert table["rows"] > 480
    assert table["quality"]["duplicates"] == 1     # planted
    assert table["quality"]["null_cells"] >= 1      # planted


@demo_only
def test_chat_trend_streams_sql_and_chart():
    with open(SAMPLE, "rb") as f:
        sid = client.post("/api/upload",
                          files=[("files", ("sales.csv", f.read(), "text/csv"))]).json()["session_id"]
    r = client.post("/api/chat", json={"session_id": sid, "message": "Show monthly sales trends"})
    assert r.status_code == 200
    evs = _events(r.text)
    types = [e["type"] for e in evs]
    assert "sql" in types
    assert "chart" in types
    assert "token" in types
    assert types[-1] == "done"
    chart = next(e for e in evs if e["type"] == "chart")
    assert chart["spec"]["data"][0]["type"] in ("scatter", "line")


@demo_only
def test_chat_anomalies():
    with open(SAMPLE, "rb") as f:
        sid = client.post("/api/upload",
                          files=[("files", ("sales.csv", f.read(), "text/csv"))]).json()["session_id"]
    r = client.post("/api/chat", json={"session_id": sid, "message": "Detect anomalies in the dataset"})
    evs = _events(r.text)
    anom = next(e for e in evs if e["type"] == "anomaly")
    rev = next(c for c in anom["columns"] if c["column"] == "revenue")
    assert rev["count"] >= 7
    assert rev["lower_bound"] is not None


def test_chat_unknown_session():
    r = client.post("/api/chat", json={"session_id": "nope", "message": "hi"})
    assert r.status_code == 404


def test_switch_requires_key_for_remote_providers(monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    r = client.post("/api/config/switch", json={"provider": "openrouter"})
    assert r.status_code == 400


def test_unknown_table_is_404():
    with open(SAMPLE, "rb") as f:
        sid = client.post("/api/upload",
                          files=[("files", ("sales.csv", f.read(), "text/csv"))]).json()["session_id"]
    for route in ("schema", "preview", "dashboard", "quality", "forecast", "report"):
        r = client.get(f"/api/{route}/{sid}/nope")
        assert r.status_code == 404, route
    r = client.get(f'/api/quality/{sid}/sales" OR 1=1 --')
    assert r.status_code == 404


def test_key_override_can_be_disabled(monkeypatch):
    monkeypatch.setattr(config, "ALLOW_KEY_OVERRIDE", False)
    r = client.post("/api/config/switch", json={"provider": "openrouter", "api_key": "sk-x"})
    assert r.status_code == 403
