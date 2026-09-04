"""Narrative reports, sheet catalog, and open-ended LLM answers."""
import re

import pandas as pd

from services import data_tools
from services.chat_keywords import _CHART_MARKERS
from services.exceptions import OllamaUnavailableError
from services.insights_service import _format_number, _get_date_period, get_basic_insights
def _ask_llm(prompt, **kwargs):
    """Тесты патчат chat_service.ask_llm — ходим туда, а не в llm_service напрямую."""
    from services import chat_service

    return chat_service.ask_llm(prompt, **kwargs)

_HELP_MARKERS = (
    "что ты умеешь",
    "что умеешь",
    "что ты можешь",
    "что можешь",
    "помощь",
    "справка",
    "как пользоваться",
    "какие команды",
)


_NARRATIVE_MARKERS = (
    "подробный отч",
    "подробный отчет",
    "напиши отч",
    "напиши отчет",
    "напиши анализ",
    "текстовый отч",
    "текстовый отчет",
    "развернут",
    "полный отч",
    "полный отчет",
    "аналитический отч",
    "аналитический отчет",
    "сформируй отч",
    "сформируй отчет",
    "сделай отч",
    "сделай отчет",
    "опиши продаж",
    "обзор продаж",
    "подробный анализ",
)


def _wants_narrative(q: str) -> bool:
    if any(marker in q for marker in _NARRATIVE_MARKERS):
        return True
    if ("отч" in q or "отчет" in q) and any(
        word in q for word in ("продаж", "выручк", "файл", "данн", "дефицит")
    ):
        return not any(marker in q for marker in _CHART_MARKERS)
    return False


def _wants_help(q: str) -> bool:
    return any(marker in q for marker in _HELP_MARKERS)


def _facts_pack(df: pd.DataFrame, file_context=None) -> str:
    lines: list[str] = []
    packed = ""
    if file_context is not None:
        block = getattr(file_context, "prompt_block", lambda: "")()
        if block:
            lines.append(block)
            packed = block
        for fact in list(getattr(file_context, "facts", None) or [])[:8]:
            if fact and fact not in packed:
                lines.append(fact)
                packed += "\n" + fact
    for item in get_basic_insights(df)[:6]:
        if item and item not in packed:
            lines.append(item)
            packed += "\n" + item
    return "\n".join(lines)


def _period_facts(df: pd.DataFrame, question: str) -> str:
    grouped = _period_groups(df, question)
    if not grouped:
        return ""
    label, items, value_col = grouped
    lines = [
        f"В файле {len(items)} срезов по {label} "
        f"(колонка «{value_col}»):"
    ]
    for name, value in items:
        lines.append(f"- {name}: {_format_number(value)}")
    return "\n".join(lines)


def _period_groups(df: pd.DataFrame, question: str):
    q = question.lower()
    if "месяц" in q:
        label, func = "месяцам", data_tools.group_by_month
    elif "год" in q or "ежегодн" in q:
        label, func = "годам", data_tools.group_by_year
    else:
        label, func = "кварталам", data_tools.group_by_quarter
    data = func(df, question)
    if "error" in data or not data.get("groups"):
        if label != "месяцам":
            data = data_tools.group_by_month(df, question)
            label = "месяцам"
        if "error" in data or not data.get("groups"):
            return None
    items = list(data["groups"].items())
    return label, items, str(data.get("value_column") or "")


_RU_COUNTS = {
    "два": 2,
    "две": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
}


def _asked_period_count(question: str) -> int | None:
    q = question.lower()
    match = re.search(r"(\d+)\s*квартал", q)
    if match:
        return int(match.group(1))
    match = re.search(r"(\d+)\s*месяц", q)
    if match:
        return int(match.group(1))
    for word, count in _RU_COUNTS.items():
        if re.search(rf"{word}\s*квартал", q) or re.search(rf"{word}\s*месяц", q):
            return count
    return None


def _delta_phrase(previous: float | None, current: float) -> str:
    if previous is None or previous == 0:
        return " — первый срез в файле"
    change = (current - previous) / abs(previous) * 100
    if abs(change) < 0.5:
        return " — почти без изменений к предыдущему"
    if change > 0:
        return f" — рост на {change:.0f}% к предыдущему"
    return f" — снижение на {abs(change):.0f}% к предыдущему"


def _top_line(df: pd.DataFrame, question: str, semantic: str, label: str) -> str:
    result = data_tools.get_top_n(df, question, semantic=semantic, n=3)
    groups = result.get("groups") or {}
    if not groups:
        return ""
    bits = [f"{name} ({_format_number(value)})" for name, value in list(groups.items())[:3]]
    return f"По {label}: " + "; ".join(bits) + "."


