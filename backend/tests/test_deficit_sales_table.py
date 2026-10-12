"""Таблица «Продажи по подразделениям» на вкладке Платежи дефицита."""
from pathlib import Path

import pandas as pd
import pytest

from models.dashboard_spec import DashboardSpec, Tab, Tile
from services.dashboard_engine import render_spec
from services.deficit_sales_table import (
    build_deficit_sales_forecast_vitrine,
    build_deficit_sales_fulfillment_board,
    build_deficit_sales_share_table,
    build_deficit_sales_table,
    parse_sales_forecast_sheet,
    parse_sales_forecast_vitrine,
)
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


def test_summary_takes_forecast_from_department_table():
    from services.deficit_sales_table import build_deficit_sales_summary

    df = _sample_df()
    tile = Tile(
        title="Выполнение прогноза",
        chart_type="table",
        source={
            "kind": "deficit_sales_summary",
            "sales_forecasts": {"СТО": 500.0, "СИО": 100.0},
        },
        agg="sum",
        top_n=50,
        sort="none",
    )
    summary = build_deficit_sales_summary(df, tile)["table"]["rows"][0]["values"]
    assert summary[0] == pytest.approx(600.0)
    assert summary[1] == pytest.approx(650.0)
    assert summary[2] == 108


def test_manual_sales_forecasts_override_vitrine():
    df = _sample_df()
    df.attrs["deficit_sales_forecast"] = {"СТО": 999.0}
    tile = Tile(
        title="Продажи по подразделениям",
        chart_type="table",
        source={
            "kind": "deficit_sales_by_department",
            "sales_forecasts": {"СТО": 500.0, "СИО": 100.0},
        },
        agg="sum",
        top_n=50,
        sort="none",
    )
    by_label = {
        row["label"]: row["values"]
        for row in build_deficit_sales_table(df, tile)["table"]["rows"]
    }
    assert by_label["СТО"][0] == 500.0
    assert by_label["СТО"][3] == 60
    assert by_label["СИО"][0] == 100.0
    assert by_label["СИО"][3] == 100


def test_parse_forecast_from_second_sheet(tmp_path):
    import openpyxl

    path = tmp_path / "sales_q.xlsx"
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "Дефицит"
    ws1["A1"] = "подразделение"
    ws2 = wb.create_sheet("Прогноз")
    ws2["B3"] = "Службы"
    ws2["C3"] = "Прогноз продаж, р."
    ws2["B4"] = "СИО и АПЛиС"
    ws2["C4"] = 142_935_816
    ws2["B5"] = "СТО"
    ws2["C5"] = 320_707_435
    ws2["B6"] = "СМЭ"
    ws2["C6"] = 128_416_800
    ws2["B7"] = "СТЕ"
    ws2["C7"] = 694_860_501
    ws2["B8"] = "СООК"
    ws2["C8"] = 35_536_500
    ws2["B9"] = "Сервис"
    ws2["C9"] = 0
    ws2["B10"] = "Совтест"
    ws2["C10"] = 1_322_457_052
    wb.save(path)

    parsed, company = parse_sales_forecast_sheet(str(path))
    assert parsed["СИО и АПЛиС"] == pytest.approx(142_935_816)
    assert company is not None
    assert company["forecast"] == pytest.approx(1_322_457_052)
    assert parsed["СТО"] == pytest.approx(320_707_435)
    assert parsed["СЕРВИС"] == pytest.approx(0)
    assert "Совтест" not in parsed


def test_forecast_vitrine_table_matches_excel_layout():
    df = _sample_df()
    df.attrs["deficit_sales_forecast"] = {
        "СИО и АПЛиС": 400.0,
        "СТО": 500.0,
    }
    df.attrs["deficit_sales_forecast_company"] = {
        "label": "Совтест АТЕ",
        "forecast": 900.0,
    }
    tile = Tile(
        title="Прогноз продаж по службам",
        chart_type="table",
        source={"kind": "deficit_sales_forecast_vitrine"},
        agg="sum",
        top_n=50,
        sort="none",
    )
    table = build_deficit_sales_forecast_vitrine(df, tile)["table"]
    assert table["columns"] == [
        "Прогноз продаж, р.",
        "Суммы заказов, р.",
        "Выполнение прогноза",
    ]
    by_label = {row["label"]: row["values"] for row in table["rows"]}
    assert by_label["СИО и АПЛиС"][1] == pytest.approx(300.0)
    assert by_label["СИО и АПЛиС"][2] == 75
    assert table["totals_label"] == "Совтест АТЕ"
    assert table["totals"][0] == pytest.approx(900.0)
    assert table["totals"][1] == pytest.approx(650.0)
    assert table["totals"][2] == 72
    assert table["totals_emphasis"] is True


