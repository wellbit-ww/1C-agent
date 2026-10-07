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
        idx = labels.index("Новая сделка")
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
        assert labels[-1] == "Новая сделка"
        assert values[-1] == pytest.approx(1026)

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
        assert thru_map["Лид"] == pytest.approx(300)
        assert curr_map["Лид"] == pytest.approx(100)
        assert curr_map["Оплата"] == pytest.approx(200)


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
        assert "2026" not in table["columns"]
        labels = [row["label"] for row in table["rows"]]
        assert labels == ["СИОиАПЛиС", "СТО", "СМЭ", "СООК"]
        by_name = {row["label"]: dict(zip(table["columns"], row["values"])) for row in table["rows"]}
        assert by_name["СИОиАПЛиС"]["1 кв 2025"] == 1
        assert by_name["СИОиАПЛиС"]["2 кв 2025"] == 1
        assert by_name["СИОиАПЛиС"]["2025"] == 2
        assert by_name["СТО"]["1 кв 2026"] == 1
        totals = dict(zip(table["columns"], table["totals"]))
        assert totals["1 кв 2025"] == 2
        assert totals["1 кв 2026"] == 2

    def test_year_column_hidden_for_single_quarter_months(self):
        df = pd.DataFrame(
            {
                "подразделение": ["СТО", "СС", "СТО"],
                "дата начала сделки": ["10.07.2026", "15.08.2026", "01.09.2026"],
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
                            source={"kind": "pivot", "group_semantic": "department", "period": "month"},
                            agg="count",
                            top_n=30,
                            sort="none",
                        )
                    ],
                )
            ]
        )
        table = render_spec(df, spec)["tabs"][0]["tiles"][0]["table"]
        assert table["columns"] == ["07.2026", "08.2026", "09.2026"]
        assert table["year_spans"] == [{"label": "2026", "count": 3}]

    def test_year_column_kept_when_periods_cross_half(self):
        df = pd.DataFrame(
            {
                "подразделение": ["СТО", "СС"],
                "дата начала сделки": ["10.01.2026", "15.07.2026"],
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
        table = render_spec(df, spec)["tabs"][0]["tiles"][0]["table"]
        assert table["columns"] == ["1 кв 2026", "2 кв 2026", "3 кв 2026", "2026"]
        assert table["totals"][-1] == 2

    def test_sales_default_starts_with_deals(self, sales_df):
        from services.report_profiles.sales_profile import build_sales_dashboard_spec

        spec = build_sales_dashboard_spec(sales_df)
        assert spec.tabs[0].title == "Данные"
        assert [t.title for t in spec.tabs] == ["Данные", "Динамика", "Конверсия", "Деньги", "Этапы продаж"]
        assert [tile.source.variant for tile in spec.tabs[4].tiles[:2]] == ["full", "counts"]
        assert spec.tabs[4].tiles[2].source.kind == "in_work_stages"
        assert spec.tabs[4].tiles[0].source.period == "quarter"
        assert spec.tabs[3].tiles[0].source.kind == "deals_money"
        assert spec.tabs[2].tiles[0].source.kind == "deals_conversion"
        assert spec.tabs[2].tiles[0].source.period == "quarter"
        assert spec.tabs[3].tiles[0].source.period == "quarter"
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


def test_deal_statuses_tables_for_quarter():
    df = pd.DataFrame(
        {
            "статус": ["В работе", "Выиграна", "Проиграна", "Отменена"],
            "дата начала сделки": pd.to_datetime(
                ["2026-07-02", "2026-08-03", "2026-09-04", "2026-07-15"]
            ),
            "количество сделок": [10, 4, 1, 5],
            "сумма по сделке": [1000.0, 400.0, 50.0, 200.0],
            "количество зк": [2, 4, 0, 1],
            "сумма зк": [100.0, 80.0, 0.0, 10.0],
        }
    )
    tile = Tile(
        title="Статусы сделок",
        chart_type="table",
        source={"kind": "deal_statuses", "period": "quarter"},
        agg="sum",
        top_n=50,
        sort="none",
    )
    rendered = render_spec(df, DashboardSpec(tabs=[Tab(title="Этапы продаж", tiles=[tile])]))[
        "tabs"
    ][0]["tiles"][0]
    assert rendered["sections"][0]["title"] == "3 кв. 2026"
    full = rendered["sections"][0]["tables"][0]
    assert full["columns"] == ["Сделки", "Сумма", "Заказы", "Сумма"]
    by_status = {row["label"]: row["values"] for row in full["rows"]}
    assert by_status["В работе"] == [10, 1000, 2, 100]
    assert by_status["Проиграна"] == [1, 50, 0, 0]
    assert by_status["Всего"] == [20, 1650, 7, 190]
    counts_tile = Tile(
        title="Сделки и заказы",
        chart_type="table",
        source={"kind": "deal_statuses", "period": "quarter", "variant": "counts"},
        agg="sum",
        top_n=50,
        sort="none",
    )
    counts = render_spec(
        df, DashboardSpec(tabs=[Tab(title="Этапы продаж", tiles=[counts_tile])])
    )["tabs"][0]["tiles"][0]["sections"][0]["tables"][0]
    assert counts["columns"] == ["Сделки", "Заказы"]
    assert {row["label"]: row["values"] for row in counts["rows"]}["Выиграна"] == [4, 4]


def test_in_work_stages_table_for_quarter():
    df = pd.DataFrame(
        {
            "статус": ["В работе", "В работе", "Выиграна"],
            "дата начала сделки": pd.to_datetime(["2026-07-02", "2026-08-03", "2026-07-15"]),
            "количество сделок": [2, 3, 9],
            "сумма по сделке": [100.0, 300.0, 900.0],
            "новая сделка (сумма)": [100.0, 300.0, 900.0],
            "подготовка техрешения (сумма)": [0.0, 300.0, 900.0],
        }
    )
    tile = Tile(
        title="Сделки со статусом В работе",
        chart_type="table",
        source={"kind": "in_work_stages", "period": "quarter"},
        agg="sum",
        top_n=50,
        sort="none",
    )
    table = render_spec(df, DashboardSpec(tabs=[Tab(title="Этапы продаж", tiles=[tile])]))[
        "tabs"
    ][0]["tiles"][0]["sections"][0]
    assert table["title"] == "Сделки со статусом В работе (3 кв. 2026)"
    rows = {row["label"]: row["values"] for row in table["tables"][0]["rows"]}
    assert rows["Новая сделка"] == [2, 100, 40, 25]
    assert rows["Подготовка техрешения"] == [3, 300, 60, 75]
    assert rows["Всего"] == [5, 400, 100, 100]
    assert "Выиграна" not in rows


def test_in_work_stages_top_department_breakdowns():
    df = pd.DataFrame(
        {
            "подразделение": [
                "Отдел АПЛиС",
                "Отдел внутрисхемного контроля",
                "Служба микроэлектроники",
                "Отдел АПЛиС",
                "Отдел неразрушающего контроля",
                "Служба технологического оборудования",
            ],
            "статус": ["В работе"] * 6,
            "дата начала сделки": pd.to_datetime(["2026-07-01"] * 6),
            "количество сделок": [5, 3, 2, 4, 1, 6],
            "сумма по сделке": [500.0, 300.0, 200.0, 400.0, 100.0, 600.0],
            "новая сделка (количество)": [1, 0, 0, 1, 0, 0],
            "новая сделка (сумма)": [100.0, 0, 0, 100.0, 0, 0],
            "подготовка техрешения (количество)": [0, 1, 0, 0, 0, 0],
            "подготовка техрешения (сумма)": [0, 300.0, 0, 0, 0, 0],
            "формирование предложения (количество)": [0, 0, 1, 0, 0, 0],
            "формирование предложения (сумма)": [0, 0, 200.0, 0, 0, 0],
            "ожидание финансирования (количество)": [0, 0, 0, 0, 1, 0],
            "ожидание финансирования (сумма)": [0, 0, 0, 0, 100.0, 0],
            "согласование кп с контрагентом (количество)": [0, 0, 0, 0, 0, 1],
            "согласование кп с контрагентом (сумма)": [0, 0, 0, 0, 0, 600.0],
        }
    )
    tile = Tile(
        title="Сделки со статусом В работе",
        chart_type="table",
        source={"kind": "in_work_stages", "period": "quarter"},
        agg="sum",
        top_n=50,
        sort="none",
    )
    section = render_spec(df, DashboardSpec(tabs=[Tab(title="Этапы продаж", tiles=[tile])]))[
        "tabs"
    ][0]["tiles"][0]["sections"][0]
    breakdown = section["stage_breakdowns"]
    count_stages = [item["stage"] for item in breakdown["by_count"]]
    sum_stages = [item["stage"] for item in breakdown["by_sum"]]
    assert count_stages == [
        "Новая сделка",
        "Согласование КП с контрагентом",
        "Подготовка техрешения",
    ]
    assert sum_stages == [
        "Новая сделка",
        "Согласование КП с контрагентом",
        "Подготовка техрешения",
    ]
    new_deal = next(item for item in breakdown["by_count"] if item["stage"] == "Новая сделка")
    by_label = {row["label"]: row["values"] for row in new_deal["table"]["rows"]}
    assert by_label["ОАПЛиС"][:2] == [9, 900]
    assert "СИО и АПЛиС" not in by_label
    assert by_label["Всего"][:2] == [9, 900]


def test_in_work_stages_counts_stage_from_quantity_without_sum():
    df = pd.DataFrame(
        {
            "статус": ["В работе", "В работе"],
            "дата начала сделки": pd.to_datetime(["2026-07-02", "2026-08-03"]),
            "количество сделок": [1, 1],
            "сумма по сделке": [None, 200.0],
            "новая сделка (количество)": [1, 1],
            "новая сделка (сумма)": [None, 200.0],
            "подготовка техрешения (количество)": [1, 0],
            "подготовка техрешения (сумма)": [None, 0.0],
        }
    )
    tile = Tile(
        title="Сделки со статусом В работе",
        chart_type="table",
        source={"kind": "in_work_stages", "period": "quarter"},
        agg="sum",
        top_n=50,
        sort="none",
    )
    rows = {
        row["label"]: row["values"]
        for row in render_spec(df, DashboardSpec(tabs=[Tab(title="Этапы продаж", tiles=[tile])]))[
            "tabs"
        ][0]["tiles"][0]["sections"][0]["tables"][0]["rows"]
    }
    assert rows["Подготовка техрешения"][:2] == [1, 0]
    assert rows["Всего"][:2] == [2, 200]
    assert "Не указан" not in rows


def test_normalize_stages_tab_keeps_only_in_work_stages_table(sales_df):
    from services.report_profiles.sales_profile import enrich_deals_tab

    spec = DashboardSpec(
        tabs=[
            Tab(
                title="Этапы продаж",
                tiles=[
                    Tile(
                        title="Статусы сделок",
                        chart_type="table",
                        source={"kind": "deal_statuses", "period": "quarter", "variant": "full"},
                        agg="sum",
                        top_n=50,
                        sort="none",
                    ),
                    Tile(
                        title="Сделки и заказы",
                        chart_type="table",
                        source={"kind": "deal_statuses", "period": "quarter", "variant": "counts"},
                        agg="sum",
                        top_n=50,
                        sort="none",
                    ),
                    Tile(
                        title="Сделки по этапам",
                        chart_type="table",
                        source={"kind": "current_stage", "columns_pattern": "(сумма)"},
                        agg="count",
                        top_n=50,
                        sort="none",
                    ),
                ],
            )
        ]
    )
    enrich_deals_tab(spec, sales_df)
    stages = next(tab for tab in spec.tabs if tab.title == "Этапы продаж")
    assert [tile.source.kind for tile in stages.tiles] == [
        "deal_statuses",
        "deal_statuses",
        "in_work_stages",
    ]
    assert stages.tiles[2].title == "Сделки со статусом В работе"
    rendered = next(tab for tab in render_spec(sales_df, spec)["tabs"] if tab["title"] == "Этапы продаж")
    in_work = rendered["tiles"][2]
    total_row = next(
        row for row in in_work["sections"][0]["tables"][0]["rows"] if row["label"] == "Всего"
    )
    in_work_count = float(total_row["values"][0])
    assert in_work_count < len(sales_df)


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
    assert department_short("Служба технологического оборудования") == "СТЕ"
    assert department_short("Отдел внутрисхемного контроля") == "СТО"
    assert department_short("Отдел неразрушающего контроля") == "СТО"
    assert department_short("Отдел функционального контроля") == "СТО"
    assert department_short("ОВК") == "СТО"
    assert department_short("Служба тестового оборудования") == "СТО"
    assert department_short("Служба оборудования обработки кабеля") == "СООК"
    assert department_short("Служба микроэлектроники") == "СМЭ"
    assert department_short("Отдел АПЛиС") == "ОАПЛиС"
    assert department_short("СИО") == "СИО"
    assert department_short("Сервисная служба") == "Сервис"
    assert department_short("СС") == "Сервис"


def _stages_deal_list_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "номер сделки": ["УП-00000001", "УП-00000002", "УП-00000003"],
            "статус": ["В работе", "Выиграна", "В работе"],
            "ответственный": ["Иванов", "Петров", "Иванов"],
            "подразделение": ["СТО", "СТЕ", "СТО"],
            "компания": ["Альфа", "Бета", "Гамма"],
            "сумма по сделке": [100.0, 50.0, 400.0],
            "новая сделка (сумма)": [100.0, 50.0, 0.0],
            "подготовка техрешения (сумма)": [0.0, 0.0, 400.0],
        }
    )


