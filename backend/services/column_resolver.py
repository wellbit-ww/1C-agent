import pandas as pd

SEMANTIC_ALIASES: dict[str, list[str]] = {
    "sales": [
        "продажа",
        "продажи",
        "продаж",
        "sales",
        "sale",
    ],
    "revenue": [
        "выручка",
        "выручк",
        "revenue",
        "доход",
        "оборот",
        "сумма продажи",
        "сумма заказа",
        "стоимость",
    ],
    "amount": [
        "amount",
        "сумма",
        "сумм",
        "долг",
        "не оплачено",
        "оплачено",
    ],
    "income": [
        "income",
        "доход",
    ],
    "client": [
        "клиент",
        "клиентов",
        "клиентам",
        "заказчик",
        "контрагент",
        "компания",
        "customer",
        "buyer",
        "покупател",
        "отправитель",
    ],
    "manager": [
        "менеджер",
        "менеджеров",
        "менеджерам",
        "manager",
        "seller",
        "продавец",
        "ответственный",
        "ответственн",
        "ответственных",
    ],
    "department": [
        "подразделение",
        "отдел",
        "департамент",
        "служба",
    ],
    "supplier": [
        "поставщик",
        "supplier",
    ],
    "deficit": [
        "дефицит",
        "неоплаченн",
        "остаток",
        "задолженность",
        "не оплачено",
        "сумма долга",
        "долг",
        "недостаток",
        "нехватка",
    ],
    "region": [
        "регион",
        "регионам",
        "регионов",
        "region",
        "область",
        "city",
        "город",
        "городам",
    ],
    "date": [
        "дата",
        "date",
        "period",
        "период",
    ],
    "month": [
        "month",
        "месяц",
        "месяцам",
        "месяцев",
    ],
    "year": [
        "year",
        "год",
        "годам",
        "годов",
    ],
}

SEMANTIC_TO_DTYPES: dict[str, str] = {
    "sales": "numeric",
    "revenue": "numeric",
    "amount": "numeric",
    "income": "numeric",
    "deficit": "numeric",
    "client": "categorical",
    "manager": "categorical",
    "department": "categorical",
    "supplier": "categorical",
    "region": "categorical",
    "date": "datetime",
    "month": "datetime",
    "year": "datetime",
}

_MONEY_HINTS = ("руб", "₽", "rub")


def _tiebreak_matches(
    df: pd.DataFrame,
    matches: list[str],
    semantic: str,
    dtype: str,
) -> str | None:
    """Выбирает одну колонку из нескольких совпавших по алиасам.

    Для денежных колонок 1С типична пара «сумма в валюте» / «сумма в рублях» —
    суммировать можно только рублёвую. Для категорий документ-ссылка
    («Заказ клиента САУП-…») почти уникальна в каждой строке, а сущность
    («Заказчик») повторяется — побеждает меньшая кардинальность.
    """
    if not matches:
        return None

    if dtype == "numeric":
        rub = [c for c in matches if any(h in _normalize(c) for h in _MONEY_HINTS)]
        if len(rub) == 1:
            return rub[0]
        ordered = [c for c in df.columns if c in matches]
        return ordered[0] if ordered else None

    if dtype == "categorical":
        def cardinality_ratio(col: str) -> float:
            series = df[col]
            if len(series) > 5000:
                series = series.sample(5000, random_state=0)
            return series.nunique() / max(len(series), 1)

        return min(matches, key=cardinality_ratio)

    return None


def _normalize(text: str) -> str:
    return str(text).lower().strip()


def _get_columns_by_dtype(df: pd.DataFrame, dtype: str) -> list[str]:
    if dtype == "numeric":
        return df.select_dtypes(include="number").columns.tolist()

    if dtype in {"datetime", "date"}:
        datetime_cols = df.select_dtypes(
            include=["datetime", "datetimetz"]
        ).columns.tolist()

        for col in df.columns:
            if col in datetime_cols:
                continue

            if pd.api.types.is_datetime64_any_dtype(df[col]):
                datetime_cols.append(col)

        return list(dict.fromkeys(datetime_cols))

    return df.select_dtypes(
        include=["object", "string", "category"]
    ).columns.tolist()


def _canonical_semantic(semantic: str) -> str:
    """LLM часто присылает русское имя ('ответственный') вместо ключа manager."""
    key = _normalize(semantic)
    if not key:
        return semantic
    if key in SEMANTIC_ALIASES:
        return key
    for name, aliases in SEMANTIC_ALIASES.items():
        if key == name or any(alias in key or key in alias for alias in aliases if len(alias) >= 4):
            return name
    return semantic


def _aliases_for_semantic(semantic: str) -> list[str]:
    key = _canonical_semantic(semantic)
    aliases = list(SEMANTIC_ALIASES.get(key, []))
    aliases.append(semantic)
    aliases.append(key)

    if key in {"sales", "revenue", "amount", "income"}:
        for extra in ("sales", "revenue", "amount", "income"):
            if extra != key:
                aliases.extend(SEMANTIC_ALIASES.get(extra, []))

    return list(dict.fromkeys(a for a in aliases if a))


