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


def test_allocate_percentages_match_total():
    from services.deficit_sales_table import _allocate_percentages, _fulfillment_percent

    forecast = 1_322_457_052.0
    amounts = [199_716_325.2, 167_990_628.6, 74_496_631.3]
    total = sum(amounts)
    target = _fulfillment_percent(total, forecast)
    assert target == 33
    # Независимое округление даёт 15+13+6=34
    assert [_fulfillment_percent(a, forecast) for a in amounts] == [15, 13, 6]
    allocated = _allocate_percentages(amounts, forecast, target=target)
    assert sum(allocated) == target
    assert allocated == [15, 13, 5]


def test_summary_period_pcts_sum_to_fulfill():
    from services.deficit_sales_table import build_deficit_sales_summary

    df = pd.DataFrame(
        {
            "подразделение": [
                "Служба тестового оборудования",
                "Служба тестового оборудования",
                "Служба тестового оборудования",
            ],
            "заказ клиента": [
                "Заказ клиента ЗК-1 от 10.07.2026",
                "Заказ клиента ЗК-2 от 10.08.2026",
                "Заказ клиента ЗК-3 от 10.09.2026",
            ],
            "сумма по заказу в рублях": [199_716_325.2, 167_990_628.6, 74_496_631.3],
            "оплачено по заказу": [0.0, 0.0, 0.0],
            "неоплаченный остаток": [199_716_325.2, 167_990_628.6, 74_496_631.3],
        }
    )
    df.attrs["deficit_sales_forecast"] = {"СТО": 1_322_457_052.0}
    tile = Tile(
        title="Выполнение прогноза",
        chart_type="table",
        source={"kind": "deficit_sales_summary", "period": "month"},
        agg="sum",
        top_n=50,
        sort="none",
    )
    data = build_deficit_sales_summary(df, tile)
    bars = [b for b in data["period_bars"] if b["order_sum"] > 0]
    assert data["fulfill_pct"] == 33
    assert sum(int(b["pct"]) for b in bars) == data["fulfill_pct"]


def test_summary_splits_order_sum_by_quarters():
    from services.deficit_sales_table import build_deficit_sales_summary
    from services.dashboard_engine import _render_deficit_sales_summary_chart

    df = pd.DataFrame(
        {
            "подразделение": [
                "Служба испытательного оборудования",
                "Служба тестового оборудования",
                "Служба тестового оборудования",
            ],
            "заказ клиента": [
                "Заказ клиента ЗК-1 от 10.01.2026",
                "Заказ клиента ЗК-2 от 15.08.2026",
                "Заказ клиента ЗК-3 от 20.09.2026",
            ],
            "сумма по заказу в рублях": [100.0, 200.0, 300.0],
            "оплачено по заказу": [0.0, 0.0, 0.0],
            "неоплаченный остаток": [100.0, 200.0, 300.0],
        }
    )
    df.attrs["deficit_sales_forecast"] = {"СИО": 400.0, "СТО": 500.0}
    tile = Tile(
        title="Выполнение прогноза",
        chart_type="table",
        source={"kind": "deficit_sales_summary", "period": "quarter"},
        agg="sum",
        top_n=50,
        sort="none",
    )
    data = build_deficit_sales_summary(df, tile)
    bars = data["period_bars"]
    assert len(bars) == 3
    assert bars[0]["order_sum"] == pytest.approx(100.0)
    assert bars[1]["order_sum"] == pytest.approx(0.0)
    assert bars[2]["order_sum"] == pytest.approx(500.0)
    assert data["order_sum"] == pytest.approx(600.0)
    # Таблица: прогноз + 3 квартала + %
    values = data["table"]["rows"][0]["values"]
    assert values[0] == pytest.approx(900.0)
    assert values[1] == pytest.approx(100.0)
    assert values[3] == pytest.approx(500.0)

    fig = _render_deficit_sales_summary_chart(
        data["table"],
        period_bars=bars,
        forecast=data["forecast"],
        order_sum=data["order_sum"],
        fulfill_pct=data["fulfill_pct"],
    )
    # Прогноз отдельно + один stacked-столбец из 3 сегментов периодов
    assert fig.layout.barmode == "stack"
    assert list(fig.data[0].x) == ["ПРОГНОЗ ПРОДАЖ, Р."]
    assert list(fig.data[0].y) == pytest.approx([900.0])
    assert len(fig.data) == 4  # прогноз + 3 сегмента
    assert all(list(tr.x) == ["ОБЩАЯ СУММА ЗАКАЗОВ, Р."] for tr in fig.data[1:])
    assert [float(tr.y[0]) for tr in fig.data[1:]] == pytest.approx([100.0, 0.0, 500.0])
    # Подписи периода и % — сбоку, не внутри тонких сегментов
    side = [
        str(a.text or "")
        for a in (fig.layout.annotations or [])
        if getattr(a, "showarrow", False)
    ]
    assert any("кв" in text.lower() and "%" in text for text in side)
    assert len(side) >= 2