def test_stages_deal_list_default_top_stage():
    from services.dashboard_engine import default_top_stage_label, _named_column, _stage_amount_columns

    df = _stages_deal_list_df()
    deal_s = _named_column(df, "сумма по сделке")
    cols = _stage_amount_columns(df)
    assert default_top_stage_label(df, cols, deal_s) == "Подготовка техрешения"


def test_stages_deal_list_filters():
    df = _stages_deal_list_df()
    tile = Tile(
        title="Список сделок",
        chart_type="table",
        source={
            "kind": "stages_deal_list",
            "stages": ["Подготовка техрешения"],
            "departments": ["СТО"],
            "managers": ["Иванов"],
            "min_potential": 200,
        },
        agg="sum",
        top_n=50,
        sort="none",
    )
    payload = render_spec(df, DashboardSpec(tabs=[Tab(title="Этапы продаж", tiles=[tile])]))[
        "tabs"
    ][0]["tiles"][0]
    assert payload["chart_type"] == "stages_deal_list"
    assert payload["deals_list"]["total_matched"] == 1
    assert payload["deals_list"]["rows"][0]["cells"][0] == "УП-00000003"
    assert payload["deals_list"]["rows"][0]["cells"][1] == "Гамма"
    assert payload["deals_list"]["rows"][0]["cells"][6] == 400


