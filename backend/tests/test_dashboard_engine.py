"""Тесты движка дашбордов: spec-валидация, воронка, единицы, рендер."""
import json

import pandas as pd
import pytest
from pydantic import ValidationError

from models.dashboard_spec import DashboardSpec, Tab, Tile, TileSource
from services.dashboard_engine import _fmt_scaled, render_spec


def _tile(**overrides) -> Tile:
    base = {
        "title": "Тест",
        "chart_type": "bar",
        "source": {"kind": "group", "group_column": "компания", "value_column": "сумма по сделке"},
    }
    base.update(overrides)
    return Tile(**base)


class TestSpecValidation:
    def test_valid_spec(self):
        spec = DashboardSpec(tabs=[Tab(title="Вкладка", tiles=[_tile()])])
        assert spec.tabs[0].tiles[0].title == "Тест"

    def test_rejects_bad_chart_type(self):
        with pytest.raises(ValidationError):
            _tile(chart_type="scatter3d")

    def test_rejects_empty_tabs(self):
        with pytest.raises(ValidationError):
            DashboardSpec(tabs=[])


class TestUnitFormatting:
    @pytest.mark.parametrize(
        "value, scale, suffix, expected",
        [
            (18_254_222_243.3, 1e9, "млрд", "18.25 млрд"),
            (7_902_260.7, 1e6, "млн", "7.9 млн"),
            (2_500.0, 1e3, "тыс.", "2.5 тыс."),
            (42.0, 1.0, "", "42"),
        ],
    )
    def test_fmt_scaled(self, value, scale, suffix, expected):
        assert _fmt_scaled(value, scale, suffix) == expected


class TestFunnel:
    def test_columns_pattern_sums(self, sales_df):
        tile = _tile(
            title="Этапы по сумме",
            chart_type="hbar",
            source={"kind": "columns_pattern", "columns_pattern": "(сумма)"},
            sort="none",
        )
        result = render_spec(sales_df, DashboardSpec(tabs=[Tab(title="T", tiles=[tile])]))
        tile_out = result["tabs"][0]["tiles"][0]
        assert "error" not in tile_out

        # контрольная сумма: значение этапа = сумме соответствующей колонки
        stats = tile_out["stats"]
        assert stats["count"] == 10  # десять этапов воронки
        expected_new = float(sales_df["новая сделка (сумма)"].sum())
        fig = json.loads(tile_out["plotly_json"])
        labels = fig["data"][0]["y"]  # hbar: категории по y
        values_scaled = fig["data"][0]["x"]
        idx = labels.index("новая сделка")
        assert values_scaled[idx] * 1e9 == pytest.approx(expected_new, rel=0.01)

    def test_current_stage_counts_deals_not_qty_column(self, sales_df):
        tile = _tile(
            title="Текущий этап",
            chart_type="hbar",
            source={"kind": "current_stage", "columns_pattern": "(сумма)"},
            agg="count",
            sort="none",
        )
        result = render_spec(sales_df, DashboardSpec(tabs=[Tab(title="T", tiles=[tile])]))
        tile_out = result["tabs"][0]["tiles"][0]
        assert "error" not in tile_out
        fig = json.loads(tile_out["plotly_json"])
        labels = list(fig["data"][0]["y"])
        values = list(fig["data"][0]["x"])
        # hbar переворачивает: первая стадия сверху
        assert labels[-1] == "новая сделка"
        assert values[-1] == pytest.approx(1002)

    def test_current_stage_uses_deal_amount(self, sales_df):
        tile = _tile(
            title="Сумма на этапе",
            chart_type="hbar",
            source={
                "kind": "current_stage",
                "columns_pattern": "(сумма)",
                "value_column": "сумма по сделке",
            },
            sort="none",
        )
        result = render_spec(sales_df, DashboardSpec(tabs=[Tab(title="T", tiles=[tile])]))
        tile_out = result["tabs"][0]["tiles"][0]
        attributed = tile_out["stats"]["total"]
        fig = json.loads(tile_out["plotly_json"])
        labels = list(fig["data"][0]["y"])
        assert "Не распределено" in labels
        expected = float(pd.to_numeric(sales_df["сумма по сделке"], errors="coerce").sum())
        assert attributed == pytest.approx(expected, rel=0.001)

    def test_current_stage_not_equal_to_column_sum_when_cumulative(self):
        df = pd.DataFrame(
            {
                "сумма по сделке": [100.0, 200.0],
                "лид (сумма)": [100.0, 200.0],
                "оплата (сумма)": [pd.NA, 200.0],
            }
        )
        stock = _tile(
            chart_type="hbar",
            source={
                "kind": "current_stage",
                "columns_pattern": "(сумма)",
                "value_column": "сумма по сделке",
            },
            sort="none",
        )
        throughput = _tile(
            chart_type="hbar",
            source={"kind": "columns_pattern", "columns_pattern": "(сумма)"},
            sort="none",
        )
        spec = DashboardSpec(tabs=[Tab(title="T", tiles=[throughput, stock])])
        tiles = render_spec(df, spec)["tabs"][0]["tiles"]
        thru = json.loads(tiles[0]["plotly_json"])
        curr = json.loads(tiles[1]["plotly_json"])
        thru_map = dict(zip(thru["data"][0]["y"], thru["data"][0]["x"]))
        curr_map = dict(zip(curr["data"][0]["y"], curr["data"][0]["x"]))
        assert thru_map["лид"] == pytest.approx(300)
        assert curr_map["лид"] == pytest.approx(100)
        assert curr_map["оплата"] == pytest.approx(200)


