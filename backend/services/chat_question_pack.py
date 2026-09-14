"""Срез pandas по вопросу: факты для LLM и быстрые compare-ответы.

LLM не считает. Сборщик берёт имена/метрики из вопроса, pandas считает,
в модель уходит только этот срез плюс 6–8 глобальных фактов файла.
"""
from __future__ import annotations

import re

import pandas as pd

from services.chat_lookup import (
    entity_name_words,
    exec_entity_metrics,
    match_entities_separately,
    match_entity_slice,
)
from services.insights_service import _format_number
from services.report_profiles.deficit_profile import (
    _col_sum,
    deficit_kpis,
    detect_deficit_money_layout,
)

_COMPARE_MARK = (
    "сравни",
    "сравнен",
    " versus",
    " vs ",
    "против ",
    "чем у",
    "разниц",
    "кто из",
)
_RANK_WHO = re.compile(r"\bкто\b")
_OWES = ("должен", "должн", "остат", "неоплач", "задолжен")
_ORDERED = ("заказал", "сумме заказ", "сумма заказ", "по сумме заказ")
_PAY_MARK = ("оплат", "остат", "дефицит", "долг", "к оплате", "задолжен")
_WHOLE_MARK = (
    "в целом",
    "по файл",
    "что происходит",
    "как обстоя",
    "обзор",
    "что в файл",
    "расскажи про",
    "что с оплат",
)
_PERIOD_MARK = ("месяц", "квартал", "год", "динамик", "период")
_GROUP_ALL_SKIP = (
    "по заказчик",
    "по клиент",
    "по менеджер",
    "по ответственн",
    "по подразделен",
    "по отдел",
    "топ-",
    "топ ",
)


def wants_named_compare(question: str) -> bool:
    words = entity_name_words(question)
    if len(words) < 2:
        return False
    q = question.lower().replace("ё", "е")
    if any(marker in q for marker in _COMPARE_MARK):
        return True
    if " и " in q and any(
        marker in q
        for marker in _PAY_MARK + _ORDERED + ("должен", "заказал")
    ):
        return True
    return False


def wants_rank_compare(question: str) -> bool:
    q = question.lower().replace("ё", "е")
    if any(marker in q for marker in _GROUP_ALL_SKIP):
        return False
    if any(marker in q for marker in ("график", "диаграмм", "построй", "кругов")):
        return False
    owes = any(marker in q for marker in _OWES)
    ordered = any(marker in q for marker in _ORDERED)
    who = bool(_RANK_WHO.search(q))
    if not (who and (owes or ordered)) and not (owes and ordered and " и " in q):
        return False
    if wants_named_compare(question):
        return False
    return True


def wants_payment_overview(question: str) -> bool:
    if entity_name_words(question):
        return False
    q = question.lower().replace("ё", "е")
    if any(marker in q for marker in ("график", "диаграмм", "построй")):
        return False
    pay = any(marker in q for marker in _PAY_MARK)
    whole = any(marker in q for marker in _WHOLE_MARK)
    return pay and whole


_FILE_OVERVIEW = (
    "что в файл",
    "что за файл",
    "расскажи про файл",
    "что в таблиц",
    "о чем файл",
    "о чём файл",
    "содержимое файл",
    "что лежит в файл",
)


def wants_file_overview(question: str) -> bool:
    """«Что в файле?» — факты pandas, без JSON-роутера."""
    if entity_name_words(question):
        return False
    q = question.lower().replace("ё", "е")
    if any(marker in q for marker in ("график", "диаграмм", "построй", "какие лист")):
        return False
    return any(marker in q for marker in _FILE_OVERVIEW)


def _entity_metric_lines(frame: pd.DataFrame, layout) -> list[str]:
    lines = [f"строк: {len(frame)}"]
    pairs = (
        ("оплачено", layout.paid),
        ("неоплаченный остаток", layout.unpaid),
        ("сумма заказов", layout.order_sum),
    )
    for label, col in pairs:
        if not col:
            continue
        lines.append(f"{label} («{col}»): {_format_number(_col_sum(frame, col))}")
    return lines


