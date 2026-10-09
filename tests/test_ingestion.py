from io import BytesIO

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import config
from app.engine import DataEngine
from app.main import app, sessions


@pytest.fixture
def engine():
    return DataEngine()


@pytest.fixture
def client_uploads(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr(config, "UPLOAD_DIR", upload_dir)
    client = TestClient(app)
    created_sessions = []
    yield client, created_sessions
    for sid in created_sessions:
        sessions.pop(sid, None)


def _post_files(client, files):
    return client.post("/api/upload", files=[("files", (name, body, content_type))
                                               for name, body, content_type in files])


def test_csv_types_values_dates_and_empty_values(tmp_path, engine):
    path = tmp_path / "mixed.csv"
    path.write_text("customer_id,name,amount,ordered_on,note\n"
                    "1,Ada,12.5,2025-01-02,\n2,Linus,,2025-01-03,hello\n", encoding="utf-8")
    engine.load_csv("mixed", str(path))
    schema = {col["name"]: col["type"] for col in engine.schema("mixed")[0]["columns"]}
    assert schema["customer_id"].startswith("BIGINT")
    assert schema["amount"].startswith(("DOUBLE", "DECIMAL"))
    assert schema["name"].startswith("VARCHAR")
    rows = engine.execute('SELECT customer_id, name, amount, TRY_CAST(ordered_on AS DATE), note '
                          'FROM "mixed" ORDER BY customer_id').values.tolist()
    assert rows[0][:4] == [1, "Ada", 12.5, pd.Timestamp("2025-01-02")]
    assert pd.isna(rows[0][4])
    assert rows[1][0:2] == [2, "Linus"]
    assert pd.isna(rows[1][2])
    assert rows[1][3:] == [pd.Timestamp("2025-01-03"), "hello"]


def test_csv_schema_cleans_unusual_and_duplicate_headers(tmp_path, engine):
    path = tmp_path / "headers.csv"
    path.write_text('"Customer ID","Customer ID","Full Name!","amount ($)"\n'
                    '1,2,Ada,3.5\n', encoding="utf-8")
    engine.load_csv("headers", str(path))
    columns = [col["name"] for col in engine.schema("headers")[0]["columns"]]
    assert columns == ["Customer ID", "Customer ID_1", "Full Name", "amount"]
    assert engine.execute('SELECT "Customer ID_1", amount FROM headers').values.tolist() == [[2, 3.5]]


def test_empty_csv_fails_cleanly(tmp_path, engine):
    path = tmp_path / "empty.csv"
    path.write_bytes(b"")
    with pytest.raises(Exception):
        engine.load_csv("empty", str(path))
    assert engine.tables == []


def test_excel_multiple_sheets_load_as_separate_tables(tmp_path, engine):
    path = tmp_path / "workbook.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame({"customer_id": [1, 2], "name": ["Ada", "Linus"]}).to_excel(
            writer, sheet_name="Customers", index=False)
        pd.DataFrame({"customer_id": [1, 2], "amount": [10.0, 20.0]}).to_excel(
            writer, sheet_name="Sales 2025", index=False)
    assert engine.load_excel("book", str(path)) == ["book_customers", "book_sales_2025"]
    assert engine.execute('SELECT COUNT(*) AS n FROM book_customers').iloc[0, 0] == 2
    assert engine.execute('SELECT SUM(amount) AS total FROM book_sales_2025').iloc[0, 0] == 30


def test_excel_single_sheet_uses_file_table_name(tmp_path, engine):
    path = tmp_path / "single.xlsx"
    pd.DataFrame({"name": ["Ada"]}).to_excel(path, index=False)
    assert engine.load_file("single", str(path)) == ["single"]
    assert engine.tables == ["single"]


def test_malformed_excel_fails_without_registering_tables(tmp_path, engine):
    path = tmp_path / "bad.xlsx"
    path.write_bytes(b"not an xlsx workbook")
    with pytest.raises(Exception):
        engine.load_excel("bad", str(path))
    assert engine.tables == []


def test_excel_sheet_name_collision_and_empty_workbook_fail_without_partial_tables(tmp_path, engine):
    collision = tmp_path / "collision.xlsx"
    with pd.ExcelWriter(collision, engine="openpyxl") as writer:
        pd.DataFrame({"value": [1]}).to_excel(writer, sheet_name="North-America", index=False)
        pd.DataFrame({"value": [2]}).to_excel(writer, sheet_name="North America", index=False)
    with pytest.raises(ValueError, match="already loaded"):
        engine.load_excel("book", str(collision))
    assert engine.tables == []

    empty = tmp_path / "empty.xlsx"
    with pd.ExcelWriter(empty, engine="openpyxl") as writer:
        pd.DataFrame().to_excel(writer, sheet_name="Empty", index=False)
    with pytest.raises(ValueError, match="no data sheets"):
        engine.load_excel("empty", str(empty))
    assert engine.tables == []