def test_plan_fact_chart_splits_orders_by_periods():
    from services.dashboard_engine import _render_deficit_sales_plan_fact_chart

    table = {
        "rows": [
            {"label": "СИО и АПЛиС", "values": [400.0, 100.0, 50.0, 150.0, 38]},
            {"label": "СТО", "values": [500.0, 0.0, 300.0, 300.0, 60]},
        ],
        "totals": [900.0, 100.0, 350.0, 450.0, 50],
        "totals_label": "Совтест АТЕ",
        "columns": [
            "Прогноз продаж, р.",
            "1 кв. 2026",
            "3 кв. 2026",
            "Суммы заказов, р.",
            "Выполнение прогноза",
        ],
        "column_kinds": ["money", "money", "money", "money", "percent"],
        "year_spans": [
            {"label": "", "count": 1},
            {"label": "1 кв. 2026", "count": 1},
            {"label": "3 кв. 2026", "count": 1},
            {"label": "Всего", "count": 1},
            {"label": "", "count": 1},
        ],
        "forecast_column_index": 0,
        "order_sum_column_index": 3,
        "fulfill_column_index": 4,
    }
    fig = _render_deficit_sales_plan_fact_chart(table)
    assert len(fig.data) == 3  # прогноз + 2 сегмента stacked
    assert fig.data[0].orientation == "h"
    assert fig.data[1].name == "1 кв. 2026"
    assert fig.data[2].name == "3 кв. 2026"
    # Горизонтально: службы по Y, суммы по X (порядок сверху вниз как в таблице)
    assert list(fig.data[0].y) == ["Совтест АТЕ", "СТО", "СИО и АПЛиС"]
    assert list(fig.data[1].x) == pytest.approx([100.0, 0.0, 100.0])
    assert list(fig.data[2].x) == pytest.approx([350.0, 300.0, 50.0])
    assert fig.data[1].offsetgroup == fig.data[2].offsetgroup == "orders"
    assert list(fig.data[2].base) == pytest.approx([100.0, 0.0, 100.0])
    # % и периоды — справа от цифр у столбцов
    side = " ".join(str(a.text or "") for a in (fig.layout.annotations or []))
    assert "50%" in side or "<b>50%</b>" in side
    assert "1 кв. 2026" in side
    assert "3 кв. 2026" in side
    # Плашки правее конца столбца (+ запас под цифры)
    peak = 900.0
    for a in fig.layout.annotations or []:
        assert float(a.x) > peak * 0.5


def test_share_chart_splits_company_total_by_periods():
    from services.dashboard_engine import _render_deficit_sales_share_chart

    table = {
        "rows": [
            {"label": "СИО и АПЛиС", "values": [100.0, 50.0, 150.0, 25.0]},
            {"label": "СТО", "values": [0.0, 450.0, 450.0, 75.0]},
        ],
        "totals": [100.0, 500.0, 600.0, 100.0],
        "totals_label": "Совтест АТЕ",
        "columns": ["1 кв. 2026", "3 кв. 2026", "Суммы заказов, р.", "Доля в продажах"],
        "column_kinds": ["money", "money", "money", "percent1"],
        "year_spans": [
            {"label": "1 кв. 2026", "count": 1},
            {"label": "3 кв. 2026", "count": 1},
            {"label": "Всего", "count": 1},
            {"label": "", "count": 1},
        ],
        "order_sum_column_index": 2,
    }
    fig = _render_deficit_sales_share_chart(table)
    assert fig.layout.barmode == "stack"
    # Два сегмента компании на одном x + службы
    assert list(fig.data[0].x) == ["Совтест АТЕ"]
    assert list(fig.data[1].x) == ["Совтест АТЕ"]
    assert float(fig.data[0].y[0]) == pytest.approx(100.0)
    assert float(fig.data[1].y[0]) == pytest.approx(500.0)
    assert fig.data[0].name == "1 кв. 2026"
    assert "СТО" in list(fig.data[-1].x)


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
    sum_col = table["order_sum_column_index"]
    fulfill_col = table["fulfill_column_index"]
    assert by_label["СИО и АПЛиС"][sum_col] == pytest.approx(300.0)
    assert by_label["СИО и АПЛиС"][fulfill_col] == 75
    assert table["totals_label"] == "Совтест АТЕ"
    assert table["totals"][0] == pytest.approx(900.0)
    assert table["totals"][sum_col] == pytest.approx(650.0)
    assert table["totals"][fulfill_col] == 72
    assert table["totals_emphasis"] is True


