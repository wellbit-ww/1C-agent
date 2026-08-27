"""Маршрутизатор чат-запросов.

Три уровня понимания вопроса:
1. Быстрый путь — ключевые слова (мгновенно, без LLM);
2. LLM-классификация вопроса в структурированную JSON-команду;
3. Честный fallback со списком возможностей и примерами.

Команды исполняются детерминированно через pandas — LLM только распознаёт
намерение, но никогда не считает числа и не строит графики кодом.
"""

import json
import logging
import re

import pandas as pd

from services import data_tools
from services.chart_service import (
    create_bar_chart,
    create_line_chart,
    create_pie_chart,
)
from services.column_resolver import resolve_semantic_column
from services.exceptions import OllamaUnavailableError
from services.insights_service import _format_number, get_basic_insights
from services.llm_service import ask_llm, classify
from services.question_service import detect_intent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Уровень 1: ключевые слова
# ---------------------------------------------------------------------------

_CHART_MARKERS = (
    "график",
    "диаграмм",
    "построй",
    "нарисуй",
    "визуализ",
    "кругов",
    "пирог",
    "разбивк",
    "распределен",
    "столбик",
    "гистограм",
)

_GROUP_KEYWORDS = {
    "client": ("клиент", "заказчик", "контрагент", "компани", "покупател"),
    "manager": ("менеджер", "ответственн", "продав"),
    "region": ("регион", "город", "област"),
    "department": ("подразделен", "отдел", "департамент", "служб"),
}

_DEFICIT_MARKERS = ("дефицит", "задолжен", "неоплач", "остаток", "долг", "дебиторк")

_PERIOD_KEYWORDS = {
    "month": ("по месяц", "динамик", "тренд"),
    "quarter": ("квартал",),
    "year": ("по год", "ежегодн"),
}

_PERIOD_FUNCS = {
    "month": data_tools.group_by_month,
    "quarter": data_tools.group_by_quarter,
    "year": data_tools.group_by_year,
}

_PERIOD_LABELS = {
    "month": "по месяцам",
    "quarter": "по кварталам",
    "year": "по годам",
}


def _extract_n(q: str, default: int = 5) -> int:
    match = re.search(r"(?:топ|top)[\s-]*(\d+)", q)
    if not match:
        match = re.search(r"(\d+)\s*(?:лучш|крупнейш)", q)
    if not match:
        return default
    return max(1, min(int(match.group(1)), 50))


def _group_semantic_from_text(q: str) -> str | None:
    for semantic, markers in _GROUP_KEYWORDS.items():
        if any(marker in q for marker in markers):
            return semantic
    return None


def _value_semantic_from_text(q: str) -> str | None:
    if any(marker in q for marker in _DEFICIT_MARKERS):
        return "deficit"
    return None


def _detect_agg(q: str) -> str:
    if "средн" in q:
        return "mean"
    if "количеств" in q or "число сделок" in q or "сколько записей" in q:
        return "count"
    return "sum"


def _keyword_chart_action(q: str) -> dict | None:
    if not any(marker in q for marker in _CHART_MARKERS):
        return None

    action = {
        "action": "chart",
        "chart_type": "bar",
        "agg": _detect_agg(q),
        "top_n": _extract_n(q, default=10),
        "group_semantic": _group_semantic_from_text(q),
        "value_semantic": _value_semantic_from_text(q),
    }

    # явно названный тип диаграммы («круговая», «столбчатая») важнее периода:
    # «круговая по месяцам» — это pie по месячным срезам, а не line
    explicit_type = None
    if "кругов" in q or "пирог" in q:
        explicit_type = "pie"
    elif "столб" in q or "гистограм" in q:
        explicit_type = "bar"
    elif "линейн" in q or "линия" in q:
        explicit_type = "line"

    period = None
    for candidate, markers in _PERIOD_KEYWORDS.items():
        if any(marker in q for marker in markers):
            period = candidate
            break

    if explicit_type:
        action["chart_type"] = explicit_type
        if period:
            action["period"] = period
    elif period:
        action["chart_type"] = "line"
        action["period"] = period

    return action


_TOP_INTENT_SEMANTIC = {
    "top_client": "client",
    "top_manager": "manager",
    "top_region": "region",
}

_TREND_INTENT_PERIOD = {
    "trend_month": "month",
    "trend_quarter": "quarter",
    "trend_year": "year",
    "chart_monthly": "month",
    "chart_quarterly": "quarter",
    "chart_yearly": "year",
}

_CHART_INTENTS = {
    "chart_regions": {"chart_type": "pie", "group_semantic": "region"},
    "chart_clients": {"chart_type": "bar", "group_semantic": "client"},
    "chart_managers": {"chart_type": "bar", "group_semantic": "manager"},
    "chart_revenue": {"chart_type": "bar"},
    "chart_sales": {"chart_type": "bar"},
}


