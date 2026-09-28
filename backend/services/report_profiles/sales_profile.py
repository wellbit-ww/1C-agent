import pandas as pd

from models.dashboard_spec import DashboardSpec, Tab, Tile, TileSource
from services.generic_dashboard import SALES_DATA_TAB_TITLE, SALES_DYNAMICS_TAB_TITLE
from services.report_profiles.base_profile import ReportProfile

_LEGACY_SALES_DATA_TAB_TITLE = "Сделки"
_REMOVED_SALES_TABS = frozenset({"Воронка", "Менеджеры", "Клиенты"})


def _is_sales_data_tab(tab: Tab) -> bool:
    return tab.title in (SALES_DATA_TAB_TITLE, _LEGACY_SALES_DATA_TAB_TITLE)


def _dynamics_deals_tile() -> Tile:
    return Tile(
        title="Количество сделок по периодам",
        chart_type="table",
        source=TileSource(
            kind="deals_dynamics",
            group_semantic="department",
            period="quarter",
        ),
        agg="sum",
        top_n=50,
        sort="desc",
    )


def _dynamics_departments_tile() -> Tile:
    return Tile(
        title="Сделки по подразделениям",
        chart_type="table",
        source=TileSource(
            kind="deals_dynamics_departments",
            group_semantic="department",
            period="quarter",
        ),
        agg="sum",
        top_n=50,
        sort="desc",
    )


def _dynamics_outcome_share_tile() -> Tile:
    return Tile(
        title="Доля отмены и проигрыша сделок",
        chart_type="table",
        source=TileSource(
            kind="deals_dynamics_outcome_share",
            group_semantic="department",
            period="quarter",
        ),
        agg="sum",
        top_n=50,
        sort="desc",
    )


def _dynamics_tab() -> Tab:
    return Tab(
        title=SALES_DYNAMICS_TAB_TITLE,
        tiles=[
            _dynamics_deals_tile(),
            _dynamics_departments_tile(),
            _dynamics_outcome_share_tile(),
        ],
    )


def _sales_pipeline_tabs(spec: DashboardSpec, df: pd.DataFrame | None = None) -> DashboardSpec:
    """«Данные» + пустая «Динамика»; убрать устаревшие вкладки Воронка/Менеджеры/Клиенты."""
    data_tab: Tab | None = None
    dynamics_tab: Tab | None = None
    for tab in spec.tabs:
        if tab.title in _REMOVED_SALES_TABS:
            continue
        if tab.title == SALES_DYNAMICS_TAB_TITLE:
            dynamics_tab = tab
            continue
        if _is_sales_data_tab(tab):
            tab.title = SALES_DATA_TAB_TITLE
            data_tab = tab
    if data_tab is None and df is not None:
        from services.generic_dashboard import build_deals_tab

        data_tab = build_deals_tab(df)
    if dynamics_tab is None:
        dynamics_tab = _dynamics_tab()
    elif not dynamics_tab.tiles:
        dynamics_tab.tiles = [
            _dynamics_deals_tile(),
            _dynamics_departments_tile(),
            _dynamics_outcome_share_tile(),
        ]
    if data_tab is not None:
        spec.tabs = [data_tab, dynamics_tab]
    elif dynamics_tab is not None:
        spec.tabs = [dynamics_tab]
    return spec


def _drop_legacy_deals_pie(spec: DashboardSpec) -> DashboardSpec:
    """Убрать устаревшую круговую по подразделениям (больше не в дефолтной спеке)."""
    for tab in spec.tabs:
        if not _is_sales_data_tab(tab):
            continue
        tab.tiles = [
            tile
            for tile in tab.tiles
            if tile.title != "Распределение сделок по подразделениям"
        ]
    return spec