def test_csv_and_xlsx_upload_together_share_one_session(client_uploads, tmp_path):
    client, created = client_uploads
    customers = tmp_path / "customers.csv"
    customers.write_text("customer_id,name,region\n1,Ada,North\n2,Linus,South\n", encoding="utf-8")
    sales = BytesIO()
    pd.DataFrame({"customer_id": [1, 2], "amount": [10, 20]}).to_excel(sales, index=False)
    response = _post_files(client, [
        ("customers.csv", customers.read_bytes(), "text/csv"),
        ("sales.xlsx", sales.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ])
    assert response.status_code == 200, response.text
    body = response.json()
    sid = body["session_id"]
    created.append(sid)
    assert {table["table"] for table in body["tables"]} == {"customers", "sales"}
    sess = sessions[sid]
    assert sess["engine"].execute(
        'SELECT c.name, SUM(s.amount) AS total FROM customers c JOIN sales s '
        'USING (customer_id) GROUP BY c.name ORDER BY c.name').values.tolist() == [["Ada", 10], ["Linus", 20]]
    assert sess["engine"].schema("customers")[0]["columns"][1]["name"] == "name"


def test_join_empty_result_and_missing_or_incompatible_keys(engine, tmp_path):
    customers = tmp_path / "customers.csv"
    sales = tmp_path / "sales.csv"
    customers.write_text("customer_id,name\n1,Ada\n", encoding="utf-8")
    sales.write_text("customer_id,amount\n2,12\n", encoding="utf-8")
    engine.load_csv("customers", str(customers))
    engine.load_csv("sales", str(sales))
    joined = engine.execute("SELECT c.name, s.amount FROM customers c JOIN sales s USING (customer_id)")
    assert joined.empty
    with pytest.raises(Exception):
        engine.execute("SELECT * FROM customers c JOIN sales s ON c.missing_id = s.customer_id")

    incompatible = tmp_path / "other.csv"
    incompatible.write_text("customer_id,amount\nnot-a-number,5\n", encoding="utf-8")
    engine.load_csv("other", str(incompatible))
    with pytest.raises(Exception):
        engine.execute("SELECT * FROM customers c JOIN other o ON c.customer_id = o.customer_id")


def test_duplicate_table_name_does_not_replace_existing_table(tmp_path, engine):
    first, second = tmp_path / "first.csv", tmp_path / "second.csv"
    first.write_text("value\nfirst\n", encoding="utf-8")
    second.write_text("value\nsecond\n", encoding="utf-8")
    engine.load_csv("same", str(first))
    with pytest.raises(ValueError, match="already loaded"):
        engine.load_csv("same", str(second))
    assert engine.execute("SELECT value FROM same").iloc[0, 0] == "first"


def test_api_rejects_name_collision_and_session_does_not_leak(client_uploads):
    client, created = client_uploads
    old_session_ids = set(sessions)
    response = _post_files(client, [
        ("customer-list.csv", b"id\n1\n", "text/csv"),
        ("customer_list.csv", b"id\n2\n", "text/csv"),
    ])
    assert response.status_code == 400
    assert "already loaded" in response.json()["detail"]
    assert "Traceback" not in response.text
    assert not list(config.UPLOAD_DIR.iterdir())
    assert set(sessions) == old_session_ids


def test_malformed_empty_unsupported_and_oversized_uploads_fail_gracefully(client_uploads, monkeypatch):
    client, _ = client_uploads
    invalid_csv = _post_files(client, [("invalid.csv", b'"unterminated\n', "text/csv")])
    empty_csv = _post_files(client, [("empty.csv", b"", "text/csv")])
    malformed_xlsx = _post_files(client, [("bad.xlsx", b"not excel", "application/octet-stream")])
    unsupported = _post_files(client, [("notes.txt", b"hello", "text/plain")])
    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 0)
    oversized = _post_files(client, [("large.csv", b"value\n1\n", "text/csv")])
    for result in (invalid_csv, empty_csv, malformed_xlsx):
        assert result.status_code == 400
        assert "Traceback" not in result.text
    assert unsupported.status_code == 400
    assert oversized.status_code == 413
    assert not list(config.UPLOAD_DIR.iterdir())


def test_sessions_keep_files_and_schemas_isolated(client_uploads, tmp_path):
    client, created = client_uploads
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    a.write_text("alpha,amount\nA,1\n", encoding="utf-8")
    b.write_text("beta,region\nB,South\n", encoding="utf-8")
    ra, rb = _post_files(client, [("alpha.csv", a.read_bytes(), "text/csv")]), \
        _post_files(client, [("beta.csv", b.read_bytes(), "text/csv")])
    sid_a, sid_b = ra.json()["session_id"], rb.json()["session_id"]
    created.extend([sid_a, sid_b])
    assert set(sessions[sid_a]["engine"].tables) == {"alpha"}
    assert set(sessions[sid_b]["engine"].tables) == {"beta"}
    assert client.get(f"/api/schema/{sid_a}/beta").status_code == 404
    assert client.get(f"/api/schema/{sid_b}/alpha").status_code == 404
    assert "beta" not in repr(sessions[sid_a]["engine"].profile())
    assert "alpha" not in repr(sessions[sid_b]["engine"].profile())
    with pytest.raises(Exception, match="beta"):
        sessions[sid_a]["engine"].execute("SELECT * FROM beta")