def _keyword_stat_action(question: str) -> dict | None:
    intent = detect_intent(question)
    if intent == "unknown":
        return None

    q = question.lower()

    if intent in _TOP_INTENT_SEMANTIC:
        return {
            "action": "stat",
            "operation": "top",
            "semantic": _TOP_INTENT_SEMANTIC[intent],
            "n": _extract_n(q),
        }

    if intent in _TREND_INTENT_PERIOD:
        return {
            "action": "chart",
            "chart_type": "line",
            "period": _TREND_INTENT_PERIOD[intent],
            "agg": _detect_agg(q),
        }

    if intent in _CHART_INTENTS:
        action = {"action": "chart", "agg": _detect_agg(q), "top_n": 10}
        action.update(_CHART_INTENTS[intent])
        return action

    if intent in ("group_sum", "group_mean", "group_count"):
        return {
            "action": "stat",
            "operation": "group",
            "agg": intent.removeprefix("group_"),
        }

    return {"action": "stat", "operation": intent}


# ---------------------------------------------------------------------------
# Уровень 2: LLM-классификация в JSON
# ---------------------------------------------------------------------------

_LLM_PROMPT = """/no_think
Ты — роутер запросов к Excel-таблице. Преобразуй вопрос пользователя в JSON-команду (или список команд для составного вопроса).

Колонки таблицы: {columns}

Возможные команды:
- {{"action": "stat", "operation": "row_count"|"column_count"|"columns"|"sum"|"mean"|"max"|"min"|"unique_count"|"null_count"|"duplicates_count"}}
- {{"action": "stat", "operation": "top", "semantic": "<группа>", "n": 5}}
- {{"action": "stat", "operation": "group", "agg": "sum"|"mean"|"count", "semantic": "<группа>"}}
- {{"action": "chart", "chart_type": "bar"|"pie"|"line", "group_semantic": "<группа>", "value_semantic": "<метрика>", "period": "month"|"quarter"|"year", "agg": "sum"|"mean"|"count", "top_n": 10}}
- {{"action": "insights"}} — основные выводы по данным
- {{"action": "general"}} — открытый вопрос о содержимом файла
- {{"action": "help"}} — вопрос не связан с данными

<группа>: client, manager, region, department. <метрика>: revenue, deficit, amount.
Если вопрос составной («и», «а также») — верни список команд: [{{...}}, {{...}}].
Ответь строго одним JSON, без пояснений и без markdown.

Вопрос: {question}"""

_ALLOWED_ACTIONS = {"stat", "chart", "insights", "general", "help"}


def _extract_json(text: str) -> dict | list | None:
    start = None
    for i, ch in enumerate(text):
        if ch in "{[":
            start = i
            break
    if start is None:
        return None

    depth = 0
    for j in range(start, len(text)):
        ch = text[j]
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:j + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _llm_classify(question: str, df: pd.DataFrame) -> list[dict] | None:
    # длинный список колонок раздувает prompt-eval на CPU — ограничиваем
    columns = ", ".join(str(c) for c in df.columns[:25])
    prompt = _LLM_PROMPT.format(columns=columns, question=question)

    try:
        raw = classify(prompt)
    except OllamaUnavailableError as exc:
        logger.warning("LLM-классификация недоступна: %s", exc)
        return None

    parsed = _extract_json(raw)
    if parsed is None:
        logger.warning("LLM вернула не-JSON: %s", raw[:200])
        return None

    actions = parsed if isinstance(parsed, list) else [parsed]
    valid = [
        a for a in actions
        if isinstance(a, dict) and a.get("action") in _ALLOWED_ACTIONS
    ]
    return valid or None


# ---------------------------------------------------------------------------
# Исполнение команд (детерминированно)
# ---------------------------------------------------------------------------

def _fmt(value) -> str:
    if isinstance(value, (int, float)):
        return _format_number(value)
    return str(value)