def exec_named_compare(df: pd.DataFrame, question: str) -> dict:
    slices = match_entities_separately(df, question)
    if len(slices) < 2:
        if slices:
            return exec_entity_metrics(df, question)
        hint = " ".join(entity_name_words(question))
        return {
            "answer": (
                f"Не нашёл двух сторон для сравнения «{hint}». "
                "Укажите заказчиков так, как в файле."
            )
        }

    layout = detect_deficit_money_layout(df)
    from services.chat_answers import (
        format_metric_line,
        layout_values,
        money_gap_note,
        money_title,
    )

    blocks: list[str] = ["**Сравнение**"]
    unpaid_best: tuple[str, float] | None = None
    order_best: tuple[str, float] | None = None
    for item in slices[:4]:
        frame = item["frame"]
        name = item["name"]
        blocks.append(f"**«{name}»**")
        blocks.append(f"• строк: {len(frame)}")
        vals = layout_values(frame, layout)
        pairs = (
            ("paid", layout.paid, vals["paid"]),
            ("unpaid", layout.unpaid, vals["unpaid"]),
            ("order", layout.order_sum, vals["order"]),
        )
        used_cols: list[str] = []
        for kind, col, value in pairs:
            if not col or value is None:
                continue
            title = money_title(kind, col)
            blocks.append(format_metric_line(title, col, value))
            used_cols.append(col)
            if kind == "unpaid":
                if unpaid_best is None or value > unpaid_best[1]:
                    unpaid_best = (name, value)
            if kind == "order":
                if order_best is None or value > order_best[1]:
                    order_best = (name, value)
        gap = money_gap_note(frame, used_cols)
        if gap:
            blocks.append(gap)
    notes = []
    if unpaid_best:
        notes.append(
            f"Больше неоплаченный остаток у «{unpaid_best[0]}» "
            f"({_format_number(unpaid_best[1])} руб.)."
        )
    if order_best:
        title = money_title("order", layout.order_sum or "")
        notes.append(
            f"Больше {title.lower()} у «{order_best[0]}» "
            f"({_format_number(order_best[1])} руб.)."
        )
    if notes:
        blocks.append("")
        blocks.extend(notes)
    if layout.unpaid and layout.order_sum:
        blocks.append(
            "Неоплаченный остаток и сумма заказа — разные колонки: "
            "кто должен больше, не обязан быть тем, кто заказал больше."
        )
    return {"answer": "\n".join(blocks)}


def _top_lines(df: pd.DataFrame, group_col: str, value_col: str, n: int = 5) -> list[str]:
    from services.file_context_service import _top_sums

    items = _top_sums(df, group_col, value_col, n=n)
    return [
        f"{i}. {name} — {_format_number(value)}"
        for i, (name, value) in enumerate(items, 1)
    ]


def exec_rank_compare(df: pd.DataFrame, question: str) -> dict:
    from services.column_resolver import resolve_semantic_column

    q = question.lower().replace("ё", "е")
    layout = detect_deficit_money_layout(df)
    group_col = resolve_semantic_column(df, "", "client", dtype="categorical")
    if not group_col:
        return {"answer": "Не нашёл колонку заказчика, чтобы сравнить лидеров."}

    wants_unpaid = any(marker in q for marker in _OWES) or not any(
        marker in q for marker in _ORDERED
    )
    wants_order = any(marker in q for marker in _ORDERED) or not any(
        marker in q for marker in _OWES
    )
    if any(marker in q for marker in _OWES) and any(marker in q for marker in _ORDERED):
        wants_unpaid = True
        wants_order = True

    blocks: list[str] = []
    if wants_unpaid and layout.unpaid:
        lines = _top_lines(df, group_col, layout.unpaid)
        if lines:
            blocks.append(
                f"**Кто больше должен** (неоплаченный остаток «{layout.unpaid}»):"
            )
            blocks.extend(lines)
    if wants_order and layout.order_sum:
        lines = _top_lines(df, group_col, layout.order_sum)
        if lines:
            from services.chat_answers import money_title

            title = money_title("order", layout.order_sum)
            blocks.append(
                f"**Кто больше заказал** ({title.lower()} «{layout.order_sum}»):"
            )
            blocks.extend(lines)
    if wants_unpaid and not layout.unpaid:
        msg = (
            "Колонки неоплаченного остатка в файле нет — "
            "«кто должен» посчитать нельзя."
        )
        if blocks:
            blocks.insert(0, msg)
        else:
            return {"answer": msg + " Есть сумма по колонкам сделок/заказов."}
    if not blocks:
        return {
            "answer": "Не удалось сравнить должников и суммы заказов: нет денежных колонок."
        }
    if wants_unpaid and wants_order and layout.unpaid and layout.order_sum and len(blocks) >= 2:
        blocks.append(
            "Лидеры по остатку и по сумме заказа могут не совпадать — "
            "это разные колонки."
        )
    return {"answer": "\n".join(blocks)}


