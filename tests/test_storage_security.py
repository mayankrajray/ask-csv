import os
import time
from io import StringIO
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import _drop_session, _new_session, app, sessions


@pytest.fixture
def storage_client(tmp_path, monkeypatch):
    upload_root = tmp_path / "uploads"
    export_root = tmp_path / "exports"
    upload_root.mkdir()
    export_root.mkdir()
    monkeypatch.setattr(config, "UPLOAD_DIR", upload_root)
    monkeypatch.setattr(config, "EXPORT_DIR", export_root)
    monkeypatch.setattr(config, "MAX_ROWS_TO_LLM", 1)
    client = TestClient(app)
    ids = []
    yield client, ids, upload_root, export_root
    for sid in ids:
        _drop_session(sid)


def _upload(client, name="sales.csv", data=b"region,sales\nNorth,100\nSouth,200\n"):
    response = client.post("/api/upload", files=[("files", (name, data, "text/csv"))])
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


def _create_export(sid, query="SELECT * FROM sales"):
    result = sessions[sid]["agent"].tb.run_sql(query)
    assert result["ok"] is True and "export" in result
    return result


def test_export_download_requires_live_owning_session_and_safe_filename(storage_client):
    client, ids, _, _ = storage_client
    sid_a = _upload(client)
    ids.append(sid_a)
    sid_b = _upload(client, "other.csv", b"id,value\n1,2\n2,3\n")
    ids.append(sid_b)
    export = _create_export(sid_a)

    own = client.get(export["export"])
    assert own.status_code == 200
    assert own.headers["content-type"].startswith("text/csv")
    assert b"North" in own.content
    assert client.get(export["export"].replace(sid_a, sid_b)).status_code == 404
    assert client.get(export["export"].replace(sid_a, "0" * 32)).status_code == 404

    root = export["export"].rsplit("/", 1)[0]
    for filename in ("../secret.csv", "../../secret.csv", r"..\secret.csv",
                     "nested%2F..%2Fsecret.csv", "%2e%2e%2fsecret.csv",
                     str(Path("C:/private/secret.csv"))):
        response = client.get(f"{root}/{filename}")
        assert response.status_code == 404
        assert "Traceback" not in response.text

    assert not any(getattr(route, "path", "") == "/api/exports" for route in app.routes)
    _drop_session(sid_a)
    ids.remove(sid_a)
    assert client.get(export["export"]).status_code == 404


def test_export_missing_file_invalid_session_and_repeated_exports_are_bounded(
        storage_client, monkeypatch):
    client, ids, _, export_root = storage_client
    sid = _upload(client)
    ids.append(sid)
    monkeypatch.setattr(config, "MAX_EXPORTS_PER_SESSION", 2)
    monkeypatch.setattr(config, "MAX_SESSION_EXPORT_MB", 10)

    first = _create_export(sid, "SELECT * FROM sales ORDER BY sales")
    second = _create_export(sid, "SELECT * FROM sales ORDER BY region")
    assert first["export"] != second["export"]
    assert len(list((export_root / sid).glob("*.csv"))) == 2
    assert "export" not in sessions[sid]["agent"].tb.run_sql("SELECT * FROM sales")

    missing_url = first["export"].replace(first["export"].rsplit("/", 1)[1], "f" * 32 + ".csv")
    assert client.get(missing_url).status_code == 404
    assert client.get(first["export"].replace(sid, "missing-session")).status_code == 404

    monkeypatch.setattr(config, "MAX_EXPORTS_PER_SESSION", 3)
    monkeypatch.setattr(config, "MAX_SESSION_EXPORT_MB", 0)
    no_space = sessions[sid]["agent"].tb.run_sql("SELECT * FROM sales")
    assert no_space["ok"] is False and "size limit" in no_space["error"]


def test_empty_and_invalid_queries_do_not_create_export_files(storage_client):
    client, ids, _, export_root = storage_client
    sid = _upload(client)
    ids.append(sid)
    tb = sessions[sid]["agent"].tb
    empty = tb.run_sql("SELECT * FROM sales WHERE 1=0")
    invalid = tb.run_sql("SELECT no_such_column FROM sales")
    assert empty["ok"] is True and empty["row_count"] == 0
    assert "export" not in empty
    assert invalid["ok"] is False
    assert not list((export_root / sid).glob("*.csv"))


def test_csv_exports_neutralize_formula_like_text_but_preserve_numeric_values(storage_client):
    client, ids, _, _ = storage_client
    payload = (b"text_value,numeric_value\n"
               b"=1+1,-2\n"
               b"+1+1,0\n"
               b"-1+1,3\n"
               b'"@SUM(1,1)",4\n'
               b'" =1+1",5\n')
    sid = _upload(client, "formula_data.csv", payload)
    ids.append(sid)
    exported = _create_export(sid, "SELECT text_value, numeric_value FROM formula_data")
    content = (sessions[sid]["agent"].tb.export_dir /
               exported["export"].rsplit("/", 1)[1]).read_text(encoding="utf-8")
    parsed = pd.read_csv(StringIO(content))

    assert parsed["text_value"].tolist() == ["'=1+1", "'+1+1", "'-1+1", "'@SUM(1,1)", "' =1+1"]
    assert parsed["numeric_value"].tolist() == [-2, 0, 3, 4, 5]

    header_export = _create_export(sid, 'SELECT text_value AS "=unsafe_header" FROM formula_data')
    header_content = (sessions[sid]["agent"].tb.export_dir /
                      header_export["export"].rsplit("/", 1)[1]).read_text(encoding="utf-8")
    assert pd.read_csv(StringIO(header_content)).columns.tolist() == ["'=unsafe_header"]


