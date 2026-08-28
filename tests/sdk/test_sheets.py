"""P1-T5: Sheets/CSV parser — app_import_csv + app_summarize.

Formula cells are never evaluated: XLSX is parsed with openpyxl
data_only=True (cached literal values only); CSV/XLSX parsing is pure
Python with no formula engine.
"""

import sqlite3
import uuid
from pathlib import Path

import pytest

from src.sdk.tools_core.apps import (
    app_create,
    app_import_csv,
    app_query,
    app_schema,
    app_summarize,
)

_TEST_USER = "sheets_test_user"


@pytest.fixture()
def csv_file(tmp_path: Path) -> Path:
    p = tmp_path / "clients.csv"
    p.write_text(
        "name,retainer,active\n"
        "Acme,5000.5,true\n"
        "Beta,1200,false\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture()
def xlsx_two_sheets(tmp_path: Path) -> Path:
    openpyxl = pytest.importorskip("openpyxl")
    p = tmp_path / "workbook.xlsx"
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "clients"
    ws1.append(["name", "fee"])
    ws1.append(["Acme", 5000])
    ws2 = wb.create_sheet("invoices")
    ws2.append(["invoice_id", "amount"])
    ws2.append([1, 250.0])
    wb.save(p)
    return p


@pytest.fixture()
def xlsx_formulas(tmp_path: Path) -> Path:
    openpyxl = pytest.importorskip("openpyxl")
    p = tmp_path / "formulas.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "calc"
    ws.append(["a", "b", "total"])
    ws.append([1, 2, "=A2+B2"])  # formula cell
    wb.save(p)
    return p


def _ensure_app(name: str) -> None:
    result = app_create.invoke(
        args={"name": name, "tables": {"t": {"name": "TEXT"}}, "user_id": _TEST_USER}
    )
    assert "Error" not in result


@pytest.fixture()
def app_name() -> str:
    return f"books_{uuid.uuid4().hex[:8]}"


def test_csv_import_creates_typed_rows(csv_file: Path, app_name: str) -> None:
    _ensure_app(app_name)
    result = app_import_csv.invoke(args={"path": str(csv_file), "app_name": app_name, "table": "clients"})
    assert "Error" not in result, result
    rows = app_query.invoke(args={"app": app_name, "query": "SELECT name, retainer FROM clients ORDER BY name"})
    assert "Acme" in rows and "5000.5" in rows


def test_two_sheet_workbook_creates_two_tables(xlsx_two_sheets: Path, app_name: str) -> None:
    _ensure_app(app_name)
    result = app_import_csv.invoke(args={"path": str(xlsx_two_sheets), "app_name": app_name})
    assert "Error" not in result, result
    schema = app_schema.invoke(args={"name": app_name})
    assert "clients" in schema and "invoices" in schema


def test_formula_cells_stored_raw_not_stored(xlsx_formulas: Path, app_name: str) -> None:
    _ensure_app(app_name)
    result = app_import_csv.invoke(args={"path": str(xlsx_formulas), "app_name": app_name, "table": "calc"})
    assert "Error" not in result, result
    db_path = app_query.invoke(args={"app": app_name, "query": "SELECT total FROM calc"})
    # The formula string itself is stored (or its cached literal) — never 3 via execution.
    assert "=A2+B2" in db_path or "3" not in db_path.replace("total", "")
    # Stronger: open the sqlite file and check raw value is the formula string.
    from src.storage.paths import get_paths

    app_db = get_paths().apps_dir() / "books" / "app.db"
    conn = sqlite3.connect(app_db)
    try:
        (stored,) = conn.execute("SELECT total FROM calc").fetchone()
    finally:
        conn.close()
    assert stored == "=A2+B2"


def test_duplicate_import_is_upsert_no_duplicate_rows(csv_file: Path, app_name: str) -> None:
    _ensure_app(app_name)
    first = app_import_csv.invoke(args={"path": str(csv_file), "app_name": app_name, "table": "clients"})
    assert "Error" not in first
    second = app_import_csv.invoke(args={"path": str(csv_file), "app_name": app_name, "table": "clients"})
    assert "Error" not in second
    count = app_query.invoke(args={"app": app_name, "query": "SELECT COUNT(*) AS n FROM clients"})
    assert "2" in count and not any(d in count for d in ("4", "6"))


def test_missing_openpyxl_degrades_cleanly(csv_file: Path, monkeypatch, app_name: str) -> None:
    import builtins

    _ensure_app(app_name)
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "openpyxl":
            raise ImportError("No module named 'openpyxl'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = app_import_csv.invoke(args={"path": str(csv_file), "app_name": app_name, "table": "clients"})
    assert "Error" not in result or "openpyxl" not in result  # csv unaffected
    # The csv path must not require openpyxl at all:
    assert "Inserted" in result or "2" in result


def test_xlsx_missing_openpyxl_clear_error(tmp_path: Path, monkeypatch, app_name: str) -> None:
    import builtins

    p = tmp_path / "w.xlsx"
    p.write_bytes(b"not really xlsx")
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "openpyxl":
            raise ImportError("No module named 'openpyxl'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = app_import_csv.invoke(args={"path": str(p), "app_name": app_name, "table": "w"})
    assert "Error" in result
    assert "openpyxl" in result


async def test_app_summarize_returns_short_description(csv_file: Path, monkeypatch, app_name: str) -> None:
    from src.sdk.messages import Message

    _ensure_app(app_name)
    app_import_csv.invoke(args={"path": str(csv_file), "app_name": app_name, "table": "clients"})

    class FakeProvider:
        async def chat(self, **kwargs):
            return Message.assistant("Client retainer ledger with billing status.")

    monkeypatch.setattr(
        "src.sdk.providers.factory.get_cached_model_provider",
        lambda model, **kwargs: FakeProvider(),
    )
    summary = await app_summarize.ainvoke(args={"app": app_name, "user_id": _TEST_USER})
    assert len(summary) <= 200
    assert summary  # non-empty