class TestRender:
    def test_group_bar(self, sales_df):
        result = render_spec(
            sales_df,
            DashboardSpec(tabs=[Tab(title="T", tiles=[_tile(top_n=5)])]),
        )
        tile_out = result["tabs"][0]["tiles"][0]
        assert tile_out["chart_type"] == "bar"
        fig = json.loads(tile_out["plotly_json"])
        assert len(fig["data"][0]["x"]) == 5
        assert tile_out["stats"]["top"][0][0] == "АЛАБУГА МАШИНЕРИ ООО"

    def test_mean_agg(self, sales_df):
        tile = _tile(agg="mean", unit="mln")
        result = render_spec(sales_df, DashboardSpec(tabs=[Tab(title="T", tiles=[tile])]))
        stats = result["tabs"][0]["tiles"][0]["stats"]
        # средний чек топ-клиента должен быть положительным и разумным
        assert stats["top"][0][1] > 0

    def test_target_line_rendered(self, sales_df):
        tile = _tile(target_line=18_254_222_243.3, unit="mlrd")
        result = render_spec(sales_df, DashboardSpec(tabs=[Tab(title="T", tiles=[tile])]))
        fig = json.loads(result["tabs"][0]["tiles"][0]["plotly_json"])
        assert fig["layout"].get("shapes"), "линия-ориентир должна быть на фигуре"

    def test_period_tile(self, sales_df):
        tile = _tile(
            title="Динамика",
            chart_type="area",
            source={"kind": "period", "period": "month", "value_column": "сумма по сделке"},
        )
        result = render_spec(sales_df, DashboardSpec(tabs=[Tab(title="T", tiles=[tile])]))
        tile_out = result["tabs"][0]["tiles"][0]
        assert "error" not in tile_out
        assert tile_out["stats"]["count"] == 15  # 15 месяцев в файле

    def test_bad_tile_does_not_crash_dashboard(self, sales_df):
        bad = _tile(title="Битый", source={"kind": "group", "group_column": "несуществующая"})
        good = _tile()
        result = render_spec(
            sales_df,
            DashboardSpec(tabs=[Tab(title="T", tiles=[bad, good])]),
        )
        tiles = result["tabs"][0]["tiles"]
        assert "error" in tiles[0]
        assert "plotly_json" in tiles[1]