def test_unsafe_upload_names_stay_in_storage_and_failed_ingestion_cleans_session(storage_client):
    client, ids, upload_root, export_root = storage_client
    for name in ("../outside.csv", r"..\..\outside2.csv", "nested/../../outside3.csv",
                 "C:\\private\\outside4.csv", f"{'x' * 260}.csv"):
        sid = _upload(client, name)
        ids.append(sid)
        assert len(sessions[sid]["engine"].tables[0]) <= 64
        assert all(path.resolve().is_relative_to(upload_root.resolve())
                   for path in upload_root.iterdir())
        assert len(list(upload_root.iterdir())) == 1
        _drop_session(sid)
        ids.remove(sid)

    before = set(sessions)
    failed = client.post("/api/upload", files=[("files", ("bad.xlsx", b"not an excel file",
                                                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))])
    assert failed.status_code == 400
    assert "Traceback" not in failed.text
    assert set(sessions) == before
    assert not list(upload_root.iterdir())
    assert not list(export_root.iterdir())


def test_evicted_session_cleans_upload_and_export_files(tmp_path, monkeypatch):
    for sid in list(sessions):
        _drop_session(sid)
    upload_root = tmp_path / "uploads"
    export_root = tmp_path / "exports"
    upload_root.mkdir()
    export_root.mkdir()
    monkeypatch.setattr(config, "UPLOAD_DIR", upload_root)
    monkeypatch.setattr(config, "EXPORT_DIR", export_root)
    monkeypatch.setattr(config, "SESSION_LIMIT", 1)

    old = _new_session(provider="demo")
    old_id = old["id"]
    upload = upload_root / f"{old_id}_data.csv"
    upload.write_text("a\n1\n", encoding="utf-8")
    export_dir = export_root / old_id
    export_dir.mkdir(exist_ok=True)
    (export_dir / "result.csv").write_text("a\n1\n", encoding="utf-8")

    new = _new_session(provider="demo")
    try:
        assert old_id not in sessions
        assert not upload.exists()
        assert not export_dir.exists()
        assert new["id"] in sessions
    finally:
        _drop_session(new["id"])


def test_startup_cleanup_removes_only_expired_orphaned_session_files(tmp_path, monkeypatch):
    upload_root = tmp_path / "uploads"
    export_root = tmp_path / "exports"
    upload_root.mkdir()
    export_root.mkdir()
    monkeypatch.setattr(config, "UPLOAD_DIR", upload_root)
    monkeypatch.setattr(config, "EXPORT_DIR", export_root)
    monkeypatch.setattr(config, "SESSION_STORAGE_RETENTION_HOURS", 24)

    old_id = "a" * 32
    recent_id = "b" * 32
    active = _new_session(provider="demo")
    active_id = active["id"]
    old_upload = upload_root / f"{old_id}_input.csv"
    old_upload.write_text("a\n1\n", encoding="utf-8")
    recent_upload = upload_root / f"{recent_id}_input.csv"
    recent_upload.write_text("a\n2\n", encoding="utf-8")
    active_upload = upload_root / f"{active_id}_input.csv"
    active_upload.write_text("a\n3\n", encoding="utf-8")
    now = time.time()
    stale_time = now - 25 * 3600
    os.utime(old_upload, (stale_time, stale_time))

    try:
        with TestClient(app):
            assert not old_upload.exists()
            assert recent_upload.exists()
            assert active_upload.exists()
        assert not old_upload.exists()
        assert recent_upload.exists()
        assert active_upload.exists()
    finally:
        _drop_session(active_id)


def test_idle_session_expiration_removes_owned_files(tmp_path, monkeypatch):
    upload_root = tmp_path / "uploads"
    export_root = tmp_path / "exports"
    upload_root.mkdir()
    export_root.mkdir()
    monkeypatch.setattr(config, "UPLOAD_DIR", upload_root)
    monkeypatch.setattr(config, "EXPORT_DIR", export_root)
    monkeypatch.setattr(config, "SESSION_STORAGE_RETENTION_HOURS", 24)
    sess = _new_session(provider="demo")
    sid = sess["id"]
    upload = upload_root / f"{sid}_data.csv"
    upload.write_text("a\n1\n", encoding="utf-8")
    export = export_root / sid / "result.csv"
    export.parent.mkdir(exist_ok=True)
    export.write_text("a\n1\n", encoding="utf-8")
    sess["last_seen"] = time.time() - 25 * 3600

    response = TestClient(app).get(f"/api/exports/{sid}/{'f' * 32}.csv")
    assert response.status_code == 404
    assert sid not in sessions
    assert not upload.exists()
    assert not export.parent.exists()


def test_upload_file_count_and_total_size_limits_are_enforced(storage_client, monkeypatch):
    client, ids, upload_root, _ = storage_client
    monkeypatch.setattr(config, "MAX_FILES_PER_SESSION", 1)
    too_many = client.post("/api/upload", files=[
        ("files", ("one.csv", b"a\n1\n", "text/csv")),
        ("files", ("two.csv", b"a\n2\n", "text/csv")),
    ])
    assert too_many.status_code == 413
    assert not list(upload_root.iterdir())

    monkeypatch.setattr(config, "MAX_FILES_PER_SESSION", 3)
    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 1)
    monkeypatch.setattr(config, "MAX_SESSION_UPLOAD_MB", 0.00001)
    oversized = client.post("/api/upload", files=[("files", ("large.csv", b"a\n" + b"x" * 200,
                                                                    "text/csv"))])
    assert oversized.status_code == 413
    assert not list(upload_root.iterdir())
    assert not ids