def test_fulfillment_board_company_first_no_row_fill():
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
    assert all(not row.get("highlight") for row in table["rows"])


def test_single_period_omits_total_block():
    df = pd.DataFrame(
        {
            "подразделение": ["Служба тестового оборудования"],
            "заказ клиента": ["Заказ клиента ЗК-1 от 10.08.2026"],
            "сумма по заказу в рублях": [300.0],
            "оплачено по заказу": [0.0],
            "неоплаченный остаток": [300.0],
        }
    )
    tile = Tile(
        title="t",
        chart_type="table",
        source={"kind": "deficit_sales_by_department", "period": "quarter"},
        agg="sum",
        top_n=50,
        sort="none",
    )
    table = build_deficit_sales_table(df, tile)["table"]
    assert table["columns"] == [
        "Прогноз продаж, р.",
        "Количество",
        "Общая сумма",
        "Выполнение прогноза",
    ]
    assert not any(s["label"] == "Всего за период" for s in table["year_spans"])
    assert table["order_sum_column_index"] == 2
    assert table["fulfill_column_index"] == 3


def test_quarter_mode_shows_all_quarters_in_file():
    df = pd.DataFrame(
        {
            "подразделение": [
                "Служба испытательного оборудования",
                "Служба тестового оборудования",
                "Служба тестового оборудования",
            ],
            "заказ клиента": [
                "Заказ клиента ЗК-1 от 10.01.2026",
                "Заказ клиента ЗК-2 от 15.08.2026",
                "Заказ клиента ЗК-3 от 20.09.2026",
            ],
            "сумма по заказу в рублях": [100.0, 200.0, 300.0],
            "оплачено по заказу": [0.0, 0.0, 0.0],
            "неоплаченный остаток": [100.0, 200.0, 300.0],
        }
    )
    df.attrs["deficit_sales_forecast"] = {"СИО": 400.0, "СТО": 500.0}
    tile = Tile(
        title="t",
        chart_type="table",
        source={"kind": "deficit_sales_by_department", "period": "quarter"},
        agg="sum",
        top_n=50,
        sort="none",
    )
    table = build_deficit_sales_table(df, tile)["table"]
    # Q1..Q3 (contiguous from Jan to Sep) = 3 quarter columns
    period_spans = [s for s in table["year_spans"] if "кв" in s["label"].lower()]
    assert len(period_spans) == 3
    by_label = {row["label"]: row["values"] for row in table["rows"]}
    assert by_label["СИО"][table["order_sum_column_index"]] == pytest.approx(100.0)
    assert by_label["СТО"][table["order_sum_column_index"]] == pytest.approx(500.0)


def test_month_mode_shows_all_months_in_file():
    df = pd.DataFrame(
        {
            "подразделение": [
                "Служба испытательного оборудования",
                "Служба тестового оборудования",
            ],
            "заказ клиента": [
                "Заказ клиента ЗК-1 от 10.07.2026",
                "Заказ клиента ЗК-2 от 20.09.2026",
            ],
            "сумма по заказу в рублях": [100.0, 300.0],
            "оплачено по заказу": [0.0, 0.0],
            "неоплаченный остаток": [100.0, 300.0],
        }
    )
    tile = Tile(
        title="t",
        chart_type="table",
        source={"kind": "deficit_sales_by_department", "period": "month"},
        agg="sum",
        top_n=50,
        sort="none",
    )
    table = build_deficit_sales_table(df, tile)["table"]
    month_spans = [
        s["label"]
        for s in table["year_spans"]
        if s["label"] and s["label"] not in {"Всего за период", "Заказы, зарегистрированные в 1С"}
    ]
    assert len(month_spans) == 3  # июль, август, сентябрь
    assert any("июл" in label.lower() for label in month_spans)
    assert any("сен" in label.lower() for label in month_spans)


