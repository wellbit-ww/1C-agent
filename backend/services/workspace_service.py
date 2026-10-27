"""Рабочее пространство: файл сделок + файл дефицита без смешивания данных."""
from __future__ import annotations

import logging
from typing import Any, Callable

import pandas as pd

from services import db_service
from services.exceptions import (
    SlotOccupiedError,
    UnsupportedReportError,
    WorkspaceNotFoundError,
)
from services.report_detector import detect_report_type
from services.storage_service import get_original_name

logger = logging.getLogger(__name__)

SALES_REPORT = "sales_pipeline"
DEFICIT_REPORT = "deficit_report"

SOURCE_ORDER = (SALES_REPORT, DEFICIT_REPORT)


def report_type_to_role(report_type: str) -> str | None:
    if report_type == SALES_REPORT:
        return SALES_REPORT
    if report_type == DEFICIT_REPORT:
        return DEFICIT_REPORT
    return None


def slot_column(role: str) -> str:
    if role == SALES_REPORT:
        return "sales_file_id"
    if role == DEFICIT_REPORT:
        return "deficit_file_id"
    raise ValueError(role)


def create_workspace() -> str:
    return db_service.create_workspace()


def get_workspace_meta(workspace_id: str) -> dict:
    row = db_service.get_workspace(workspace_id)
    if not row:
        raise WorkspaceNotFoundError(f"Workspace {workspace_id} не найден")
    slots = []
    for role in SOURCE_ORDER:
        file_id = row["sales_file_id"] if role == SALES_REPORT else row["deficit_file_id"]
        if not file_id:
            continue
        filename = get_original_name(file_id) or file_id
        slots.append(
            {
                "role": role,
                "file_id": file_id,
                "filename": filename,
                "report_type": role,
            }
        )
    return {
        "workspace_id": workspace_id,
        "sales_file_id": row["sales_file_id"],
        "deficit_file_id": row["deficit_file_id"],
        "slots": slots,
    }


def attach_file(
    workspace_id: str,
    file_id: str,
    load_df: Callable[[str, str], pd.DataFrame],
    file_path: str,
    *,
    replace: bool = False,
) -> dict:
    row = db_service.get_workspace(workspace_id)
    if not row:
        raise WorkspaceNotFoundError(f"Workspace {workspace_id} не найден")

    df = load_df(file_id, file_path)
    filename = get_original_name(file_id) or ""
    report_type = detect_report_type(df, filename=filename)
    role = report_type_to_role(report_type)
    if role is None:
        raise UnsupportedReportError(
            "Этот файл не подходит: нужна выгрузка этапов продаж или дефицита."
        )

    key = slot_column(role)
    current = row[key]
    if current and current != file_id and not replace:
        raise SlotOccupiedError(
            f"Слот «{role}» уже занят. Передайте replace=true, чтобы заменить файл."
        )

    if role == SALES_REPORT:
        db_service.update_workspace_files(workspace_id, sales_file_id=file_id)
    else:
        db_service.update_workspace_files(workspace_id, deficit_file_id=file_id)

    return {
        "workspace_id": workspace_id,
        "file_id": file_id,
        "report_type": report_type,
        "role": role,
        "filename": filename,
        "replaced": bool(current and current != file_id),
    }


_PAYMENTS_TAB_TILE_KINDS = frozenset(
    {
        "deficit_sales_by_department",
        "deficit_sales_forecast_vitrine",
        "deficit_sales_fulfillment_board",
        "deficit_sales_share",
        "deficit_sales_summary",
    }
)
_PAYMENTS_TAB_TILE_ORDER = (
    "deficit_sales_by_department",
    "deficit_sales_summary",
    "deficit_sales_forecast_vitrine",
    "deficit_sales_fulfillment_board",
    "deficit_sales_share",
)


def _order_payments_tab_tiles(tiles: list) -> list:
    by_kind = {t.source.kind: t for t in tiles}
    return [by_kind[k] for k in _PAYMENTS_TAB_TILE_ORDER if k in by_kind]