def test_fulfillment_board_highlights_above_company():
    df = _sample_df()
    df.attrs["deficit_sales_forecast"] = {
        "СИО и АПЛиС": 400.0,
        "СТО": 500.0,
        "СМЭ": 1000.0,
    }
    df.attrs["deficit_sales_forecast_company"] = {
        "label": "Совтест АТЕ",
        "forecast": 1900.0,
    }
    tile = Tile(
        title="Выполнение прогноза по подразделениям",
        chart_type="table",
        source={"kind": "deficit_sales_fulfillment_board"},
        agg="sum",
        top_n=50,
        sort="none",
    )
    table = build_deficit_sales_fulfillment_board(df, tile)["table"]
    assert table["index_label"] == "Подразделение"
    assert table["columns"] == ["Выполнение прогноза"]
    assert table["rows"][0]["label"] == "Совтест АТЕ"
    assert table["rows"][0]["emphasis"] is True
    by_label = {row["label"]: row for row in table["rows"][1:]}
    assert by_label["СИО и АПЛиС"]["highlight"] is True
    assert by_label["СТО"]["highlight"] is True
    assert by_label["СМЭ"]["highlight"] is False


def test_share_table_from_order_sums():
    df = _sample_df()
    df.attrs["deficit_sales_forecast"] = {"СИО и АПЛиС": 400.0, "СТО": 500.0}
    df.attrs["deficit_sales_forecast_company"] = {
        "label": "Совтест АТЕ",
        "forecast": 900.0,
    }
    tile = Tile(
        title="Доля подразделений в продажах",
        chart_type="table",
        source={"kind": "deficit_sales_share"},
        agg="sum",
        top_n=50,
        sort="none",
    )
    table = build_deficit_sales_share_table(df, tile)["table"]
    assert table["columns"] == ["Суммы заказов, р.", "Доля в продажах"]
    by_label = {row["label"]: row["values"] for row in table["rows"]}
    assert by_label["СИО и АПЛиС"][0] == pytest.approx(300.0)
    assert by_label["СИО и АПЛиС"][1] == pytest.approx(46.2)
    assert by_label["СТО"][1] == pytest.approx(46.2)
    assert table["totals_label"] == "Совтест АТЕ"
    assert table["totals"][0] == pytest.approx(650.0)
    assert table["totals"][1] == pytest.approx(100.0)
    assert table["totals_emphasis"] is True


def test_share_chart_company_first_then_sorted():
    from services.dashboard_engine import _render_deficit_sales_share_chart

    table = {
        "rows": [
            {"label": "СИО и АПЛиС", "values": [89_118_254.0, 20.2]},
            {"label": "СТО", "values": [265_042_003.0, 59.9]},
            {"label": "СМЭ", "values": [96_000.0, 0.0]},
            {"label": "СТЕ", "values": [50_781_701.0, 11.5]},
            {"label": "СООК", "values": [16_087_203.0, 3.6]},
            {"label": "СЕРВИС", "values": [21_108_808.0, 4.8]},
        ],
        "totals": [442_233_969.0, 100.0],
        "totals_label": "Совтест АТЕ",
    }
    fig = _render_deficit_sales_share_chart(table)
    assert list(fig.data[0].x) == [
        "Совтест АТЕ",
        "СТО",
        "СИО и АПЛиС",
        "СТЕ",
        "СЕРВИС",
        "СООК",
        "СМЭ",
    ]
    assert list(fig.data[0].marker.color)[0] == "#C00000"
    assert all(c == "#5B9BD5" for c in list(fig.data[0].marker.color)[1:])
    assert any("100,0%" in a["text"] for a in (fig.layout.annotations or []))


def test_fulfillment_chart_colors_and_order():
    from services.dashboard_engine import _render_deficit_sales_fulfillment_chart

    table = {
        "rows": [
            {"label": "Совтест АТЕ", "values": [33], "emphasis": True},
            {"label": "СИО и АПЛиС", "values": [62], "highlight": True},
            {"label": "СТО", "values": [83], "highlight": True},
            {"label": "СМЭ", "values": [0], "highlight": False},
            {"label": "СТЕ", "values": [7], "highlight": False},
            {"label": "СООК", "values": [45], "highlight": True},
        ]
    }
    fig = _render_deficit_sales_fulfillment_chart(table)
    assert list(fig.data[0].x) == [
        "СТО",
        "СИО и АПЛиС",
        "СООК",
        "Совтест АТЕ",
        "СТЕ",
        "СМЭ",
    ]
    assert list(fig.data[0].y) == [83, 62, 45, 33, 7, 0]
    assert list(fig.data[0].marker.color) == [
        "#70AD47",
        "#70AD47",
        "#70AD47",
        "#C00000",
        "#5B9BD5",
        "#5B9BD5",
    ]


