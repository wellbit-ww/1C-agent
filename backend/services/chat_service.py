"""Маршрутизатор чат-запросов.

Три уровня понимания вопроса:
1. Быстрый путь — ключевые слова (мгновенно, без LLM);
2. LLM-классификация вопроса в структурированную JSON-команду;
3. Честный fallback со списком возможностей и примерами.

Команды исполняются детерминированно через pandas — LLM только распознаёт
намерение и формулирует текст. Цифры считает pandas, не модель.
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
from services.insights_service import (
    _format_number,
    _get_date_period,
    get_basic_insights,
)
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
    "supplier": ("поставщик",),
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
{file_context_block}{history_block}
Возможные команды:
- {{"action": "stat", "operation": "row_count"|"column_count"|"columns"|"sum"|"mean"|"max"|"min"|"unique_count"|"null_count"|"duplicates_count"}}
- {{"action": "stat", "operation": "top", "semantic": "<группа>", "n": 5}}
- {{"action": "stat", "operation": "group", "agg": "sum"|"mean"|"count", "semantic": "<группа>"}}
- {{"action": "chart", "chart_type": "bar"|"pie"|"line", "group_semantic": "<группа>", "value_semantic": "<метрика>", "period": "month"|"quarter"|"year", "agg": "sum"|"mean"|"count", "top_n": 10, "group_column": "<точное имя>", "value_column": "<точное имя>"}}
- {{"action": "insights"}} — основные выводы по данным
- {{"action": "general"}} — открытый вопрос о содержимом файла
- {{"action": "help"}} — вопрос не связан с данными

<группа>: client, manager, region, department, supplier, status. <метрика>: revenue, deficit, amount.
group_column / value_column — точные имена из списка колонок (предпочтительнее semantic, если имя известно). Не выдумывай колонки.
Предпочитай action=general (ответ по фактам файла), а не help. help — только если спросили «что ты умеешь».
Просьба написать отчёт, подробный анализ, обзор продаж — action=general, не chart.
Если вопрос составной («и», «а также») — верни список команд: [{{...}}, {{...}}].
Если вопрос-уточнение («а по менеджерам?», «а теперь круговая») — учитывай контекст диалога.
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


def _build_history_block(history: list[dict] | None) -> str:
    """Компактный контекст диалога для follow-up вопросов («а по менеджерам?»)."""
    if not history:
        return ""

    from config import CHAT_HISTORY_CONTEXT

    lines = []
    for message in history[-CHAT_HISTORY_CONTEXT:]:
        role = "Пользователь" if message.get("role") == "user" else "Ассистент"
        text = str(message.get("content", ""))[:150]
        lines.append(f"{role}: {text}")

    return "Контекст диалога:\n" + "\n".join(lines) + "\n"


def _file_context_block(file_context) -> str:
    if file_context is None:
        return ""
    block = getattr(file_context, "prompt_block", lambda: "")()
    if not block:
        return ""
    return block.replace("{", "{{").replace("}", "}}") + "\n"


def _match_column(df: pd.DataFrame, name) -> str | None:
    if not name:
        return None
    target = str(name).strip()
    for col in df.columns:
        if str(col) == target:
            return col
    return None


def _llm_classify(
    question: str,
    df: pd.DataFrame,
    history: list[dict] | None = None,
    file_context=None,
) -> list[dict] | None:
    # длинный список колонок раздувает prompt-eval на CPU — ограничиваем
    columns = ", ".join(str(c) for c in df.columns[:25])
    prompt = _LLM_PROMPT.format(
        columns=columns,
        file_context_block=_file_context_block(file_context),
        history_block=_build_history_block(history),
        question=question,
    )

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
        chart["pin_spec"] = {
            "title": title,
            "chart_type": chart_type if chart_type in ("bar", "pie", "line") else "line",
            "source": {
                "kind": "period",
                "period": period,
                "value_column": data.get("value_column"),
            },
            "agg": "sum",
            "sort": "none",
        }
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
    group_col = _match_column(df, action.get("group_column"))
    if not group_col and group_semantic:
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
        value_col = _match_column(df, action.get("value_column"))
        value_semantic = action.get("value_semantic")
        if not value_col and value_semantic:
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

    chart["pin_spec"] = {
        "title": title,
        "chart_type": "pie" if chart_type == "pie" else "bar",
        "source": {
            "kind": "group",
            "group_column": group_col,
            "value_column": value_col,
        },
        "agg": agg,
        "top_n": top_n,
    }
    leaders = ", ".join(
        f"{name} ({_fmt(value)})" for name, value in list(grouped.items())[:3]
    )
    return {
        "answer": f"Построил диаграмму «{title}». Лидеры: {leaders}.",
        "chart": chart,
    }


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
        answer = ask_llm(prompt)
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
        polished = (ask_llm(prompt, num_predict=1800) or "").strip()
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


_INTERPRET_HINT = re.compile(
    r"поясн|интерпрет|почему\s+так|что это знач|прокоммент",
    re.I,
)


def _wants_interpret(question: str) -> bool:
    return bool(_INTERPRET_HINT.search(question or ""))


def _maybe_interpret(question: str, answer: str, file_context=None) -> str:
    if not answer:
        return ""
    facts = ""
    if file_context is not None:
        facts = "; ".join(list(getattr(file_context, "facts", None) or [])[:6])
    prompt = f"""Добавь 1–3 предложения интерпретации к ответу аналитика. Не меняй цифры.

