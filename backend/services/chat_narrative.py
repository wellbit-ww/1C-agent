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


ANALYST_SYSTEM_PROMPT = """Ты — агент-аналитик коммерческих выгрузок 1С.
Разбираешь файл, объясняешь цифры руководителю и даёшь практические советы.
Типы файлов: воронка этапов продаж, дефицит/задолженность, отчёт ПДО, гарантия.

Как отвечать
1. Сразу ответь на вопрос: суть в 1–2 предложениях.
2. Факты — только из блока «Посчитанные факты». Не выдумывай периоды, клиентов, службы и суммы. Не подменяй одну метрику другой.
3. Смысл — что цифра значит для выручки, воронки, оплаты или производства.
4. Советы — 2–4 конкретных шага: кого разобрать, какой риск, что проверить в данных. Каждый совет должен следовать из фактов. Если фактов мало — скажи, чего не хватает, и не строй план из догадок.

Дашборды уже построены в интерфейсе. Опирайся на них и подсказывай, куда смотреть:
- Воронка продаж: «Данные», «Динамика», «Конверсия», «Деньги», «Этапы продаж».
- Дефицит: «Продажи» (прогноз и зарегистрированные заказы по службам), «Оплата» (структура задолженности).
Если вопрос про динамику, конверсию, деньги, этапы, прогноз или оплату — назови вкладку и добавь то, чего на графике нет: концентрацию, причины, риск.

Правила данных
- Зерно строки бери из справки по файлу (сделка, заказ, строка дефицита). Витрины 1С на других листах не складывай с рабочим листом.
- «Сумма по сделке» — потенциал воронки, не поступившие деньги.
- Неоплаченный остаток не равен сумме заказа и не равен оплаченному.
- Один период в файле — это не тренд и не «пик относительно других кварталов». Объясняй состав суммы: клиенты, менеджеры, службы.
- Не копируй карточку файла, не перечисляй пустые колонки и не предлагай меню команд.
- Запрещено копировать структуру «Отчёт / Динамика / Лидеры / Выводы» или отдавать готовый шаблон отчёта. Пиши связный анализ под вопрос.

Пиши по-русски, коротко и по делу, языком для руководителя."""

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
    "проанализируй",
    "проанализир",
    "сделай вывод",
    "дай вывод",
    "сделай совет",
    "дай совет",
    "как увеличить",
    "как вырастить",
    "как поднять",
)


def _wants_narrative(q: str) -> bool:
    if any(marker in q for marker in _NARRATIVE_MARKERS):
        return True
    if any(word in q for word in ("вывод", "совет", "рекоменд")) and any(
        word in q for word in ("файл", "данн", "продаж", "доход", "выручк", "дефицит")
    ):
        return not any(marker in q for marker in _CHART_MARKERS)
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


def _looks_like_template_report(text: str) -> bool:
    """Старый шаблон отчёта: Динамика + Лидеры + Выводы."""
    low = (text or "").lower()
    hits = sum(
        1
        for marker in (
            "динамика по",
            "**лидеры**",
            "лидеры\n",
            "**выводы**",
            "выводы\n",
            "отчёт по продажам",
            "отчет по продажам",
            "отчёт по дефициту",
            "отчет по дефициту",
        )
        if marker in low
    )
    return hits >= 2


