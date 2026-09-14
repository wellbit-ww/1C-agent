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

from services.chat_executors import _exec_chart, _exec_stat
from services.chat_keywords import (
    _CHART_MARKERS,
    _group_semantic_from_text,
    _keyword_chart_action,
    _keyword_stat_action,
)
from services.chat_lookup import (
    entity_name_words,
    exec_entity_metrics,
    exec_order_lookup,
    wants_entity_metrics,
    wants_order_lookup,
)
from services.chat_narrative import (
    _describe_other_sheets,
    _exec_general,
    _exec_narrative,
    _help_answer,
    _wants_help,
    _wants_narrative,
)
from services.chat_question_pack import (
    exec_named_compare,
    exec_rank_compare,
    wants_file_overview,
    wants_named_compare,
    wants_payment_overview,
    wants_rank_compare,
)
from services.exceptions import OllamaUnavailableError
from services.llm_service import ask_llm, classify
from services.question_service import detect_intent

logger = logging.getLogger(__name__)

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
- {{"action": "lookup", "query": "<номер заказа, заказчик или дата>"}} — один заказ: комментарий и карточка строки
- {{"action": "entity", "query": "<заказчик или менеджер>"}} — оплачено, остаток, сумма заказов по имени из вопроса
- {{"action": "compare"}} — сравнить двух заказчиков или «кто больше должен / кто больше заказал»
- {{"action": "general"}} — открытый вопрос о содержимом файла
- {{"action": "help"}} — вопрос не связан с данными

<группа>: client, manager, region, department, supplier, status. <метрика>: revenue, deficit, amount.
group_column / value_column — точные имена из списка колонок (предпочтительнее semantic, если имя известно). Не выдумывай колонки.
Имя из вопроса (КЭАЗ, Алабуга, Кусков) — action=entity, не group. Два имени («сравни Алабугу и Робел») — action=compare.
Предпочитай action=general (ответ по фактам файла), а не help. help — только если спросили «что ты умеешь».
Просьба написать отчёт, подробный анализ, обзор продаж — action=general, не chart.
Если вопрос составной («и», «а также») — верни список команд: [{{...}}, {{...}}].
Если вопрос-уточнение («а по менеджерам?», «а теперь круговая») — учитывай контекст диалога.
Ответь строго одним JSON, без пояснений и без markdown.

