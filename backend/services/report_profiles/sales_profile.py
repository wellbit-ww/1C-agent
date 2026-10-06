import pandas as pd

from models.dashboard_spec import DashboardSpec, Tab, Tile, TileSource
from services.generic_dashboard import (
    SALES_CONVERSION_TAB_TITLE,
    SALES_DATA_TAB_TITLE,
    SALES_DYNAMICS_TAB_TITLE,
    SALES_MONEY_TAB_TITLE,
    SALES_HALFYEAR_TILE_TITLE,
    SALES_STAGES_TAB_TITLE,
)
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


def _conversion_deals_tile() -> Tile:
    return Tile(
        title="Конверсия сделок в заказы",
        chart_type="table",
        source=TileSource(
            kind="deals_conversion",
            group_semantic="department",
            period="quarter",
        ),
        agg="sum",
        top_n=50,
        sort="desc",
    )


def _conversion_tab() -> Tab:
    return Tab(title=SALES_CONVERSION_TAB_TITLE, tiles=[_conversion_deals_tile()])


def _money_tile() -> Tile:
    return Tile(
        title="Финансовые показатели сделок",
        chart_type="table",
        source=TileSource(
            kind="deals_money",
            group_semantic="department",
            period="quarter",
        ),
        agg="sum",
        top_n=50,
        sort="desc",
    )


def _money_tab() -> Tab:
    return Tab(title=SALES_MONEY_TAB_TITLE, tiles=[_money_tile()])


def _stages_status_tile(period: str = "quarter", variant: str = "full") -> Tile:
    title = "Сделки и заказы" if variant == "counts" else "Статусы сделок"
    return Tile(
        title=title,
        chart_type="table",
        source=TileSource(kind="deal_statuses", period=period, variant=variant),
        agg="sum",
        top_n=50,
        sort="none",
    )


def _in_work_stages_tile(period: str = "quarter") -> Tile:
    return Tile(
        title="Сделки со статусом В работе",
        chart_type="table",
        source=TileSource(kind="in_work_stages", period=period),
        agg="sum",
        top_n=50,
        sort="none",
    )


def _stages_tab() -> Tab:
    return Tab(
        title=SALES_STAGES_TAB_TITLE,
        tiles=[
            _stages_status_tile("quarter", "full"),
            _stages_status_tile("quarter", "counts"),
            _in_work_stages_tile(),
        ],
    )


def _stages_tab_period(tiles: list[Tile]) -> str:
    """Период вкладки «Этапы продаж»: приоритет у «Статусы сделок», не у последнего тайла в списке."""
    for tile in tiles:
        if tile.source.kind != "deal_statuses" or tile.source.variant != "full":
            continue
        raw = tile.source.period
        if raw in ("month", "quarter", "half"):
            return raw
    for tile in tiles:
        if tile.source.kind not in ("deal_statuses", "in_work_stages"):
            continue
        raw = tile.source.period
        if raw in ("month", "quarter", "half"):
            return raw
    return "quarter"


