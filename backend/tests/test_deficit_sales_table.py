"""Таблица «Продажи по подразделениям» на вкладке Платежи дефицита."""
import pandas as pd
import pytest

from models.dashboard_spec import DashboardSpec, Tab, Tile
from services.dashboard_engine import render_spec
from services.deficit_sales_table import build_deficit_sales_table
from services.workspace_service import normalize_deficit_dashboard_spec
from services.report_profiles.deficit_profile import build_deficit_dashboard_spec


def _sample_df():
    return pd.DataFrame(
        {
            "подразделение": [
                "Служба испытательного оборудования",
                "Отдел АПЛиС",
                "Служба тестового оборудования",
                "Служба микроэлектроники",
            ],
            "заказ клиента": ["ЗК-1", "ЗК-2", "ЗК-3", "ЗК-3"],
            "сумма по заказу в рублях": [100.0, 200.0, 300.0, 50.0],
            "оплачено по заказу": [0.0, 0.0, 0.0, 0.0],
            "неоплаченный остаток": [100.0, 200.0, 300.0, 50.0],
        }
    )


def test_aggregate_orders_splits_sio_and_aplis():
    df = _sample_df()
    df.attrs["deficit_sales_forecast"] = {
        "СИО": 400.0,
        "ОАПЛиС": 600.0,
        "СТО": 500.0,
    }
    tile = Tile(
        title="Продажи по подразделениям",
        chart_type="table",
        source={"kind": "deficit_sales_by_department"},
        agg="sum",
        top_n=50,
        sort="none",
    )
    data = build_deficit_sales_table(df, tile)
    table = data["table"]
    by_label = {row["label"]: row["values"] for row in table["rows"]}
    assert by_label["СИО"][1] == 1
    assert by_label["СИО"][2] == pytest.approx(100.0)
    assert by_label["СИО"][3] == 25
    assert by_label["ОАПЛиС"][1] == 1
    assert by_label["ОАПЛиС"][2] == pytest.approx(200.0)
    assert by_label["ОАПЛиС"][3] == 33
    assert by_label["СТО"][1] == 1
    assert by_label["СТО"][3] == 60
    assert table["totals_label"] == "Всего"
    assert table["totals"][1] == 4
    assert table["totals"][2] == pytest.approx(650.0)
    assert table["year_spans"][1]["label"] == "Заказы, зарегистрированные в 1С"


def test_normalize_keeps_sales_table_on_payments_tab():
    spec = build_deficit_dashboard_spec(_sample_df())
    spec = normalize_deficit_dashboard_spec(spec)
    pay = next(tab for tab in spec.tabs if tab.title == "Платежи")
    assert pay.tiles[0].source.kind == "deficit_sales_by_department"
    rendered = render_spec(_sample_df(), spec)
    pay_out = next(tab for tab in rendered["tabs"] if tab["title"] == "Платежи")
    assert pay_out["tiles"][0]["table"]["columns"][0] == "Прогноз продаж, р."
