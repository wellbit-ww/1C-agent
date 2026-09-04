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
    _keyword_chart_action,
    _keyword_stat_action,
)
from services.chat_narrative import (
    _describe_other_sheets,
    _exec_general,
    _exec_narrative,
    _help_answer,
    _wants_help,
    _wants_narrative,
)
from services.exceptions import OllamaUnavailableError
from services.llm_service import ask_llm, classify

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

_INTERPRET_HINT = re.compile(
    r"поясн|интерпрет|почему\s+так|что это знач|прокоммент",
    re.I,
)

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


def _llm_classify(
    question: str,
    df: pd.DataFrame,
    history: list[dict] | None = None,
    file_context=None,
) -> list[dict] | None:
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
    for action in actions:
        kind = action.get("action")
        if kind == "help" and not _wants_help(q):
            out.append({**action, "action": "general"})
        else:
            out.append(action)
    return out or [{"action": "general"}]


def _is_compound(q: str) -> bool:
    """Составной вопрос: минимум две части с самостоятельными просьбами."""
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
