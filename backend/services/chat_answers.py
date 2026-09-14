"""Шаблоны ответа чата: имя как в файле, единицы, смысл, пропуски.

Цифры уже посчитаны pandas. Шаблон только собирает текст; LLM их не считает.
"""
from __future__ import annotations

import pandas as pd

from services.insights_service import _format_number
from services.report_profiles.deficit_profile import detect_deficit_money_layout


def money_title(kind: str, col: str) -> str:
    low = str(col).lower()
    if kind == "paid":
        return "Оплачено"
    if kind == "unpaid":
        return "Неоплаченный остаток"
    if "сделк" in low:
        return "Сумма по сделке"
    if "зк" in low and "сумм" in low:
        return "Сумма ЗК"
    return "Сумма заказов"


def rub(value: float) -> str:
    return f"{_format_number(value)} руб."


def money_gap_note(frame: pd.DataFrame, columns: list[str]) -> str:
    bits: list[str] = []
    n = len(frame)
    for col in columns:
        if not col or col not in frame.columns:
            continue
        empty = int(pd.to_numeric(frame[col], errors="coerce").isna().sum())
        if empty:
            bits.append(f"«{col}» пусто в {empty} из {n}")
    if not bits:
        return ""
    return "Срез неполный: " + "; ".join(bits) + " — эти строки в сумму не вошли как 0, а как пропуск."


def deficit_meaning(
    *,
    unpaid: float | None,
    order: float | None,
    paid: float | None,
    order_col: str | None = None,
) -> str:
    if unpaid is None or order is None:
        return ""
    title = money_title("order", order_col or "")
    extra = f", уже оплачено {rub(paid)}" if paid is not None else ""
    return (
        f"Неоплаченный остаток — это не вся {title.lower()}: "
        f"в файле {title.lower()} {rub(order)}, осталось {rub(unpaid)}{extra}."
    )


def format_metric_line(title: str, col: str, value: float) -> str:
    return f"• {title} («{col}»): **{rub(value)}**"


def layout_values(frame: pd.DataFrame, layout) -> dict[str, float | None]:
    from services.report_profiles.deficit_profile import _col_sum

    def one(col: str | None) -> float | None:
        if not col:
            return None
        return _col_sum(frame, col)

    return {
        "paid": one(layout.paid),
        "unpaid": one(layout.unpaid),
        "order": one(layout.order_sum),
    }


def format_entity_answer(
    *,
    who: str,
    n: int,
    items: list[tuple[str, str, float]],
    frame: pd.DataFrame,
    layout,
    also_count: bool = False,
) -> str:
    lines = [f"**{who}**", f"Строк в выборке: {n}."]
    for title, col, value in items:
        lines.append(format_metric_line(title, col, value))
    if also_count:
        lines.append(f"• Заказов (строк): **{n}**")
    vals = layout_values(frame, layout)
    meaning = deficit_meaning(
        unpaid=vals["unpaid"] if layout.unpaid else None,
        order=vals["order"] if layout.order_sum else None,
        paid=vals["paid"] if layout.paid else None,
        order_col=layout.order_sum,
    )
    if meaning and (layout.unpaid and layout.order_sum):
        lines.append(meaning)
    gap = money_gap_note(frame, [col for _, col, _ in items])
    if gap:
        lines.append(gap)
    return "\n".join(lines)


def report_kind_hint(file_context=None, df: pd.DataFrame | None = None) -> str:
    kind = ""
    if file_context is not None:
        kind = str(getattr(file_context, "report_kind", "") or "")
    if not kind and df is not None:
        from services.report_detector import detect_report_type

        kind = detect_report_type(df)
    low = kind.lower()
    if "дефицит" in low or "задолжен" in low:
        return "deficit"
    if "этап" in low or "воронк" in low or "продаж" in low or "pipeline" in low:
        return "funnel"
    if "пдо" in low:
        return "pdo"
    if "гарант" in low or "warranty" in low:
        return "warranty"
    layout = detect_deficit_money_layout(df) if df is not None else None
    if layout and (layout.unpaid or layout.paid or layout.stages):
        return "deficit"
    return "generic"


def typed_frame_rules(kind: str) -> str:
    common = (
        "Ответ строго: 1) назови объект как в файле; "
        "2) перечисли запрошенные метрики с единицами (руб. или шт.); "
        "3) одно предложение, что значит цифра; "
        "4) если в фактах есть пропуски — скажи явно. "
        "Не выдумывай числа. Не копируй карточку файла."
    )
    extra = {
        "deficit": (
            "Это дефицит/задолженность. Неоплаченный остаток ≠ сумма заказа. "
            "Не подменяй срез по заказчику итогом файла."
        ),
        "funnel": (
            "Это воронка этапов продаж. «Сумма по сделке» — не оплата. "
            "Витрины 1С на других листах не складывай с листом данных."
        ),
        "pdo": (
            "Это отчёт ПДО (производство), не дефицит и не воронка продаж."
        ),
        "warranty": (
            "Это журнал гарантии. Срок гарантии и инженер — не суммы заказов."
        ),
    }.get(kind, "Не путай типы отчётов. Используй только факты ниже.")
    return common + " " + extra


def typed_overview_draft(df: pd.DataFrame, file_context=None) -> str:
    """Готовый текст обзора, если LLM недоступна или испортила ответ."""
    from services.report_profiles.deficit_profile import deficit_kpis

    kind = report_kind_hint(file_context, df)
    lines: list[str] = []
    title = ""
    if file_context is not None:
        title = str(getattr(file_context, "title", "") or "")
        grain = str(getattr(file_context, "grain", "") or "")
        if title:
            lines.append(f"**{title}**")
        if grain:
            lines.append(grain.capitalize() + ".")
    layout = detect_deficit_money_layout(df)
    if kind == "deficit" and (layout.unpaid or layout.paid or layout.stages):
        lines.append("По рабочему листу:")
        for kpi in deficit_kpis(df)[:8]:
            unit = " руб." if kpi["name"] not in {"unique_customers", "unique_departments"} else ""
            lines.append(f"• {kpi['label']}: **{kpi['value']}{unit}**")
        if layout.unpaid and layout.order_sum:
            vals = layout_values(df, layout)
            meaning = deficit_meaning(
                unpaid=vals["unpaid"],
                order=vals["order"],
                paid=vals["paid"],
                order_col=layout.order_sum,
            )
            if meaning:
                lines.append(meaning)
        return "\n".join(lines)
    from services.generic_dashboard import pick_metrics
    from services.insights_service import _get_date_period

    metrics = [str(c) for c in pick_metrics(df)[:2]]
    span = _get_date_period(df)
    opener = f"В выгрузке {len(df)} строк"
    if span:
        opener += f" за {span}"
    opener += "."
    lines.append(opener)
    for name in metrics:
        total = float(pd.to_numeric(df[name], errors="coerce").sum())
        lines.append(f"• «{name}»: **{rub(total)}**")
    if kind == "funnel":
        lines.append(
            "Это сумма колонки сделок, не оплаты. Другие листы книги — витрины 1С, "
            "их итоги не смешивать с этим зерном."
        )
    return "\n".join(lines)
