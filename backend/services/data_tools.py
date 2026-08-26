import warnings

import pandas as pd

from services.column_resolver import (
    resolve_column,
    resolve_group_and_value_columns,
    resolve_semantic_column,
)


def _error(message: str) -> dict:
    return {"error": message}


_DEFICIT_MARKERS = ("дефицит", "неоплаченн", "остаток", "задолженность")


def _resolve_numeric_column(df, question: str | None) -> str | None:
    if question:
        semantics = ["revenue", "sales", "amount", "income"]
        if any(m in question.lower() for m in _DEFICIT_MARKERS):
            semantics.insert(0, "deficit")

        for semantic in semantics:
            col = resolve_semantic_column(
                df,
                question,
                semantic,
                dtype="numeric",
            )
            if col:
                return col

        return resolve_column(df, question, dtype="numeric")

    return resolve_column(df, "", dtype="numeric")


def _resolve_categorical_column(df, question: str | None) -> str | None:
    if not question:
        return resolve_column(df, "", dtype="categorical")

    for semantic in ("client", "manager", "region", "department"):
        col = resolve_semantic_column(
            df,
            question,
            semantic,
            dtype="categorical",
        )
        if col:
            return col

    return resolve_column(df, question, dtype="categorical")


def get_row_count(df):
    return {"value": len(df)}


def get_column_count(df):
    return {"value": len(df.columns)}


def get_columns(df):
    return {"columns": list(df.columns)}


def get_sum(df, question: str | None = None):
    col = _resolve_numeric_column(df, question)

    if not col:
        return _error("Числовая колонка не найдена")

    return {"column": col, "value": float(df[col].sum())}


def get_mean(df, question: str | None = None):
    col = _resolve_numeric_column(df, question)

    if not col:
        return _error("Числовая колонка не найдена")

    return {"column": col, "value": round(float(df[col].mean()), 2)}


def get_max(df, question: str | None = None):
    col = _resolve_numeric_column(df, question)

    if not col:
        return _error("Числовая колонка не найдена")

    return {"column": col, "value": float(df[col].max())}


def get_min(df, question: str | None = None):
    col = _resolve_numeric_column(df, question)

    if not col:
        return _error("Числовая колонка не найдена")

    return {"column": col, "value": float(df[col].min())}


def get_unique_count(df, question: str | None = None):
    col = _resolve_categorical_column(df, question)

    if not col:
        return _error("Колонка для подсчёта уникальных значений не найдена")

    return {"column": col, "value": int(df[col].nunique())}


def get_null_count(df, question: str | None = None):
    if question:
        col = resolve_column(df, question, dtype="numeric")

        if not col:
            col = _resolve_categorical_column(df, question)

        if col:
            return {
                "column": col,
                "value": int(df[col].isna().sum()),
            }

    nulls_by_column = {
        column: int(df[column].isna().sum())
        for column in df.columns
        if df[column].isna().sum() > 0
    }

    return {
        "columns": nulls_by_column,
        "value": int(df.isna().sum().sum()),
    }


def get_duplicates_count(df):
    return {"value": int(df.duplicated().sum())}


def group_sum(df, question: str | None = None, top_n: int | None = None):
    group_col, value_col = resolve_group_and_value_columns(df, question or "")

    if not group_col or not value_col:
        missing = []
        if not group_col: missing.append("для группировки (категория/текст)")
        if not value_col: missing.append("со значениями (числа)")
        return _error(f"Не удалось найти подходящие колонки в таблице: {' и '.join(missing)}")

    grouped = (
        df.groupby(group_col)[value_col]
        .sum()
        .sort_values(ascending=False)
    )

    if top_n:
        grouped = grouped.head(top_n)

    return {
        "group_column": group_col,
        "value_column": value_col,
        "aggregation": "sum",
        "groups": {
            str(key): float(value)
            for key, value in grouped.to_dict().items()
        },
    }


def group_mean(df, question: str | None = None, top_n: int | None = None):
    group_col, value_col = resolve_group_and_value_columns(df, question or "")

    if not group_col or not value_col:
        missing = []
        if not group_col: missing.append("для группировки (категория/текст)")
        if not value_col: missing.append("со значениями (числа)")
        return _error(f"Не удалось найти подходящие колонки в таблице: {' и '.join(missing)}")

    grouped = (
        df.groupby(group_col)[value_col]
        .mean()
        .sort_values(ascending=False)
    )

    if top_n:
        grouped = grouped.head(top_n)

    return {
        "group_column": group_col,
        "value_column": value_col,
        "aggregation": "mean",
        "groups": {
            str(key): round(float(value), 2)
            for key, value in grouped.to_dict().items()
        },
    }