def _draft_deficit_narrative(df: pd.DataFrame) -> str:
    from services.chat_answers import deficit_meaning, layout_values, rub
    from services.column_resolver import resolve_semantic_column
    from services.file_context_service import _top_sums
    from services.report_profiles.deficit_profile import deficit_kpis, detect_deficit_money_layout

    layout = detect_deficit_money_layout(df)
    span = _get_date_period(df)
    lines = [
        "ФАКТЫ ДЕФИЦИТА (для анализа, не копировать как отчёт):",
        f"- строк: {len(df)}" + (f"; период: {span}" if span else ""),
        "- зерно: одна строка = заказ/позиция задолженности",
    ]
    for kpi in deficit_kpis(df)[:8]:
        unit = "" if kpi["name"] in {"unique_customers", "unique_departments"} else " руб."
        lines.append(f"- {kpi['label']}: {_format_number(kpi['raw_value'])}{unit}")
    vals = layout_values(df, layout)
    meaning = deficit_meaning(
        unpaid=vals["unpaid"] if layout.unpaid else None,
        order=vals["order"] if layout.order_sum else None,
        paid=vals["paid"] if layout.paid else None,
        order_col=layout.order_sum,
    )
    if meaning:
        lines.append(f"- смысл: {meaning}")
    client = resolve_semantic_column(df, "", "client", dtype="categorical")
    unpaid_top: list = []
    if client and layout.unpaid:
        unpaid_top = _top_sums(df, client, layout.unpaid, n=5)
        if unpaid_top:
            lines.append("- кто больше должен:")
            for name, value in unpaid_top:
                lines.append(f"  • {name}: {rub(value)}")
    if client and layout.order_sum:
        order_top = _top_sums(df, client, layout.order_sum, n=5)
        if order_top:
            lines.append("- кто больше заказал:")
            for name, value in order_top:
                lines.append(f"  • {name}: {rub(value)}")
            if layout.unpaid and unpaid_top and order_top and unpaid_top[0][0] != order_top[0][0]:
                lines.append(
                    f"- лидеры разные: остаток «{unpaid_top[0][0]}», "
                    f"сумма заказа «{order_top[0][0]}»"
                )
    lines.append("- остаток ≠ объём заказов")
    return "\n".join(lines)


def _draft_narrative(df: pd.DataFrame, question: str, file_context=None) -> str:
    """Сырые факты pandas для LLM — не готовый отчёт пользователю."""
    from services.chat_answers import report_kind_hint

    if report_kind_hint(file_context, df) == "deficit":
        return _draft_deficit_narrative(df)
    total = data_tools.get_sum(df, question)
    span = _get_date_period(df)
    metric = total.get("column") or "сумма"
    lines: list[str] = [
        "ФАКТЫ (для анализа, не копировать как отчёт):",
        f"- строк в выгрузке: {len(df)}"
        + (f"; период: {span}" if span else ""),
    ]
    if "value" in total:
        lines.append(f"- итог «{metric}»: {_format_number(total['value'])}")

    grouped = _period_groups(df, question)
    if grouped:
        label, items, value_col = grouped
        lines.append(
            f"- срезы по {label}"
            + (f" («{value_col}»)" if value_col else "")
            + f": {len(items)} шт."
        )
        asked = _asked_period_count(question)
        if asked and len(items) < asked:
            lines.append(
                f"- запрошено срезов: {asked}, в данных только {len(items)}"
            )
        previous = None
        for name, value in items:
            lines.append(
                f"  • {name}: {_format_number(value)}{_delta_phrase(previous, value)}"
            )
            previous = value
        peak_name, peak_value = max(items, key=lambda item: item[1])
        low_name, low_value = min(items, key=lambda item: item[1])
        lines.append(
            f"- максимум: {peak_name} ({_format_number(peak_value)}); "
            f"минимум: {low_name} ({_format_number(low_value)})"
        )
    else:
        lines.append("- разбивка по датам недоступна")

    for semantic, label in (
        ("client", "клиентам"),
        ("manager", "менеджерам"),
        ("department", "подразделениям"),
    ):
        line = _top_line(df, question, semantic, label)
        if line:
            lines.append(f"- {line.lstrip('• ').strip()}")
    return "\n".join(lines)


def _facts_fallback_answer(facts: str, *, llm_down: bool = False) -> str:
    """Если модель недоступна — краткие факты, без шаблона «Динамика/Лидеры/Выводы»."""
    head = (
        "Модель сейчас недоступна, ниже только посчитанные факты. "
        if llm_down
        else "Не удалось собрать развёрнутый анализ, ниже посчитанные факты. "
    )
    return head + "Задайте вопрос ещё раз или уточните срез.\n\n" + facts


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