def _exec_stat(df: pd.DataFrame, action: dict) -> dict:
    op = action.get("operation", "")
    q = action.get("question", "")

    if op == "row_count":
        return {"answer": f"В таблице **{len(df)}** строк."}

    if op == "column_count":
        return {"answer": f"В таблице **{len(df.columns)}** колонок."}

    if op == "columns":
        listing = "\n".join(f"{i}. {c}" for i, c in enumerate(df.columns, 1))
        return {"answer": f"Колонки таблицы ({len(df.columns)}):\n{listing}"}

    if op == "insights":
        lines = "\n".join(f"• {i}" for i in get_basic_insights(df))
        return {"answer": f"**Основные факты:**\n{lines}"}

    if op == "duplicates_count":
        result = data_tools.get_duplicates_count(df)
        return {"answer": f"Полных дубликатов строк: **{result['value']}**."}

    if op == "null_count":
        result = data_tools.get_null_count(df)
        worst = sorted(result["columns"].items(), key=lambda x: -x[1])[:3]
        answer = f"Всего пропусков: **{result['value']}**."
        if worst:
            listing = "\n".join(f"• «{c}»: {n}" for c, n in worst)
            answer += f"\nБольше всего пропусков:\n{listing}"
        return {"answer": answer}

    simple_ops = {
        "sum": ("Сумма", data_tools.get_sum),
        "mean": ("Среднее", data_tools.get_mean),
        "max": ("Максимум", data_tools.get_max),
        "min": ("Минимум", data_tools.get_min),
        "unique_count": ("Уникальных значений", data_tools.get_unique_count),
    }
    if op in simple_ops:
        label, func = simple_ops[op]
        result = func(df, q)
        if "error" in result:
            return {"answer": f"Не удалось посчитать: {result['error']}"}
        return {
            "answer": f"{label} по колонке «{result['column']}»: **{_fmt(result['value'])}**"
        }

    if op == "top":
        semantic = action.get("semantic") or "client"
        n = int(action.get("n") or 5)
        result = data_tools.get_top_n(df, q, semantic=semantic, n=n)
        if "error" in result:
            return {"answer": f"Не удалось найти лидеров: {result['error']}"}
        lines = [
            f"{i}. {name} — {_fmt(value)}"
            for i, (name, value) in enumerate(result["groups"].items(), 1)
        ]
        return {
            "answer": (
                f"Топ-{n} по «{result['group_column']}» "
                f"(по сумме «{result['value_column']}»):\n" + "\n".join(lines)
            )
        }

    if op == "group":
        agg = action.get("agg", "sum")
        func = {
            "sum": data_tools.group_sum,
            "mean": data_tools.group_mean,
            "count": data_tools.group_count,
        }.get(agg, data_tools.group_sum)
        result = func(df, q, top_n=10)
        if "error" in result:
            return {"answer": f"Не удалось сгруппировать: {result['error']}"}
        agg_label = {"sum": "сумма", "mean": "среднее", "count": "количество"}[agg]
        value_part = f" «{result['value_column']}»" if result.get("value_column") else ""
        lines = [
            f"{i}. {name} — {_fmt(value)}"
            for i, (name, value) in enumerate(result["groups"].items(), 1)
        ]
        return {
            "answer": (
                f"Группировка по «{result['group_column']}» "
                f"({agg_label}{value_part}, топ-10):\n" + "\n".join(lines)
            )
        }

    return {"answer": "Не удалось выполнить операцию."}


def _exec_chart(df: pd.DataFrame, action: dict) -> dict:
    q = action.get("question", "")
    chart_type = action.get("chart_type", "bar")
    period = action.get("period")

    if chart_type == "line" and not period:
        period = "month"

    if period:
        func = _PERIOD_FUNCS.get(period, data_tools.group_by_month)
        data = func(df, q)
        if "error" in data or not data.get("groups"):
            logger.warning(
                "period chart failed: %s | columns=%s | dates=%s",
                data.get("error"),
                list(df.columns),
                data_tools.detect_date_columns(df)["columns"],
            )
            return {
                "answer": "Не удалось построить график динамики: "
                f"{data.get('error', 'нет данных')}."
            }
        title = f"{data['value_column']} {_PERIOD_LABELS[period]}"
        if chart_type == "pie":
            chart = create_pie_chart(data, title=title)
        elif chart_type == "bar":
            chart = create_bar_chart(data, title=title)
        else:
            chart = create_line_chart(data, title=title)
        if "error" in chart:
            return {"answer": chart["error"]}
        best = max(data["groups"].items(), key=lambda x: x[1])
        total = sum(data["groups"].values())
        return {
            "answer": (
                f"График «{title}». Всего за период: **{_fmt(total)}**, "
                f"пик — {best[0]} ({_fmt(best[1])})."
            ),
            "chart": chart,
        }

    group_semantic = action.get("group_semantic")
    group_col = None
    if group_semantic:
        group_col = resolve_semantic_column(df, q, group_semantic, dtype="categorical")
    if not group_col:
        group_col = data_tools._resolve_categorical_column(df, q)

    if not group_col:
        return {
            "answer": (
                "Не нашёл колонку для группировки. Уточните параметр, "
                "например: «диаграмма по подразделениям» или «график по менеджерам»."
            )
        }

    agg = action.get("agg", "sum")
    top_n = int(action.get("top_n") or 10)
    value_col = None

    if agg != "count":
        value_semantic = action.get("value_semantic")
        if value_semantic:
            value_col = resolve_semantic_column(df, q, value_semantic, dtype="numeric")
        if not value_col:
            value_col = data_tools._resolve_numeric_column(df, q)
        if not value_col:
            return {
                "answer": (
                    "Не нашёл числовую метрику для диаграммы. Уточните, "
                    "например: «дефицит по менеджерам» или «выручка по клиентам»."
                )
            }

    if agg == "count":
        grouped = df.groupby(group_col).size().sort_values(ascending=False)
        metric_label = "Количество записей"
    elif agg == "mean":
        grouped = df.groupby(group_col)[value_col].mean().sort_values(ascending=False)
        metric_label = f"Среднее «{value_col}»"
    else:
        grouped = df.groupby(group_col)[value_col].sum().sort_values(ascending=False)
        metric_label = f"«{value_col}»"

    grouped = grouped.head(top_n)
    if grouped.empty:
        logger.warning(
            "chart failed: empty groups | group_col=%s value_col=%s columns=%s",
            group_col,
            value_col,
            list(df.columns),
        )
        return {"answer": "Нет данных для построения диаграммы."}

    data = {
        "groups": {str(k): float(v) for k, v in grouped.to_dict().items()},
        "group_column": group_col,
        "value_column": value_col,
    }
    title = f"{metric_label} по «{group_col}» (топ-{top_n})"
    chart = (
        create_pie_chart(data, title=title)
        if chart_type == "pie"
        else create_bar_chart(data, title=title)
    )
    if "error" in chart:
        return {"answer": chart["error"]}

    leaders = ", ".join(
        f"{name} ({_fmt(value)})" for name, value in list(grouped.items())[:3]
    )
    return {
        "answer": f"Построил диаграмму «{title}». Лидеры: {leaders}.",
        "chart": chart,
    }


