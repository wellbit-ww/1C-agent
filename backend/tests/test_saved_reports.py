"""Канвас отчёта: CRUD, render блока, Excel."""
import pandas as pd
import pytest

from services import db_service
from services.saved_report_service import (
    delete_report,
    export_report_excel,
    get_report,
    list_reports,
    render_block,
    save_report,
)


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    path = tmp_path / "agent.db"
    monkeypatch.setattr(db_service, "DB_PATH", path)
    import config

    monkeypatch.setattr(config, "DB_PATH", path)
    db_service.init_db()


def test_saved_report_crud():
    row = save_report(
        title="Тест",
        payload={
            "title": "Тест",
            "blocks": [
                {
                    "id": "t1",
                    "kind": "text",
                    "title": "Заметка",
                    "layout": {"x": 0, "y": 0, "w": 6, "h": 4},
                    "text": "Привет",
                }
            ],
        },
        file_id="file1",
    )
    assert row["id"]
    assert row["title"] == "Тест"
    listed = list_reports(file_id="file1")
    assert len(listed) == 1
    loaded = get_report(row["id"])
    assert loaded["payload"]["blocks"][0]["text"] == "Привет"
    assert delete_report(row["id"]) is True
    assert get_report(row["id"]) is None


def test_export_excel_one_sheet():
    data = export_report_excel(
        {
            "title": "Отчёт Q3",
            "blocks": [
                {
                    "id": "a",
                    "kind": "text",
                    "title": "Ввод",
                    "layout": {"x": 0, "y": 0, "w": 12, "h": 3},
                    "text": "Строка 1\nСтрока 2",
                },
                {
                    "id": "b",
                    "kind": "table",
                    "title": "Службы",
                    "layout": {"x": 0, "y": 3, "w": 12, "h": 8},
                    "table": {
                        "index_label": "Службы",
                        "columns": ["Сумма"],
                        "column_kinds": ["money"],
                        "rows": [{"label": "СТО", "values": [100.0]}],
                        "totals": [100.0],
                        "totals_label": "Всего",
                    },
                },
            ],
        }
    )
    assert data[:2] == b"PK"  # zip/xlsx


def test_render_block_sales_table():
    df = pd.DataFrame(
        {
            "подразделение": ["Служба тестового оборудования"],
            "заказ клиента": ["ЗК-1"],
            "сумма по заказу в рублях": [300.0],
            "оплачено по заказу": [0.0],
            "неоплаченный остаток": [300.0],
        }
    )
    df.attrs["deficit_sales_forecast"] = {"СТО": 500.0}
    out = render_block(
        df,
        title="Продажи",
        source={"kind": "deficit_sales_by_department", "period": "quarter"},
        period="quarter",
    )
    assert "error" not in out
    assert out.get("table")
    assert out["table"]["columns"][0] == "Прогноз продаж, р."


def test_chat_message_parses_saved_report_marker():
    db_service.add_chat_message(
        "f1",
        "assistant",
        "Отчёт сохранён",
        charts=[{"kind": "saved_report", "report_id": "abc", "title": "Мой"}],
    )
    history = db_service.get_chat_history("f1")
    assert history[-1]["saved_report"]["id"] == "abc"
    assert history[-1]["charts"] == []