def _format_sheet_sample(sample: list | None, *, showcase: bool = False) -> str:
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
    prefix = " На витрине 1С в образце: " if showcase else " Образец: "
    return prefix + "; ".join(rows) + "."


def _sheet_mentioned(question: str, name: str) -> bool:
    q = (question or "").lower().replace("ё", "е")
    n = (name or "").strip().lower().replace("ё", "е")
    if not n:
        return False
    if n in q:
        return True
    tokens = re.findall(r"[a-zа-я0-9]{4,}", n)
    tokens = [t for t in tokens if t not in {"лист", "sheet"}]
    if not tokens:
        return False

    def _in_question(token: str) -> bool:
        if token in q:
            return True
        stem = token[:4] if len(token) >= 4 else token
        return len(stem) >= 4 and stem in q

    if len(tokens) >= 2:
        return all(_in_question(t) for t in tokens)
    return _in_question(tokens[0])


def _sheet_catalog_answer(file_context, sheets) -> str:
    active = getattr(file_context, "active_sheet", "") or next(
        (s.name for s in sheets if getattr(s, "active", False)), ""
    )
    lines = ["В книге такие листы:"]
    for sheet in sheets:
        mark = " (рабочий)" if getattr(sheet, "active", False) else ""
        role = getattr(sheet, "role_label", "") or getattr(sheet, "role", "")
        role_bit = f", {role}" if role else ""
        cols = ", ".join(f"«{c}»" for c in list(sheet.columns)[:8])
        lines.append(
            f"• «{sheet.name}»{mark}{role_bit}: {sheet.rows} строк"
            + (f", колонки {cols}" if cols else "")
            + "."
        )
        note = getattr(sheet, "grain_note", "") or ""
        if note and not getattr(sheet, "active", False):
            lines.append(f"  {note}")
    if active:
        lines.append(
            f"Цифры дашборда и расчёты чата сейчас берутся с листа «{active}»."
        )
        vitrines = [
            s.name
            for s in sheets
            if getattr(s, "role", "") in {"dashboard", "summary"}
        ]
        if vitrines:
            lines.append(
                "Витрины 1С ("
                + ", ".join(f"«{n}»" for n in vitrines)
                + ") — другое зерно, их итоги не складывать с рабочим листом."
            )
    return "\n".join(lines)


_SHEET_METRIC = (
    "скольк",
    "сумм",
    "топ",
    "график",
    "диаграмм",
    "остат",
    "оплат",
    "выручк",
)


def _describe_other_sheets(question: str, file_context) -> str | None:
    if file_context is None:
        return None
    sheets = list(getattr(file_context, "sheets", None) or [])
    if not sheets:
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

    if len(sheets) < 2:
        return None

    matches = [
        sheet
        for sheet in sheets
        if _sheet_mentioned(question, getattr(sheet, "name", "") or "")
    ]
    if not matches:
        return None
    only_active = (
        len(matches) == 1
        and getattr(matches[0], "active", False)
        and any(marker in q for marker in _SHEET_METRIC)
    )
    if only_active:
        return None
    parts = []
    for sheet in matches:
        cols = ", ".join(f"«{c}»" for c in list(sheet.columns)[:12])
        role = getattr(sheet, "role_label", "") or getattr(sheet, "role", "")
        showcase = getattr(sheet, "role", "") in {"dashboard", "summary"}
        sample = _format_sheet_sample(
            getattr(sheet, "sample", None) or [], showcase=showcase
        )
        extra = getattr(sheet, "grain_note", "") or ""
        facts = [
            f
            for f in list(getattr(sheet, "facts", None) or [])[:4]
            if f and f != extra
        ]
        fact_text = (" " + " ".join(facts)) if facts else ""
        if not extra and not getattr(sheet, "active", False):
            active_name = getattr(file_context, "active_sheet", "") or ""
            if active_name:
                extra = (
                    f"Цифры дашборда считаются по листу «{active_name}», не по этому."
                )
        parts.append(
            f"Лист «{sheet.name}» ({role or 'лист'}): {sheet.rows} строк, "
            f"{sheet.n_columns} колонок. Колонки: {cols}.{sample}{fact_text}"
            + (f" {extra}" if extra else "")
        )
    return "\n".join(parts)


