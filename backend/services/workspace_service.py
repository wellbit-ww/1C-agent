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


_PAYMENTS_TAB_TILE_KINDS = frozenset({"deficit_sales_by_department"})


def normalize_deficit_dashboard_spec(spec):
    """Вкладка «Платежи» (Продажи в UI): таблица продаж; платёжные графики — на «Структура»."""
    from models.dashboard_spec import DashboardSpec, Tab

    if not isinstance(spec, DashboardSpec):
        return spec
    pay = next((tab for tab in spec.tabs if tab.title == "Платежи"), None)
    if pay is None:
        return spec
    structure = next((tab for tab in spec.tabs if tab.title == "Структура"), None)
    stay = [t for t in pay.tiles if t.source.kind in _PAYMENTS_TAB_TILE_KINDS]
    moved = [t for t in pay.tiles if t.source.kind not in _PAYMENTS_TAB_TILE_KINDS]
    moved.extend(list(structure.tiles) if structure else [])
    pay.tiles = stay
    if structure is not None:
        structure.tiles = moved[:8]
    elif moved:
        spec.tabs.append(Tab(title="Структура", tiles=moved[:8]))
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
