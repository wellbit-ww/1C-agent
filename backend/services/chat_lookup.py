"""Поиск конкретного заказа в выгрузке: комментарий и карточка строки.

Срабатывает на вопросы вроде «Что с заказом Алабуги», «Что с заказом САУП-000450»,
«Что с заказом от 29.12». Цифры и текст берём из строки pandas, не из LLM.
"""
from __future__ import annotations

import re
from datetime import datetime, date

import pandas as pd

from services.insights_service import _format_number

_ORDER_WORD = re.compile(r"\bзаказ(?:а|у|ом|е|ы|ов)?\b", re.I)
_CODE_RE = re.compile(
    r"[A-Za-zА-Яа-яЁё]{2,}\d*[-_/][0-9A-Za-zА-Яа-яЁё._/-]{2,}"
)
_DATE_RE = re.compile(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\b")
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё]{3,}")

_STOP = {
    "что", "как", "дела", "дело", "там", "этот", "эта", "это", "эти",
    "расскажи", "скажи", "покажи", "подскажи", "статус", "состояние",
    "состоянии", "комментарий", "комментарии", "пожалуйста", "про",
    "заказ", "заказа", "заказу", "заказом", "заказе", "заказы", "заказов",
    "клиент", "клиента", "клиенту", "номер", "номера", "номеру",
    "договор", "договора", "сделка", "сделки", "сделке",
    "наш", "наша", "наше", "нас", "мне", "его", "её", "ее",
}

_LOOKUP_HINTS = (
    "что с",
    "что по",
    "как дела",
    "комментар",
    "статус",
    "состояни",
    "расскажи",
    "подробн",
    "что там",
)

_STEM_SUFFIXES = (
    "ами", "ями", "ого", "его", "ому", "ему", "ыми", "ими",
    "ах", "ях", "ой", "ей", "ий", "ый", "ая", "ое", "ые", "ие",
    "ов", "ев", "ам", "ям", "ом", "ем", "ую", "юю", "ии",
    "ы", "и", "а", "я", "у", "ю", "о", "е", "ь",
)

_COMMENT_MARKERS = ("комментар", "примечан")
_SKIP_DISPLAY = ("unnamed", "№ п/п", "№")
_ID_COL_MARKERS = ("заказ клиента", "объект расчет", "номер заказа", "номер договор")
_CLIENT_MARKERS = ("заказчик", "клиент", "контрагент", "компани", "наименование заказ")
_DEAL_MARKERS = ("сделка", "оборудован")
_DATE_COL_MARKERS = ("дата", "срок")

_DISPLAY_ORDER = (
    "заказ клиента",
    "объект расчет",
    "заказчик",
    "наименование заказчика",
    "сделка",
    "ответственн",
    "подразделение",
    "номер договора",
    "договор",
    "категория",
    "дата поставки",
    "дата отгрузки",
    "дата реализации",
    "дата пнр",
    "срок аттестации",
    "сумма по заказу в рублях",
    "сумма заказа",
    "сумма всего",
    "валюта",
    "сумма по заказу в валюте",
    "к оплате",
    "оплачен",
    "неоплачен",
    "не оплачен",
    "остаток",
    "сумма долга",
)


def _norm(text: str) -> str:
    return (
        str(text)
        .lower()
        .replace("ё", "е")
        .replace("—", "-")
        .replace("–", "-")
        .strip()
    )


def _stem(word: str) -> str:
    w = _norm(word)
    for suffix in _STEM_SUFFIXES:
        if len(w) > len(suffix) + 3 and w.endswith(suffix):
            return w[: -len(suffix)]
    return w


def _is_blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text == "" or text.lower() in {"nan", "none", "nat", "-"}


def _comment_column(df: pd.DataFrame) -> str | None:
    for col in df.columns:
        name = str(col).lower()
        if any(m in name for m in _COMMENT_MARKERS):
            return col
    return None


def _col_matches(col, markers: tuple[str, ...]) -> bool:
    name = str(col).lower()
    return any(m in name for m in markers)