def test_stages_deal_list_status_filter_and_sort():
    df = _stages_deal_list_df()
    tile = Tile(
        title="Список сделок",
        chart_type="table",
        source={
            "kind": "stages_deal_list",
            "stages": ["Новая сделка", "Подготовка техрешения"],
            "statuses": ["Выиграна"],
        },
        agg="sum",
        top_n=50,
        sort="none",
    )
    payload = render_spec(df, DashboardSpec(tabs=[Tab(title="Этапы продаж", tiles=[tile])]))[
        "tabs"
    ][0]["tiles"][0]
    assert payload["deals_list"]["total_matched"] == 1
    assert payload["deals_list"]["rows"][0]["cells"][0] == "УП-00000002"
    assert payload["deals_list"]["rows"][0]["cells"][1] == "Бета"

    tile.source.list_sort_column = "Сделка"
    tile.sort = "asc"
    payload2 = render_spec(df, DashboardSpec(tabs=[Tab(title="Этапы продаж", tiles=[tile])]))[
        "tabs"
    ][0]["tiles"][0]
    names = [row["cells"][1] for row in payload2["deals_list"]["rows"]]
    assert names == sorted(names, key=lambda item: str(item).casefold())


def test_ensure_stages_deal_list_tile_and_normalize_preserve_filters(sales_df):
    from services.report_profiles.sales_profile import (
        ensure_stages_deal_list_tile,
        enrich_deals_tab,
    )

    spec = DashboardSpec(tabs=[Tab(title="Этапы продаж", tiles=[])])
    ensure_stages_deal_list_tile(spec, sales_df)
    assert len(spec.tabs[0].tiles) == 1
    assert spec.tabs[0].tiles[0].source.kind == "stages_deal_list"
    assert spec.tabs[0].tiles[0].source.stages

    spec.tabs[0].tiles[0].source.min_potential = 1_000_000
    enrich_deals_tab(spec, sales_df)
    stages = next(tab for tab in spec.tabs if tab.title == "Этапы продаж")
    deal_list = next(tile for tile in stages.tiles if tile.source.kind == "stages_deal_list")
    assert deal_list.source.min_potential == 1_000_000
    assert len(stages.tiles) == 4