Вопрос: {question}
Ответ: {answer[:800]}
Факты: {facts[:600]}

Только интерпретация на русском, без повторения всего ответа."""
    try:
        note = ask_llm(prompt)
    except Exception:
        return ""
    return (note or "").strip()


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


def _normalize_actions(actions: list[dict] | None, question: str) -> list[dict]:
    if not actions:
        return [{"action": "general"}]
    q = question.lower()
    if _wants_narrative(q):
        return [{"action": "general"}]
    out = []
    for action in actions:
        kind = action.get("action")
        if kind == "help" and not _wants_help(q):
            out.append({**action, "action": "general"})
        else:
            out.append(action)
    return out or [{"action": "general"}]


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

# маркеры, которые делают часть составного вопроса «самостоятельной» просьбой
_INTENT_MARKERS = _CHART_MARKERS + (
    "топ",
    "лучш",
    "сколько",
    "сумм",
    "общ",
    "средн",
    "максим",
    "миним",
    "динамик",
    "инсайт",
    "выручк",
    "дефицит",
)

_COMPOUND_SEPARATORS = re.compile(r"\s+(?:и|а также|также|плюс)\s+")


def _is_compound(q: str) -> bool:
    """Составной вопрос: минимум две части с самостоятельными просьбами.

    Для них быстрый путь отвечает только на первую часть — поэтому
    такие вопросы сначала отправляем в LLM-разбор (он вернёт список команд).
    """
    parts = [part.strip() for part in _COMPOUND_SEPARATORS.split(q) if part.strip()]
    if len(parts) < 2:
        return False
    meaningful = sum(
        1 for part in parts if any(marker in part for marker in _INTENT_MARKERS)
    )
    return meaningful >= 2


def _execute_actions(
    df: pd.DataFrame,
    question: str,
    actions: list[dict],
    file_context=None,
) -> dict:
    answers: list[str] = []
    charts: list[dict] = []
    actions = _normalize_actions(actions, question)

    for action in actions[:3]:
        action["question"] = question
        kind = action.get("action")
        if kind == "chart":
            result = _exec_chart(df, action)
        elif kind == "general":
            result = _exec_general(df, action, file_context=file_context)
        elif kind == "help":
            result = _help_answer(df, file_context=file_context)
        else:
            if kind == "insights":
                action["operation"] = "insights"
            result = _exec_stat(df, action)

        answers.append(result["answer"])
        if result.get("chart"):
            charts.append(result["chart"])

    text = "\n\n".join(answers)
    kinds = {a.get("action") for a in actions[:3]}
    if (
        kinds & {"stat", "chart"}
        and "general" not in kinds
        and _wants_interpret(question)
    ):
        note = _maybe_interpret(question, text, file_context=file_context)
        if note:
            text = f"{text}\n\n{note}"
    return {"answer": text, "charts": charts}


def handle_question(
    df: pd.DataFrame,
    question: str,
    history: list[dict] | None = None,
    file_context=None,
) -> dict:
    """Возвращает {"answer": str, "charts": [chart_dict, ...]}."""
    q = question.lower().strip()

    if _wants_help(q):
        return {**_help_answer(df, file_context=file_context), "charts": []}

    if _wants_narrative(q):
        result = _exec_narrative(df, question, file_context=file_context)
        return {"answer": result["answer"], "charts": []}

    sheet_text = _describe_other_sheets(question, file_context)
    if sheet_text:
        return {"answer": sheet_text, "charts": []}

    # 0. Составной вопрос — приоритет LLM-разбора (вернёт список команд).
    # Если LLM недоступна — проваливаемся в быстрый путь (частичный ответ).
    if _is_compound(q):
        actions = _llm_classify(
            question, df, history, file_context=file_context
        )
        if actions:
            return _execute_actions(df, question, actions, file_context=file_context)

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

    # 3. LLM-классификация в JSON (поддерживает составные вопросы и контекст)
    actions = _llm_classify(
        question, df, history, file_context=file_context
    )
    return _execute_actions(
        df, question, _normalize_actions(actions, question), file_context=file_context
    )
