import pandas as pd

from models.dashboard_spec import DashboardSpec, Tab, Tile, TileSource
from services.generic_dashboard import SALES_DATA_TAB_TITLE
from services.report_profiles.base_profile import ReportProfile

_LEGACY_SALES_DATA_TAB_TITLE = "Сделки"


def _is_sales_data_tab(tab: Tab) -> bool:
    return tab.title in (SALES_DATA_TAB_TITLE, _LEGACY_SALES_DATA_TAB_TITLE)


def _sales_data_tab_only(spec: DashboardSpec, df: pd.DataFrame | None = None) -> DashboardSpec:
    """Одна вкладка «Данные»: убрать Воронку/Менеджеры/Клиенты, переименовать «Сделки»."""
    data_tab: Tab | None = None
    for tab in spec.tabs:
        if _is_sales_data_tab(tab):
            tab.title = SALES_DATA_TAB_TITLE
            data_tab = tab
            break
    if data_tab is None and df is not None:
        from services.generic_dashboard import build_deals_tab

        data_tab = build_deals_tab(df)
    if data_tab is not None:
        spec.tabs = [data_tab]
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


def enrich_deals_tab(spec: DashboardSpec, df: pd.DataFrame | None = None) -> DashboardSpec:
    """Вкладка «Данные»: таблицы сделок/ЗК без лишних вкладок дашборда."""
    spec = _sales_data_tab_only(spec, df)
    spec = _drop_legacy_deals_pie(spec)
    return ensure_status_summary_tile(ensure_halfyear_tile(ensure_outcome_tile(spec)))


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
    """Дашборд этапов продаж: одна вкладка «Данные» с таблицами."""
    from services.generic_dashboard import build_deals_tab, build_generic_spec

    has_funnel = any(str(c).endswith("(сумма)") for c in df.columns)
    if not has_funnel:
        return build_generic_spec(df)

    deals = build_deals_tab(df)
    if not deals:
        return build_generic_spec(df)
    return DashboardSpec(tabs=[deals])


class SalesProfile(ReportProfile):
    """Спека воронки. KPI/графики/инсайты — только через ReportEngine."""

    def get_dashboard_spec(self, df):
        return build_sales_dashboard_spec(df)