def _draft_narrative(df: pd.DataFrame, question: str) -> str:
    """Готовый отчёт для руководителя: цифры pandas, без карточки файла."""
    total = data_tools.get_sum(df, question)
    span = _get_date_period(df)
    metric = total.get("column") or "сумма"
    lines: list[str] = []
    lines.append("**Отчёт по продажам**")
    lines.append("")
    opener = f"В выгрузке {len(df)} записей"
    if span:
        opener += f" за период {span}"
    opener += "."
    if "value" in total:
        opener += (
            f" Итого по колонке «{metric}»: {_format_number(total['value'])}."
        )
    lines.append(opener)

    grouped = _period_groups(df, question)
    if grouped:
        label, items, value_col = grouped
        adj = {
            "кварталам": "квартальных",
            "месяцам": "месячных",
            "годам": "годовых",
        }.get(label, "периодных")
        lines.append("")
        lines.append(f"**Динамика по {label}**")
        lines.append(
            f"В файле {len(items)} {adj} срезов"
            + (f" по «{value_col}»" if value_col else "")
            + "."
        )
        asked = _asked_period_count(question)
        if asked and len(items) < asked:
            lines.append(
                f"Запрошено {asked} срезов, в данных есть только {len(items)} — "
                "ниже все доступные периоды, без домыслов."
            )
        previous = None
        for name, value in items:
            lines.append(
                f"• {name}: {_format_number(value)}{_delta_phrase(previous, value)}"
            )
            previous = value
        peak_name, peak_value = max(items, key=lambda item: item[1])
        low_name, low_value = min(items, key=lambda item: item[1])
        lines.append(
            f"Максимум — {peak_name} ({_format_number(peak_value)}), "
            f"минимум — {low_name} ({_format_number(low_value)})."
        )

    lines.append("")
    lines.append("**Лидеры**")
    for semantic, label in (
        ("client", "клиентам"),
        ("manager", "менеджерам"),
        ("department", "подразделениям"),
    ):
        line = _top_line(df, question, semantic, label)
        if line:
            lines.append(line)
    if lines[-1] == "**Лидеры**":
        lines.append("В файле не удалось выделить топ по клиентам или менеджерам.")

    lines.append("")
    lines.append("**Выводы**")
    if grouped:
        _, items, _ = grouped
        peak_name, _ = max(items, key=lambda item: item[1])
        lines.append(
            f"Продажи распределены неравномерно: основной вклад даёт {peak_name}. "
            "Имеет смысл отдельно разобрать причины пика и просадки соседних периодов."
        )
        if len(items) >= 2:
            last_name, last_value = items[-1]
            prev_value = items[-2][1]
            if last_value < prev_value:
                lines.append(
                    f"Последний срез ({last_name}) слабее предыдущего — "
                    "проверьте, не обрезан ли период неполной выгрузкой."
                )
    else:
        lines.append(
            "По датам разбить продажи не удалось: в файле нет надёжной колонки периода."
        )
    return "\n".join(lines)


def _looks_like_file_card(text: str) -> bool:
    low = (text or "").lower()
    markers = (
        "понимание этого файла",
        "пустых ячеек",
        "зерно:",
        "одна строка =",
        "колонки:",
        "llm_ready",
        "дашборд (сделки)",
        "таблица (сделки)",
    )
    return any(marker in low for marker in markers)