def normalize_deficit_dashboard_spec(spec):
    """Вкладка «Платежи» (Продажи в UI): таблицы продаж.

    «Структура» — отдельный пустой дашборд «Оплата»; автографики на неё не вешаем.
    """
    from models.dashboard_spec import DashboardSpec, Tab, Tile, TileSource

    if not isinstance(spec, DashboardSpec):
        return spec
    pay = next((tab for tab in spec.tabs if tab.title == "Платежи"), None)
    if pay is None:
        return spec
    stay = [t for t in pay.tiles if t.source.kind in _PAYMENTS_TAB_TILE_KINDS]
    if not any(t.source.kind == "deficit_sales_by_department" for t in stay):
        stay.insert(
            0,
            Tile(
                title="Продажи по подразделениям",
                chart_type="table",
                source=TileSource(kind="deficit_sales_by_department", period="quarter"),
                agg="sum",
                top_n=50,
                sort="none",
            ),
        )
    if not any(t.source.kind == "deficit_sales_summary" for t in stay):
        insert_at = next(
            (
                i + 1
                for i, t in enumerate(stay)
                if t.source.kind == "deficit_sales_by_department"
            ),
            len(stay),
        )
        stay.insert(
            insert_at,
            Tile(
                title="Выполнение прогноза",
                chart_type="table",
                source=TileSource(kind="deficit_sales_summary", period="quarter"),
                agg="sum",
                top_n=50,
                sort="none",
            ),
        )
    if not any(t.source.kind == "deficit_sales_forecast_vitrine" for t in stay):
        insert_at = next(
            (
                i + 1
                for i, t in enumerate(stay)
                if t.source.kind == "deficit_sales_summary"
            ),
            len(stay),
        )
        stay.insert(
            insert_at,
            Tile(
                title="Прогноз продаж по службам",
                chart_type="table",
                source=TileSource(kind="deficit_sales_forecast_vitrine", period="quarter"),
                agg="sum",
                top_n=50,
                sort="none",
            ),
        )
    if not any(t.source.kind == "deficit_sales_fulfillment_board" for t in stay):
        stay.append(
            Tile(
                title="Выполнение прогноза по подразделениям",
                chart_type="table",
                source=TileSource(kind="deficit_sales_fulfillment_board", period="quarter"),
                agg="sum",
                top_n=50,
                sort="none",
            )
        )
    if not any(t.source.kind == "deficit_sales_share" for t in stay):
        stay.append(
            Tile(
                title="Доля подразделений в продажах",
                chart_type="table",
                source=TileSource(kind="deficit_sales_share", period="quarter"),
                agg="sum",
                top_n=50,
                sort="none",
            )
        )
    # Синхронизируем период на всех тайлах продаж
    period = next(
        (
            t.source.period
            for t in stay
            if t.source.kind == "deficit_sales_by_department" and t.source.period
        ),
        "quarter",
    )
    if period not in {"half", "quarter", "month"}:
        period = "quarter"
    stay = [
        t.model_copy(
            update={"source": t.source.model_copy(update={"period": period})}
        )
        for t in stay
    ]
    pay.tiles = _order_payments_tab_tiles(stay)
    from services.report_profiles.deficit_profile import deficit_payment_tab_tiles

    _PAYMENT_KINDS = {
        "deficit_payment_receipt",
        "deficit_payment_by_department",
        "deficit_payment_fulfillment_board",
        "deficit_payment_share",
    }
    payment_shell = next((tab for tab in spec.tabs if tab.title == "Структура"), None)
    payment_period = next(
        (
            t.source.period
            for t in (payment_shell.tiles if payment_shell else [])
            if t.source.kind in _PAYMENT_KINDS
            and t.source.period in {"half", "quarter", "month"}
        ),
        period,
    )
    if payment_period not in {"half", "quarter", "month"}:
        payment_period = "quarter"
    # Отдельные тайлы как на «Продажах», с единым периодом вкладки «Оплата».
    payment_shell = Tab(
        title="Структура",
        tiles=deficit_payment_tab_tiles(payment_period),
    )
    spec.tabs = [pay, payment_shell]
    return spec


def build_source_dashboard(
    file_id: str,
    file_path: str,
    load_df: Callable[[str, str], pd.DataFrame],
) -> dict[str, Any]:
    """Один источник: KPI, spec, tabs — как get_dashboard, но без workspace."""
    from pathlib import Path

    from services.report_service import get_profile_for_df
    from services import dashboard_service
    from services.dashboard_engine import render_spec
    from services.insights_service import _get_date_period

    df = load_df(file_id, file_path)
    filename = get_original_name(file_id) or Path(file_path).name
    report_type, profile = get_profile_for_df(df, filename=filename)

    summary = ""
    if hasattr(profile, "get_summary"):
        summary = profile.get_summary(df)

    period = _get_date_period(df) or "Не определен"
    payload: dict[str, Any] = {
        "role": report_type,
        "file_id": file_id,
        "filename": filename,
        "report_type": report_type,
        "summary": summary,
        "kpis": profile.get_kpis(df),
        "insights": profile.get_insights(df),
        "charts": [],
        "metadata": {
            "rows": len(df),
            "columns": len(df.columns),
            "period": period,
            "column_names": [str(c) for c in df.columns],
        },
        "tabs": [],
        "spec": None,
    }

    spec = dashboard_service.get_current_spec(file_id, df)
    if spec is None:
        payload["charts"] = profile.get_charts(df)
        return payload

    if report_type == DEFICIT_REPORT:
        spec = normalize_deficit_dashboard_spec(spec)

    if report_type == SALES_REPORT:
        from services.report_profiles.sales_profile import enrich_deals_tab
        from agents.excel_agent import _persist_enriched_spec

        spec = enrich_deals_tab(spec, df)
        _persist_enriched_spec(file_id, spec)

    try:
        rendered = render_spec(df, spec)
        tabs = []
        for tab in rendered.get("tabs") or []:
            tab_out = dict(tab)
            tab_out["source_file_id"] = file_id
            tab_out["source_role"] = report_type
            tabs.append(tab_out)
        payload["tabs"] = tabs
        payload["spec"] = spec.model_dump(mode="json")
    except Exception as exc:
        logger.exception("Не удалось отрисовать дашборд для %s", file_id)
        payload["warning"] = f"Дашборд не собран: {exc}"
        payload["charts"] = profile.get_charts(df)

    from services.file_context_service import ensure_context, get_context

    try:
        ctx = get_context(file_id) or ensure_context(
            file_id, df, filename=filename, use_llm=False
        )
        payload["file_context"] = ctx.model_dump()
    except Exception:
        pass

    return payload


def build_workspace_dashboard(
    workspace_id: str,
    load_df: Callable[[str, str], pd.DataFrame],
    get_file_path: Callable[[str], str],
) -> dict[str, Any]:
    meta = get_workspace_meta(workspace_id)
    sources: list[dict[str, Any]] = []
    for role in SOURCE_ORDER:
        file_id = meta["sales_file_id"] if role == SALES_REPORT else meta["deficit_file_id"]
        if not file_id:
            continue
        path = get_file_path(file_id)
        sources.append(build_source_dashboard(file_id, path, load_df))

    return {
        "workspace_id": workspace_id,
        "sales_file_id": meta["sales_file_id"],
        "deficit_file_id": meta["deficit_file_id"],
        "sources": sources,
    }