def group_count(df, question: str | None = None, top_n: int | None = None):
    group_col, _ = resolve_group_and_value_columns(df, question or "")

    if not group_col:
        group_col = _resolve_categorical_column(df, question)

    if not group_col:
        return _error("Не удалось найти подходящую колонку для группировки (категория/текст)")

    grouped = df.groupby(group_col).size().sort_values(ascending=False)

    if top_n:
        grouped = grouped.head(top_n)

    return {
        "group_column": group_col,
        "aggregation": "count",
        "groups": {
            str(key): int(value)
            for key, value in grouped.to_dict().items()
        },
    }


def get_top_n(
    df,
    question: str | None = None,
    semantic: str = "client",
    n: int = 5,
):
    group_col = resolve_semantic_column(
        df,
        question or "",
        semantic,
        dtype="categorical",
    )
    value_col = _resolve_numeric_column(df, question)

    if not group_col or not value_col:
        missing = []
        if not group_col: missing.append(f"для группировки (ожидалась колонка типа '{semantic}')")
        if not value_col: missing.append("со значениями (числа)")
        return _error(f"Не удалось найти подходящие колонки в таблице: {' и '.join(missing)}")

    grouped = (
        df.groupby(group_col)[value_col]
        .sum()
        .sort_values(ascending=False)
        .head(n)
    )

    return {
        "group_column": group_col,
        "value_column": value_col,
        "top_n": n,
        "groups": {
            str(key): float(value)
            for key, value in grouped.to_dict().items()
        },
    }


def detect_date_columns(df):
    date_columns: list[str] = []

    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            date_columns.append(col)
            continue

        if pd.api.types.is_object_dtype(df[col]) or pd.api.types.is_string_dtype(df[col]):
            series = df[col].dropna()
            if series.empty:
                continue

            # object-колонки с числами («К оплате» и т.п.) — не даты:
            # иначе to_datetime трактует их как unix-timestamp и даёт 1970
            numeric_ratio = pd.to_numeric(series, errors="coerce").notna().mean()
            if numeric_ratio >= 0.8:
                continue

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                parsed = pd.to_datetime(series, errors="coerce", dayfirst=True)
            valid_ratio = parsed.notna().mean()

            if valid_ratio >= 0.5:
                date_columns.append(col)

    return {"columns": date_columns}


def _resolve_date_column(df, question: str | None = None) -> str | None:
    detected = detect_date_columns(df)["columns"]

    if question:
        for semantic in ("date", "month", "year"):
            col = resolve_semantic_column(
                df,
                question,
                semantic,
                dtype="datetime",
            )
            if col:
                return col

        for col in detected:
            if _normalize_col_in_question(col, question):
                return col

    if len(detected) == 1:
        return detected[0]

    return detected[0] if detected else None


def _normalize_col_in_question(column: str, question: str) -> bool:
    col_norm = str(column).lower().strip()
    question_norm = str(question).lower().strip()
    return col_norm in question_norm


def _group_by_period(df, question: str | None, period: str):
    date_col = _resolve_date_column(df, question)
    value_col = _resolve_numeric_column(df, question)

    if not date_col or not value_col:
        missing = []
        if not date_col: missing.append("с датой/временем")
        if not value_col: missing.append("со значениями (числа)")
        return _error(f"Не удалось найти подходящие колонки в таблице: {' и '.join(missing)}")

    working_df = df.copy()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        working_df[date_col] = pd.to_datetime(
            working_df[date_col],
            errors="coerce",
            dayfirst=True,
        )

    if working_df[date_col].isna().all():
        return _error("Колонка даты не содержит валидных значений")

    if period == "month":
        working_df["_period"] = working_df[date_col].dt.to_period("M")
    elif period == "year":
        working_df["_period"] = working_df[date_col].dt.to_period("Y")
    elif period == "quarter":
        working_df["_period"] = working_df[date_col].dt.to_period("Q")
    else:
        return _error("Неподдерживаемый период группировки")

    grouped = (
        working_df.groupby("_period")[value_col]
        .sum()
        .sort_index()
    )

    return {
        "date_column": date_col,
        "value_column": value_col,
        "period": period,
        "groups": {
            str(key): float(value)
            for key, value in grouped.to_dict().items()
        },
    }


def group_by_month(df, question: str | None = None):
    return _group_by_period(df, question, "month")


def group_by_year(df, question: str | None = None):
    return _group_by_period(df, question, "year")


def group_by_quarter(df, question: str | None = None):
    return _group_by_period(df, question, "quarter")