def _drops_period_facts(polished: str, draft: str) -> bool:
    periods = re.findall(r"20\d{2}Q[1-4]", draft)
    if len(periods) < 2:
        return False
    found = sum(1 for period in periods if period in polished)
    return found < max(2, len(periods) // 2)


def _format_sheet_sample(sample: list | None) -> str:
    if not sample:
        return ""
    rows: list[str] = []
    for row in sample[:2]:
        if not isinstance(row, dict):
            continue
        bits = [
            f"{k}={v}"
            for k, v in list(row.items())[:6]
            if v not in (None, "", "nan", "None")
        ]
        if bits:
            rows.append(" · ".join(bits))
    if not rows:
        return ""
    return " Образец: " + "; ".join(rows) + "."


def _sheet_catalog_answer(file_context, sheets) -> str:
    active = getattr(file_context, "active_sheet", "") or next(
        (s.name for s in sheets if getattr(s, "active", False)), ""
    )
    lines = ["В книге такие листы:"]
    for sheet in sheets:
        mark = " (рабочий)" if getattr(sheet, "active", False) else ""
        cols = ", ".join(f"«{c}»" for c in list(sheet.columns)[:8])
        lines.append(
            f"• «{sheet.name}»{mark}: {sheet.rows} строк"
            + (f", колонки {cols}" if cols else "")
            + "."
        )
    if active:
        lines.append(
            f"Цифры дашборда и расчёты чата сейчас берутся с листа «{active}»."
        )
    return "\n".join(lines)


def _describe_other_sheets(question: str, file_context) -> str | None:
    if file_context is None:
        return None
    sheets = list(getattr(file_context, "sheets", None) or [])
    if len(sheets) < 2:
        return None
    q = question.lower()
    catalog_markers = (
        "какие лист",
        "сколько лист",
        "все лист",
        "листы книги",
        "какие есть лист",
        "другие лист",
        "остальные лист",
    )
    if any(marker in q for marker in catalog_markers):
        return _sheet_catalog_answer(file_context, sheets)

    active = (getattr(file_context, "active_sheet", "") or "").lower()
    matches = []
    for sheet in sheets:
        name = (getattr(sheet, "name", "") or "").strip()
        if name and name.lower() in q and name.lower() != active:
            matches.append(sheet)
    if not matches:
        return None
    active_name = getattr(file_context, "active_sheet", "") or ""
    parts = []
    for sheet in matches:
        cols = ", ".join(f"«{c}»" for c in list(sheet.columns)[:12])
        sample = _format_sheet_sample(getattr(sheet, "sample", None) or [])
        extra = ""
        if not getattr(sheet, "active", False) and active_name:
            extra = (
                f" Цифры дашборда считаются по листу «{active_name}», не по этому."
            )
        parts.append(
            f"Лист «{sheet.name}»: {sheet.rows} строк, "
            f"{sheet.n_columns} колонок. Колонки: {cols}.{sample}{extra}"
        )
    return "\n".join(parts)


def _exec_general(df: pd.DataFrame, action: dict, file_context=None) -> dict:
    if _wants_narrative(str(action.get("question") or "").lower()):
        return _exec_narrative(df, str(action.get("question") or ""), file_context)
    facts = _facts_pack(df, file_context)
    prompt = f"""Ты аналитик выгрузок 1С. Ответь на вопрос 2–5 предложениями на русском.

Вопрос: {action.get("question", "")}

Посчитанные факты (опирайся ТОЛЬКО на них, не выдумывай цифры и колонки):
{facts}

Если фактов не хватает — скажи, чего не хватает. Не предлагай меню умений."""

    try:
        answer = _ask_llm(prompt)
    except OllamaUnavailableError:
        if facts:
            return {"answer": facts}
        return {"answer": "LLM недоступна, а посчитанных фактов по файлу нет."}

    return {"answer": answer}


def _exec_narrative(df: pd.DataFrame, question: str, file_context=None) -> dict:
    draft = _draft_narrative(df, question)
    prompt = f"""Ты аналитик продаж. Ниже уже посчитанный отчёт с верными цифрами.
Перепиши его живым языком для руководителя: 8–15 предложений, абзацы.
Сохрани ВСЕ цифры и названия периодов. Можно чуть пояснить рост/падение.
Запрещено: копировать карточку файла, писать «понимание файла», перечислять колонки
и долю пустых ячеек, выдумывать периоды и суммы, которых нет в тексте.

Вопрос пользователя: {question}

Готовый отчёт:
{draft}"""

    try:
        polished = (_ask_llm(prompt, num_predict=1800) or "").strip()
    except OllamaUnavailableError:
        return {"answer": draft}

    if (
        not polished
        or _looks_like_file_card(polished)
        or _drops_period_facts(polished, draft)
        or len(polished) < max(280, len(draft) // 3)
    ):
        return {"answer": draft}
    return {"answer": polished}


def _help_answer(df: pd.DataFrame, file_context=None) -> dict:
    columns = ", ".join(f"«{c}»" for c in list(df.columns)[:12])
    ideas = ""
    if file_context is not None:
        items = list(getattr(file_context, "dashboard_ideas", None) or [])[:4]
        if items:
            listing = "\n".join(f"• «{idea}»" for idea in items)
            ideas = f"\nПо этому файлу можно спросить:\n{listing}\n"
        summary = getattr(file_context, "summary", "") or ""
        if summary:
            ideas = f"\n{summary}\n" + ideas
    return {
        "answer": (
            "Вот что я умею:\n"
            "• **Показатели:** «Общая выручка», «Средний чек», «Сколько строк?»\n"
            "• **Лидеры:** «Топ-5 клиентов», «Лучший менеджер»\n"
            "• **Диаграммы:** «Круговая диаграмма дефицита по подразделениям», "
            "«График выручки по месяцам», «Диаграмма по менеджерам»\n"
            "• **Выводы:** «Основные инсайты»\n"
            f"{ideas}"
            f"Колонки в файле: {columns}…"
        )
    }