Вопрос: {question}"""

_ALLOWED_ACTIONS = {"stat", "chart", "insights", "lookup", "entity", "compare", "general", "help"}

_INTERPRET_HINT = re.compile(
    r"поясн|интерпрет|почему\s+так|что это знач|прокоммент",
    re.I,
)

_COMPOUND_SEPARATORS = re.compile(r"\s+(?:и|а также|также|плюс)\s+")


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FOLLOWUP_CHART = re.compile(
    r"^\s*а\s+(?:теперь\s+)?(кругов|пирог|столб|гистограм|линейн|линия)",
    re.I,
)


def _extract_json(text: str) -> dict | list | None:
    """Один объект, массив или несколько JSON подряд (так отвечает 1.7B)."""
    if not text:
        return None
    cleaned = _THINK_RE.sub("", text)
    decoder = json.JSONDecoder()
    found: list[dict] = []
    i = 0
    n = len(cleaned)
    while i < n:
        while i < n and cleaned[i] not in "{[":
            i += 1
        if i >= n:
            break
        try:
            obj, end = decoder.raw_decode(cleaned[i:])
        except json.JSONDecodeError:
            break
        if isinstance(obj, list):
            found.extend(item for item in obj if isinstance(item, dict))
        elif isinstance(obj, dict):
            found.append(obj)
        i += end
    if not found:
        return None
    return found if len(found) > 1 else found[0]


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


def _file_context_block(file_context, *, compact: bool = False) -> str:
    if file_context is None:
        return ""
    if compact:
        block = getattr(file_context, "router_block", lambda: "")()
    else:
        block = getattr(file_context, "prompt_block", lambda: "")()
    if not block:
        return ""
    return block.replace("{", "{{").replace("}", "}}") + "\n"


def _llm_classify(
    question: str,
    df: pd.DataFrame,
    history: list[dict] | None = None,
    file_context=None,
) -> list[dict] | None:
    columns = ", ".join(str(c) for c in df.columns[:25])
    prompt = _LLM_PROMPT.format(
        columns=columns,
        file_context_block=_file_context_block(file_context, compact=True),
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


def _normalize_actions(actions: list[dict] | None, question: str) -> list[dict]:
    if not actions:
        return [{"action": "general"}]
    q = question.lower()
    if _wants_narrative(q):
        return [{"action": "general"}]
    out = []
    words = entity_name_words(question)
    for action in actions:
        kind = action.get("action")
        op = action.get("operation")
        is_group = kind == "group" or (kind == "stat" and op == "group")
        if is_group and wants_named_compare(question):
            out.append({**action, "action": "compare"})
            continue
        if is_group and words and not any(
            marker in q
            for marker in (
                "по заказчик",
                "по клиент",
                "по менеджер",
                "по ответственн",
                "по подразделен",
                "по отдел",
            )
        ):
            out.append({**action, "action": "entity", "query": action.get("query") or question})
            continue
        if kind == "help" and not _wants_help(q):
            out.append({**action, "action": "general"})
        else:
            out.append(action)
    return out or [{"action": "general"}]


def _is_compound(q: str) -> bool:
    """Составной вопрос: минимум две части с самостоятельными просьбами.

    «Топ X по количеству и сумме» — один запрос с двумя метриками, не составной.
    """
    parts = [part.strip() for part in _COMPOUND_SEPARATORS.split(q) if part.strip()]
    if len(parts) < 2:
        return False
    meaningful = 0
    for part in parts:
        if any(marker in part for marker in _CHART_MARKERS):
            meaningful += 1
        elif detect_intent(part) != "unknown":
            meaningful += 1
    return meaningful >= 2


def _keyword_compound_actions(question: str) -> list[dict] | None:
    """Две понятные части без роутера: «дефицит и топ-заказчик»."""
    q = (question or "").lower().strip()
    if not _is_compound(q):
        return None
    parts = [part.strip() for part in _COMPOUND_SEPARATORS.split(q) if part.strip()]
    actions: list[dict] = []
    for part in parts:
        chart = _keyword_chart_action(part)
        if chart:
            actions.append(chart)
            continue
        stat = _keyword_stat_action(part)
        if stat:
            actions.append(stat)
    return actions if len(actions) >= 2 else None


def _followup_chart_action(question: str, history: list[dict] | None) -> dict | None:
    """«А теперь круговая» — сменить тип диаграммы, не звать 1.7B."""
    if not history:
        return None
    match = _FOLLOWUP_CHART.search(question or "")
    if not match:
        return None
    token = match.group(1).lower()
    if token.startswith("кругов") or token.startswith("пирог"):
        chart_type = "pie"
    elif token.startswith("линейн") or token.startswith("линия"):
        chart_type = "line"
    else:
        chart_type = "bar"
    last = ""
    for message in reversed(history):
        if message.get("role") == "user":
            last = str(message.get("content") or "")
            break
    prev = _keyword_chart_action(last.lower()) or {}
    group = prev.get("group_semantic") or _group_semantic_from_text(last.lower())
    period = prev.get("period")
    action = {
        "action": "chart",
        "chart_type": chart_type,
        "agg": prev.get("agg") or "sum",
        "top_n": prev.get("top_n") or 10,
        "question": f"{last} {question}".strip(),
    }
    if group:
        action["group_semantic"] = group
    elif period or chart_type == "line":
        action["period"] = period or "month"
    else:
        action["group_semantic"] = "client"
    return action


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
        elif kind == "lookup":
            result = exec_order_lookup(df, str(action.get("query") or question))
        elif kind == "entity":
            result = exec_entity_metrics(df, str(action.get("query") or question))
        elif kind == "compare":
            if wants_rank_compare(question) and not wants_named_compare(question):
                result = exec_rank_compare(df, question)
            else:
                result = exec_named_compare(df, question)
        elif kind == "general":
            result = _exec_general(df, action, file_context=file_context)
        elif kind == "help":
            result = _help_answer(df, file_context=file_context)
        else:
            if kind == "insights":
                action["operation"] = "insights"
            result = _exec_stat(df, action)

        if not answers or result["answer"] != answers[-1]:
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


_FOLLOWUP_METRIC = (
    "остат",
    "остал",
    "оплат",
    "сумм",
    "выручк",
    "неоплач",
    "долг",
    "задолжен",
    "дефицит",
)


def _rewrite_followup(question: str, history: list[dict] | None) -> str:
    """«А по остатку?» после вопроса про КЭАЗ → тот же срез, без роутера."""
    if not history:
        return question
    q = (question or "").strip()
    if not q or len(q) > 72:
        return question
    low = q.lower().replace("ё", "е")
    if not (low.startswith("а ") or low.startswith("а?")):
        return question
    if not any(marker in low for marker in _FOLLOWUP_METRIC):
        return question
    last = ""
    for message in reversed(history):
        if message.get("role") == "user":
            last = str(message.get("content") or "").strip()
            break
    if not last or last.lower().replace("ё", "е") == low:
        return question
    return f"{last.rstrip(' ?!.')} {q}"


def handle_question(
    df: pd.DataFrame,
    question: str,
    history: list[dict] | None = None,
    file_context=None,
) -> dict:
    """Возвращает {"answer": str, "charts": [chart_dict, ...]}."""
    question = _rewrite_followup(question, history)
    q = question.lower().strip()

    if _wants_help(q):
        return {**_help_answer(df, file_context=file_context), "charts": []}

    if _wants_narrative(q):
        result = _exec_narrative(df, question, file_context=file_context)
        return {"answer": result["answer"], "charts": []}

    chart_follow = _followup_chart_action(question, history)
    if chart_follow:
        result = _exec_chart(df, chart_follow)
        return {
            "answer": result["answer"],
            "charts": [result["chart"]] if result.get("chart") else [],
        }

    sheet_text = _describe_other_sheets(question, file_context)
    if sheet_text:
        return {"answer": sheet_text, "charts": []}

    if wants_order_lookup(question):
        result = exec_order_lookup(df, question)
        return {"answer": result["answer"], "charts": []}

    if wants_named_compare(question):
        result = exec_named_compare(df, question)
        return {"answer": result["answer"], "charts": []}

    if wants_rank_compare(question):
        result = exec_rank_compare(df, question)
        return {"answer": result["answer"], "charts": []}

    if wants_payment_overview(question) or wants_file_overview(question):
        result = _exec_general(
            df, {"action": "general", "question": question}, file_context=file_context
        )
        return {"answer": result["answer"], "charts": []}

    if wants_entity_metrics(question, df):
        result = exec_entity_metrics(df, question)
        return {"answer": result["answer"], "charts": []}

    keyword_compound = _keyword_compound_actions(question)
    if keyword_compound:
        return _execute_actions(df, question, keyword_compound, file_context=file_context)

    if _is_compound(q):
        actions = _llm_classify(
            question, df, history, file_context=file_context
        )
        if actions:
            return _execute_actions(df, question, actions, file_context=file_context)

    chart_action = _keyword_chart_action(q)
    if chart_action:
        chart_action["question"] = question
        result = _exec_chart(df, chart_action)
        return {"answer": result["answer"], "charts": [result["chart"]] if result.get("chart") else []}

    stat_action = _keyword_stat_action(question)
    if stat_action:
        stat_action["question"] = question
        if stat_action.get("action") == "chart":
            result = _exec_chart(df, stat_action)
        else:
            result = _exec_stat(df, stat_action)
        return {"answer": result["answer"], "charts": [result["chart"]] if result.get("chart") else []}

    actions = _llm_classify(
        question, df, history, file_context=file_context
    )
    return _execute_actions(
        df, question, _normalize_actions(actions, question), file_context=file_context
    )