def extract_order_clues(question: str) -> dict[str, list[str]]:
    """Номер документа, дата и текстовый хвост («Алабуги») из вопроса."""
    codes_raw = [m.group(0).rstrip(".,;:)") for m in _CODE_RE.finditer(question)]
    codes = [_norm(c) for c in codes_raw]
    dates = [m.group(0) for m in _DATE_RE.finditer(question)]
    leftover = question
    for raw in codes_raw + dates:
        leftover = leftover.replace(raw, " ")
    words = []
    for token in _WORD_RE.findall(leftover):
        n = _norm(token)
        if n in _STOP or len(_stem(n)) < 4:
            continue
        words.append(token)
    return {"codes": codes, "dates": dates, "words": words}


def wants_order_lookup(question: str) -> bool:
    q = _norm(question)
    clues = extract_order_clues(question)
    has_clue = bool(clues["codes"] or clues["dates"] or clues["words"])
    if not has_clue:
        return False
    if clues["codes"]:
        return True
    hinted = any(h in q for h in _LOOKUP_HINTS)
    has_order = bool(_ORDER_WORD.search(q))
    if has_order and (hinted or clues["dates"] or clues["words"]):
        return True
    return False


def _parse_date_tuple(text: str) -> tuple[int, int, int | None] | None:
    match = _DATE_RE.search(str(text))
    if not match:
        return None
    day, month = int(match.group(1)), int(match.group(2))
    if not (1 <= day <= 31 and 1 <= month <= 12):
        return None
    year = match.group(3)
    if year:
        y = int(year)
        if y < 100:
            y += 2000
        return day, month, y
    return day, month, None


def _dates_in_value(value) -> list[tuple[int, int, int | None]]:
    found: list[tuple[int, int, int | None]] = []
    if isinstance(value, datetime):
        found.append((value.day, value.month, value.year))
        return found
    if isinstance(value, date):
        found.append((value.day, value.month, value.year))
        return found
    if _is_blank(value) or isinstance(value, (int, float, bool)):
        return found
    parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
    if not pd.isna(parsed):
        ts = pd.Timestamp(parsed)
        found.append((int(ts.day), int(ts.month), int(ts.year)))
    for match in _DATE_RE.finditer(str(value)):
        item = _parse_date_tuple(match.group(0))
        if item and item not in found:
            found.append(item)
    return found


def _date_matches(query: str, value) -> bool:
    wanted = _parse_date_tuple(query)
    if not wanted:
        return False
    qd, qm, qy = wanted
    for d, m, y in _dates_in_value(value):
        if d == qd and m == qm and (qy is None or y == qy):
            return True
    return False


def _text_matches(query: str, value) -> bool:
    if _is_blank(value):
        return False
    cell = _norm(value)
    qn = _norm(query)
    if qn and qn in cell:
        return True
    qs = _stem(qn)
    if len(qs) < 4:
        return False
    for token in _WORD_RE.findall(cell):
        ts = _stem(token)
        if not ts:
            continue
        if qs == ts:
            return True
        if len(qs) >= 5 and (ts.startswith(qs) or qs.startswith(ts)):
            return True
    return False


def _code_matches(code: str, value) -> bool:
    if _is_blank(value):
        return False
    return _norm(code) in _norm(value)


def _score_row(row: pd.Series, clues: dict[str, list[str]]) -> int:
    score = 0
    for col, value in row.items():
        name = str(col).lower()
        in_id = _col_matches(col, _ID_COL_MARKERS)
        in_client = _col_matches(col, _CLIENT_MARKERS)
        in_deal = _col_matches(col, _DEAL_MARKERS)
        in_comment = _col_matches(col, _COMMENT_MARKERS)
        for code in clues["codes"]:
            if _code_matches(code, value):
                score += 120 if in_id else 70 if not in_comment else 20
        for word in clues["words"]:
            if _text_matches(word, value):
                if in_client:
                    score += 80
                elif in_deal:
                    score += 50
                elif in_id:
                    score += 40
                elif in_comment:
                    score += 15
                else:
                    score += 25
        for raw_date in clues["dates"]:
            if _date_matches(raw_date, value):
                if in_id or "заказ" in name:
                    score += 90
                elif _col_matches(col, _DATE_COL_MARKERS):
                    score += 25
                else:
                    score += 40
    return score


