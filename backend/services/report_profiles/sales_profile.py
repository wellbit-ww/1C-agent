from services.column_resolver import resolve_semantic_column
from models.dashboard_spec import DashboardSpec, Tab, Tile, TileSource
from services.report_profiles.base_profile import ReportProfile


def ensure_outcome_tile(spec: DashboardSpec) -> DashboardSpec:
    """На вкладке «Сделки» таблица проигранных и отменённых стоит после сводки и круговой."""
    for tab in spec.tabs:
        if tab.title != "Сделки":
            continue
        if any(tile.source.kind == "outcome" for tile in tab.tiles):
            return spec
        if len(tab.tiles) >= 8:
            return spec
        tab.tiles.insert(
            min(2, len(tab.tiles)),
            Tile(
                title="Проигранные и отменённые сделки",
                chart_type="table",
                source=TileSource(
                    kind="outcome",
                    group_semantic="department",
                    period="quarter",
                ),
                agg="count",
                top_n=50,
                sort="none",
            ),
        )
        return spec
    return spec


def ensure_halfyear_tile(spec: DashboardSpec) -> DashboardSpec:
    """Третья таблица вкладки «Сделки»: сделки и ЗК по полугодиям."""
    for tab in spec.tabs:
        if tab.title != "Сделки":
            continue
        if any(tile.source.kind == "halfyear" for tile in tab.tiles):
            return spec
        if len(tab.tiles) >= 8:
            return spec
        tile = Tile(
            title="Сделки и ЗК по полугодиям",
            chart_type="table",
            source=TileSource(kind="halfyear", group_semantic="department"),
            agg="sum",
            top_n=50,
            sort="none",
        )
        after = next((i for i, item in enumerate(tab.tiles) if item.source.kind == "outcome"), None)
        tab.tiles.insert(len(tab.tiles) if after is None else after + 1, tile)
        return spec
    return spec


def build_sales_dashboard_spec(df):
    """Вкладочный дашборд «Воронка / Менеджеры / Клиенты» (эталон 1С)."""
    from services.generic_dashboard import build_deals_tab, build_generic_spec

    has_funnel = any(str(c).endswith("(сумма)") for c in df.columns)
    if not has_funnel:
        return build_generic_spec(df)

    deals = build_deals_tab(df)

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
        ],
    )

    mgr_col = resolve_semantic_column(df, "", semantic="manager", dtype="categorical")
    mean_line = None
    if mgr_col and sum_col:
        mean_line = float(df.groupby(mgr_col)[sum_col].sum().mean())

    managers_tab = Tab(
        title="Менеджеры",
        tiles=[
            Tile(
                title="Средний чек по менеджерам",
                chart_type="hbar",
                source=TileSource(
                    kind="group", group_semantic="manager", value_semantic="revenue"
                ),
                agg="mean",
                top_n=15,
                unit="auto",
            ),
            Tile(
                title="Сумма по менеджерам",
                chart_type="hbar",
                source=TileSource(
                    kind="group", group_semantic="manager", value_semantic="revenue"
                ),
                agg="sum",
                top_n=15,
                unit="auto",
                target_line=mean_line,
            ),
            Tile(
                title="Продажи по менеджерам",
                chart_type="hbar",
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

    tabs = [deals, funnel_tab, managers_tab, clients_tab] if deals else [
        funnel_tab,
        managers_tab,
        clients_tab,
    ]
    return DashboardSpec(tabs=tabs)


class SalesProfile(ReportProfile):
    """Спека воронки. KPI/графики/инсайты — только через ReportEngine."""

    def get_dashboard_spec(self, df):
        return build_sales_dashboard_spec(df)