def test_stages_tab_period_prefers_statuses_tile_over_in_work():
    from models.dashboard_spec import Tab, Tile, TileSource
    from services.report_profiles.sales_profile import _normalize_stages_tab, _stages_tab_period

    tiles = [
        Tile(
            title="Статусы сделок",
            chart_type="table",
            source=TileSource(kind="deal_statuses", period="half", variant="full"),
            agg="sum",
            top_n=50,
            sort="none",
        ),
        Tile(
            title="Сделки и заказы",
            chart_type="table",
            source=TileSource(kind="deal_statuses", period="quarter", variant="counts"),
            agg="sum",
            top_n=50,
            sort="none",
        ),
        Tile(
            title="В работе",
            chart_type="table",
            source=TileSource(kind="in_work_stages", period="quarter"),
            agg="sum",
            top_n=50,
            sort="none",
        ),
    ]
    assert _stages_tab_period(tiles) == "half"
    tab = Tab(title="Этапы продаж", tiles=tiles)
    _normalize_stages_tab(tab)
    assert [t.source.period for t in tab.tiles] == ["half", "half", "half"]


def test_enrich_migrates_halfyear_tile_title(sales_df):
    from services.generic_dashboard import SALES_HALFYEAR_TILE_TITLE
    from services.report_profiles.sales_profile import enrich_deals_tab

    spec = DashboardSpec(
        tabs=[
            Tab(
                title="Данные",
                tiles=[
                    Tile(
                        title="Сделки и ЗК по полугодиям",
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
    enrich_deals_tab(spec, sales_df)
    halfyear = next(
        tile
        for tab in spec.tabs
        for tile in tab.tiles
        if tile.source.kind == "halfyear"
    )
    assert halfyear.title == SALES_HALFYEAR_TILE_TITLE


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
    assert payload["table"]["columns"][-1] == "2025"
    q2 = next(i for i, c in enumerate(payload["table"]["columns"]) if "2 кв" in c)
    assert payload["table"]["rows"][0]["values"][q2] == 100
    assert payload["table"]["rows"][0]["values"][-1] == 50
    fig = json.loads(payload["plotly_json"])
    assert fig["layout"]["title"]["text"] == "ДОЛЯ ОТМЕНЫ/ПРОИГРЫША СДЕЛОК"
    assert [trace["name"] for trace in fig["data"]] == payload["table"]["columns"][:-1]


def test_department_filter_limits_company_and_rows():
    df = pd.DataFrame(
        {
            "подразделение": [
                "Служба испытательного оборудования",
                "Служба технологического оборудования",
            ],
            "дата начала сделки": pd.to_datetime(["2026-02-01", "2026-03-01"]),
            "количество сделок": [2, 5],
            "сумма по сделке": [10.0, 100.0],
            "количество зк": [1, 1],
            "сумма зк": [4.0, 40.0],
        }
    )
    tile = Tile(
        title="Финансовые показатели сделок",
        chart_type="table",
        source={
            "kind": "deals_money",
            "group_semantic": "department",
            "period": "half",
            "departments": ["СИО"],
        },
        agg="sum",
        top_n=50,
        sort="desc",
    )
    payload = render_spec(df, DashboardSpec(tabs=[Tab(title="Деньги", tiles=[tile])]))["tabs"][0][
        "tiles"
    ][0]
    rows = {row["label"]: row["values"][0] for row in payload["money"]["metrics"][0]["rows"]}
    assert rows == {"Совтест": 10, "СИО": 10}
    assert payload["departments"] == ["СИО", "СТЕ"]


def test_control_departments_merge_into_test_equipment():
    df = pd.DataFrame(
        {
            "подразделение": [
                "Отдел внутрисхемного контроля",
                "Отдел неразрушающего контроля",
                "Отдел функционального контроля",
                "Служба технологического оборудования",
            ],
            "дата начала сделки": pd.to_datetime(["2026-02-01"] * 4),
            "количество сделок": [1, 1, 1, 5],
            "сумма по сделке": [10.0, 20.0, 30.0, 100.0],
            "количество зк": [1, 0, 1, 1],
            "сумма зк": [5.0, 0.0, 7.0, 40.0],
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
    rows = render_spec(df, DashboardSpec(tabs=[Tab(title="Деньги", tiles=[tile])]))["tabs"][0][
        "tiles"
    ][0]["money"]["metrics"][0]["rows"]
    by_label = {row["label"]: row["values"][0] for row in rows}
    assert by_label["СТО"] == 60
    assert by_label["СТЕ"] == 100
    assert "ОВК" not in by_label
    assert "ОНК" not in by_label
    assert "ОФК" not in by_label


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
    assert by_label["СТЕ"] == [100, 200]
    assert by_label["Сервис"] == [10, 40]
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
    assert set(labels) == {"Совтест", "Сервис", "СООК", "СМЭ"}
    by_label = {row["label"]: row["values"][-1] for row in payload["table"]["rows"]}
    assert by_label["Совтест"] == 50
    assert by_label["Сервис"] == 100
    assert by_label["СООК"] == 0
    assert payload["table"]["column_kinds"][0] == "percent"
    fig = json.loads(payload["plotly_json"])
    assert fig["data"][0]["x"] == ["Сервис", "Совтест", "СООК", "СМЭ"]
    assert fig["data"][0]["marker"]["color"] == ["#548235", "#C00000", "#5B9BD5", "#5B9BD5"]
    assert fig["layout"]["title"]["text"] == "КОНВЕРСИЯ СДЕЛОК В ЗАКАЗЫ (1 ПОЛУГ. 2026)"
    assert len(payload["period_charts"]) == len(payload["table"]["columns"])


def test_chart_title_notes_excluded_departments():
    df = pd.DataFrame(
        {
            "подразделение": [
                "Служба испытательного оборудования",
                "Служба технологического оборудования",
            ],
            "дата начала сделки": pd.to_datetime(["2026-02-01", "2026-03-01"]),
            "количество сделок": [2, 5],
            "количество зк": [1, 1],
        }
    )
    tile = Tile(
        title="Конверсия",
        chart_type="table",
        source={
            "kind": "deals_conversion",
            "group_semantic": "department",
            "period": "half",
            "departments": ["СИО"],
        },
        agg="sum",
        top_n=50,
        sort="desc",
    )
    payload = render_spec(df, DashboardSpec(tabs=[Tab(title="T", tiles=[tile])]))["tabs"][0][
        "tiles"
    ][0]
    fig = json.loads(payload["plotly_json"])
    assert fig["layout"]["title"]["text"] == "КОНВЕРСИЯ СДЕЛОК В ЗАКАЗЫ (1 ПОЛУГ. 2026) (БЕЗ СТЕ)"


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
    assert payload["table"]["columns"][-1] == "2025"
    fig = json.loads(payload["plotly_json"])
    assert [trace["name"] for trace in fig["data"]] == payload["table"]["columns"][:-1]
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
    assert payload["table"]["columns"][-1] == "2025"
    assert len(payload["table"]["rows"]) == 1
    assert payload["table"]["rows"][0]["label"] == "Совтест"
    assert payload["table"]["rows"][0]["values"][0] == 1
    assert payload["table"]["rows"][0]["values"][-1] == 5
    assert payload.get("plotly_json")
    fig = json.loads(payload["plotly_json"])
    assert fig["data"][0]["x"] == ["1 кв 2025", "2 кв 2025", "3 кв 2025"]
    assert fig["layout"].get("autosize") is False
    assert all(t.get("type") != "scatter" for t in fig["data"])


def test_dynamics_month_table_total_is_file_quarter_and_not_on_chart():
    df = pd.DataFrame(
        {
            "подразделение": ["Служба технологического оборудования"] * 3,
            "дата начала сделки": pd.to_datetime(["2025-10-05", "2025-11-10", "2025-12-15"]),
            "статус": ["Выиграна", "Проиграна", "Отменена"],
            "количество сделок": [2, 1, 1],
        }
    )
    tiles = [
        Tile(
            title=title,
            chart_type="table",
            source={"kind": kind, "group_semantic": "department", "period": "month"},
            agg="sum",
            top_n=10,
            sort="desc",
        )
        for title, kind in (
            ("Количество сделок по периодам", "deals_dynamics"),
            ("Сделки по подразделениям", "deals_dynamics_departments"),
            ("Доля отмены и проигрыша сделок", "deals_dynamics_outcome_share"),
        )
    ]
    rendered = render_spec(df, DashboardSpec(tabs=[Tab(title="Динамика", tiles=tiles)]))["tabs"][0][
        "tiles"
    ]
    company, departments, share = rendered
    assert company["table"]["columns"] == ["10.2025", "11.2025", "12.2025", "4 кв 2025"]
    assert company["table"]["rows"][0]["values"] == [2, 1, 1, 4]
    company_fig = json.loads(company["plotly_json"])
    assert company_fig["data"][0]["x"] == ["10.2025", "11.2025", "12.2025"]

    assert departments["table"]["columns"][-1] == "4 кв 2025"
    assert departments["table"]["rows"][0]["values"][-1] == 4
    dept_fig = json.loads(departments["plotly_json"])
    assert [trace["name"] for trace in dept_fig["data"]] == ["10.2025", "11.2025", "12.2025"]

    assert share["table"]["columns"][-1] == "4 кв 2025"
    assert share["table"]["rows"][0]["values"][-1] == 50
    share_fig = json.loads(share["plotly_json"])
    assert [trace["name"] for trace in share_fig["data"]] == ["10.2025", "11.2025", "12.2025"]


def test_outcome_share_chart_ranks_departments_by_share_desc():
    df = pd.DataFrame(
        {
            "подразделение": [
                "Служба микроэлектроники",
                "Служба микроэлектроники",
                "Сервисная служба",
            ],
            "дата начала сделки": pd.to_datetime(["2025-10-01", "2025-11-01", "2025-10-05"]),
            "статус": ["Выиграна", "Выиграна", "Проиграна"],
            "количество сделок": [5, 5, 1],
        }
    )
    tile = Tile(
        title="Доля отмены и проигрыша сделок",
        chart_type="table",
        source={
            "kind": "deals_dynamics_outcome_share",
            "group_semantic": "department",
            "period": "month",
        },
        agg="sum",
        top_n=10,
        sort="desc",
    )
    payload = render_spec(df, DashboardSpec(tabs=[Tab(title="Динамика", tiles=[tile])]))["tabs"][0][
        "tiles"
    ][0]
    assert [row["label"] for row in payload["table"]["rows"]] == ["Совтест", "СМЭ", "Сервис"]
    fig = json.loads(payload["plotly_json"])
    assert list(fig["data"][0]["x"]) == ["Сервис", "Совтест", "СМЭ"]
    by_label = {row["label"]: row["values"][-1] for row in payload["table"]["rows"]}
    assert [by_label[name] for name in fig["data"][0]["x"]] == [100, 9, 0]


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
                        title="Сделки и ЗК в заданный период",
                        chart_type="table",
                        source={
                            "kind": "halfyear",
                            "group_semantic": "department",
                            "period": "half",
                        },
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
