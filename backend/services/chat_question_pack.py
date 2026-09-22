"""Срез pandas по вопросу: факты для LLM и быстрые compare-ответы.

LLM не считает. Сборщик берёт имена/метрики из вопроса, pandas считает,
в модель уходит только этот срез плюс 6–8 глобальных фактов файла.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass

import pandas as pd

from services.chat_answers import format_chat_number, is_money_column
from services.chat_lookup import (
    _WORD_RE,
    _norm,
    _stem,
    department_keys,
    entity_name_words,
    exec_entity_metrics,
    match_entities_separately,
    match_entity_slice,
    wants_entity_metrics,
)
from services.report_profiles.deficit_profile import (
    _col_sum,
    deficit_kpis,
    detect_deficit_money_layout,
)

_COMPARE_MARK = (
    "сравни",
    "сравнен",
    " versus",
    " vs ",
    "против ",
    "чем у",
    "разниц",
    "кто из",
)
_RANK_WHO = re.compile(r"\bкто\b")
_OWES = ("должен", "должн", "остат", "неоплач", "задолжен")
_ORDERED = ("заказал", "сумме заказ", "сумма заказ", "по сумме заказ")
_PAY_MARK = ("оплат", "остат", "дефицит", "долг", "к оплате", "задолжен")
_WHOLE_MARK = (
    "в целом",
    "по файл",
    "что происходит",
    "как обстоя",
    "обзор",
    "что в файл",
    "расскажи про",
    "что с оплат",
)
_PERIOD_MARK = ("месяц", "квартал", "год", "динамик", "период")
_GROUP_ALL_SKIP = (
    "по заказчик",
    "по клиент",
    "по менеджер",
    "по ответственн",
    "по подразделен",
    "по отдел",
    "топ-",
    "топ ",
)


_PRODUCTISH_STEMS = ("шкаф", "издел", "модел", "товар", "номенклатур", "печ", "пульт")


def _compare_party_words(question: str) -> list[str]:
    """Имена сторон для сравнения, без «шкафов» и обломков «и каких»."""
    parties: list[str] = []
    for word in entity_name_words(question):
        stem = _stem(_norm(word))
        if any(stem.startswith(kind) or kind.startswith(stem) for kind in _PRODUCTISH_STEMS):
            continue
        if stem.startswith("как"):
            continue
        parties.append(word)
    return parties


def wants_named_compare(question: str) -> bool:
    q = question.lower().replace("ё", "е")
    if re.search(r"\sи\s+как(их|ие|ой|ая|ое|ова)\b", q) and not any(
        marker in q for marker in _COMPARE_MARK
    ):
        return False
    words = _compare_party_words(question)
    if len(words) < 2:
        return False
    if any(marker in q for marker in _COMPARE_MARK):
        return True
    if " и " in q and any(
        marker in q
        for marker in _PAY_MARK + _ORDERED + ("должен", "заказал")
    ):
        return True
    return False


def wants_rank_compare(question: str) -> bool:
    q = question.lower().replace("ё", "е")
    if any(marker in q for marker in _GROUP_ALL_SKIP):
        return False
    if any(marker in q for marker in ("график", "диаграмм", "построй", "кругов")):
        return False
    owes = any(marker in q for marker in _OWES)
    ordered = any(marker in q for marker in _ORDERED)
    who = bool(_RANK_WHO.search(q))
    if not (who and (owes or ordered)) and not (owes and ordered and " и " in q):
        return False
    if wants_named_compare(question):
        return False
    return True


def wants_payment_overview(question: str) -> bool:
    if entity_name_words(question):
        return False
    q = question.lower().replace("ё", "е")
    if any(marker in q for marker in ("график", "диаграмм", "построй")):
        return False
    pay = any(marker in q for marker in _PAY_MARK)
    whole = any(marker in q for marker in _WHOLE_MARK)
    return pay and whole


_FILE_OVERVIEW = (
    "что в файл",
    "что за файл",
    "расскажи про файл",
    "что в таблиц",
    "о чем файл",
    "о чём файл",
    "содержимое файл",
    "что лежит в файл",
)


def wants_file_overview(question: str) -> bool:
    """«Что в файле?» — факты pandas, без JSON-роутера."""
    if entity_name_words(question):
        return False
    q = question.lower().replace("ё", "е")
    if any(marker in q for marker in ("график", "диаграмм", "построй", "какие лист")):
        return False
    return any(marker in q for marker in _FILE_OVERVIEW)


_PRODUCT_COL_MARKERS = (
    "номенклатур",
    "модель",
    "сделка",
    "оборудован",
    "наименован",
    "товар",
    "изделие",
)
_PRODUCT_TEXT_MARKERS = _PRODUCT_COL_MARKERS + (
    "комментар",
    "примечан",
)
_PRODUCT_ASK = ("скольк", "каких", "какие", "какая", "какой")
_PRODUCT_ORDERED = ("заказал", "поставл", "купил", "отгруз", "продан")
_PRODUCT_SKIP = {
    "скольк", "каких", "какие", "какой", "какая", "какое", "каков",
    "заказал", "заказ", "постав", "штук", "модель", "модел", "издел",
    "клиент", "файл", "таблиц", "строк", "колон", "запис",
}
_PRODUCT_KIND = ("шкаф", "модел", "издел", "номенклатур", "товар", "печ", "пульт")
_GENERIC_PRODUCT = ("шкаф", "издел", "товар")
_MODEL_QTY = re.compile(
    r"(?P<model>.+?)\s*[×xхX]\s*(?P<qty>\d+)\s*шт",
    re.I,
)
_QTY_ONLY = re.compile(r"(\d+)\s*шт", re.I)
_PRODUCT_ALIASES = {
    "шкаф": ("шкаф", "шсх"),
}


def _product_columns(df: pd.DataFrame) -> list[str]:
    cols: list[str] = []
    for col in df.columns:
        name = str(col).lower()
        if any(marker in name for marker in _PRODUCT_COL_MARKERS):
            cols.append(col)
    return cols


def _product_search_columns(df: pd.DataFrame) -> list[str]:
    cols: list[str] = []
    for col in df.columns:
        name = str(col).lower()
        if any(marker in name for marker in _PRODUCT_TEXT_MARKERS):
            cols.append(col)
    return cols


def _has_model_column(df: pd.DataFrame) -> bool:
    return any(
        "модель" in str(col).lower() or "номенклатур" in str(col).lower()
        for col in df.columns
    )


def _product_tokens(question: str) -> list[str]:
    tokens: list[str] = []
    for raw in _WORD_RE.findall(question or ""):
        n = _norm(raw)
        stem = _stem(n)
        if len(stem) < 4:
            continue
        if any(stem.startswith(skip) or skip.startswith(stem) for skip in _PRODUCT_SKIP):
            continue
        tokens.append(stem)
    return tokens


def _product_token_hits(df: pd.DataFrame, tokens: list[str]) -> bool:
    search_cols = _product_search_columns(df)
    if not search_cols or not tokens:
        return False
    needles: list[str] = []
    for token in tokens:
        needles.extend(_token_needles(token))
    for col in search_cols:
        blob = df[col].astype(str)
        for needle in needles:
            if blob.str.contains(needle, case=False, na=False, regex=False).any():
                return True
    return False


def _token_needles(token: str) -> tuple[str, ...]:
    extra = _PRODUCT_ALIASES.get(token, ())
    return (token, *extra)


def wants_product_catalog(question: str, df: pd.DataFrame | None = None) -> bool:
    """«Сколько и каких шкафов заказали?» — каталог изделий, не группировка денег."""
    if df is not None and wants_entity_metrics(question, df):
        return False
    q = (question or "").lower().replace("ё", "е")
    if any(marker in q for marker in ("график", "диаграмм", "построй", "кругов")):
        return False
    if re.search(r"скольк\w*\s+(строк|колонок|запис)", q):
        return False
    if not any(marker in q for marker in _PRODUCT_ASK):
        return False
    tokens = _product_tokens(question)
    mix = "каких" in q or "какие" in q or "модел" in q
    ordered = any(marker in q for marker in _PRODUCT_ORDERED)
    if df is not None:
        if not _product_columns(df) and not _product_search_columns(df):
            return False
        if tokens and _product_token_hits(df, tokens):
            return True
        if not tokens and mix and (ordered or "скольк" in q) and _has_model_column(df):
            return True
        return False
    productish = any(
        token.startswith(kind) or kind.startswith(token)
        for token in tokens
        for kind in _PRODUCT_KIND
    )
    return bool(tokens) and productish and (ordered or mix)


def _parse_model_qty(value) -> tuple[str, int]:
    text = str(value or "").replace("\n", " ").strip()
    if not text or text.lower() in {"nan", "none", "nat", "-"}:
        return "", 0
    match = _MODEL_QTY.search(text)
    if match:
        model = re.sub(r"\s+", " ", match.group("model")).strip(" .;,-")
        return model, int(match.group("qty"))
    qty_match = _QTY_ONLY.search(text)
    qty = int(qty_match.group(1)) if qty_match else 1
    model = re.sub(r"\s+", " ", _QTY_ONLY.sub("", text)).strip(" .;,-") or text
    return model, qty


def exec_product_catalog(df: pd.DataFrame, question: str) -> dict:
    """Сколько штук каких моделей: колонка «модель/ количество» и имена сделок."""
    search_cols = _product_search_columns(df)
    model_cols = [
        col for col in df.columns
        if "модель" in str(col).lower() or "номенклатур" in str(col).lower()
    ]
    deal_cols = [
        col for col in df.columns
        if any(marker in str(col).lower() for marker in ("сделка", "наименован"))
    ]
    client_cols = [
        col for col in df.columns
        if any(marker in str(col).lower() for marker in ("заказчик", "клиент", "компани"))
    ]
    qty_col = next((c for c in model_cols if "количеств" in str(c).lower()), None)
    if qty_col is None and model_cols:
        qty_col = model_cols[0]

    tokens = _product_tokens(question)
    frame = df
    label = "изделий"
    generic = any(
        token.startswith(kind) or kind.startswith(token)
        for token in tokens
        for kind in _GENERIC_PRODUCT
    )
    if tokens:
        needles = []
        for token in tokens:
            needles.extend(_token_needles(token))
        label = "шкафов" if any(token.startswith("шкаф") for token in tokens) else tokens[0]
        mask = pd.Series(False, index=df.index)
        for col in search_cols:
            blob = df[col].astype(str)
            for needle in needles:
                mask = mask | blob.str.contains(needle, case=False, na=False, regex=False)
        # Сделка может называться «печь», а в «модель/ количество» — шкафы.
        # Берём ту же модель, что уже нашли по слову «шкаф», и все строки этой колонки,
        # если тип общий, а артикулы в файле не содержат слово из вопроса.
        if qty_col is not None and generic:
            parsed = df[qty_col].map(_parse_model_qty)
            models = parsed.map(lambda item: item[0])
            skus = {sku for sku, keep in zip(models, mask) if keep and sku}
            if skus:
                mask = mask | models.isin(skus)
            elif not mask.any():
                mask = parsed.map(lambda item: item[1] > 0)
        frame = df.loc[mask]
        if frame.empty:
            hint = " / ".join(tokens)
            return {
                "answer": (
                    f"Не нашёл «{hint}» в колонках изделий. "
                    "Уточните название как в файле (сделка, модель, номенклатура)."
                )
            }

    name_col = next((c for c in df.columns if "сделка" in str(c).lower()), None)
    if name_col is None:
        name_col = next(
            (
                c for c in deal_cols
                if "заказчик" not in str(c).lower()
            ),
            deal_cols[0] if deal_cols else (search_cols[0] if search_cols else None),
        )
    client_col = next(
        (c for c in df.columns if "заказчик" in str(c).lower()),
        None,
    )
    if client_col is None:
        client_col = next(
            (
                c for c in client_cols
                if "заказ клиента" not in str(c).lower()
            ),
            None,
        )
    extra_qty_col = next(
        (
            c for c in df.columns
            if "количеств" in str(c).lower() and c not in model_cols
        ),
        None,
    )

    totals: dict[str, int] = {}
    orders: list[tuple[str, str, int]] = []
    for _, row in frame.iterrows():
        model, qty = ("", 0)
        if qty_col:
            model, qty = _parse_model_qty(row.get(qty_col))
        extra_qty = 0
        if extra_qty_col is not None:
            parsed = pd.to_numeric(row.get(extra_qty_col), errors="coerce")
            if pd.notna(parsed) and parsed:
                extra_qty = int(parsed)
        if qty <= 0:
            qty = extra_qty or 1
        if not model and name_col:
            model = str(row.get(name_col) or "").strip() or "без названия"
        if not model:
            model = "без названия"
        totals[model] = totals.get(model, 0) + qty
        who = ""
        if client_col:
            who = str(row.get(client_col) or "").strip()
        title = ""
        if name_col:
            title = str(row.get(name_col) or "").strip()
        orders.append((who or title or model, model, qty))

    pieces = sum(totals.values())
    n_orders = len(frame)
    orders_word = "заказе" if n_orders % 10 == 1 and n_orders % 100 != 11 else "заказах"
    verb = "Продано" if "продан" in (question or "").lower().replace("ё", "е") else "Заказано"
    lines = [
        f"{verb} **{pieces} шт.** {label} в **{n_orders}** {orders_word}."
    ]
    ranked = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    lines.append("**По моделям**")
    for i, (model, qty) in enumerate(ranked[:12], 1):
        lines.append(f"{i}. {model} — {qty} шт.")
    lines.append("**Заказы**")
    for who, model, qty in orders[:12]:
        bit = f"{who} — {model} × {qty} шт." if who and who != model else f"{model} × {qty} шт."
        lines.append(f"• {bit}")
    extra = len(orders) - 12
    if extra > 0:
        lines.append(f"… и ещё {extra}")
    return {"answer": "\n".join(lines)}


_QUARTER_SPAN = {
    1: "01.01–31.03",
    2: "01.04–30.06",
    3: "01.07–30.09",
    4: "01.10–31.12",
}
_START_DATE_MARK = ("начал", "нчал")
_START_DATE_SKIP = (
    "окончан",
    "закрыт",
    "поставк",
    "отгруз",
    "реализац",
    "пнр",
    "готовност",
    "аттестац",
)


def _start_date_column(df: pd.DataFrame) -> str | None:
    """Колонка начала сделки: «дата начала сделки», в том числе опечатка «нчала»."""
    ranked: list[tuple[int, str]] = []
    for col in df.columns:
        name = str(col).lower().replace("ё", "е")
        if any(skip in name for skip in _START_DATE_SKIP):
            continue
        if not any(mark in name for mark in _START_DATE_MARK):
            continue
        score = 0
        if "сделк" in name:
            score += 2
        if "дат" in name:
            score += 1
        ranked.append((score, col))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (-item[0], str(item[1])))
    return ranked[0][1]


def _deals_word(n: int) -> str:
    n_abs = abs(int(n)) % 100
    n1 = n_abs % 10
    if 11 <= n_abs <= 14:
        return "сделок"
    if n1 == 1:
        return "сделка"
    if 2 <= n1 <= 4:
        return "сделки"
    return "сделок"


_MONTH_STEMS = (
    ("декабр", 12),
    ("ноябр", 11),
    ("октябр", 10),
    ("сентябр", 9),
    ("август", 8),
    ("феврал", 2),
    ("январ", 1),
    ("апрел", 4),
    ("июн", 6),
    ("июл", 7),
    ("март", 3),
    ("май", 5),
    ("мая", 5),
    ("мае", 5),
    ("маю", 5),
)
_MONTH_RU = {
    1: "январь",
    2: "февраль",
    3: "март",
    4: "апрель",
    5: "май",
    6: "июнь",
    7: "июль",
    8: "август",
    9: "сентябрь",
    10: "октябрь",
    11: "ноябрь",
    12: "декабрь",
}
_MONTH_ALT = (
    r"январ[аяеьюй]*|феврал[аяеьюй]*|март[аеуом]*|апрел[аяеьюй]*|"
    r"ма[йяею]|июн[аяеьюй]*|июл[аяеьюй]*|август[аеуом]*|"
    r"сентябр[аяеьюй]*|октябр[аяеьюй]*|ноябр[аяеьюй]*|декабр[аяеьюй]*"
)
_DOT_DATE = re.compile(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\b")
_DOT_TOKEN = r"(\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?)"


@dataclass(frozen=True)
class _PeriodSpec:
    kind: str
    year: int | None = None
    month: int | None = None
    day: int | None = None
    quarter: int | None = None
    start: tuple[int, int, int | None] | None = None
    end: tuple[int, int, int | None] | None = None


@dataclass(frozen=True)
class _DealPeriod:
    windows: tuple[tuple[pd.Timestamp, pd.Timestamp], ...]
    label: str
    span: str
    kind: str


def _month_num(text: str) -> int | None:
    q = (text or "").lower().replace("ё", "е")
    for stem, num in _MONTH_STEMS:
        if stem in q:
            return num
    return None


def _pull_year(q: str) -> tuple[str, int | None]:
    year = None
    match = re.search(r"(20\d{2})", q)
    if match:
        year = int(match.group(1))
        q = q[: match.start()] + " " + q[match.end() :]
    else:
        short = re.search(r"(?<!\d)(\d{2})\s*(?:год|г(?:\.|\b))", q)
        if short:
            year = 2000 + int(short.group(1))
            q = q[: short.start()] + " " + q[short.end() :]
    return q, year


def _dot_parts(text: str, fallback_year: int | None = None) -> tuple[int, int, int | None] | None:
    match = _DOT_DATE.search(text or "")
    if not match:
        return None
    day, month = int(match.group(1)), int(match.group(2))
    if not (1 <= day <= 31 and 1 <= month <= 12):
        return None
    raw = match.group(3)
    if raw:
        year = int(raw)
        if year < 100:
            year += 2000
        return day, month, year
    return day, month, fallback_year


def _extract_period_request(question: str) -> _PeriodSpec | None:
    """День, неделя, месяц, квартал или явный диапазон дат из вопроса."""
    original = (question or "").lower().replace("ё", "е").replace("—", "-").replace("–", "-")
    q, year = _pull_year(original)
    want_week = "недел" in q

    dotted_range = re.search(
        rf"(?:с|от|за)\s*{_DOT_TOKEN}\s*(?:по|до|-)\s*{_DOT_TOKEN}",
        original,
    ) or re.search(rf"{_DOT_TOKEN}\s*(?:по|-)\s*{_DOT_TOKEN}", original)
    if dotted_range:
        start = _dot_parts(dotted_range.group(1), year)
        end = _dot_parts(dotted_range.group(2), year)
        if start and end:
            if start[2] is None and end[2] is not None:
                start = (start[0], start[1], end[2])
            if end[2] is None and start[2] is not None:
                end = (end[0], end[1], start[2])
            kind = "week" if want_week else "range"
            return _PeriodSpec(kind=kind, year=year, start=start, end=end)

    named_span = re.search(
        rf"с\s+(\d{{1,2}})\s+({_MONTH_ALT})\s*(?:по|до|-)\s*(\d{{1,2}})\s+({_MONTH_ALT})",
        q,
    )
    if named_span:
        start_m = _month_num(named_span.group(2))
        end_m = _month_num(named_span.group(4))
        if start_m and end_m:
            kind = "week" if want_week else "range"
            return _PeriodSpec(
                kind=kind,
                year=year,
                start=(int(named_span.group(1)), start_m, year),
                end=(int(named_span.group(3)), end_m, year),
            )

    same_month_span = re.search(
        rf"(?:с|от|за)\s+(\d{{1,2}})\s*(?:по|до|-)\s*(\d{{1,2}})\s+({_MONTH_ALT})",
        q,
    ) or re.search(
        rf"(\d{{1,2}})\s*(?:по|-)\s*(\d{{1,2}})\s+({_MONTH_ALT})",
        q,
    )
    if same_month_span:
        month = _month_num(same_month_span.group(3))
        if month:
            kind = "week" if want_week else "range"
            return _PeriodSpec(
                kind=kind,
                year=year,
                start=(int(same_month_span.group(1)), month, year),
                end=(int(same_month_span.group(2)), month, year),
            )

    dots = list(_DOT_DATE.finditer(original))
    if len(dots) == 1:
        parts = _dot_parts(dots[0].group(0), year)
        if parts:
            kind = "week" if want_week else "day"
            return _PeriodSpec(kind=kind, year=parts[2], month=parts[1], day=parts[0])

    day_month = re.search(rf"(?<!\d)(\d{{1,2}})\s+({_MONTH_ALT})", q)
    if day_month:
        month = _month_num(day_month.group(2))
        day = int(day_month.group(1))
        if month and 1 <= day <= 31:
            kind = "week" if want_week else "day"
            return _PeriodSpec(kind=kind, year=year, month=month, day=day)

    month = _month_num(q)
    if month:
        return _PeriodSpec(kind="month", year=year, month=month)

    want_q, want_year = _parse_quarter_request(question)
    if want_q:
        return _PeriodSpec(kind="quarter", year=want_year, quarter=want_q)

    if year and re.search(r"\bгод", original) and not want_week:
        return _PeriodSpec(kind="year", year=year)
    return None


def _file_years(df: pd.DataFrame | None, month: int | None = None, day: int | None = None) -> list[int]:
    if df is None:
        return []
    col = _start_date_column(df)
    if not col:
        return []
    parsed = pd.to_datetime(df[col], errors="coerce", dayfirst=True).dropna()
    if parsed.empty:
        return []
    picked = parsed
    if month:
        picked = picked[picked.dt.month == month]
    if day:
        picked = picked[picked.dt.day == day]
    years = sorted({int(value) for value in picked.dt.year})
    if years:
        return years
    return sorted({int(value) for value in parsed.dt.year})


def _ts(year: int, month: int, day: int) -> pd.Timestamp:
    last = calendar.monthrange(year, month)[1]
    return pd.Timestamp(year=year, month=month, day=min(max(int(day), 1), last))


def _next_day(ts: pd.Timestamp) -> pd.Timestamp:
    return ts.normalize() + pd.Timedelta(days=1)


def _fmt_day(ts: pd.Timestamp) -> str:
    return ts.strftime("%d.%m.%Y")


def _resolve_years(spec: _PeriodSpec, df: pd.DataFrame | None) -> list[int]:
    if spec.year:
        return [spec.year]
    if spec.start and spec.start[2]:
        return [int(spec.start[2])]
    if spec.end and spec.end[2]:
        return [int(spec.end[2])]
    month = spec.month
    day = spec.day
    if spec.start:
        month = spec.start[1]
        day = spec.start[0] if spec.kind == "day" else None
    return _file_years(df, month=month, day=day)


def _range_window(
    start: tuple[int, int, int | None],
    end: tuple[int, int, int | None],
    year: int,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    sy = start[2] or year
    ey = end[2] or year
    start_ts = _ts(sy, start[1], start[0])
    end_ts = _ts(ey, end[1], end[0])
    if (start[1], start[0]) > (end[1], end[0]) and start[2] is None and end[2] is None:
        end_ts = _ts(sy + 1, end[1], end[0])
    if end_ts < start_ts:
        start_ts, end_ts = end_ts, start_ts
    return start_ts.normalize(), _next_day(end_ts)


def _resolve_deal_period(spec: _PeriodSpec, df: pd.DataFrame | None) -> _DealPeriod | None:
    years = _resolve_years(spec, df)
    if spec.kind in {"range", "week"} and spec.start and spec.end:
        if not years and (spec.start[2] or spec.end[2] or spec.year):
            years = [int(spec.start[2] or spec.end[2] or spec.year)]
        if not years:
            return None
        windows = tuple(_range_window(spec.start, spec.end, year) for year in years)
        if spec.kind == "week":
            label = (
                f"неделя {_fmt_day(windows[0][0])}–"
                f"{_fmt_day(windows[0][1] - pd.Timedelta(days=1))}"
            )
        else:
            label = (
                f"{_fmt_day(windows[0][0])}–"
                f"{_fmt_day(windows[0][1] - pd.Timedelta(days=1))}"
            )
        span = label.replace("неделя ", "")
        if len(windows) > 1:
            span = " и ".join(
                f"{_fmt_day(a)}–{_fmt_day(b - pd.Timedelta(days=1))}" for a, b in windows
            )
        return _DealPeriod(windows=windows, label=label, span=span, kind=spec.kind)

    if spec.kind == "week" and spec.month and spec.day:
        if not years:
            return None
        windows = []
        for year in years:
            day = _ts(year, spec.month, spec.day).normalize()
            start = day - pd.Timedelta(days=int(day.dayofweek))
            windows.append((start, start + pd.Timedelta(days=7)))
        label = (
            f"неделя {_fmt_day(windows[0][0])}–{_fmt_day(windows[0][1] - pd.Timedelta(days=1))}"
        )
        if len(windows) > 1:
            label = "недели " + " и ".join(
                f"{_fmt_day(a)}–{_fmt_day(b - pd.Timedelta(days=1))}" for a, b in windows
            )
        return _DealPeriod(windows=tuple(windows), label=label, span="", kind="week")

    if spec.kind == "day" and spec.month and spec.day:
        if not years:
            return None
        windows = tuple(
            (
                _ts(year, spec.month, spec.day).normalize(),
                _next_day(_ts(year, spec.month, spec.day)),
            )
            for year in years
        )
        label = " и ".join(_fmt_day(start) for start, _ in windows)
        return _DealPeriod(windows=windows, label=label, span="", kind="day")

    if spec.kind == "month" and spec.month:
        if not years:
            return None
        windows = []
        for year in years:
            start = _ts(year, spec.month, 1)
            end = _ts(year + 1, 1, 1) if spec.month == 12 else _ts(year, spec.month + 1, 1)
            windows.append((start, end))
        name = _MONTH_RU[spec.month]
        if len(years) == 1:
            label = f"{name} {years[0]}"
        elif len(years) == 2:
            label = f"{name} {years[0]} и {years[-1]}"
        else:
            label = f"{name} {', '.join(str(y) for y in years)}"
        span = " и ".join(
            f"{_fmt_day(a)}–{_fmt_day(b - pd.Timedelta(days=1))}" for a, b in windows
        )
        return _DealPeriod(windows=tuple(windows), label=label, span=span, kind="month")

    if spec.kind == "quarter" and spec.quarter:
        if not years:
            return None
        windows = []
        start_month = (spec.quarter - 1) * 3 + 1
        for year in years:
            start = _ts(year, start_month, 1)
            end_month = start_month + 3
            end = (
                _ts(year + 1, end_month - 12, 1)
                if end_month > 12
                else _ts(year, end_month, 1)
            )
            windows.append((start, end))
        if spec.year:
            label = f"{spec.quarter} квартал {spec.year}"
        elif len(years) == 1:
            label = f"{spec.quarter} квартал {years[0]}"
        else:
            label = f"{spec.quarter} квартал"
        span = _QUARTER_SPAN[spec.quarter]
        return _DealPeriod(windows=tuple(windows), label=label, span=span, kind="quarter")

    if spec.kind == "year" and spec.year:
        start = _ts(spec.year, 1, 1)
        end = _ts(spec.year + 1, 1, 1)
        return _DealPeriod(
            windows=((start, end),),
            label=f"{spec.year} год",
            span=f"01.01.{spec.year}–31.12.{spec.year}",
            kind="year",
        )
    return None


def _period_mask(
    parsed: pd.Series, windows: tuple[tuple[pd.Timestamp, pd.Timestamp], ...]
) -> pd.Series:
    mask = pd.Series(False, index=parsed.index)
    valid = parsed.notna()
    for start, end in windows:
        mask = mask | ((parsed >= start) & (parsed < end))
    return mask & valid


def _parse_quarter_request(question: str) -> tuple[int | None, int | None]:
    """Номер квартала 1–4 и год, если человек их назвал."""
    q = (question or "").lower().replace("ё", "е")
    year = None
    year_match = re.search(r"(20\d{2})", q)
    if year_match:
        year = int(year_match.group(1))
        q = q[: year_match.start()] + " " + q[year_match.end() :]
    else:
        short = re.search(r"(?<!\d)(\d{2})\s*(?:год|г(?:\.|\b))", q)
        if short:
            year = 2000 + int(short.group(1))
            q = q[: short.start()] + " " + q[short.end() :]

    if re.search(r"\bперв", q) or re.search(r"\bq\s*1\b", q) or re.search(r"\bi\s*кв", q):
        return 1, year
    if re.search(r"\bвтор", q) or re.search(r"\bq\s*2\b", q) or re.search(r"\bii\s*кв", q):
        return 2, year
    if re.search(r"\bтрет", q) or re.search(r"\bq\s*3\b", q) or re.search(r"\biii\s*кв", q):
        return 3, year
    if re.search(r"\bчетверт", q) or re.search(r"\bq\s*4\b", q) or re.search(r"\biv\s*кв", q):
        return 4, year

    num = re.search(r"(?<!\d)([1-4])\s*[-.]?\s*(?:й\s+)?(?:квартал|кв)", q)
    if num:
        return int(num.group(1)), year
    return None, year


def wants_quarter_deals(question: str, df: pd.DataFrame | None = None) -> bool:
    """«Сколько сделок в первом квартале?» — счёт по дате начала, не график."""
    q = (question or "").lower().replace("ё", "е")
    if any(
        marker in q
        for marker in ("график", "диаграмм", "построй", "кругов", "динамик", "тренд", "линейн")
    ):
        return False
    has_quarter = (
        "квартал" in q
        or re.search(r"\bкв\.?\b", q)
        or re.search(r"\bq\s*[1-4]\b", q)
    )
    if not has_quarter:
        return False
    return bool(
        re.search(r"скольк", q)
        or "сделк" in q
        or "разбив" in q
        or "по квартал" in q
        or _parse_quarter_request(question)[0] is not None
    )


def wants_department_deals(question: str, df: pd.DataFrame | None = None) -> bool:
    """«Сколько сделок в подразделении COOK?» — счёт по отделу, не деньги."""
    q = (question or "").lower().replace("ё", "е")
    if any(
        marker in q
        for marker in ("график", "диаграмм", "построй", "кругов", "динамик", "тренд")
    ):
        return False
    if any(
        marker in q
        for marker in ("остат", "оплат", "дефицит", "долг", "неоплач", "сумм", "выручк")
    ):
        return False
    if not (
        "подраздел" in q
        or "департамент" in q
        or re.search(r"\bотдел", q)
        or department_keys(question)
    ):
        return False
    return bool(re.search(r"скольк", q) or "сделк" in q or "разбив" in q or "по подраздел" in q)


def wants_period_deals(question: str, df: pd.DataFrame | None = None) -> bool:
    """«Сколько сделок в январе / за неделю / 15.03?» — счёт за названный период."""
    q = (question or "").lower().replace("ё", "е")
    if any(
        marker in q
        for marker in ("график", "диаграмм", "построй", "кругов", "динамик", "тренд", "линейн")
    ):
        return False
    if any(
        marker in q
        for marker in ("остат", "оплат", "дефицит", "долг", "неоплач", "сумм", "выручк")
    ):
        return False
    if not (re.search(r"скольк", q) or "сделк" in q or "разбив" in q):
        return False
    spec = _extract_period_request(question)
    if spec is None:
        return False
    if spec.kind == "quarter":
        return False
    return True


def _department_column(df: pd.DataFrame) -> str | None:
    from services.column_resolver import resolve_semantic_column

    return resolve_semantic_column(df, "", "department", dtype="categorical")


def exec_quarter_deals(df: pd.DataFrame, question: str) -> dict:
    """Сделки по кварталу даты начала и/или по подразделению."""
    q = (question or "").lower().replace("ё", "е")
    dept_col = _department_column(df)
    breakdown = bool(
        re.search(r"по\s+подраздел", q)
        or re.search(r"по\s+отдел", q)
        or re.search(r"по\s+департамент", q)
    ) and not re.search(r"в\s+подраздел", q) and not re.search(r"в\s+отдел", q)

    found = match_entity_slice(df, question)
    leftover = entity_name_words(question)
    if leftover and (not found or not found.get("names") or found.get("frame") is None):
        if breakdown:
            leftover = []
        else:
            hint = " ".join(leftover)
            names = []
            if dept_col:
                names = sorted(
                    {
                        str(value).strip()
                        for value in df[dept_col].dropna().unique()
                        if str(value).strip() and str(value).strip().lower() not in {"nan", "-"}
                    }
                )
            listing = ", ".join(f"«{name}»" for name in names[:8])
            extra = f" В файле: {listing}." if listing else ""
            where = "подразделений" if dept_col else "заказчиков и подразделений"
            return {
                "answer": (
                    f"Не нашёл «{hint}» среди {where}.{extra} "
                    "Укажите название как в файле."
                )
            }

    frame = df
    who = ""
    if found and found.get("names") and found.get("frame") is not None:
        frame = found["frame"]
        names = found["names"]
        col_name = str(found.get("column") or "").lower()
        if any(m in col_name for m in ("подраздел", "отдел", "департамент", "служб")):
            label = names[0] if len(names) == 1 else ", ".join(names[:3])
            who = f" в подразделении «{label}»"
        else:
            who = f" у «{names[0]}»" if len(names) == 1 else (
                " у " + ", ".join(f"«{name}»" for name in names[:3])
            )

    spec = _extract_period_request(question)
    period = _resolve_deal_period(spec, df) if spec else None
    start_col = _start_date_column(df)
    if spec and spec.kind != "quarter" and not start_col:
        dates = [
            str(name)
            for name in df.columns
            if "дат" in str(name).lower()
        ]
        hint = ", ".join(f"«{name}»" for name in dates[:6]) if dates else "нет колонок с датой"
        return {
            "answer": (
                "Чтобы посчитать сделки за день, неделю или месяц, нужна колонка "
                f"**дата начала сделки**. Сейчас в файле: {hint}."
            )
        }
    if spec and period and period.windows and start_col:
        parsed = pd.to_datetime(frame[start_col], errors="coerce", dayfirst=True)
        frame = frame.loc[_period_mask(parsed, period.windows)]
        n = len(frame)
        head = f"За **{period.label}**"
        if period.span:
            head += f" ({period.span})"
        head += f" — **{n}** {_deals_word(n)}{who}."
        if n == 0:
            return {"answer": head + f" Смотрел колонку «{start_col}»."}
        if dept_col and not who:
            counts = (
                frame[dept_col]
                .dropna()
                .astype(str)
                .str.strip()
                .replace({"": None, "nan": None, "-": None})
                .dropna()
                .value_counts()
            )
            lines = [head, f"По подразделениям («{dept_col}»):"]
            for i, (name, cnt) in enumerate(counts.head(12).items(), 1):
                lines.append(f"{i}. {name} — **{int(cnt)}** {_deals_word(int(cnt))}")
            extra = len(counts) - 12
            if extra > 0:
                lines.append(f"… и ещё {extra}")
            return {"answer": "\n".join(lines)}
        return {"answer": head}

    if breakdown and dept_col and not leftover:
        counts = (
            frame[dept_col]
            .dropna()
            .astype(str)
            .str.strip()
            .replace({"": None, "nan": None, "-": None})
            .dropna()
            .value_counts()
        )
        if counts.empty:
            return {"answer": "В колонке подразделения нет названий."}
        head = f"Сделки по подразделениям («{dept_col}»)"
        start_col = _start_date_column(frame)
        if start_col:
            parsed = pd.to_datetime(frame[start_col], errors="coerce", dayfirst=True)
            valid = parsed.dropna()
            if not valid.empty:
                a = valid.min().strftime("%d.%m.%Y")
                b = valid.max().strftime("%d.%m.%Y")
                head += f" за период {a}–{b}"
        lines = [head + ":"]
        for i, (name, n) in enumerate(counts.head(12).items(), 1):
            lines.append(f"{i}. {name} — **{int(n)}** {_deals_word(int(n))}")
        extra = len(counts) - 12
        if extra > 0:
            lines.append(f"… и ещё {extra}")
        return {"answer": "\n".join(lines)}

    col = _start_date_column(df)
    want_q, want_year = _parse_quarter_request(question)
    if not col and not want_q and "квартал" not in q:
        return {
            "answer": (
                f"Сделок{who}: **{len(frame)}** {_deals_word(len(frame))}."
            )
        }
    if not col:
        dates = [
            str(name)
            for name in df.columns
            if "дат" in str(name).lower()
        ]
        hint = ", ".join(f"«{name}»" for name in dates[:6]) if dates else "нет колонок с датой"
        return {
            "answer": (
                "Чтобы разложить сделки по кварталам, нужна колонка "
                "**дата начала сделки**. Сделка относится к кварталу, "
                "в котором она началась: 1 кв. 01.01–31.03, 2 кв. 01.04–30.06, "
                f"3 кв. 01.07–30.09, 4 кв. 01.10–31.12. Сейчас в файле: {hint}."
            )
        }

    parsed = pd.to_datetime(frame[col], errors="coerce", dayfirst=True)
    valid = parsed.notna()
    dated = frame.loc[valid].copy()
    dated["_year"] = parsed.loc[valid].dt.year.astype(int)
    dated["_quarter"] = parsed.loc[valid].dt.quarter.astype(int)
    blank = int((~valid).sum())

    grouped = (
        dated.groupby(["_year", "_quarter"], sort=True)
        .size()
        .rename("n")
    )

    def _line(year: int, quarter: int, n: int) -> str:
        span = _QUARTER_SPAN[quarter]
        return f"**{quarter} кв. {year}** ({span}) — **{n}** {_deals_word(n)}"

    if not want_q and leftover and "квартал" not in q:
        n = len(frame)
        lines = [f"Сделок{who}: **{n}** {_deals_word(n)}."]
        if not grouped.empty:
            lines.append(f"По дате начала («{col}»):")
            for (year, quarter), cnt in grouped.items():
                lines.append(f"• {_line(int(year), int(quarter), int(cnt))}")
        if blank:
            lines.append(f"Без даты начала: {blank}.")
        return {"answer": "\n".join(lines)}

    if want_q:
        rows = [
            (year, quarter, int(n))
            for (year, quarter), n in grouped.items()
            if quarter == want_q and (want_year is None or year == want_year)
        ]
        if want_year is not None:
            if not rows:
                extra = f" Без даты начала: {blank}." if blank else ""
                return {
                    "answer": (
                        f"В **{want_q} квартале {want_year}** ({_QUARTER_SPAN[want_q]}) "
                        f"сделок{who} нет. Смотрел колонку «{col}».{extra}"
                    )
                }
        if not rows:
            extra = f" Без даты начала: {blank}." if blank else ""
            return {
                "answer": (
                    f"В **{want_q} квартале** ({_QUARTER_SPAN[want_q]}) "
                    f"сделок{who} нет. Смотрел колонку «{col}».{extra}"
                )
            }
        total = sum(item[2] for item in rows)
        lines = [
            f"Сделки{who} по дате начала («{col}»). "
            f"Квартал — три месяца, сделка в том, **где началась**."
        ]
        if len(rows) == 1:
            year, quarter, n = rows[0]
            lines.append(_line(year, quarter, n) + ".")
        else:
            lines.append(
                f"**{want_q} квартал** ({_QUARTER_SPAN[want_q]}): **{total}** "
                f"{_deals_word(total)}."
            )
            for year, quarter, n in rows:
                lines.append(f"• {_line(year, quarter, n)}")
        if blank:
            lines.append(f"Без даты начала: {blank}.")
        return {"answer": "\n".join(lines)}

    if grouped.empty:
        return {
            "answer": (
                f"В колонке «{col}» нет распознанных дат начала, "
                "кварталы посчитать нельзя."
            )
        }
    lines = [
        f"Сделки{who} по кварталам даты начала («{col}»). "
        "1 кв. 01.01–31.03, 2 кв. 01.04–30.06, "
        "3 кв. 01.07–30.09, 4 кв. 01.10–31.12."
    ]
    for (year, quarter), n in grouped.items():
        lines.append(f"• {_line(int(year), int(quarter), int(n))}")
    if blank:
        lines.append(f"Без даты начала: {blank}.")
    return {"answer": "\n".join(lines)}


def _entity_metric_lines(frame: pd.DataFrame, layout) -> list[str]:
    lines = [f"строк: {len(frame)}"]
    pairs = (
        ("оплачено", layout.paid),
        ("неоплаченный остаток", layout.unpaid),
        ("сумма заказов", layout.order_sum),
    )
    for label, col in pairs:
        if not col:
            continue
        lines.append(f"{label} («{col}»): {format_chat_number(_col_sum(frame, col), money=True)}")
    return lines


def exec_named_compare(df: pd.DataFrame, question: str) -> dict:
    slices = match_entities_separately(df, question)
    if len(slices) < 2:
        if slices:
            return exec_entity_metrics(df, question)
        hint = " ".join(entity_name_words(question))
        return {
            "answer": (
                f"Не нашёл двух сторон для сравнения «{hint}». "
                "Укажите заказчиков так, как в файле."
            )
        }

    layout = detect_deficit_money_layout(df)
    from services.chat_answers import (
        format_chat_number,
        format_metric_line,
        layout_values,
        money_gap_note,
        money_title,
    )

    blocks: list[str] = ["**Сравнение**"]
    unpaid_best: tuple[str, float] | None = None
    order_best: tuple[str, float] | None = None
    metric_best: tuple[str, str, float] | None = None
    for item in slices[:4]:
        frame = item["frame"]
        name = item["name"]
        blocks.append(f"**«{name}»**")
        blocks.append(f"• строк: {len(frame)}")
        vals = layout_values(frame, layout)
        pairs = (
            ("paid", layout.paid, vals["paid"]),
            ("unpaid", layout.unpaid, vals["unpaid"]),
            ("order", layout.order_sum, vals["order"]),
        )
        used_cols: list[str] = []
        for kind, col, value in pairs:
            if not col or value is None:
                continue
            title = money_title(kind, col)
            blocks.append(format_metric_line(title, col, value))
            used_cols.append(col)
            if kind == "unpaid":
                if unpaid_best is None or value > unpaid_best[1]:
                    unpaid_best = (name, value)
            if kind == "order":
                if order_best is None or value > order_best[1]:
                    order_best = (name, value)
        if not used_cols:
            from services.chat_answers import (
                count_breakdown_answer,
                format_chat_number,
                is_money_column,
                slice_metric,
            )

            metric = slice_metric(df, frame, question)
            if metric:
                col, value = metric
                blocks.append(
                    f"• {col}: **{format_chat_number(value, money=is_money_column(col))}**"
                )
                if metric_best is None or value > metric_best[2]:
                    metric_best = (name, col, value)
            else:
                blocks.append(count_breakdown_answer(frame, f"«{name}»"))
        gap = money_gap_note(frame, used_cols)
        if gap:
            blocks.append(gap)
    notes = []
    if unpaid_best:
        notes.append(
            f"Больше неоплаченный остаток у «{unpaid_best[0]}» "
            f"({format_chat_number(unpaid_best[1], money=True)})."
        )
    if order_best:
        title = money_title("order", layout.order_sum or "")
        notes.append(
            f"Больше {title.lower()} у «{order_best[0]}» "
            f"({format_chat_number(order_best[1], money=True)})."
        )
    if metric_best:
        notes.append(
            f"Больше «{metric_best[1]}» у «{metric_best[0]}» "
            f"({format_chat_number(metric_best[2], money=is_money_column(metric_best[1]))})."
        )
    if notes:
        blocks.append("")
        blocks.extend(notes)
    if layout.unpaid and layout.order_sum:
        blocks.append(
            "Неоплаченный остаток и сумма заказа — разные колонки: "
            "кто должен больше, не обязан быть тем, кто заказал больше."
        )
    return {"answer": "\n".join(blocks)}


def _top_lines(df: pd.DataFrame, group_col: str, value_col: str, n: int = 5) -> list[str]:
    from services.file_context_service import _top_sums

    items = _top_sums(df, group_col, value_col, n=n)
    return [
        f"{i}. {name} — {format_chat_number(value, money=True)}"
        for i, (name, value) in enumerate(items, 1)
    ]


def exec_rank_compare(df: pd.DataFrame, question: str) -> dict:
    from services.column_resolver import resolve_semantic_column

    q = question.lower().replace("ё", "е")
    layout = detect_deficit_money_layout(df)
    group_col = resolve_semantic_column(df, "", "client", dtype="categorical")
    if not group_col:
        return {"answer": "Не нашёл колонку заказчика, чтобы сравнить лидеров."}

    wants_unpaid = any(marker in q for marker in _OWES) or not any(
        marker in q for marker in _ORDERED
    )
    wants_order = any(marker in q for marker in _ORDERED) or not any(
        marker in q for marker in _OWES
    )
    if any(marker in q for marker in _OWES) and any(marker in q for marker in _ORDERED):
        wants_unpaid = True
        wants_order = True

    blocks: list[str] = []
    if wants_unpaid and layout.unpaid:
        lines = _top_lines(df, group_col, layout.unpaid)
        if lines:
            blocks.append(
                f"**Кто больше должен** (неоплаченный остаток «{layout.unpaid}»):"
            )
            blocks.extend(lines)
    if wants_order and layout.order_sum:
        lines = _top_lines(df, group_col, layout.order_sum)
        if lines:
            from services.chat_answers import money_title

            title = money_title("order", layout.order_sum)
            blocks.append(
                f"**Кто больше заказал** ({title.lower()} «{layout.order_sum}»):"
            )
            blocks.extend(lines)
    if wants_unpaid and not layout.unpaid:
        msg = (
            "Колонки неоплаченного остатка в файле нет — "
            "«кто должен» посчитать нельзя."
        )
        if blocks:
            blocks.insert(0, msg)
        else:
            return {"answer": msg + " Есть сумма по колонкам сделок/заказов."}
    if not blocks:
        return {
            "answer": "Не удалось сравнить должников и суммы заказов: нет денежных колонок."
        }
    if wants_unpaid and wants_order and layout.unpaid and layout.order_sum and len(blocks) >= 2:
        blocks.append(
            "Лидеры по остатку и по сумме заказа могут не совпадать — "
            "это разные колонки."
        )
    return {"answer": "\n".join(blocks)}


def _period_slice(df: pd.DataFrame, question: str) -> str:
    from services import data_tools

    q = question.lower()
    if not any(marker in q for marker in _PERIOD_MARK):
        return ""
    if "месяц" in q:
        data = data_tools.group_by_month(df, question)
        label = "месяцам"
    elif "год" in q or "ежегодн" in q:
        data = data_tools.group_by_year(df, question)
        label = "годам"
    else:
        data = data_tools.group_by_quarter(df, question)
        label = "кварталам"
    if "error" in data or not data.get("groups"):
        return ""
    items = list(data["groups"].items())[:8]
    col = data.get("value_column") or ""
    lines = [f"Срезы по {label}" + (f" («{col}»)" if col else "") + ":"]
    for name, value in items:
        lines.append(f"- {name}: {format_chat_number(value, money=True)}")
    return "\n".join(lines)


def _payment_overview_lines(df: pd.DataFrame) -> list[str]:
    layout = detect_deficit_money_layout(df)
    if not (layout.unpaid or layout.paid or layout.stages):
        return []
    lines = ["Итоги по оплатам (весь рабочий лист):"]
    for kpi in deficit_kpis(df):
        lines.append(f"- {kpi['label']}: {kpi['value']}")
    return lines


def question_slice_facts(df: pd.DataFrame, question: str) -> str:
    """Pandas-цифры, относящиеся к этому вопросу. Пустая строка — нечего добавить."""
    parts: list[str] = []
    slices = match_entities_separately(df, question)
    layout = detect_deficit_money_layout(df)
    if len(slices) >= 2:
        for item in slices[:4]:
            body = "; ".join(_entity_metric_lines(item["frame"], layout))
            parts.append(f"«{item['name']}»: {body}")
    elif len(slices) == 1:
        item = slices[0]
        body = "; ".join(_entity_metric_lines(item["frame"], layout))
        parts.append(f"Срез «{item['name']}»: {body}")
    elif wants_rank_compare(question):
        rank = exec_rank_compare(df, question)["answer"]
        parts.append(rank)
    elif wants_payment_overview(question) or (
        any(m in question.lower() for m in _PAY_MARK)
        and not entity_name_words(question)
    ):
        parts.extend(_payment_overview_lines(df))

    found = match_entity_slice(df, question) if not slices else None
    if found and found["names"] and not slices:
        frame = found["frame"]
        if frame is not None and not frame.empty:
            who = ", ".join(found["names"][:3])
            body = "; ".join(_entity_metric_lines(frame, layout))
            parts.append(f"Срез «{who}»: {body}")

    period = _period_slice(df, question)
    if period:
        parts.append(period)
    return "\n".join(p for p in parts if p)


def compact_file_header(file_context, *, n_facts: int = 8) -> str:
    if file_context is None:
        return ""
    lines: list[str] = []
    title = getattr(file_context, "title", "") or ""
    kind = getattr(file_context, "report_kind", "") or ""
    grain = getattr(file_context, "grain", "") or ""
    summary = getattr(file_context, "summary", "") or ""
    if title:
        lines.append(f"Название: {title}")
    if kind:
        lines.append(f"Тип: {kind}")
    if grain:
        lines.append(f"Зерно: {grain}")
    if summary:
        lines.append(summary)
    caveats = list(getattr(file_context, "caveats", None) or [])[:2]
    if caveats:
        lines.append("Ограничения: " + "; ".join(caveats))
    facts = list(getattr(file_context, "facts", None) or [])[:n_facts]
    if facts:
        lines.append("Факты по файлу:")
        lines.extend(f"- {fact}" for fact in facts)
    return "\n".join(lines)


def build_answer_facts(
    df: pd.DataFrame,
    question: str,
    file_context=None,
) -> str:
    """Глобальные факты файла + срез по вопросу для ответа LLM."""
    header = compact_file_header(file_context)
    if not header:
        from services.insights_service import get_basic_insights

        header = "\n".join(get_basic_insights(df)[:6])
    slice_text = question_slice_facts(df, question)
    parts = [header]
    if slice_text:
        parts.append("Срез по вопросу (посчитан pandas, не выдумывай другие цифры):")
        parts.append(slice_text)
    return "\n".join(p for p in parts if p)