class TestDealsPivot:
    def test_quarter_department_table(self):
        df = pd.DataFrame(
            {
                "подразделение": [
                    "СИОиАПЛиС",
                    "СТО",
                    "СИОиАПЛиС",
                    "СМЭ",
                    "СТО",
                    "СООК",
                ],
                "дата начала сделки": [
                    "10.01.2025",
                    "15.01.2025",
                    "01.04.2025",
                    "10.04.2025",
                    "01.01.2026",
                    "20.01.2026",
                ],
            }
        )
        spec = DashboardSpec(
            tabs=[
                Tab(
                    title="Сделки",
                    tiles=[
                        Tile(
                            title="Сделки по кварталам и подразделениям",
                            chart_type="table",
                            source={"kind": "pivot", "group_semantic": "department", "period": "quarter"},
                            agg="count",
                            top_n=30,
                            sort="none",
                        )
                    ],
                )
            ]
        )
        tile = render_spec(df, spec)["tabs"][0]["tiles"][0]
        assert "error" not in tile
        table = tile["table"]
        assert "1 кв 2025" in table["columns"]
        assert "2 кв 2025" in table["columns"]
        assert "2025" in table["columns"]
        assert "1 кв 2026" in table["columns"]
        labels = [row["label"] for row in table["rows"]]
        assert labels == ["СИОиАПЛиС", "СТО", "СМЭ", "СООК"]
        by_name = {row["label"]: dict(zip(table["columns"], row["values"])) for row in table["rows"]}
        assert by_name["СИОиАПЛиС"]["1 кв 2025"] == 1
        assert by_name["СИОиАПЛиС"]["2 кв 2025"] == 1
        assert by_name["СИОиАПЛиС"]["2025"] == 2
        assert by_name["СТО"]["1 кв 2026"] == 1
        totals = dict(zip(table["columns"], table["totals"]))
        assert totals["1 кв 2025"] == 2
        assert totals["2026"] == 2

    def test_sales_default_starts_with_deals(self, sales_df):
        from services.report_profiles.sales_profile import build_sales_dashboard_spec

        spec = build_sales_dashboard_spec(sales_df)
        assert spec.tabs[0].title == "Данные"
        assert [t.title for t in spec.tabs] == ["Данные"]
        rendered = render_spec(sales_df, spec)
        deals = rendered["tabs"][0]["tiles"]
        assert deals[0]["chart_type"] == "table"
        assert "1 кв" in " ".join(deals[0]["table"]["columns"])
        assert deals[1]["title"] == "Проигранные и отменённые сделки"
        assert deals[1]["table"]["totals_label"] == "Совтест"
        assert deals[-1]["title"] == "Сделки и ЗК по статусам"
        assert deals[-1]["table"]["index_label"] == "Статус"


def test_status_summary_by_deal_status():
    df = pd.DataFrame(
        {
            "статус": ["В работе", "В работе", "Выиграна", "Проиграна", "Отменена"],
            "количество сделок": [10, 5, 3, 1, 2],
            "сумма по сделке": [100.0, 50.0, 30.0, 10.0, 20.0],
            "количество зк": [2, 0, 1, 0, 0],
            "сумма зк": [40.0, 0.0, 5.0, 0.0, 0.0],
        }
    )
    spec = DashboardSpec(
        tabs=[
            Tab(
                title="Сделки",
                tiles=[
                    Tile(
                        title="Сделки и ЗК по статусам",
                        chart_type="table",
                        source={"kind": "status_summary"},
                        agg="sum",
                        top_n=50,
                        sort="none",
                    )
                ],
            )
        ]
    )
    table = render_spec(df, spec)["tabs"][0]["tiles"][0]["table"]
    by_status = {row["label"]: row["values"] for row in table["rows"]}
    assert by_status["В работе"][:2] == [15, 150]
    assert by_status["В работе"][2:] == [2, 40]
    assert by_status["Проиграна"][2:] == [None, None]
    assert table["totals"][:2] == [21, 210]


def test_department_short_names():
    from services.dashboard_engine import department_short

    assert department_short("Служба испытательного оборудования") == "СИО"
    assert department_short("Служба технологического оборудования") == "СТО"
    assert department_short("Служба оборудования обработки кабеля") == "СООК"
    assert department_short("Служба микроэлектроники") == "СМЭ"
    assert department_short("Отдел АПЛиС") == "ОАПЛиС"
    assert department_short("СИО") == "СИО"


def test_halfyear_splits_company_and_departments():
    df = pd.DataFrame(
        {
            "подразделение": [
                "Служба испытательного оборудования",
                "Отдел АПЛиС",
                "Служба микроэлектроники",
            ],
            "дата начала сделки": pd.to_datetime(["2026-02-01", "2026-03-01", "2026-08-01"]),
            "количество сделок": [2, 1, 4],
            "сумма по сделке": [100.0, 50.0, 80.0],
            "количество зк": [1, 0, 2],
            "сумма зк": [40.0, 0.0, 10.0],
        }
    )
    spec = DashboardSpec(
        tabs=[
            Tab(
                title="Сделки",
                tiles=[
                    Tile(
                        title="Сделки и ЗК по полугодиям",
                        chart_type="table",
                        source={"kind": "halfyear", "group_semantic": "department"},
                        agg="sum",
                        top_n=50,
                        sort="none",
                    )
                ],
            )
        ]
    )
    sections = render_spec(df, spec)["tabs"][0]["tiles"][0]["sections"]
    assert [block["title"] for block in sections] == ["1 полуг. 2026", "2 полуг. 2026"]
    first = sections[0]["tables"]
    assert [item["title"] for item in first] == ["Совтест", "СИО", "ОАПЛиС"]
    company = first[0]["rows"]
    assert company[0]["values"][:2] == [3, 150]
    assert company[1]["values"] == [1, 40, 33, 26.7]
    second = sections[1]["tables"]
    assert [item["title"] for item in second] == ["Совтест", "СМЭ"]
    assert second[1]["rows"][1]["values"][3] == 12.5