def _exec_general(df: pd.DataFrame, action: dict) -> dict:
    from services.analysis_service import get_basic_info

    info = get_basic_info(df)
    prompt = f"""Ты аналитик данных. Ответь на вопрос пользователя по Excel-таблице.

Вопрос: {action.get("question", "")}

Информация о таблице:
{info}

Ответь кратко и по делу на русском языке. Если данных для ответа недостаточно — честно скажи об этом."""

    try:
        answer = ask_llm(prompt)
    except OllamaUnavailableError as exc:
        return {"answer": f"LLM недоступна: {exc}"}

    return {"answer": answer}


def _help_answer(df: pd.DataFrame) -> dict:
    columns = ", ".join(f"«{c}»" for c in list(df.columns)[:12])
    return {
        "answer": (
            "Я пока не понял этот запрос. Вот что я умею:\n"
            "• **Показатели:** «Общая выручка», «Средний чек», «Сколько строк?»\n"
            "• **Лидеры:** «Топ-5 клиентов», «Лучший менеджер»\n"
            "• **Диаграммы:** «Круговая диаграмма дефицита по подразделениям», "
            "«График выручки по месяцам», «Диаграмма по менеджерам»\n"
            "• **Выводы:** «Основные инсайты»\n\n"
            f"Колонки в файле: {columns}…"
        )
    }


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def handle_question(df: pd.DataFrame, question: str) -> dict:
    """Возвращает {"answer": str, "charts": [chart_dict, ...]}."""
    q = question.lower().strip()

    # 1. Быстрый путь: явный запрос графика по ключевым словам
    chart_action = _keyword_chart_action(q)
    if chart_action:
        chart_action["question"] = question
        result = _exec_chart(df, chart_action)
        return {"answer": result["answer"], "charts": [result["chart"]] if result.get("chart") else []}

    # 2. Быстрый путь: известные keyword-интенты статистики/трендов
    stat_action = _keyword_stat_action(question)
    if stat_action:
        stat_action["question"] = question
        if stat_action.get("action") == "chart":
            result = _exec_chart(df, stat_action)
        else:
            result = _exec_stat(df, stat_action)
        return {"answer": result["answer"], "charts": [result["chart"]] if result.get("chart") else []}

    # 3. LLM-классификация в JSON (поддерживает составные вопросы)
    actions = _llm_classify(question, df)
    if not actions:
        return {"answer": _help_answer(df)["answer"], "charts": []}

    answers: list[str] = []
    charts: list[dict] = []

    for action in actions[:3]:
        action["question"] = question
        kind = action.get("action")
        if kind == "chart":
            result = _exec_chart(df, action)
        elif kind == "general":
            result = _exec_general(df, action)
        elif kind == "help":
            result = _help_answer(df)
        else:
            if kind == "insights":
                action["operation"] = "insights"
            result = _exec_stat(df, action)

        answers.append(result["answer"])
        if result.get("chart"):
            charts.append(result["chart"])

    return {"answer": "\n\n".join(answers), "charts": charts}