def test_plan_fact_chart_from_vitrine_table():
    from services.dashboard_engine import _render_deficit_sales_plan_fact_chart

    table = {
        "rows": [
            {"label": "СИО и АПЛиС", "values": [400.0, 300.0, 75]},
            {"label": "СТО", "values": [500.0, 300.0, 60]},
            {"label": "СЕРВИС", "values": [0.0, 50.0, None]},
        ],
        "totals": [900.0, 650.0, 72],
        "totals_label": "Совтест АТЕ",
    }
    fig = _render_deficit_sales_plan_fact_chart(table)
    assert len(fig.data) == 2
    assert list(fig.data[0].x) == ["СИО и АПЛиС", "СТО", "СЕРВИС", "Совтест АТЕ"]
    assert list(fig.data[0].y) == [400.0, 500.0, 0.0, 900.0]
    assert list(fig.data[1].y) == [300.0, 300.0, 50.0, 650.0]
    pct_texts = [a["text"] for a in (fig.layout.annotations or [])]
    assert "<b>75%</b>" in pct_texts
    assert "<b>72%</b>" in pct_texts
    assert not any("None" in t for t in pct_texts)


def test_combined_sio_aplis_row_from_vitrine():
    df = _sample_df()
    df.attrs["deficit_sales_forecast"] = {
        "СИО и АПЛиС": 400.0,
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
    by_label = {
        row["label"]: row["values"]
        for row in build_deficit_sales_table(df, tile)["table"]["rows"]
    }
    assert "СИО и АПЛиС" in by_label
    assert "СИО" not in by_label
    assert "ОАПЛиС" not in by_label
    assert by_label["СИО и АПЛиС"][1] == 2
    assert by_label["СИО и АПЛиС"][2] == pytest.approx(300.0)
    assert by_label["СИО и АПЛиС"][0] == pytest.approx(400.0)
    assert by_label["СИО и АПЛиС"][3] == 75


@pytest.mark.skipif(
    not Path(r"C:\Users\USER2\Downloads\Продажи_3 кв 2026.xlsx").exists(),
    reason="локальный файл прогноза продаж",
)
def test_parse_real_sales_q3_2026_file():
    path = r"C:\Users\USER2\Downloads\Продажи_3 кв 2026.xlsx"
    parsed = parse_sales_forecast_vitrine(path)
    assert parsed["СИО и АПЛиС"] == pytest.approx(142_935_816)
    assert parsed["СТО"] == pytest.approx(320_707_435)
    assert parsed["СМЭ"] == pytest.approx(128_416_800)
    assert parsed["СТЕ"] == pytest.approx(694_860_501)
    assert parsed["СООК"] == pytest.approx(35_536_500)
    assert parsed["СЕРВИС"] == pytest.approx(0)
    assert sum(v for k, v in parsed.items() if k != "СЕРВИС" or v) > 0


def test_normalize_keeps_sales_table_on_payments_tab():
    df = _sample_df()
    df.attrs["deficit_sales_forecast"] = {
        "СИО и АПЛиС": 400.0,
        "СТО": 500.0,
        "СМЭ": 1000.0,
    }
    df.attrs["deficit_sales_forecast_company"] = {
        "label": "Совтест АТЕ",
        "forecast": 1900.0,
    }
    spec = build_deficit_dashboard_spec(df)
    spec = normalize_deficit_dashboard_spec(spec)
    pay = next(tab for tab in spec.tabs if tab.title == "Платежи")
    assert [t.source.kind for t in pay.tiles] == [
        "deficit_sales_by_department",
        "deficit_sales_summary",
        "deficit_sales_forecast_vitrine",
        "deficit_sales_fulfillment_board",
        "deficit_sales_share",
    ]
    rendered = render_spec(df, spec)
    pay_out = next(tab for tab in rendered["tabs"] if tab["title"] == "Платежи")
    assert pay_out["tiles"][0]["table"]["columns"][0] == "Прогноз продаж, р."
    assert pay_out["tiles"][1]["table"]["columns"][1] == "Общая сумма заказов, р."
    assert pay_out["tiles"][1].get("plotly_json")
    assert pay_out["tiles"][2]["table"]["columns"][1] == "Суммы заказов, р."
    assert pay_out["tiles"][2].get("plotly_json")
    assert pay_out["tiles"][3]["table"]["columns"][0] == "Выполнение прогноза"
    assert pay_out["tiles"][3].get("plotly_json")
    assert pay_out["tiles"][4]["table"]["columns"][1] == "Доля в продажах"
    assert pay_out["tiles"][4]["table"]["totals_label"] == "Совтест АТЕ"
    assert pay_out["tiles"][4].get("plotly_json")