def _normalize_stages_tab(stages_tab: Tab) -> None:
    """Только три тайла: статусы (полный/счётчики) и «В работе» по этапам.

    Старые спеки могли содержать лишний график «Сделки по этапам» с kind=current_stage
    или columns_pattern — он считал все 428 сделок, а не 327 «В работе».
    """
    period = _stages_tab_period(stages_tab.tiles)
    titles: dict[str, str] = {}
    for tile in stages_tab.tiles:
        kind = tile.source.kind
        if kind == "deal_statuses" and tile.source.variant == "full":
            titles["full"] = tile.title
        elif kind == "deal_statuses" and tile.source.variant == "counts":
            titles["counts"] = tile.title
        elif kind == "in_work_stages":
            titles["in_work"] = tile.title
        elif tile.chart_type == "table" and "этап" in tile.title.lower():
            titles.setdefault("in_work", tile.title)

    full = _stages_status_tile(period, "full")
    counts = _stages_status_tile(period, "counts")
    in_work = _in_work_stages_tile(period)
    if "full" in titles:
        full.title = titles["full"]
    if "counts" in titles:
        counts.title = titles["counts"]
    if "in_work" in titles:
        custom = titles["in_work"]
        if custom.strip().lower() != "сделки по этапам":
            in_work.title = custom
    stages_tab.tiles = [full, counts, in_work]


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
    """«Данные», «Динамика», «Конверсия», «Деньги» и пустые «Этапы продаж»."""
    data_tab: Tab | None = None
    dynamics_tab: Tab | None = None
    conversion_tab: Tab | None = None
    money_tab: Tab | None = None
    stages_tab: Tab | None = None
    for tab in spec.tabs:
        if tab.title in _REMOVED_SALES_TABS:
            continue
        if tab.title == SALES_DYNAMICS_TAB_TITLE:
            dynamics_tab = tab
            continue
        if tab.title == SALES_CONVERSION_TAB_TITLE:
            conversion_tab = tab
            continue
        if tab.title == SALES_MONEY_TAB_TITLE:
            money_tab = tab
            continue
        if tab.title == SALES_STAGES_TAB_TITLE:
            stages_tab = tab
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
    if conversion_tab is None:
        conversion_tab = _conversion_tab()
    if money_tab is None:
        money_tab = _money_tab()
    if stages_tab is None:
        stages_tab = _stages_tab()
    else:
        _normalize_stages_tab(stages_tab)
    ordered = [
        tab
        for tab in (data_tab, dynamics_tab, conversion_tab, money_tab, stages_tab)
        if tab is not None
    ]
    if ordered:
        spec.tabs = ordered
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
        saved = {
            tile.source.kind: list(tile.source.departments)
            for tile in tab.tiles
            if tile.source.departments
        }
        company = _dynamics_deals_tile()
        departments = _dynamics_departments_tile()
        outcome = _dynamics_outcome_share_tile()
        company.source.period = period
        departments.source.period = period
        outcome.source.period = period
        if saved.get("deals_dynamics"):
            company.source.departments = saved["deals_dynamics"]
        if saved.get("deals_dynamics_departments"):
            departments.source.departments = saved["deals_dynamics_departments"]
        if saved.get("deals_dynamics_outcome_share"):
            outcome.source.departments = saved["deals_dynamics_outcome_share"]
        tab.tiles = [company, departments, outcome]
        return spec
    return spec


def ensure_conversion_tile(spec: DashboardSpec) -> DashboardSpec:
    """Вкладка «Конверсия»: доля заказов от сделок по компании и всем подразделениям."""
    for tab in spec.tabs:
        if tab.title != SALES_CONVERSION_TAB_TITLE:
            continue
        period = "quarter"
        saved = None
        for tile in tab.tiles:
            if tile.source.kind == "deals_conversion" and tile.source.departments:
                saved = list(tile.source.departments)
            if tile.source.kind == "deals_conversion" and tile.source.period in (
                "month",
                "quarter",
                "half",
            ):
                period = tile.source.period
        tile = _conversion_deals_tile()
        tile.source.period = period
        if saved:
            tile.source.departments = saved
        tab.tiles = [tile]
        return spec
    return spec


def ensure_money_tile(spec: DashboardSpec) -> DashboardSpec:
    """Вкладка «Деньги»: потенциал сделок и сумма заказов."""
    for tab in spec.tabs:
        if tab.title != SALES_MONEY_TAB_TITLE:
            continue
        period = "quarter"
        saved = None
        for tile in tab.tiles:
            if tile.source.kind == "deals_money" and tile.source.departments:
                saved = list(tile.source.departments)
            if tile.source.kind == "deals_money" and tile.source.period in (
                "month",
                "quarter",
                "half",
            ):
                period = tile.source.period
        tile = _money_tile()
        tile.source.period = period
        if saved:
            tile.source.departments = saved
        tab.tiles = [tile]
        return spec
    return spec


def enrich_deals_tab(spec: DashboardSpec, df: pd.DataFrame | None = None) -> DashboardSpec:
    """Вкладки «Данные», «Динамика» и «Конверсия» для этапов продаж."""
    _migrate_halfyear_tile_titles(spec)
    spec = _sales_pipeline_tabs(spec, df)
    spec = _drop_legacy_deals_pie(spec)
    spec = ensure_status_summary_tile(ensure_halfyear_tile(ensure_outcome_tile(spec)))
    spec = ensure_dynamics_deals_tile(spec)
    spec = ensure_conversion_tile(spec)
    return ensure_money_tile(spec)


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


