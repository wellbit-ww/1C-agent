from services.column_resolver import resolve_semantic_column
from models.dashboard_spec import DashboardSpec, Tab, Tile, TileSource
from services.report_profiles.base_profile import ReportProfile


def build_sales_dashboard_spec(df):
    """Вкладочный дашборд «Воронка / Менеджеры / Клиенты» (эталон 1С)."""
    has_funnel = any(str(c).endswith("(сумма)") for c in df.columns)
    if not has_funnel:
        from services.generic_dashboard import build_generic_spec
        return build_generic_spec(df)

    sum_col = resolve_semantic_column(df, "", semantic="sales", dtype="numeric")
    total = float(df[sum_col].sum()) if sum_col else None

    funnel_tab = Tab(
        title="Воронка",
        tiles=[
            Tile(
                title="Сделки на текущем этапе (сумма)",
                chart_type="hbar",
                source=TileSource(
                    kind="current_stage",
                    columns_pattern="(сумма)",
                    value_semantic="revenue",
                ),
                unit="auto",
                sort="none",
            ),
            Tile(
                title="Сделки на текущем этапе (количество)",
                chart_type="hbar",
                source=TileSource(
                    kind="current_stage",
                    columns_pattern="(сумма)",
                ),
                agg="count",
                unit="auto",
                sort="none",
            ),
            Tile(
                title="Динамика продаж по месяцам",
                chart_type="area",
                source=TileSource(kind="period", period="month", value_semantic="revenue"),
                unit="auto",
                sort="none",
            ),
            Tile(
                title="Распределение сделок по подразделениям",
                chart_type="pie",
                source=TileSource(kind="group", group_semantic="department"),
                agg="count",
                top_n=12,
            ),
        ],
    )

    managers_tab = Tab(
        title="Менеджеры",
        tiles=[
            Tile(
                title="Средний чек по менеджерам",
                chart_type="bar",
                source=TileSource(
                    kind="group", group_semantic="manager", value_semantic="revenue"
                ),
                agg="mean",
                top_n=15,
                unit="auto",
            ),
            Tile(
                title="Сумма по менеджерам",
                chart_type="bar",
                source=TileSource(
                    kind="group", group_semantic="manager", value_semantic="revenue"
                ),
                agg="sum",
                top_n=15,
                unit="auto",
                target_line=total,
            ),
            Tile(
                title="Продажи по менеджерам",
                chart_type="bar",
                source=TileSource(kind="group", group_semantic="manager"),
                agg="count",
                top_n=15,
                unit="auto",
            ),
        ],
    )

    clients_tab = Tab(
        title="Клиенты",
        tiles=[
            Tile(
                title="Сумма по клиентам",
                chart_type="hbar",
                source=TileSource(
                    kind="group", group_semantic="client", value_semantic="revenue"
                ),
                agg="sum",
                top_n=10,
                unit="auto",
            ),
            Tile(
                title="Средний чек по клиентам",
                chart_type="hbar",
                source=TileSource(
                    kind="group", group_semantic="client", value_semantic="revenue"
                ),
                agg="mean",
                top_n=10,
                unit="auto",
            ),
            Tile(
                title="Продажи по клиентам",
                chart_type="hbar",
                source=TileSource(kind="group", group_semantic="client"),
                agg="count",
                top_n=10,
                unit="auto",
            ),
        ],
    )

    return DashboardSpec(tabs=[funnel_tab, managers_tab, clients_tab])


class SalesProfile(ReportProfile):
    """Спека воронки. KPI/графики/инсайты — только через ReportEngine."""

    def get_dashboard_spec(self, df):
        return build_sales_dashboard_spec(df)
