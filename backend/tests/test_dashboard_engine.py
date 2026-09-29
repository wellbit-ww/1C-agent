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
        assert [t.title for t in spec.tabs] == ["Данные", "Динамика", "Конверсия", "Деньги"]
        assert spec.tabs[3].tiles[0].source.kind == "deals_money"
        assert spec.tabs[2].tiles[0].source.kind == "deals_conversion"
        assert spec.tabs[2].tiles[0].source.period == "half"
        assert spec.tabs[1].tiles[0].source.kind == "deals_dynamics"
        assert spec.tabs[1].tiles[1].source.kind == "deals_dynamics_departments"
        assert spec.tabs[1].tiles[2].source.kind == "deals_dynamics_outcome_share"
        rendered = render_spec(sales_df, spec)
        dynamics = rendered["tabs"][1]["tiles"][0]
        assert dynamics["chart_type"] == "deals_dynamics"
        assert dynamics.get("plotly_json")
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


def test_enrich_dynamics_replaces_pivot_tile(sales_df):
    from services.report_profiles.sales_profile import enrich_deals_tab

    spec = DashboardSpec(
        tabs=[
            Tab(title="Данные", tiles=[]),
            Tab(
                title="Динамика",
                tiles=[
                    Tile(
                        title="Количество сделок по периодам",
                        chart_type="table",
                        source={
                            "kind": "pivot",
                            "group_semantic": "department",
                            "period": "month",
                        },
                        agg="count",
                        top_n=50,
                        sort="desc",
                    )
                ],
            ),
        ]
    )
    enrich_deals_tab(spec, sales_df)
    assert len(spec.tabs[1].tiles) == 3
    assert spec.tabs[1].tiles[0].source.kind == "deals_dynamics"
    assert spec.tabs[1].tiles[1].source.kind == "deals_dynamics_departments"
    assert spec.tabs[1].tiles[2].source.kind == "deals_dynamics_outcome_share"
    assert spec.tabs[1].tiles[0].source.period == "month"
    assert spec.tabs[1].tiles[1].source.period == "month"
    rendered = render_spec(sales_df, spec)["tabs"][1]["tiles"][0]
    assert len(rendered["table"]["rows"]) == 1
    assert rendered["table"]["rows"][0]["label"] == "Совтест"
    dept_tile = render_spec(sales_df, spec)["tabs"][1]["tiles"][1]
    assert dept_tile["chart_type"] == "deals_dynamics_departments"
    assert len(dept_tile["table"]["rows"]) > 1
    assert dept_tile.get("plotly_json")


def test_deals_dynamics_outcome_share_table_and_chart():
    df = pd.DataFrame(
        {
            "подразделение": ["Служба технологического оборудования"] * 4,
            "дата начала сделки": pd.to_datetime(
                ["2025-01-15", "2025-04-10", "2025-04-12", "2025-07-20"]
            ),
            "статус": ["В работе", "Проиграна", "Отменена", "Выиграна"],
            "количество сделок": [1, 1, 1, 1],
            "сумма по сделке": [10.0, 10.0, 10.0, 10.0],
        }
    )
    tile = Tile(
        title="Доля отмены и проигрыша сделок",
        chart_type="table",
        source={
            "kind": "deals_dynamics_outcome_share",
            "group_semantic": "department",
            "period": "quarter",
        },
        agg="sum",
        top_n=10,
        sort="desc",
    )
    out = render_spec(df, DashboardSpec(tabs=[Tab(title="Динамика", tiles=[tile])]))
    payload = out["tabs"][0]["tiles"][0]
    assert payload["chart_type"] == "deals_dynamics_outcome_share"
    assert payload["table"]["rows"][0]["label"] == "Совтест"
    assert payload["table"]["column_kinds"][0] == "percent"
    q2 = next(i for i, c in enumerate(payload["table"]["columns"]) if "2 кв" in c)
    assert payload["table"]["rows"][0]["values"][q2] == 100
    fig = json.loads(payload["plotly_json"])
    assert fig["layout"]["title"]["text"] == "ДОЛЯ ОТМЕНЫ/ПРОИГРЫША СДЕЛОК"


def test_deals_money_compares_same_period_last_year():
    df = pd.DataFrame(
        {
            "подразделение": ["Служба технологического оборудования"] * 2
            + ["Сервисная служба"] * 2,
            "дата начала сделки": pd.to_datetime(
                ["2025-02-01", "2026-03-01", "2025-02-10", "2026-04-01"]
            ),
            "количество сделок": [1, 1, 1, 1],
            "сумма по сделке": [100.0, 200.0, 10.0, 40.0],
            "сумма зк": [50.0, 80.0, 10.0, 30.0],
        }
    )
    tile = Tile(
        title="Финансовые показатели сделок",
        chart_type="table",
        source={"kind": "deals_money", "group_semantic": "department", "period": "half"},
        agg="sum",
        top_n=50,
        sort="desc",
    )
    payload = render_spec(df, DashboardSpec(tabs=[Tab(title="Деньги", tiles=[tile])]))["tabs"][0][
        "tiles"
    ][0]
    assert payload["chart_type"] == "deals_money"
    periods = payload["money"]["periods"]
    assert [item["label"] for item in periods] == ["1 полуг. 2025", "1 полуг. 2026"]
    potential = payload["money"]["metrics"][0]
    assert potential["title"] == "Потенциал сделок"
    by_label = {row["label"]: row["values"] for row in potential["rows"]}
    assert by_label["Совтест"] == [110, 240]
    assert by_label["СТО"] == [100, 200]
    assert by_label["СС"] == [10, 40]
    orders = payload["money"]["metrics"][1]
    assert orders["rows"][0]["values"] == [60, 110]


