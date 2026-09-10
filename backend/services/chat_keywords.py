"""Keyword fast-path for chat: chart/stat intents without LLM."""
import re

from services import data_tools
from services.question_service import detect_intent

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