def _exec_general(df: pd.DataFrame, action: dict, file_context=None) -> dict:
    if _wants_narrative(str(action.get("question") or "").lower()):
        return _exec_narrative(df, str(action.get("question") or ""), file_context)
    from services.chat_answers import (
        report_kind_hint,
        typed_frame_rules,
        typed_overview_draft,
    )
    from services.chat_question_pack import build_answer_facts

    question = str(action.get("question") or "")
    kind = report_kind_hint(file_context, df)
    draft = typed_overview_draft(df, file_context)
    facts = build_answer_facts(df, question, file_context=file_context)
    prompt = f"""{ANALYST_SYSTEM_PROMPT}

Дополнительно для этого файла: {typed_frame_rules(kind)}

Вопрос: {question}

Посчитанные факты:
{facts}

Краткая справка по файлу (только цифры, не копируй как ответ):
{draft}

Ответь связным текстом под вопрос. Не используй заголовки «Динамика», «Лидеры», «Выводы»."""

    try:
        answer = _ask_llm(prompt)
    except OllamaUnavailableError:
        return {
            "answer": _facts_fallback_answer(
                facts or draft or "Фактов по файлу нет.", llm_down=True
            )
        }

    polished = (answer or "").strip()
    if not polished or _looks_like_file_card(polished) or _looks_like_template_report(polished):
        return {"answer": _facts_fallback_answer(facts or draft or "")}
    return {"answer": polished}


def _exec_narrative(df: pd.DataFrame, question: str, file_context=None) -> dict:
    facts = _draft_narrative(df, question, file_context)
    kind_line = (
        "Для дефицита сохрани разницу остатка и суммы заказа."
        if "неоплачен" in facts.lower() or "дефицит" in facts.lower()
        else "Сохрани цифры и названия периодов из фактов."
    )
    prompt = f"""{ANALYST_SYSTEM_PROMPT}

{kind_line}

Вопрос пользователя: {question}

Посчитанные факты:
{facts}

Напиши новый аналитический ответ: вывод, объяснение динамики/состава и 2–4 совета.
Не копируй список фактов. Не используй заголовки «Отчёт по продажам», «Динамика», «Лидеры», «Выводы»."""

    try:
        polished = (_ask_llm(prompt, num_predict=2500) or "").strip()
    except OllamaUnavailableError:
        return {"answer": _facts_fallback_answer(facts, llm_down=True)}

    if not polished:
        return {"answer": _facts_fallback_answer(facts)}
    if _looks_like_file_card(polished) or _looks_like_template_report(polished):
        # Второй проход: модель ушла в шаблон — запретить явно
        retry = f"""{ANALYST_SYSTEM_PROMPT}

Вопрос: {question}

Факты:
{facts}

Прошлый ответ был шаблонным. Перепиши заново живым текстом без заголовков
«Динамика / Лидеры / Выводы / Отчёт»."""
        try:
            polished = (_ask_llm(retry, num_predict=2500) or "").strip()
        except OllamaUnavailableError:
            return {"answer": _facts_fallback_answer(facts, llm_down=True)}
        if not polished or _looks_like_template_report(polished) or _looks_like_file_card(polished):
            return {"answer": _facts_fallback_answer(facts)}
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
            "• **Заказ:** «Что с заказом САУП-000450», «Что с заказом Алабуги», "
            "«Что с заказом от 29.12»\n"
            "• **Сделки за период:** «Сколько сделок в январе 2025», "
            "«за неделю с 13 по 19 января», «сколько сделок 15.03.2025»\n"
            f"{ideas}"
            f"Колонки в файле: {columns}…"
        )
    }