def _migrate_halfyear_tile_titles(spec: DashboardSpec) -> None:
    for tab in spec.tabs:
        for tile in tab.tiles:
            if tile.source.kind == "halfyear":
                tile.title = SALES_HALFYEAR_TILE_TITLE


def ensure_halfyear_tile(spec: DashboardSpec) -> DashboardSpec:
    """Третья таблица вкладки «Данные»: сделки и ЗК за выбранный период."""
    for tab in spec.tabs:
        if not _is_sales_data_tab(tab):
            continue
        tab.title = SALES_DATA_TAB_TITLE
        if any(tile.source.kind == "halfyear" for tile in tab.tiles):
            return spec
        if len(tab.tiles) >= 8:
            return spec
        tile = Tile(
            title=SALES_HALFYEAR_TILE_TITLE,
            chart_type="table",
            source=TileSource(
                kind="halfyear",
                group_semantic="department",
                period="quarter",
            ),
            agg="sum",
            top_n=50,
            sort="none",
        )
        after = next((i for i, item in enumerate(tab.tiles) if item.source.kind == "outcome"), None)
        tab.tiles.insert(len(tab.tiles) if after is None else after + 1, tile)
        return spec
    return spec


def sales_pipeline_kpis(df: pd.DataFrame) -> list[dict]:
    """Карточки над дашбордом воронки: сделки по статусам и потенциал."""
    from services.dashboard_engine import (
        _deal_count_in_frame,
        _named_column,
        _normalize_deal_status,
        _status_column,
        _sum_numeric,
    )
    from services.kpi_engine import _format_number

    deal_n = _named_column(df, "количество сделок")
    deal_s = _named_column(df, "сумма по сделке")
    status_col = _status_column(df)

    work = df.copy()
    if status_col:
        work["_status"] = work[status_col].map(_normalize_deal_status)

    def count_frame(frame: pd.DataFrame) -> float:
        return _deal_count_in_frame(frame, deal_n)

    status_counts = {
        "В работе": 0.0,
        "Выиграна": 0.0,
        "Проиграна": 0.0,
        "Отменена": 0.0,
    }
    if status_col:
        for label in status_counts:
            part = work.loc[work["_status"] == label]
            status_counts[label] = count_frame(part)

    total_count = count_frame(work)
    total_sum = _sum_numeric(work, deal_s) if deal_s else 0.0

    def count_kpi(name: str, label: str, value: float) -> dict:
        n = int(round(value))
        return {"name": name, "label": label, "value": str(n), "raw_value": float(n)}

    def money_kpi(name: str, label: str, value: float) -> dict:
        return {
            "name": name,
            "label": label,
            "value": _format_number(value),
            "raw_value": float(value),
        }

    return [
        count_kpi("deals_total", "Количество сделок", total_count),
        money_kpi("deals_potential_sum", "Потенциальная сумма сделок", total_sum),
        count_kpi("deals_in_progress", "Сделки в работе", status_counts["В работе"]),
        count_kpi("deals_won", "Сделки выиграны", status_counts["Выиграна"]),
        count_kpi("deals_lost", "Сделки проиграны", status_counts["Проиграна"]),
        count_kpi("deals_cancelled", "Сделки отменены", status_counts["Отменена"]),
    ]


def build_sales_dashboard_spec(df):
    """Дашборд этапов продаж: «Данные», «Динамика», «Конверсия», «Деньги» и пустые «Этапы продаж»."""
    from services.generic_dashboard import build_deals_tab, build_generic_spec

    has_funnel = any(str(c).endswith("(сумма)") for c in df.columns)
    if not has_funnel:
        return build_generic_spec(df)

    deals = build_deals_tab(df)
    if not deals:
        return build_generic_spec(df)
    return DashboardSpec(
        tabs=[deals, _dynamics_tab(), _conversion_tab(), _money_tab(), _stages_tab()]
    )


class SalesProfile(ReportProfile):
    """Спека воронки. KPI/графики/инсайты — только через ReportEngine."""

    def get_dashboard_spec(self, df):
        return build_sales_dashboard_spec(df)