def test_deals_conversion_includes_company_and_every_department():
    df = pd.DataFrame(
        {
            "подразделение": [
                "Сервисная служба",
                "Сервисная служба",
                "Служба оборудования обработки кабеля",
                "Служба микроэлектроники",
            ],
            "дата начала сделки": pd.to_datetime(
                ["2026-02-01", "2026-03-01", "2026-04-01", "2026-05-01"]
            ),
            "количество сделок": [1, 1, 1, 1],
            "количество зк": [1, 1, 0, 0],
        }
    )
    tile = Tile(
        title="Конверсия сделок в заказы",
        chart_type="table",
        source={
            "kind": "deals_conversion",
            "group_semantic": "department",
            "period": "half",
        },
        agg="sum",
        top_n=50,
        sort="desc",
    )
    payload = render_spec(df, DashboardSpec(tabs=[Tab(title="Конверсия", tiles=[tile])]))[
        "tabs"
    ][0]["tiles"][0]
    assert payload["chart_type"] == "deals_conversion"
    labels = [row["label"] for row in payload["table"]["rows"]]
    assert labels[0] == "Совтест"
    assert set(labels) == {"Совтест", "СС", "СООК", "СМЭ"}
    by_label = {row["label"]: row["values"][-1] for row in payload["table"]["rows"]}
    assert by_label["Совтест"] == 50
    assert by_label["СС"] == 100
    assert by_label["СООК"] == 0
    assert payload["table"]["column_kinds"][0] == "percent"
    fig = json.loads(payload["plotly_json"])
    assert fig["data"][0]["x"][0] == "СС"
    assert fig["layout"]["title"]["text"] == "Конверсия сделок в заказы (1 полуг. 2026)"
    assert len(payload["period_charts"]) == len(payload["table"]["columns"])


def test_deals_dynamics_departments_grouped_chart():
    df = pd.DataFrame(
        {
            "подразделение": [
                "Служба испытательного оборудования",
                "Служба испытательного оборудования",
                "Служба технологического оборудования",
                "Отдел АПЛиС",
            ],
            "дата начала сделки": pd.to_datetime(
                ["2025-01-15", "2025-04-10", "2025-04-12", "2025-07-20"]
            ),
            "количество сделок": [1, 1, 2, 3],
            "сумма по сделке": [10.0, 10.0, 20.0, 30.0],
        }
    )
    tile = Tile(
        title="Сделки по подразделениям",
        chart_type="table",
        source={
            "kind": "deals_dynamics_departments",
            "group_semantic": "department",
            "period": "quarter",
        },
        agg="sum",
        top_n=10,
        sort="desc",
    )
    out = render_spec(df, DashboardSpec(tabs=[Tab(title="Динамика", tiles=[tile])]))
    payload = out["tabs"][0]["tiles"][0]
    assert payload["chart_type"] == "deals_dynamics_departments"
    assert len(payload["table"]["rows"]) >= 2
    fig = json.loads(payload["plotly_json"])
    assert len(fig["data"]) == len(payload["table"]["columns"])
    assert fig["layout"]["barmode"] == "group"


def test_deals_dynamics_table_and_chart():
    df = pd.DataFrame(
        {
            "подразделение": ["Служба испытательного оборудования"] * 3 + ["Отдел АПЛиС"],
            "дата начала сделки": pd.to_datetime(
                ["2025-01-15", "2025-04-10", "2025-07-20", "2025-04-12"]
            ),
            "количество сделок": [1, 1, 1, 2],
            "сумма по сделке": [10.0, 10.0, 10.0, 20.0],
        }
    )
    tile = Tile(
        title="Количество сделок по периодам",
        chart_type="table",
        source={"kind": "deals_dynamics", "group_semantic": "department", "period": "quarter"},
        agg="sum",
        top_n=10,
        sort="desc",
    )
    out = render_spec(df, DashboardSpec(tabs=[Tab(title="Динамика", tiles=[tile])]))
    payload = out["tabs"][0]["tiles"][0]
    assert payload["chart_type"] == "deals_dynamics"
    assert "1 кв 2025" in payload["table"]["columns"]
    assert len(payload["table"]["rows"]) == 1
    assert payload["table"]["rows"][0]["label"] == "Совтест"
    assert payload["table"]["rows"][0]["values"][0] == 1
    assert payload.get("plotly_json")
    fig = json.loads(payload["plotly_json"])
    assert fig["layout"].get("autosize") is False
    assert all(t.get("type") != "scatter" for t in fig["data"])


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