def ensure_outcome_tile(spec: DashboardSpec) -> DashboardSpec:
    """На вкладке «Сделки» таблица проигранных и отменённых — после сводной по кварталам."""
    for tab in spec.tabs:
        if not _is_sales_data_tab(tab):
            continue
        tab.title = SALES_DATA_TAB_TITLE
        if any(tile.source.kind == "outcome" for tile in tab.tiles):
            return spec
        if len(tab.tiles) >= 8:
            return spec
        tab.tiles.insert(
            min(1, len(tab.tiles)),
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


def ensure_dynamics_deals_tile(spec: DashboardSpec) -> DashboardSpec:
    """Вкладка «Динамика»: Совтест + подразделения (без устаревшего pivot)."""
    for tab in spec.tabs:
        if tab.title != SALES_DYNAMICS_TAB_TITLE:
            continue
        period = "quarter"
        for tile in tab.tiles:
            raw = tile.source.period
            if raw in ("month", "quarter", "half"):
                period = raw
            if tile.source.kind in (
                "deals_dynamics",
                "deals_dynamics_departments",
                "deals_dynamics_outcome_share",
            ):
                if tile.source.period in ("month", "quarter", "half"):
                    period = tile.source.period
        company = _dynamics_deals_tile()
        departments = _dynamics_departments_tile()
        outcome = _dynamics_outcome_share_tile()
        company.source.period = period
        departments.source.period = period
        outcome.source.period = period
        tab.tiles = [company, departments, outcome]
        return spec
    return spec


def enrich_deals_tab(spec: DashboardSpec, df: pd.DataFrame | None = None) -> DashboardSpec:
    """Вкладки «Данные» и «Динамика» для этапов продаж."""
    spec = _sales_pipeline_tabs(spec, df)
    spec = _drop_legacy_deals_pie(spec)
    spec = ensure_status_summary_tile(ensure_halfyear_tile(ensure_outcome_tile(spec)))
    return ensure_dynamics_deals_tile(spec)


def ensure_status_summary_tile(spec: DashboardSpec) -> DashboardSpec:
    """Последняя таблица вкладки «Сделки»: сделки и ЗК по статусам."""
    for tab in spec.tabs:
        if not _is_sales_data_tab(tab):
            continue
        tab.title = SALES_DATA_TAB_TITLE
        if any(tile.source.kind == "status_summary" for tile in tab.tiles):
            return spec
        if len(tab.tiles) >= 8:
            return spec
        tab.tiles.append(
            Tile(
                title="Сделки и ЗК по статусам",
                chart_type="table",
                source=TileSource(kind="status_summary"),
                agg="sum",
                top_n=50,
                sort="none",
            )
        )
        return spec
    return spec


def ensure_halfyear_tile(spec: DashboardSpec) -> DashboardSpec:
    """Третья таблица вкладки «Сделки»: сделки и ЗК по полугодиям."""
    for tab in spec.tabs:
        if not _is_sales_data_tab(tab):
            continue
        tab.title = SALES_DATA_TAB_TITLE
        if any(tile.source.kind == "halfyear" for tile in tab.tiles):
            return spec
        if len(tab.tiles) >= 8:
            return spec
        tile = Tile(
            title="Сделки и ЗК по полугодиям",
            chart_type="table",
            source=TileSource(
                kind="halfyear",
                group_semantic="department",
                period="half",
            ),
            agg="sum",
            top_n=50,
            sort="none",
        )
        after = next((i for i, item in enumerate(tab.tiles) if item.source.kind == "outcome"), None)
        tab.tiles.insert(len(tab.tiles) if after is None else after + 1, tile)
        return spec
    return spec


def build_sales_dashboard_spec(df):
    """Дашборд этапов продаж: «Данные» с таблицами и пустая «Динамика»."""
    from services.generic_dashboard import build_deals_tab, build_generic_spec

    has_funnel = any(str(c).endswith("(сумма)") for c in df.columns)
    if not has_funnel:
        return build_generic_spec(df)

    deals = build_deals_tab(df)
    if not deals:
        return build_generic_spec(df)
    return DashboardSpec(tabs=[deals, _dynamics_tab()])


class SalesProfile(ReportProfile):
    """Спека воронки. KPI/графики/инсайты — только через ReportEngine."""

    def get_dashboard_spec(self, df):
        return build_sales_dashboard_spec(df)