def find_order_rows(df: pd.DataFrame, question: str, limit: int = 5) -> pd.DataFrame:
    clues = extract_order_clues(question)
    if not (clues["codes"] or clues["dates"] or clues["words"]):
        return df.iloc[0:0]
    scores = df.apply(lambda row: _score_row(row, clues), axis=1)
    ranked = df.assign(_lookup_score=scores)
    ranked = ranked[ranked["_lookup_score"] > 0].sort_values(
        "_lookup_score", ascending=False
    )
    return ranked.head(limit).drop(columns=["_lookup_score"])


def _fmt_cell(value) -> str:
    if _is_blank(value):
        return ""
    if isinstance(value, (datetime, date)) and not isinstance(value, bool):
        try:
            return pd.Timestamp(value).strftime("%d.%m.%Y")
        except Exception:
            return str(value)
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _format_number(float(value))
    text = str(value).strip()
    if re.fullmatch(r"-?\d+(?:[.,]\d+)?", text.replace(" ", "").replace("\xa0", "")):
        try:
            return _format_number(float(text.replace(" ", "").replace("\xa0", "").replace(",", ".")))
        except ValueError:
            return text
    return text


def _display_columns(df: pd.DataFrame, comment_col: str | None) -> list[str]:
    ordered: list[str] = []
    for marker in _DISPLAY_ORDER:
        for col in df.columns:
            if col == comment_col or col in ordered:
                continue
            if marker in str(col).lower():
                ordered.append(col)
    for col in df.columns:
        if col == comment_col or col in ordered:
            continue
        name = str(col).lower()
        if name in _SKIP_DISPLAY or name.startswith("unnamed"):
            continue
        ordered.append(col)
    return ordered


def _title_for_row(row: pd.Series) -> str:
    for markers in (_ID_COL_MARKERS, _DEAL_MARKERS, _CLIENT_MARKERS):
        for col in row.index:
            if _col_matches(col, markers) and not _is_blank(row[col]):
                return str(row[col]).strip()
    return "Заказ"


def format_order_card(row: pd.Series, comment_col: str | None) -> str:
    lines = [f"**{_title_for_row(row)}**", ""]
    comment = ""
    if comment_col and comment_col in row.index and not _is_blank(row[comment_col]):
        comment = str(row[comment_col]).strip()
    if comment:
        lines.append("**Комментарий**")
        lines.append(comment)
        lines.append("")
    else:
        lines.append("В колонке комментария по этой строке пусто.")
        lines.append("")

    lines.append("**Основное**")
    dummy = pd.DataFrame([row])
    for col in _display_columns(dummy, comment_col):
        text = _fmt_cell(row[col])
        if not text:
            continue
        lines.append(f"• {col}: {text}")
    return "\n".join(lines).rstrip()


def exec_order_lookup(df: pd.DataFrame, question: str) -> dict:
    matches = find_order_rows(df, question)
    comment_col = _comment_column(df)
    if matches.empty:
        clues = extract_order_clues(question)
        hint = ", ".join(
            clues["codes"] or clues["words"] or clues["dates"] or [question.strip()]
        )
        return {
            "answer": (
                f"Не нашёл заказ по запросу «{hint}». "
                "Уточните номер (например САУП-000450), заказчика или дату."
            )
        }

    cards = [format_order_card(row, comment_col) for _, row in matches.iterrows()]
    if len(cards) == 1:
        return {"answer": cards[0]}
    header = f"Нашёл {len(cards)} заказ(а) по запросу. Карточки ниже."
    return {"answer": header + "\n\n" + "\n\n---\n\n".join(cards)}