def test_half_mode_shows_all_halves_in_file():
    df = pd.DataFrame(
        {
            "подразделение": [
                "Служба тестового оборудования",
                "Служба тестового оборудования",
            ],
            "заказ клиента": [
                "Заказ клиента ЗК-1 от 10.03.2026",
                "Заказ клиента ЗК-2 от 20.09.2026",
            ],
            "сумма по заказу в рублях": [100.0, 200.0],
            "оплачено по заказу": [0.0, 0.0],
            "неоплаченный остаток": [100.0, 200.0],
        }
    )
    tile = Tile(
        title="t",
        chart_type="table",
        source={"kind": "deficit_sales_by_department", "period": "half"},
        agg="sum",
        top_n=50,
        sort="none",
    )
    table = build_deficit_sales_table(df, tile)["table"]
    half_spans = [s["label"] for s in table["year_spans"] if "полуг" in s["label"].lower()]
    assert len(half_spans) == 2
    by_label = {row["label"]: row["values"] for row in table["rows"]}
    assert by_label["СТО"][table["order_sum_column_index"]] == pytest.approx(300.0)


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
    sum_col = table["order_sum_column_index"]
    share_col = sum_col + 1
    by_label = {row["label"]: row["values"] for row in table["rows"]}
    assert by_label["СИО и АПЛиС"][sum_col] == pytest.approx(300.0)
    assert by_label["СИО и АПЛиС"][share_col] == pytest.approx(46.2)
    assert by_label["СТО"][share_col] == pytest.approx(46.2)
    assert table["totals_label"] == "Совтест АТЕ"
    assert table["totals"][sum_col] == pytest.approx(650.0)
    assert table["totals"][share_col] == pytest.approx(100.0)
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
        "order_sum_column_index": 0,
    }
    fig = _render_deficit_sales_share_chart(table)
    assert list(fig.data[0].x) == ["Совтест АТЕ"]
    assert float(fig.data[0].y[0]) == pytest.approx(442_233_969.0)
    assert fig.data[0].marker.color == "#C00000"
    assert list(fig.data[1].x) == [
        "СТО",
        "СИО и АПЛиС",
        "СТЕ",
        "СЕРВИС",
        "СООК",
        "СМЭ",
    ]
    dept_colors = fig.data[1].marker.color
    if isinstance(dept_colors, str):
        assert dept_colors == "#5B9BD5"
    else:
        assert all(c == "#5B9BD5" for c in list(dept_colors))
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
        "forecast_column_index": 0,
        "order_sum_column_index": 1,
        "fulfill_column_index": 2,
    }
    fig = _render_deficit_sales_plan_fact_chart(table)
    assert len(fig.data) == 2
    assert fig.data[0].orientation == "h"
    assert list(fig.data[0].y) == ["Совтест АТЕ", "СЕРВИС", "СТО", "СИО и АПЛиС"]
    assert list(fig.data[0].x) == pytest.approx([900.0, 0.0, 500.0, 400.0])
    assert list(fig.data[1].x) == pytest.approx([650.0, 50.0, 300.0, 300.0])
    side = " ".join(str(a.text or "") for a in (fig.layout.annotations or []))
    assert "<b>75%</b>" in side
    assert "<b>72%</b>" in side
    assert "None" not in side


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
    assert "Суммы заказов, р." in pay_out["tiles"][2]["table"]["columns"]
    assert pay_out["tiles"][2].get("plotly_json")
    assert pay_out["tiles"][3]["table"]["columns"][0] == "Выполнение прогноза"
    assert pay_out["tiles"][3].get("plotly_json")
    assert "Доля в продажах" in pay_out["tiles"][4]["table"]["columns"]
    assert pay_out["tiles"][4]["table"]["totals_label"] == "Совтест АТЕ"
    assert pay_out["tiles"][4].get("plotly_json")
    structure = next(tab for tab in spec.tabs if tab.title == "Структура")
    assert [t.source.kind for t in structure.tiles] == [
        "deficit_payment_receipt",
        "deficit_payment_by_department",
        "deficit_payment_fulfillment_board",
        "deficit_payment_share",
    ]