def test_halfyear_respects_quarter_and_month_buckets():
    df = pd.DataFrame(
        {
            "подразделение": ["СИО", "СИО", "СТО"],
            "дата начала сделки": pd.to_datetime(["2026-01-15", "2026-04-10", "2026-02-01"]),
            "количество сделок": [1, 2, 3],
            "сумма по сделке": [10.0, 20.0, 30.0],
            "количество зк": [0, 1, 1],
            "сумма зк": [0.0, 5.0, 3.0],
        }
    )
    quarter_spec = DashboardSpec(
        tabs=[
            Tab(
                title="Сделки",
                tiles=[
                    Tile(
                        title="Сделки и ЗК",
                        chart_type="table",
                        source={
                            "kind": "halfyear",
                            "group_semantic": "department",
                            "period": "quarter",
                        },
                        agg="sum",
                        top_n=50,
                        sort="none",
                    )
                ],
            )
        ]
    )
    q_sections = render_spec(df, quarter_spec)["tabs"][0]["tiles"][0]["sections"]
    assert [b["title"] for b in q_sections] == ["1 кв. 2026", "2 кв. 2026"]
    assert q_sections[0]["tables"][0]["rows"][0]["values"][0] == 4

    month_spec = DashboardSpec(
        tabs=[
            Tab(
                title="Сделки",
                tiles=[
                    Tile(
                        title="Сделки и ЗК",
                        chart_type="table",
                        source={
                            "kind": "halfyear",
                            "group_semantic": "department",
                            "period": "month",
                        },
                        agg="sum",
                        top_n=50,
                        sort="none",
                    )
                ],
            )
        ]
    )
    m_sections = render_spec(df, month_spec)["tabs"][0]["tiles"][0]["sections"]
    assert [b["title"] for b in m_sections] == ["янв 2026", "фев 2026", "апр 2026"]
    assert m_sections[0]["tables"][0]["rows"][0]["values"][0] == 1
    assert m_sections[1]["tables"][0]["rows"][0]["values"][0] == 3


def test_outcome_table_counts_lost_and_cancelled():
    df = pd.DataFrame(
        {
            "подразделение": ["СИО", "СИО", "СТО", "СТО"],
            "статус": ["Проиграна", "Отменена", "Выиграна", "Отменена"],
            "дата начала сделки": pd.to_datetime(
                ["2025-01-10", "2025-02-10", "2025-04-01", "2025-01-20"]
            ),
        }
    )
    spec = DashboardSpec(
        tabs=[
            Tab(
                title="Сделки",
                tiles=[
                    Tile(
                        title="Проигранные и отменённые сделки",
                        chart_type="table",
                        source={
                            "kind": "outcome",
                            "group_semantic": "department",
                            "period": "quarter",
                        },
                        agg="count",
                        top_n=50,
                        sort="none",
                    )
                ],
            )
        ]
    )
    table = render_spec(df, spec)["tabs"][0]["tiles"][0]["table"]
    assert table["year_spans"] == [
        {"label": "1 кв 2025", "count": 5},
        {"label": "2 кв 2025", "count": 5},
    ]
    assert table["columns"][:5] == ["Все сделки", "Проиграны", "Отменены", "Всего (П+О)", "%"]
    by_name = {row["label"]: row["values"] for row in table["rows"]}
    assert by_name["СИО"] == [2, 1, 1, 2, 100, 0, 0, 0, 0, 0]
    assert by_name["СТО"] == [1, 0, 1, 1, 100, 1, 0, 0, 0, 0]
    assert table["totals_label"] == "Совтест"
    assert table["totals"] == [3, 1, 2, 3, 100, 1, 0, 0, 0, 0]