def _match_columns_by_aliases(
    question: str,
    candidates: list[str],
    aliases: list[str],
) -> list[str]:
    matched: list[str] = []
    question_has_alias = any(alias in question for alias in aliases)

    for col in candidates:
        col_norm = _normalize(col)
        col_parts = col_norm.replace("_", " ").replace("-", " ").split()

        if any(alias == col_norm or alias in col_norm for alias in aliases):
            matched.append(col)
            continue

        if any(part in aliases for part in col_parts):
            matched.append(col)
            continue

        if question_has_alias and any(
            alias in question and (part in alias or alias in part)
            for alias in aliases
            for part in col_parts
            if len(part) > 2
        ):
            matched.append(col)

    return list(dict.fromkeys(matched))


def _detect_semantics_in_question(question: str) -> list[str]:
    question_norm = _normalize(question)
    detected: list[str] = []

    for semantic, aliases in SEMANTIC_ALIASES.items():
        if any(alias in question_norm for alias in aliases):
            detected.append(semantic)

    return detected


def resolve_semantic_column(
    df: pd.DataFrame,
    question: str,
    semantic: str,
    dtype: str | None = None,
) -> str | None:
    resolved_dtype = dtype or SEMANTIC_TO_DTYPES.get(semantic, "categorical")
    aliases = _aliases_for_semantic(semantic)
    question_norm = _normalize(question)
    candidates = _get_columns_by_dtype(df, resolved_dtype)

    if not candidates:
        return None

    if not any(alias in question_norm for alias in aliases):
        alias_matches = _match_columns_by_aliases(
            question_norm,
            candidates,
            aliases,
        )

        if len(alias_matches) == 1:
            return alias_matches[0]

        if len(alias_matches) > 1:
            return _tiebreak_matches(df, alias_matches, semantic, resolved_dtype)

        if len(candidates) == 1:
            return candidates[0]

        return None

    alias_matches = _match_columns_by_aliases(
        question_norm,
        candidates,
        aliases,
    )

    if len(alias_matches) == 1:
        return alias_matches[0]

    if len(alias_matches) > 1:
        return _tiebreak_matches(df, alias_matches, semantic, resolved_dtype)

    return resolve_column(df, question, dtype=resolved_dtype)


def resolve_group_and_value_columns(
    df: pd.DataFrame,
    question: str,
) -> tuple[str | None, str | None]:
    question_norm = _normalize(question)
    group_col = None

    group_priority = [
        ("region", ["по регион", "регионам", "регионов", "регион"]),
        ("manager", ["по менеджер", "менеджерам", "менеджеров", "менеджер", "по ответственн"]),
        ("client", ["по клиент", "клиентам", "клиентов", "клиент", "по заказчик", "по компани"]),
        ("department", ["по подразделен", "по отдел", "по департамент", "по служб"]),
    ]

    for semantic, markers in group_priority:
        if any(marker in question_norm for marker in markers):
            group_col = resolve_semantic_column(
                df,
                question,
                semantic,
                dtype="categorical",
            )
            break

    if group_col is None:
        for semantic in ("region", "manager", "client", "department"):
            if semantic in _detect_semantics_in_question(question):
                group_col = resolve_semantic_column(
                    df,
                    question,
                    semantic,
                    dtype="categorical",
                )
                if group_col:
                    break

    value_col = None
    money_semantics = ["revenue", "sales", "amount", "income"]
    if any(m in question_norm for m in SEMANTIC_ALIASES["deficit"]):
        money_semantics.insert(0, "deficit")

    for semantic in money_semantics:
        value_col = resolve_semantic_column(
            df,
            question,
            semantic,
            dtype="numeric",
        )
        if value_col:
            break

    if value_col is None:
        value_col = resolve_column(df, question, dtype="numeric")

    return group_col, value_col


def resolve_column(
    df: pd.DataFrame,
    question: str,
    dtype: str = "numeric",
) -> str | None:
    question_norm = _normalize(question)
    candidates = _get_columns_by_dtype(df, dtype)

    if not candidates:
        return None

    for col in candidates:
        col_norm = _normalize(col)

        if col_norm == question_norm or col_norm in question_norm:
            return col

    for col in candidates:
        col_norm = _normalize(col)
        col_parts = col_norm.replace("_", " ").replace("-", " ").split()

        if any(len(part) > 2 and part in question_norm for part in col_parts):
            return col

        if len(col_norm) > 2 and any(
            word in col_norm
            for word in question_norm.split()
            if len(word) > 2
        ):
            return col

    detected_semantics = _detect_semantics_in_question(question_norm)

    for semantic in detected_semantics:
        aliases = _aliases_for_semantic(semantic)
        alias_matches = _match_columns_by_aliases(
            question_norm,
            candidates,
            aliases,
        )

        if len(alias_matches) == 1:
            return alias_matches[0]

    alias_matches: list[str] = []

    for aliases in SEMANTIC_ALIASES.values():
        if not any(alias in question_norm for alias in aliases):
            continue

        alias_matches.extend(
            _match_columns_by_aliases(question_norm, candidates, aliases)
        )

    alias_matches = list(dict.fromkeys(alias_matches))

    if len(alias_matches) == 1:
        return alias_matches[0]

    if len(alias_matches) > 1:
        return _tiebreak_matches(df, alias_matches, "", dtype)

    if len(candidates) == 1:
        return candidates[0]

    return None
