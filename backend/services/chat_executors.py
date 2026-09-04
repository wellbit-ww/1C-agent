"""Deterministic pandas executors for chat stat/chart commands."""
import logging

import pandas as pd

from services import data_tools
from services.chart_service import (
    create_bar_chart,
    create_line_chart,
    create_pie_chart,
)
from services.chat_keywords import _PERIOD_FUNCS, _PERIOD_LABELS
from services.column_resolver import resolve_semantic_column, _canonical_semantic
from services.insights_service import _format_number, get_basic_insights

logger = logging.getLogger(__name__)


def _match_column(df: pd.DataFrame, name) -> str | None:
    if not name:
        return None
    target = str(name).strip()
    for col in df.columns:
        if str(col) == target:
            return col
    return None

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
        semantic = _canonical_semantic(action.get("semantic") or "client")
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