def _period_slice(df: pd.DataFrame, question: str) -> str:
    from services import data_tools

    q = question.lower()
    if not any(marker in q for marker in _PERIOD_MARK):
        return ""
    if "месяц" in q:
        data = data_tools.group_by_month(df, question)
        label = "месяцам"
    elif "год" in q or "ежегодн" in q:
        data = data_tools.group_by_year(df, question)
        label = "годам"
    else:
        data = data_tools.group_by_quarter(df, question)
        label = "кварталам"
    if "error" in data or not data.get("groups"):
        return ""
    items = list(data["groups"].items())[:8]
    col = data.get("value_column") or ""
    lines = [f"Срезы по {label}" + (f" («{col}»)" if col else "") + ":"]
    for name, value in items:
        lines.append(f"- {name}: {_format_number(value)}")
    return "\n".join(lines)


def _payment_overview_lines(df: pd.DataFrame) -> list[str]:
    layout = detect_deficit_money_layout(df)
    if not (layout.unpaid or layout.paid or layout.stages):
        return []
    lines = ["Итоги по оплатам (весь рабочий лист):"]
    for kpi in deficit_kpis(df):
        lines.append(f"- {kpi['label']}: {kpi['value']}")
    return lines


def question_slice_facts(df: pd.DataFrame, question: str) -> str:
    """Pandas-цифры, относящиеся к этому вопросу. Пустая строка — нечего добавить."""
    parts: list[str] = []
    slices = match_entities_separately(df, question)
    layout = detect_deficit_money_layout(df)
    if len(slices) >= 2:
        for item in slices[:4]:
            body = "; ".join(_entity_metric_lines(item["frame"], layout))
            parts.append(f"«{item['name']}»: {body}")
    elif len(slices) == 1:
        item = slices[0]
        body = "; ".join(_entity_metric_lines(item["frame"], layout))
        parts.append(f"Срез «{item['name']}»: {body}")
    elif wants_rank_compare(question):
        rank = exec_rank_compare(df, question)["answer"]
        parts.append(rank)
    elif wants_payment_overview(question) or (
        any(m in question.lower() for m in _PAY_MARK)
        and not entity_name_words(question)
    ):
        parts.extend(_payment_overview_lines(df))

    found = match_entity_slice(df, question) if not slices else None
    if found and found["names"] and not slices:
        frame = found["frame"]
        if frame is not None and not frame.empty:
            who = ", ".join(found["names"][:3])
            body = "; ".join(_entity_metric_lines(frame, layout))
            parts.append(f"Срез «{who}»: {body}")

    period = _period_slice(df, question)
    if period:
        parts.append(period)
    return "\n".join(p for p in parts if p)


def compact_file_header(file_context, *, n_facts: int = 8) -> str:
    if file_context is None:
        return ""
    lines: list[str] = []
    title = getattr(file_context, "title", "") or ""
    kind = getattr(file_context, "report_kind", "") or ""
    grain = getattr(file_context, "grain", "") or ""
    summary = getattr(file_context, "summary", "") or ""
    if title:
        lines.append(f"Название: {title}")
    if kind:
        lines.append(f"Тип: {kind}")
    if grain:
        lines.append(f"Зерно: {grain}")
    if summary:
        lines.append(summary)
    caveats = list(getattr(file_context, "caveats", None) or [])[:2]
    if caveats:
        lines.append("Ограничения: " + "; ".join(caveats))
    facts = list(getattr(file_context, "facts", None) or [])[:n_facts]
    if facts:
        lines.append("Факты по файлу:")
        lines.extend(f"- {fact}" for fact in facts)
    return "\n".join(lines)


def build_answer_facts(
    df: pd.DataFrame,
    question: str,
    file_context=None,
) -> str:
    """Глобальные факты файла + срез по вопросу для ответа LLM."""
    header = compact_file_header(file_context)
    if not header:
        from services.insights_service import get_basic_insights

        header = "\n".join(get_basic_insights(df)[:6])
    slice_text = question_slice_facts(df, question)
    parts = [header]
    if slice_text:
        parts.append("Срез по вопросу (посчитан pandas, не выдумывай другие цифры):")
        parts.append(slice_text)
    return "\n".join(p for p in parts if p)
