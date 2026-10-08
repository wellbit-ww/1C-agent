"""Движок дашбордов: Dashboard Spec -> набор plotly-фигур.

Всё исполнение детерминированное: pandas считает, plotly рисует.
LLM может только генерировать/редактировать спеку — никогда не считает.
"""
import logging
import re

import pandas as pd
import plotly.graph_objects as go

from models.dashboard_spec import DashboardSpec, Tab, Tile
from services import data_tools
from services.chart_style import chart_title_caps
from services.dept_chart_colors import COMPANY_CHART_COLOR, department_chart_color
from services.column_resolver import resolve_semantic_column

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Единицы измерения (млрд/млн/тыс)
# ---------------------------------------------------------------------------

_UNIT_SCALES = {
    "rub": (1.0, "₽"),
    "k": (1e3, "тыс."),
    "mln": (1e6, "млн"),
    "mlrd": (1e9, "млрд"),
}


def _auto_scale(max_value: float) -> tuple[float, str]:
    v = abs(max_value)
    if v >= 1e9:
        return _UNIT_SCALES["mlrd"]
    if v >= 1e6:
        return _UNIT_SCALES["mln"]
    if v >= 1e3:
        return _UNIT_SCALES["k"]
    return (1.0, "")


def _resolve_scale(unit: str, values: list[float]) -> tuple[float, str]:
    peak = max((abs(v) for v in values), default=0.0)
    if unit in ("auto", "") or (unit == "rub" and peak >= 1_000_000):
        return _auto_scale(peak)
    return _UNIT_SCALES.get(unit, (1.0, ""))


def _fmt_scaled(value: float, scale: float, suffix: str) -> str:
    scaled = value / scale
    if scale >= 1e3:
        text = f"{scaled:,.2f}".rstrip("0").rstrip(".")
    else:
        text = f"{scaled:,.0f}" if float(scaled).is_integer() else f"{scaled:,.1f}"
    return f"{text} {suffix}".strip()


# ---------------------------------------------------------------------------
# Данные для тайла
# ---------------------------------------------------------------------------

def _resolve_group_column(df: pd.DataFrame, tile: Tile) -> str | None:
    source = tile.source
    if source.group_column:
        # явное имя колонки обязано существовать — иначе это баг спеки,
        # а не повод молча рисовать другую колонку
        return source.group_column if source.group_column in df.columns else None
    if source.group_semantic:
        col = resolve_semantic_column(df, tile.title, source.group_semantic, dtype="categorical")
        if col:
            return col
    return data_tools._resolve_categorical_column(df, tile.title)


def _resolve_value_column(df: pd.DataFrame, tile: Tile) -> str | None:
    source = tile.source
    if source.value_column:
        return source.value_column if source.value_column in df.columns else None
    if source.value_semantic:
        col = resolve_semantic_column(df, tile.title, source.value_semantic, dtype="numeric")
        if col:
            return col
    return data_tools._resolve_numeric_column(df, tile.title)


_JUNK_LABELS = {"", "nan", "none", "-", "не указан", "не указано", "без названия", "null"}
_YES_NO = {"да", "нет", "yes", "no"}


def _drop_yes_no_junk(grouped: pd.Series) -> pd.Series:
    """В колонке да/нет единичные ФИО и заголовки не становятся категориями."""
    labels = {str(key).strip().lower() for key in grouped.index}
    if not ({"да", "нет"} <= labels or {"yes", "no"} <= labels):
        return grouped
    keep = [key for key in grouped.index if str(key).strip().lower() in _YES_NO]
    return grouped.loc[keep]


def _clean_groups(grouped: pd.Series) -> dict[str, float]:
    cleaned: dict[str, float] = {}
    for key, value in grouped.items():
        label = str(key).strip()
        if label.lower() in _JUNK_LABELS:
            continue
        if pd.isna(value):
            continue
        cleaned[label] = float(value)
    return cleaned


def _group_data(df: pd.DataFrame, tile: Tile) -> dict:
    group_col = _resolve_group_column(df, tile)
    if not group_col:
        return {"error": f"Не нашёл колонку группировки для «{tile.title}»"}

    if tile.agg == "count":
        grouped = df.groupby(group_col).size()
        value_col = None
    else:
        value_col = _resolve_value_column(df, tile)
        if not value_col:
            return {"error": f"Не нашёл числовую метрику для «{tile.title}»"}
        series = df.groupby(group_col)[value_col]
        grouped = series.mean() if tile.agg == "mean" else series.sum()

    grouped = _drop_yes_no_junk(grouped)
    if tile.sort == "desc":
        grouped = grouped.sort_values(ascending=False)
    elif tile.sort == "asc":
        grouped = grouped.sort_values(ascending=True)

    grouped = grouped.head(tile.top_n)
    groups = _clean_groups(grouped)
    if len(groups) < 2:
        return {"error": "Слишком мало категорий для графика"}

    return {
        "groups": groups,
        "group_column": group_col,
        "value_column": value_col,
    }


def _column_chart_label(col: str, pattern: str = "") -> str:
    text = str(col)
    if pattern and text.endswith(pattern):
        text = text[: -len(pattern)].strip() or text
    for sep in (" — ", " – ", " - "):
        if sep in text:
            text = text.split(sep)[-1].strip()
            break
    return _pretty_stage_label(text)


def _aggregate_columns(df: pd.DataFrame, tile: Tile, columns: list, pattern: str = "") -> dict:
    values: dict[str, float] = {}
    for col in columns:
        if col not in df.columns:
            continue
        series = pd.to_numeric(df[col], errors="coerce")
        if tile.agg == "mean":
            value = series.mean()
        elif tile.agg == "count":
            value = series.notna().sum()
        else:
            value = series.sum()
        if pd.isna(value):
            value = 0.0
        values[_column_chart_label(str(col), pattern)] = float(value)

    if not values:
        return {"error": "Нет данных для тайла"}

    items = list(values.items())
    if tile.sort == "desc":
        items.sort(key=lambda x: -x[1])
    elif tile.sort == "asc":
        items.sort(key=lambda x: x[1])
    return {"groups": dict(items), "group_column": pattern or "columns", "value_column": None}


def _columns_pattern_data(df: pd.DataFrame, tile: Tile) -> dict:
    """Агрегат по каждой колонке, оканчивающейся на pattern (проход через этап)."""
    pattern = tile.source.columns_pattern or ""
    matched = [c for c in df.columns if str(c).endswith(pattern)]
    if not matched:
        return {"error": f"Нет колонок с шаблоном «{pattern}»"}
    data = _aggregate_columns(df, tile, matched, pattern)
    if "error" in data:
        return {"error": f"Колонки по шаблону «{pattern}» пусты"}
    return data


def _named_columns_data(df: pd.DataFrame, tile: Tile) -> dict:
    """Сумма каждой явно перечисленной колонки — блоки «К оплате» и пары оплачено/остаток."""
    names = tile.source.column_names or []
    matched = [c for c in names if c in df.columns]
    if not matched:
        return {"error": f"Нет колонок для «{tile.title}»"}
    return _aggregate_columns(df, tile, matched)


def _stage_labels(columns: list, pattern: str) -> list[str]:
    if not pattern:
        return [str(c) for c in columns]
    return [str(c)[: -len(pattern)].strip() or str(c) for c in columns]


def _last_filled_stage_index(numeric: pd.DataFrame) -> pd.Series:
    """Индекс последнего ненулевого этапа по строке; -1 если ни одного."""
    filled = numeric.notna() & numeric.ne(0)
    has = filled.any(axis=1)
    reversed_cols = filled.iloc[:, ::-1]
    last_name = reversed_cols.idxmax(axis=1)
    positions = {c: i for i, c in enumerate(numeric.columns)}
    idx = last_name.map(positions).astype(int)
    return idx.where(has, other=-1).astype(int)


def _current_stage_data(df: pd.DataFrame, tile: Tile) -> dict:
    """Воронка 1С: каждая сделка на последнем заполненном этапе."""
    pattern = tile.source.columns_pattern or "(сумма)"
    matched = [c for c in df.columns if str(c).endswith(pattern)]
    if not matched:
        matched = [
            c for c in df.columns
            if str(c).endswith("(сумма)") or str(c).endswith("(количество)")
        ]
        # keep one family: prefer (сумма)
        sum_cols = [c for c in matched if str(c).endswith("(сумма)")]
        matched = sum_cols or matched
        pattern = "(сумма)" if sum_cols else (tile.source.columns_pattern or "")
    if not matched:
        return {"error": "Нет колонок этапов для воронки «текущий этап»"}

    sum_cols = [c for c in matched if str(c).endswith("(сумма)")] or list(matched)
    stage_idx = _funnel_current_stage_index(df, sum_cols)
    labels = [
        _pretty_stage_label(label)
        for label in _stage_labels(sum_cols, "(сумма)" if sum_cols else pattern)
    ]
    numeric = df[sum_cols].apply(pd.to_numeric, errors="coerce")
    valid = stage_idx >= 0
    groups = {label: 0.0 for label in labels}
    value_col = None

    if not valid.any():
        return {"error": "Не удалось определить текущий этап ни у одной строки"}

    if tile.agg == "count":
        counts = stage_idx[valid].value_counts()
        for pos, count in counts.items():
            groups[labels[int(pos)]] = float(count)
    else:
        value_col = _resolve_value_column(df, tile)
        if value_col:
            amounts = pd.to_numeric(df[value_col], errors="coerce")
        else:
            arr = numeric.to_numpy()
            amounts = pd.Series(
                [
                    arr[i, int(pos)] if pos >= 0 else float("nan")
                    for i, pos in enumerate(stage_idx.tolist())
                ],
                index=df.index,
            )
        staged = amounts[valid].groupby(stage_idx[valid])
        aggregated = staged.mean() if tile.agg == "mean" else staged.sum()
        for pos, value in aggregated.items():
            if pd.isna(value):
                continue
            groups[labels[int(pos)]] = float(value)

    items = list(groups.items())
    if tile.sort == "desc":
        items.sort(key=lambda x: -x[1])
    elif tile.sort == "asc":
        items.sort(key=lambda x: x[1])

    leftover_n = int((~valid).sum())
    if leftover_n:
        if tile.agg == "count":
            items.append(("Не распределено", float(leftover_n)))
        elif value_col:
            leftover_val = pd.to_numeric(df.loc[~valid, value_col], errors="coerce").sum()
            if pd.notna(leftover_val) and float(leftover_val):
                items.append(("Не распределено", float(leftover_val)))

    return {
        "groups": dict(items),
        "group_column": "current_stage",
        "value_column": value_col,
    }


def _period_data(df: pd.DataFrame, tile: Tile) -> dict:
    func = {
        "month": data_tools.group_by_month,
        "quarter": data_tools.group_by_quarter,
        "year": data_tools.group_by_year,
    }.get(tile.source.period or "month", data_tools.group_by_month)

    question = tile.source.value_column or tile.title
    data = func(df, question)
    if "error" in data:
        return data
    return {
        "groups": data["groups"],
        "group_column": data.get("date_column"),
        "value_column": data.get("value_column"),
    }


def _pivot_date_column(df: pd.DataFrame) -> str | None:
    from services.chat_question_pack import _start_date_column

    start = _start_date_column(df)
    if start:
        return start
    dates = data_tools.detect_date_columns(df).get("columns") or []
    return dates[0] if dates else None


def _pivot_cell(value) -> float:
    if pd.isna(value):
        return 0.0
    number = float(value)
    if abs(number - round(number)) < 1e-9:
        return float(int(round(number)))
    return number


def _period_label(period: pd.Period, freq: str) -> str:
    if freq == "Q":
        return f"{int(period.quarter)} кв {int(period.year)}"
    if freq == "M":
        return f"{int(period.month):02d}.{int(period.year)}"
    return str(int(period.year))


def _year_total_is_redundant(periods, freq: str) -> bool:
    """Итог года повторяет видимые колонки, если они умещаются в одно полугодие, квартал или месяц."""
    if not periods or freq == "Y":
        return True
    if freq == "M":
        months = [int(period.month) for period in periods]
        return max(months) <= 6 or min(months) >= 7
    if freq == "Q":
        quarters = [int(period.quarter) for period in periods]
        return max(quarters) <= 2 or min(quarters) >= 3
    if freq == "H":
        halves = {int(bucket[1]) for bucket in periods}
        return len(halves) <= 1
    return False


def _append_year_total(
    columns: list[str],
    matrix: list[list[float]],
    col_totals: list[float],
    start_len: int,
    year: int,
) -> None:
    columns.append(str(year))
    year_vals = [_pivot_cell(sum(row[start_len:])) for row in matrix]
    for i, value in enumerate(year_vals):
        matrix[i].append(value)
    col_totals.append(_pivot_cell(sum(year_vals)))


def _pivot_data(df: pd.DataFrame, tile: Tile) -> dict:
    group_col = _resolve_group_column(df, tile)
    date_col = _pivot_date_column(df)
    if not group_col:
        return {"error": f"Не нашёл колонку подразделения для «{tile.title}»"}
    if not date_col:
        return {"error": f"Не нашёл дату начала сделки для «{tile.title}»"}

    freq = {"month": "M", "year": "Y", "quarter": "Q"}.get(
        tile.source.period or "quarter", "Q"
    )
    work = df[[group_col, date_col]].copy()
    value_col = None
    if tile.agg != "count":
        value_col = _resolve_value_column(df, tile)
        if not value_col:
            return {"error": f"Не нашёл числовую метрику для «{tile.title}»"}
        work[value_col] = pd.to_numeric(df[value_col], errors="coerce")

    names = work[group_col].astype(str).str.strip()
    blank = names.str.lower().isin({"", "nan", "-", "none"})
    work = work.loc[~blank].copy()
    work[group_col] = _department_labels(names.loc[~blank])
    work[date_col] = pd.to_datetime(work[date_col], errors="coerce", dayfirst=True)
    work = work.dropna(subset=[date_col, group_col])
    if work.empty:
        return {"error": "Нет сделок с датой начала и подразделением"}

    if (tile.source.period or "quarter") == "half":
        return _pivot_half_data(work, tile, group_col, value_col, date_col)

    work["_period"] = work[date_col].dt.to_period(freq)
    if tile.agg == "count":
        grouped = work.groupby([group_col, "_period"], sort=False).size()
    elif tile.agg == "mean":
        grouped = work.groupby([group_col, "_period"], sort=False)[value_col].mean()
    else:
        grouped = work.groupby([group_col, "_period"], sort=False)[value_col].sum()
    pivot = grouped.unstack(fill_value=0)
    if pivot.empty:
        return {"error": "Нет данных для таблицы сделок"}

    all_periods = pd.period_range(pivot.columns.min(), pivot.columns.max(), freq=freq)
    pivot = pivot.reindex(columns=all_periods, fill_value=0)

    order: list[str] = []
    for name in work[group_col]:
        text = str(name).strip()
        if text and text not in order:
            order.append(text)
    pivot = pivot.reindex(index=[name for name in order if name in pivot.index])
    row_totals = pivot.sum(axis=1)
    if tile.sort == "desc":
        pivot = pivot.loc[row_totals.sort_values(ascending=False).index]
    elif tile.sort == "asc":
        pivot = pivot.loc[row_totals.sort_values(ascending=True).index]
    pivot = pivot.head(tile.top_n)

    columns: list[str] = []
    year_spans: list[dict] = []
    matrix: list[list[float]] = [[] for _ in range(len(pivot.index))]
    col_totals: list[float] = []

    years: list[int] = []
    for period in pivot.columns:
        year = int(period.year)
        if not years or years[-1] != year:
            years.append(year)

    for year in years:
        year_periods = [p for p in pivot.columns if int(p.year) == year]
        start_len = len(columns)
        for period in year_periods:
            columns.append(_period_label(period, freq))
            series = pivot[period]
            values = [_pivot_cell(v) for v in series.tolist()]
            for i, value in enumerate(values):
                matrix[i].append(value)
            col_totals.append(_pivot_cell(series.sum()))
        if not _year_total_is_redundant(year_periods, freq):
            _append_year_total(columns, matrix, col_totals, start_len, year)
        year_spans.append({"label": str(year), "count": len(columns) - start_len})

    rows = []
    groups: dict[str, float] = {}
    for i, name in enumerate(pivot.index.astype(str)):
        period_total = _pivot_cell(float(row_totals.loc[name]))
        rows.append({"label": name, "values": matrix[i], "total": period_total})
        groups[name] = period_total

    return {
        "groups": groups,
        "group_column": group_col,
        "value_column": value_col,
        "table": {
            "index_label": str(group_col),
            "columns": columns,
            "year_spans": year_spans,
            "rows": rows,
            "totals": col_totals,
        },
    }


def _pivot_half_data(
    work: pd.DataFrame,
    tile: Tile,
    group_col: str,
    value_col: str | None,
    date_col: str,
) -> dict:
    """Сводная по полугодиям: те же строки, что у кварталов, плюс итог года."""
    work = work.copy()
    work["_bucket"] = _deals_zk_bucket_key(work[date_col], "half")
    buckets = _sort_time_buckets(list(work["_bucket"].unique()), "half")
    if tile.agg == "count":
        grouped = work.groupby([group_col, "_bucket"], sort=False).size()
    elif tile.agg == "mean":
        grouped = work.groupby([group_col, "_bucket"], sort=False)[value_col].mean()
    else:
        grouped = work.groupby([group_col, "_bucket"], sort=False)[value_col].sum()
    pivot = grouped.unstack(fill_value=0).reindex(columns=buckets, fill_value=0)
    if pivot.empty:
        return {"error": "Нет данных для таблицы сделок"}

    order: list[str] = []
    for name in work[group_col]:
        text = str(name).strip()
        if text and text not in order:
            order.append(text)
    pivot = pivot.reindex(index=[name for name in order if name in pivot.index])
    row_totals = pivot.sum(axis=1)
    if tile.sort == "desc":
        pivot = pivot.loc[row_totals.sort_values(ascending=False).index]
    elif tile.sort == "asc":
        pivot = pivot.loc[row_totals.sort_values(ascending=True).index]
    pivot = pivot.head(tile.top_n)

    columns: list[str] = []
    year_spans: list[dict] = []
    matrix: list[list[float]] = [[] for _ in range(len(pivot.index))]
    col_totals: list[float] = []
    years: list[int] = []
    for year, _half in buckets:
        if not years or years[-1] != int(year):
            years.append(int(year))
    for year in years:
        year_buckets = [bucket for bucket in buckets if int(bucket[0]) == year]
        start_len = len(columns)
        for bucket in year_buckets:
            columns.append(_deals_zk_section_title(bucket, "half"))
            series = pivot[bucket]
            values = [_pivot_cell(v) for v in series.tolist()]
            for i, value in enumerate(values):
                matrix[i].append(value)
            col_totals.append(_pivot_cell(series.sum()))
        if not _year_total_is_redundant(year_buckets, "H"):
            _append_year_total(columns, matrix, col_totals, start_len, year)
        year_spans.append({"label": str(year), "count": len(columns) - start_len})

    rows = []
    groups: dict[str, float] = {}
    for i, name in enumerate(pivot.index.astype(str)):
        period_total = _pivot_cell(float(row_totals.loc[name]))
        rows.append({"label": name, "values": matrix[i], "total": period_total})
        groups[name] = period_total
    return {
        "groups": groups,
        "group_column": group_col,
        "value_column": value_col,
        "table": {
            "index_label": str(group_col),
            "columns": columns,
            "year_spans": year_spans,
            "rows": rows,
            "totals": col_totals,
        },
    }


_OUTCOME_HEADERS = ("Все сделки", "Проиграны", "Отменены", "Всего (П+О)", "%")
_OUTCOME_KINDS = ("count", "count", "count", "count", "percent")
_DEPT_SHORT = {
    "служба испытательного оборудования": "СИО",
    "служба технологического оборудования": "СТЕ",
    "служба оборудования обработки кабеля": "СООК",
    "служба микроэлектроники": "СМЭ",
    "отдел аплис": "ОАПЛиС",
    "сервисная служба": "Сервис",
    "сс": "Сервис",
    "служба тестового оборудования": "СТО",
    "отдел функционального контроля": "СТО",
    "отдел неразрушающего контроля": "СТО",
    "отдел внутрисхемного контроля": "СТО",
    "офк": "СТО",
    "онк": "СТО",
    "овк": "СТО",
}


def department_short(name: str) -> str:
    """Короткое имя подразделения. ОВК, ОНК и ОФК вместе — это СТО, не СТЕ."""
    text = str(name or "").strip()
    key = text.lower().replace("ё", "е")
    if key in _DEPT_SHORT:
        return _DEPT_SHORT[key]
    if len(text) <= 8 and " " not in text:
        return text
    prefix = ""
    rest = text
    for head, letter in (("Отдел ", "О"), ("Служба ", "С"), ("Департамент ", "Д")):
        if text.lower().replace("ё", "е").startswith(head.lower()):
            prefix = letter
            rest = text[len(head) :].strip()
            break
    if rest and " " not in rest and len(rest) <= 12:
        if prefix and not rest.upper().startswith(prefix):
            return prefix + rest
        return rest
    words = [word for word in rest.replace("-", " ").split() if word]
    letters = "".join(word[0].upper() for word in words if word[0].isalpha())
    if prefix and letters and not letters.startswith(prefix):
        letters = prefix + letters
    return letters or text


def _department_labels(names: pd.Series) -> pd.Series:
    """Подписи строк: одинаковый короткий код склеивает подразделения в одну группу."""
    return names.map(lambda value: department_short(str(value).strip()))


_DEPT_BREAKDOWN_ORDER = ("СИО", "ОАПЛиС", "СТО", "СМЭ", "СТЕ", "СООК", "Сервис")
_STO_SUBDIVISION_ORDER = ("ОВК", "ОНК", "ОФК")


def _dept_breakdown_group(short_label: str) -> str:
    return short_label


def _dept_sto_subdivision(raw_name: str) -> str | None:
    key = str(raw_name or "").strip().lower().replace("ё", "е")
    if "внутрисхем" in key or key == "овк":
        return "ОВК"
    if "неразрушающ" in key or key == "онк":
        return "ОНК"
    if "функциональн" in key or key == "офк":
        return "ОФК"
    return None


def _stage_dept_breakdown_table(
    part: pd.DataFrame,
    group_col: str,
    deal_n: str,
    deal_s: str,
) -> dict:
    """Таблица «Служба × кол-во × сумма» для сделок на одном этапе воронки."""
    names = part[group_col].astype(str).str.strip()
    blank = names.str.lower().isin({"", "nan", "-", "none"})
    work = part.loc[~blank].copy()
    if work.empty:
        return {
            "index_label": "Служба",
            "columns": ["Кол-во", "Сумма, р."],
            "rows": [
                {
                    "label": "Всего",
                    "values": [0, 0],
                    "kinds": ["count", "money"],
                }
            ],
        }

    work["_raw"] = names.loc[~blank].values
    work["_short"] = _department_labels(work["_raw"])
    work["_group"] = work["_short"].map(_dept_breakdown_group)
    work["_sto_sub"] = work["_raw"].map(_dept_sto_subdivision)

    def metrics(frame: pd.DataFrame) -> tuple[float, float]:
        if frame.empty:
            return 0.0, 0.0
        return _sum_numeric(frame, deal_n), _sum_numeric(frame, deal_s)

    rows: list[dict] = []
    for group in _DEPT_BREAKDOWN_ORDER:
        group_frame = work.loc[work["_group"] == group]
        if group_frame.empty:
            continue
        count, amount = metrics(group_frame)
        if not count and not amount:
            continue
        rows.append(
            {
                "label": group,
                "values": [_json_number(count), _json_number(amount)],
                "kinds": ["count", "money"],
                "row_role": "group" if group == "СТО" else None,
            }
        )
        if group != "СТО":
            continue
        for sub in _STO_SUBDIVISION_ORDER:
            sub_frame = group_frame.loc[group_frame["_sto_sub"] == sub]
            sub_count, sub_amount = metrics(sub_frame)
            if not sub_count and not sub_amount:
                continue
            rows.append(
                {
                    "label": sub,
                    "values": [_json_number(sub_count), _json_number(sub_amount)],
                    "kinds": ["count", "money"],
                    "indent": True,
                    "row_role": "sub",
                }
            )

    total_count, total_amount = metrics(work)
    rows.append(
        {
            "label": "Всего",
            "values": [_json_number(total_count), _json_number(total_amount)],
            "kinds": ["count", "money"],
        }
    )
    return {
        "index_label": "Служба",
        "columns": ["Кол-во", "Сумма, р."],
        "rows": rows,
    }


def _top_in_work_stages(
    labels: list[str],
    counts: list[float],
    sums: list[float],
    metric: str,
    limit: int = 3,
) -> list[tuple[str, int]]:
    """Топ этапов по количеству или сумме (индекс этапа в воронке)."""
    ranked: list[tuple[str, float, int]] = []
    for index, label in enumerate(labels):
        value = counts[index] if metric == "count" else sums[index]
        if value > 0:
            ranked.append((label, float(value), index))
    ranked.sort(key=lambda item: (-item[1], item[0].lower()))
    return [(label, index) for label, _, index in ranked[:limit]]


_DEPT_FILTER_KINDS = {
    "pivot",
    "outcome",
    "halfyear",
    "deals_dynamics",
    "deals_dynamics_departments",
    "deals_dynamics_outcome_share",
    "deals_conversion",
    "deals_money",
}


def department_catalog(df: pd.DataFrame) -> list[str]:
    """Все короткие имена подразделений в файле, без пустых."""
    from services.column_resolver import resolve_semantic_column

    group_col = resolve_semantic_column(df, "", "department", dtype="categorical")
    if not group_col or group_col not in df.columns:
        return []
    names = df[group_col].astype(str).str.strip()
    blank = names.str.lower().isin({"", "nan", "-", "none"})
    seen: list[str] = []
    for label in _department_labels(names.loc[~blank]):
        if label and label not in seen:
            seen.append(label)
    return seen


def _selected_department_labels(tile: Tile) -> set[str] | None:
    raw = tile.source.departments
    if not raw:
        return None
    labels = {department_short(str(item).strip()) for item in raw if str(item).strip()}
    return labels or None


def _limit_departments(df: pd.DataFrame, tile: Tile) -> pd.DataFrame:
    """None в настройках — все службы. Список — только отмеченные."""
    selected = _selected_department_labels(tile)
    if not selected:
        return df
    group_col = _resolve_group_column(df, tile)
    if not group_col or group_col not in df.columns:
        return df
    labels = _department_labels(df[group_col].astype(str).str.strip())
    return df.loc[labels.isin(selected)].copy()


def _excluded_department_labels(catalog: list[str], tile: Tile) -> list[str]:
    selected = _selected_department_labels(tile)
    if not selected or not catalog:
        return []
    return [label for label in catalog if label not in selected]


def _chart_title_with_departments(base: str, tile: Tile, catalog: list[str]) -> str:
    excluded = _excluded_department_labels(catalog, tile)
    if not excluded:
        return base
    return f"{base} (без {', '.join(excluded)})"


def _status_column(df: pd.DataFrame) -> str | None:
    for col in df.columns:
        name = str(col).lower().strip()
        if name == "статус" or name.startswith("статус"):
            return str(col)
    return None


def _outcome_percent(part: int, whole: int) -> int:
    if whole <= 0:
        return 0
    return int(round(100.0 * part / whole))


def _outcome_data(df: pd.DataFrame, tile: Tile) -> dict:
    """Подразделения × кварталы: все сделки, проигранные, отменённые и их доля."""
    group_col = _resolve_group_column(df, tile)
    date_col = _pivot_date_column(df)
    status_col = _status_column(df)
    if not group_col:
        return {"error": "Не нашёл колонку подразделения"}
    if not date_col:
        return {"error": "Не нашёл дату начала сделки"}
    if not status_col:
        return {"error": "Не нашёл колонку статуса"}

    work = df[[group_col, date_col, status_col]].copy()
    names = work[group_col].astype(str).str.strip()
    blank = names.str.lower().isin({"", "nan", "-", "none"})
    work = work.loc[~blank].copy()
    work[group_col] = _department_labels(names.loc[~blank])
    work[date_col] = pd.to_datetime(work[date_col], errors="coerce", dayfirst=True)
    work = work.dropna(subset=[date_col, group_col])
    if work.empty:
        return {"error": "Нет сделок с датой начала и подразделением"}

    status = work[status_col].astype(str).str.lower()
    lost = status.str.contains("проигран", na=False)
    cancelled = status.str.contains("отмен", na=False) & ~lost
    bucket_mode = _deals_zk_bucket_mode(tile) if tile.source.period else "quarter"
    work["_bucket"] = _deals_zk_bucket_key(work[date_col], bucket_mode)
    buckets = _sort_time_buckets(list(work["_bucket"].unique()), bucket_mode)

    order: list[str] = []
    for name in work[group_col]:
        text = str(name).strip()
        if text and text not in order:
            order.append(text)

    def _counts(frame: pd.DataFrame) -> list[int]:
        cells: list[int] = []
        for bucket in buckets:
            part = frame.loc[frame["_bucket"] == bucket]
            all_n = int(len(part))
            lost_n = int(lost.loc[part.index].sum()) if all_n else 0
            cancel_n = int(cancelled.loc[part.index].sum()) if all_n else 0
            both = lost_n + cancel_n
            cells.extend([all_n, lost_n, cancel_n, both, _outcome_percent(both, all_n)])
        return cells

    rows = [
        {"label": name, "values": _counts(work.loc[work[group_col] == name])}
        for name in order
    ]
    totals = _counts(work)
    columns: list[str] = []
    kinds: list[str] = []
    spans: list[dict] = []
    for bucket in buckets:
        columns.extend(_OUTCOME_HEADERS)
        kinds.extend(_OUTCOME_KINDS)
        if bucket_mode == "half":
            span_label = _deals_zk_section_title(bucket, "half")
        elif bucket_mode == "month":
            span_label = _period_label(bucket, "M")
        else:
            span_label = _period_label(bucket, "Q")
        spans.append({"label": span_label, "count": len(_OUTCOME_HEADERS)})

    step = len(_OUTCOME_HEADERS)
    groups = {
        row["label"]: float(sum(row["values"][i] for i in range(0, len(row["values"]), step)))
        for row in rows
    }

    return {
        "groups": groups,
        "group_column": group_col,
        "table": {
            "index_label": "Подразделение",
            "columns": columns,
            "column_kinds": kinds,
            "year_spans": spans,
            "rows": rows,
            "totals": totals,
            "totals_label": "Совтест",
        },
    }


def _named_column(df: pd.DataFrame, name: str) -> str | None:
    target = name.lower().replace("ё", "е").strip()
    for col in df.columns:
        if str(col).lower().replace("ё", "е").strip() == target:
            return str(col)
    return None


def _deal_number_column(df: pd.DataFrame) -> str | None:
    """Колонка «Номер сделки» (УП-00001234) в выгрузке воронки 1С."""
    exact = _named_column(df, "номер сделки")
    if exact:
        return exact
    for col in df.columns:
        text = str(col).lower().replace("ё", "е")
        if "номер" in text and "сдел" in text:
            return str(col)
    return None


def _deal_start_date_column(df: pd.DataFrame) -> str | None:
    exact = _named_column(df, "дата начала сделки")
    if exact:
        return exact
    for col in df.columns:
        text = str(col).lower().replace("ё", "е")
        if "дат" in text and "начал" in text and "сдел" in text:
            return str(col)
    return None


def _format_deal_start_date(value) -> str:
    if value is None or (isinstance(value, float) and value != value):
        return "—"
    try:
        if pd.isna(value):
            return "—"
    except (TypeError, ValueError):
        pass
    parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
    if parsed is None or pd.isna(parsed):
        return "—"
    return parsed.strftime("%d.%m.%Y")


def _half_percent(part: float, whole: float, digits: int) -> float | None:
    if whole <= 0:
        return None
    scale = 10 ** digits
    return int(100 * part / whole * scale + 0.5) / scale


def _halfyear_metrics(frame: pd.DataFrame, deal_n, deal_s, zk_n, zk_s) -> dict:
    deals_count = float(pd.to_numeric(frame[deal_n], errors="coerce").sum())
    deals_sum = float(pd.to_numeric(frame[deal_s], errors="coerce").sum())
    zk_count = float(pd.to_numeric(frame[zk_n], errors="coerce").sum())
    zk_sum = float(pd.to_numeric(frame[zk_s], errors="coerce").sum())
    return {
        "title": "",
        "columns": ["Кол-во", "Сумма", "Конверсия", "Доля продаж в потенциале сделок"],
        "rows": [
            {
                "label": "Сделки",
                "values": [deals_count, deals_sum, None, None],
                "kinds": ["count", "money", "empty", "empty"],
            },
            {
                "label": "ЗК",
                "values": [
                    zk_count,
                    zk_sum,
                    _half_percent(zk_count, deals_count, 0),
                    _half_percent(zk_sum, deals_sum, 1),
                ],
                "kinds": ["count", "money", "percent", "percent1"],
            },
        ],
    }


_STATUS_SUMMARY_ORDER = ("В работе", "Выиграна", "Проиграна", "Отменена")
_STATUS_SUMMARY_COLUMNS = ("Кол-во", "Сумма", "ЗК", "Сумма")
_STATUS_SUMMARY_KINDS = ("count", "money", "count", "money")


def _normalize_deal_status(value) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    if "проигран" in text:
        return "Проиграна"
    if "отмен" in text:
        return "Отменена"
    if "выигран" in text:
        return "Выиграна"
    if "работ" in text:
        return "В работе"
    for label in _STATUS_SUMMARY_ORDER:
        if text == label.lower().replace("ё", "е"):
            return label
    return "В работе"


def _sum_numeric(frame: pd.DataFrame, col: str) -> float:
    return float(pd.to_numeric(frame[col], errors="coerce").fillna(0).sum())


def _json_number(value: float | int | None) -> int | float | None:
    if value is None:
        return None
    number = float(value)
    if number != number:  # NaN
        return None
    if abs(number - round(number)) < 1e-9:
        return int(round(number))
    return number


def _zk_display(value: float) -> float | None:
    return None if value == 0 else value


def _status_summary_data(df: pd.DataFrame, tile: Tile) -> dict:
    """Сделки и ЗК по статусам: количество и суммы в разрезе статуса."""
    status_col = _status_column(df)
    deal_n = _named_column(df, "количество сделок")
    deal_s = _named_column(df, "сумма по сделке")
    zk_n = _named_column(df, "количество зк")
    zk_s = _named_column(df, "сумма зк")
    if not status_col:
        return {"error": "Не нашёл колонку статуса"}
    if not all([deal_n, deal_s, zk_n, zk_s]):
        return {"error": "Нет колонок количества и суммы сделок или ЗК"}

    work = df.copy()
    work["_status"] = work[status_col].map(_normalize_deal_status)
    rows = []
    totals = [0.0, 0.0, 0.0, 0.0]
    for label in _STATUS_SUMMARY_ORDER:
        part = work.loc[work["_status"] == label]
        if part.empty:
            continue
        deals_count = _sum_numeric(part, deal_n)
        deals_sum = _sum_numeric(part, deal_s)
        zk_count = _sum_numeric(part, zk_n)
        zk_sum = _sum_numeric(part, zk_s)
        rows.append(
            {
                "label": label,
                "values": [
                    _json_number(deals_count),
                    _json_number(deals_sum),
                    _json_number(_zk_display(zk_count)),
                    _json_number(_zk_display(zk_sum)),
                ],
            }
        )
        totals[0] += deals_count
        totals[1] += deals_sum
        totals[2] += zk_count
        totals[3] += zk_sum

    if not rows:
        return {"error": "Нет сделок со статусом"}
    return {
        "groups": {row["label"]: float(row["values"][0] or 0) for row in rows},
        "table": {
            "index_label": "Статус",
            "columns": list(_STATUS_SUMMARY_COLUMNS),
            "column_kinds": list(_STATUS_SUMMARY_KINDS),
            "rows": rows,
            "totals": [_json_number(v) for v in totals],
            "totals_label": "Всего",
        },
    }


def _deal_statuses_data(df: pd.DataFrame, tile: Tile) -> dict:
    """Статусы сделок за каждый период: суммы и отдельно только количества."""
    status_col = _status_column(df)
    date_col = _pivot_date_column(df)
    deal_n = _named_column(df, "количество сделок")
    deal_s = _named_column(df, "сумма по сделке")
    zk_n = _named_column(df, "количество зк")
    zk_s = _named_column(df, "сумма зк")
    if not status_col:
        return {"error": "Не нашёл колонку статуса"}
    if not date_col:
        return {"error": "Не нашёл дату начала сделки"}
    if not all([deal_n, deal_s, zk_n, zk_s]):
        return {"error": "Нет колонок количества и суммы сделок или ЗК"}

    bucket_mode = _deals_zk_bucket_mode(tile)
    work = df.copy()
    work[date_col] = pd.to_datetime(work[date_col], errors="coerce", dayfirst=True)
    work = work.dropna(subset=[date_col])
    if work.empty:
        return {"error": "Нет сделок с датой начала"}
    work["_status"] = work[status_col].map(_normalize_deal_status)
    work["_bucket"] = _deals_zk_bucket_key(work[date_col], bucket_mode)
    buckets = _sort_time_buckets(list(work["_bucket"].unique()), bucket_mode)

    def _metrics(part: pd.DataFrame) -> tuple[float, float, float, float]:
        if part.empty:
            return 0.0, 0.0, 0.0, 0.0
        return (
            _sum_numeric(part, deal_n),
            _sum_numeric(part, deal_s),
            _sum_numeric(part, zk_n),
            _sum_numeric(part, zk_s),
        )

    sections = []
    for bucket in buckets:
        period = work.loc[work["_bucket"] == bucket]
        full_rows = []
        count_rows = []
        totals = [0.0, 0.0, 0.0, 0.0]
        for label in _STATUS_SUMMARY_ORDER:
            deals_count, deals_sum, zk_count, zk_sum = _metrics(
                period.loc[period["_status"] == label]
            )
            full_rows.append(
                {
                    "label": label,
                    "values": [
                        _json_number(deals_count),
                        _json_number(deals_sum),
                        _json_number(zk_count),
                        _json_number(zk_sum),
                    ],
                    "kinds": ["count", "money", "count", "money"],
                }
            )
            count_rows.append(
                {
                    "label": label,
                    "values": [_json_number(deals_count), _json_number(zk_count)],
                    "kinds": ["count", "count"],
                }
            )
            totals[0] += deals_count
            totals[1] += deals_sum
            totals[2] += zk_count
            totals[3] += zk_sum
        full_rows.append(
            {
                "label": "Всего",
                "values": [_json_number(v) for v in totals],
                "kinds": ["count", "money", "count", "money"],
            }
        )
        count_rows.append(
            {
                "label": "Всего",
                "values": [_json_number(totals[0]), _json_number(totals[2])],
                "kinds": ["count", "count"],
            }
        )
        period_title = _deals_zk_section_title(bucket, bucket_mode)
        counts_only = tile.source.variant == "counts"
        sections.append(
            {
                "title": period_title,
                "tables": [
                    {
                        "title": "",
                        "index_label": "Статус",
                        "columns": ["Сделки", "Заказы"] if counts_only else ["Сделки", "Сумма", "Заказы", "Сумма"],
                        "rows": count_rows if counts_only else full_rows,
                    }
                ],
            }
        )
    if not sections:
        return {"error": "Нет сделок в выбранных периодах"}
    return {"sections": sections, "bucket_period": bucket_mode}


def _stage_amount_columns(df: pd.DataFrame) -> list[str]:
    columns: list[str] = []
    for col in df.columns:
        name = str(col).strip()
        low = name.lower().replace("ё", "е")
        if not low.endswith("(сумма)"):
            continue
        if "зк" in low:
            continue
        columns.append(name)
    return columns


def _stage_quantity_column(df: pd.DataFrame, sum_col: str) -> str | None:
    name = str(sum_col).strip()
    low = name.lower().replace("ё", "е")
    if not low.endswith("(сумма)"):
        return None
    base = name[: -len("(сумма)")].strip()
    target = f"{base} (количество)".lower().replace("ё", "е")
    for col in df.columns:
        if str(col).strip().lower().replace("ё", "е") == target:
            return str(col)
    return None


def _funnel_stage_markers(df: pd.DataFrame, sum_cols: list[str]) -> pd.DataFrame:
    """Матрица этапов: 1, если в выгрузке 1С заполнена сумма или количество этапа."""
    series: dict[str, pd.Series] = {}
    for col in sum_cols:
        sums = pd.to_numeric(df[col], errors="coerce").fillna(0)
        qty_col = _stage_quantity_column(df, col)
        if qty_col:
            qty = pd.to_numeric(df[qty_col], errors="coerce").fillna(0)
            series[col] = ((sums != 0) | (qty != 0)).astype(float)
        else:
            series[col] = (sums != 0).astype(float)
    return pd.DataFrame(series, index=df.index)


def _funnel_current_stage_index(df: pd.DataFrame, sum_cols: list[str]) -> pd.Series:
    if not sum_cols:
        return pd.Series(-1, index=df.index, dtype=int)
    return _last_filled_stage_index(_funnel_stage_markers(df, sum_cols))


_STAGE_KP_RE = re.compile(r"(?i)(?<![а-яёa-z])кп(?![а-яёa-z])")


def _pretty_stage_label(label: str) -> str:
    text = str(label or "").strip()
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    return _STAGE_KP_RE.sub("КП", text)


def _in_work_stages_data(df: pd.DataFrame, tile: Tile) -> dict:
    """Сделки «В работе» по текущему этапу: количество, сумма и доли."""
    status_col = _status_column(df)
    date_col = _pivot_date_column(df)
    deal_n = _named_column(df, "количество сделок")
    deal_s = _named_column(df, "сумма по сделке")
    stage_cols = _stage_amount_columns(df)
    if not status_col:
        return {"error": "Не нашёл колонку статуса"}
    if not date_col:
        return {"error": "Не нашёл дату начала сделки"}
    if not deal_n or not deal_s:
        return {"error": "Нет колонок количества и суммы сделок"}
    if not stage_cols:
        return {"error": "Нет колонок этапов"}
    from services.column_resolver import resolve_semantic_column

    group_col = resolve_semantic_column(df, "", "department", dtype="categorical")

    bucket_mode = _deals_zk_bucket_mode(tile)
    work = df.copy()
    work[date_col] = pd.to_datetime(work[date_col], errors="coerce", dayfirst=True)
    work = work.dropna(subset=[date_col])
    if work.empty:
        return {"error": "Нет сделок с датой начала"}
    work["_status"] = work[status_col].map(_normalize_deal_status)
    work["_bucket"] = _deals_zk_bucket_key(work[date_col], bucket_mode)
    work["_stage"] = _funnel_current_stage_index(work, stage_cols)
    labels = [_pretty_stage_label(label) for label in _stage_labels(stage_cols, "(сумма)")]
    buckets = _sort_time_buckets(list(work["_bucket"].unique()), bucket_mode)

    sections = []
    for bucket in buckets:
        period = work.loc[(work["_bucket"] == bucket) & (work["_status"] == "В работе")]
        counts: list[float] = []
        sums: list[float] = []
        for index in range(len(labels)):
            part = period.loc[period["_stage"] == index]
            counts.append(_sum_numeric(part, deal_n) if not part.empty else 0.0)
            sums.append(_sum_numeric(part, deal_s) if not part.empty else 0.0)
        missing = period.loc[period["_stage"] < 0]
        missing_count = _sum_numeric(missing, deal_n) if not missing.empty else 0.0
        missing_sum = _sum_numeric(missing, deal_s) if not missing.empty else 0.0
        row_labels = list(labels)
        if missing_count or missing_sum:
            row_labels.append("Не указан")
            counts.append(missing_count)
            sums.append(missing_sum)
        total_count = float(sum(counts))
        total_sum = float(sum(sums))
        rows = []
        for label, count, amount in zip(row_labels, counts, sums):
            rows.append(
                {
                    "label": label,
                    "values": [
                        _json_number(count),
                        _json_number(amount),
                        _half_percent(count, total_count, 0) or 0,
                        _half_percent(amount, total_sum, 1) or 0,
                    ],
                    "kinds": ["count", "money", "percent", "percent1"],
                }
            )
        rows.append(
            {
                "label": "Всего",
                "values": [
                    _json_number(total_count),
                    _json_number(total_sum),
                    100 if total_count else 0,
                    100 if total_sum else 0,
                ],
                "kinds": ["count", "money", "percent", "percent"],
            }
        )
        period_title = _deals_zk_section_title(bucket, bucket_mode)
        stage_breakdowns: dict[str, list[dict]] = {"by_count": [], "by_sum": []}
        if group_col and group_col in period.columns:
            for stage_label, stage_index in _top_in_work_stages(
                labels, counts, sums, "count"
            ):
                stage_part = period.loc[period["_stage"] == stage_index]
                stage_breakdowns["by_count"].append(
                    {
                        "stage": stage_label,
                        "table": _stage_dept_breakdown_table(
                            stage_part, group_col, deal_n, deal_s
                        ),
                    }
                )
            for stage_label, stage_index in _top_in_work_stages(
                labels, counts, sums, "sum"
            ):
                stage_part = period.loc[period["_stage"] == stage_index]
                stage_breakdowns["by_sum"].append(
                    {
                        "stage": stage_label,
                        "table": _stage_dept_breakdown_table(
                            stage_part, group_col, deal_n, deal_s
                        ),
                    }
                )
        sections.append(
            {
                "title": f"{tile.title} ({period_title})",
                "tables": [
                    {
                        "title": "",
                        "index_label": "Этап",
                        "columns": ["Кол-во", "Сумма, р.", "Доля в кол-ве", "Доля в сумме"],
                        "rows": rows,
                    }
                ],
                "stage_breakdowns": stage_breakdowns,
            }
        )
    if not sections:
        return {"error": "Нет сделок в выбранных периодах"}
    return {"sections": sections, "bucket_period": bucket_mode}


def stage_catalog(df: pd.DataFrame) -> list[str]:
    cols = _stage_amount_columns(df)
    return [_pretty_stage_label(label) for label in _stage_labels(cols, "(сумма)")]


def default_top_stage_label(df: pd.DataFrame, stage_cols: list[str], deal_s: str) -> str:
    labels = [_pretty_stage_label(label) for label in _stage_labels(stage_cols, "(сумма)")]
    if not labels:
        return ""
    stage_idx = _funnel_current_stage_index(df, stage_cols)
    potential = pd.to_numeric(df[deal_s], errors="coerce").fillna(0)
    totals: dict[str, float] = {label: 0.0 for label in labels}
    for index, label in enumerate(labels):
        mask = stage_idx == index
        totals[label] = float(potential.loc[mask].sum())
    return max(totals.items(), key=lambda item: item[1])[0]


def status_catalog(df: pd.DataFrame) -> list[str]:
    status_col = _status_column(df)
    if not status_col or status_col not in df.columns:
        return list(_STATUS_SUMMARY_ORDER)
    normalized = df[status_col].map(_normalize_deal_status)
    seen: list[str] = []
    for label in _STATUS_SUMMARY_ORDER:
        if (normalized == label).any():
            seen.append(label)
    for label in sorted({str(item) for item in normalized if str(item).strip()}, key=str.casefold):
        if label not in seen:
            seen.append(label)
    return seen


def manager_catalog(df: pd.DataFrame, department_labels: set[str] | None = None) -> list[str]:
    from services.column_resolver import resolve_semantic_column

    mgr_col = resolve_semantic_column(df, "", "manager", dtype="categorical")
    dept_col = resolve_semantic_column(df, "", "department", dtype="categorical")
    if not mgr_col or mgr_col not in df.columns:
        return []
    work = df
    if department_labels and dept_col and dept_col in df.columns:
        names = _department_labels(work[dept_col].astype(str).str.strip())
        work = work.loc[names.isin(department_labels)]
    managers = work[mgr_col].astype(str).str.strip()
    blank = managers.str.lower().isin({"", "nan", "-", "none"})
    seen: list[str] = []
    for name in managers.loc[~blank]:
        if name not in seen:
            seen.append(name)
    return sorted(seen, key=lambda item: item.casefold())


def _selected_manager_labels(tile: Tile) -> set[str] | None:
    raw = tile.source.managers
    if not raw:
        return None
    labels = {str(item).strip() for item in raw if str(item).strip()}
    return labels or None


def _selected_status_labels(tile: Tile) -> set[str] | None:
    raw = tile.source.statuses
    if not raw:
        return None
    labels = {str(item).strip() for item in raw if str(item).strip()}
    return labels or None


_DEAL_LIST_COLUMNS = (
    "УП",
    "Сделка",
    "Дата начала сделки",
    "Статус",
    "Этап",
    "Подразделение",
    "Ответственный",
    "Потенциал",
)
_DEAL_LIST_SORT_FIELDS = {
    "УП": "_deal_no",
    "Сделка": "_client",
    "Дата начала сделки": "_deal_start",
    "Статус": "_status",
    "Этап": "_stage",
    "Подразделение": "_dept",
    "Ответственный": "_manager",
    "Потенциал": "_potential",
}


def _sort_stages_deal_list(work: pd.DataFrame, tile: Tile) -> pd.DataFrame:
    sort_col = tile.source.list_sort_column or "Потенциал"
    if sort_col not in _DEAL_LIST_SORT_FIELDS:
        sort_col = "Потенциал"
    field = _DEAL_LIST_SORT_FIELDS[sort_col]
    ascending = tile.sort == "asc"
    if tile.sort == "none" and not tile.source.list_sort_column:
        field = "_potential"
        ascending = False
    return work.sort_values(field, ascending=ascending, kind="mergesort")


def _resolved_stage_filters(tile: Tile, catalog: list[str], df: pd.DataFrame, deal_s: str) -> list[str]:
    stage_cols = _stage_amount_columns(df)
    raw = tile.source.stages
    if raw:
        picked = [str(item).strip() for item in raw if str(item).strip()]
        return [label for label in picked if label in catalog]
    if stage_cols and deal_s:
        top = default_top_stage_label(df, stage_cols, deal_s)
        return [top] if top else []
    return []


def _stages_deal_list_data(df: pd.DataFrame, tile: Tile) -> dict:
    """Строки сделок по текущему этапу воронки и фильтрам спеки."""
    from services.column_resolver import resolve_semantic_column

    status_col = _status_column(df)
    deal_s = _named_column(df, "сумма по сделке")
    stage_cols = _stage_amount_columns(df)
    if not deal_s:
        return {"error": "Нет колонки суммы по сделке"}
    if not stage_cols:
        return {"error": "Нет колонок этапов"}

    catalog = stage_catalog(df)
    resolved_stages = _resolved_stage_filters(tile, catalog, df, deal_s)
    if not resolved_stages:
        return {"error": "Не удалось определить этапы для фильтра"}

    dept_col = resolve_semantic_column(df, "", "department", dtype="categorical")
    mgr_col = resolve_semantic_column(df, "", "manager", dtype="categorical")
    client_col = resolve_semantic_column(df, "", "client", dtype="categorical")
    deal_no_col = _deal_number_column(df)
    deal_start_col = _deal_start_date_column(df)

    work = df.copy()
    work["_stage_idx"] = _funnel_current_stage_index(work, stage_cols)
    labels = [_pretty_stage_label(label) for label in _stage_labels(stage_cols, "(сумма)")]
    work["_stage"] = work["_stage_idx"].map(
        lambda idx: labels[int(idx)] if 0 <= int(idx) < len(labels) else "Не указан"
    )
    work["_potential"] = pd.to_numeric(work[deal_s], errors="coerce").fillna(0)
    if status_col:
        work["_status"] = work[status_col].map(_normalize_deal_status)
    else:
        work["_status"] = ""

    if dept_col and dept_col in work.columns:
        work["_dept"] = _department_labels(work[dept_col].astype(str).str.strip())
    else:
        work["_dept"] = ""

    if mgr_col and mgr_col in work.columns:
        work["_manager"] = work[mgr_col].astype(str).str.strip()
    else:
        work["_manager"] = ""

    if client_col and client_col in work.columns:
        work["_client"] = work[client_col].astype(str).str.strip()
    else:
        work["_client"] = ""

    if deal_no_col and deal_no_col in work.columns:
        work["_deal_no"] = work[deal_no_col].astype(str).str.strip()
    else:
        work["_deal_no"] = ""

    if deal_start_col and deal_start_col in work.columns:
        work["_deal_start"] = pd.to_datetime(work[deal_start_col], errors="coerce", dayfirst=True)
    else:
        work["_deal_start"] = pd.NaT

    work = work.loc[work["_stage"].isin(resolved_stages)]

    selected_depts = _selected_department_labels(tile)
    if selected_depts:
        work = work.loc[work["_dept"].isin(selected_depts)]

    selected_mgrs = _selected_manager_labels(tile)
    if selected_mgrs:
        work = work.loc[work["_manager"].isin(selected_mgrs)]

    selected_statuses = _selected_status_labels(tile)
    if selected_statuses:
        work = work.loc[work["_status"].isin(selected_statuses)]

    min_pot = tile.source.min_potential
    if min_pot is not None and float(min_pot) > 0:
        work = work.loc[work["_potential"] >= float(min_pot)]

    work = _sort_stages_deal_list(work, tile)
    total_matched = int(len(work))
    limit = max(1, min(500, int(tile.top_n or 200)))
    truncated = total_matched > limit
    work = work.head(limit)

    columns = list(_DEAL_LIST_COLUMNS)
    rows = []
    for _, row in work.iterrows():
        deal_no = str(row["_deal_no"] or "").strip()
        if deal_no.lower() in {"", "nan", "-", "none"}:
            deal_no = "—"
        client = str(row["_client"] or "").strip()
        if client.lower() in {"", "nan", "-", "none"}:
            client = "—"
        rows.append(
            {
                "cells": [
                    deal_no,
                    client,
                    _format_deal_start_date(row["_deal_start"]),
                    str(row["_status"] or "—"),
                    str(row["_stage"] or "—"),
                    str(row["_dept"] or "—"),
                    str(row["_manager"] or "—"),
                    _json_number(row["_potential"]),
                ]
            }
        )

    dept_catalog = department_catalog(df)
    mgr_catalog = manager_catalog(df, selected_depts)

    top_default = default_top_stage_label(df, stage_cols, deal_s) if stage_cols else ""

    return {
        "groups": {},
        "deals_list": {
            "columns": columns,
            "rows": rows,
            "truncated": truncated,
            "total_matched": total_matched,
        },
        "filter_meta": {
            "stages": catalog,
            "departments": dept_catalog,
            "managers": mgr_catalog,
            "statuses": status_catalog(df),
            "default_stages": [top_default] if top_default else [],
            "resolved_stages": resolved_stages,
            "sort_column": tile.source.list_sort_column or "Потенциал",
            "sort_dir": "asc" if tile.sort == "asc" else "desc",
        },
    }


def _deals_zk_bucket_mode(tile: Tile) -> str:
    mode = tile.source.period or "quarter"
    if mode in ("month", "quarter", "half"):
        return mode
    return "quarter"


def _deals_zk_bucket_key(date_col: pd.Series, mode: str) -> pd.Series:
    if mode == "month":
        return date_col.dt.to_period("M")
    if mode == "quarter":
        return date_col.dt.to_period("Q")
    return date_col.map(lambda ts: (int(ts.year), 1 if int(ts.month) <= 6 else 2))


def _deals_zk_section_title(bucket, mode: str) -> str:
    if mode == "half":
        year, half = bucket
        return f"{half} полуг. {year}"
    if mode == "month":
        from services.data_tools import _period_axis_label

        return _period_axis_label(bucket, "month")
    from services.data_tools import _period_axis_label

    return _period_axis_label(bucket, "quarter")


def _deals_zk_group_suffix(bucket, mode: str) -> str:
    if mode == "half":
        year, half = bucket
        return f"{year}-{half}"
    return str(bucket)


def _halfyear_data(df: pd.DataFrame, tile: Tile) -> dict:
    """Сделки и ЗК по периодам (полугодие / квартал / месяц): компания и каждое подразделение."""
    group_col = _resolve_group_column(df, tile)
    date_col = _pivot_date_column(df)
    deal_n = _named_column(df, "количество сделок")
    deal_s = _named_column(df, "сумма по сделке")
    zk_n = _named_column(df, "количество зк")
    zk_s = _named_column(df, "сумма зк")
    bucket_mode = _deals_zk_bucket_mode(tile)
    if not date_col:
        return {"error": "Не нашёл дату начала сделки"}
    if not all([deal_n, deal_s, zk_n, zk_s]):
        return {"error": "Нет колонок количества и суммы сделок или ЗК"}

    work = df.copy()
    work[date_col] = pd.to_datetime(work[date_col], errors="coerce", dayfirst=True)
    work = work.dropna(subset=[date_col])
    if work.empty:
        return {"error": "Нет сделок с датой начала"}
    work["_bucket"] = _deals_zk_bucket_key(work[date_col], bucket_mode)

    sections = []
    groups: dict[str, float] = {}
    for bucket in sorted(work["_bucket"].unique(), key=str):
        part = work.loc[work["_bucket"] == bucket]
        company = _halfyear_metrics(part, deal_n, deal_s, zk_n, zk_s)
        company["title"] = "Совтест"
        tables = [company]
        suffix = _deals_zk_group_suffix(bucket, bucket_mode)
        if group_col and group_col in part.columns:
            names = _department_labels(part[group_col].astype(str).str.strip())
            blank = part[group_col].astype(str).str.strip().str.lower().isin({"", "nan", "-", "none"})
            ranked = (
                part.loc[~blank]
                .assign(_dept=names.loc[~blank])
                .groupby("_dept", sort=False)[deal_n]
                .apply(lambda s: float(pd.to_numeric(s, errors="coerce").sum()))
                .sort_values(ascending=False)
            )
            for dept in ranked.index:
                block = _halfyear_metrics(
                    part.loc[names == dept], deal_n, deal_s, zk_n, zk_s
                )
                block["title"] = str(dept)
                tables.append(block)
                groups[f"{suffix}:{dept}"] = float(block["rows"][0]["values"][0] or 0)
        sections.append({"title": _deals_zk_section_title(bucket, bucket_mode), "tables": tables})

    if not sections:
        return {"error": "Нет сделок в выбранных периодах"}
    return {
        "groups": groups
        or {"Совтест": float(sections[-1]["tables"][0]["rows"][0]["values"][0] or 0)},
        "sections": sections,
        "bucket_period": bucket_mode,
    }


_COMPANY_ROW_LABEL = "Совтест"


def _bucket_column_label(bucket, mode: str) -> str:
    if mode == "half":
        year, half = bucket
        return f"{half} пг {year}"
    if mode == "month":
        return _period_label(bucket, "M")
    return _period_label(bucket, "Q")


def _file_span_column_label(dates: pd.Series) -> str:
    """Подпись итога за весь файл: месяц, квартал, полугодие, год или диапазон лет."""
    start = pd.Timestamp(dates.min())
    end = pd.Timestamp(dates.max())
    if pd.isna(start) or pd.isna(end):
        return "Итого"
    same_year = int(start.year) == int(end.year)
    if same_year and int(start.month) == int(end.month):
        return f"{int(start.month):02d}.{int(start.year)}"
    start_q = (int(start.month) - 1) // 3
    end_q = (int(end.month) - 1) // 3
    if same_year and start_q == end_q:
        return f"{start_q + 1} кв {int(start.year)}"
    start_h = 1 if int(start.month) <= 6 else 2
    end_h = 1 if int(end.month) <= 6 else 2
    if same_year and start_h == end_h:
        return f"{start_h} пг {int(start.year)}"
    if same_year:
        return str(int(start.year))
    return f"{int(start.year)}–{int(end.year)}"


def _append_span_column(columns: list[str], rows: list[dict], label: str, totals: list[float]) -> list[str]:
    for row, total in zip(rows, totals):
        row["values"] = [*row["values"], total]
    return [*columns, label]


def _sort_time_buckets(buckets: list, mode: str) -> list:
    if mode == "half":
        return sorted(buckets, key=lambda item: (int(item[0]), int(item[1])))
    return sorted(buckets, key=str)


def _deal_count_in_frame(frame: pd.DataFrame, deal_n: str | None) -> float:
    if deal_n and deal_n in frame.columns:
        return float(pd.to_numeric(frame[deal_n], errors="coerce").fillna(0).sum())
    return float(len(frame))


def _deals_dynamics_prepare(df: pd.DataFrame, tile: Tile) -> dict:
    """Общая подготовка: сделки с датой, бакеты периодов и подписи колонок."""
    date_col = _pivot_date_column(df)
    deal_n = _named_column(df, "количество сделок")
    bucket_mode = _deals_zk_bucket_mode(tile)
    if not date_col:
        return {"error": "Не нашёл дату начала сделки"}

    work = df.copy()
    work[date_col] = pd.to_datetime(work[date_col], errors="coerce", dayfirst=True)
    work = work.dropna(subset=[date_col])
    if work.empty:
        return {"error": "Нет сделок с датой начала"}
    work["_bucket"] = _deals_zk_bucket_key(work[date_col], bucket_mode)

    buckets = _sort_time_buckets(list(work["_bucket"].unique()), bucket_mode)
    columns = [_bucket_column_label(bucket, bucket_mode) for bucket in buckets]
    return {
        "work": work,
        "buckets": buckets,
        "columns": columns,
        "deal_n": deal_n,
        "bucket_mode": bucket_mode,
        "date_col": date_col,
        "total_label": _file_span_column_label(work[date_col]),
    }


def _department_rows_for_dynamics(
    work: pd.DataFrame,
    group_col: str,
    buckets: list,
    deal_n: str | None,
    top_n: int,
    sort: str,
) -> list[dict]:
    names = _department_labels(work[group_col].astype(str).str.strip())
    blank = work[group_col].astype(str).str.strip().str.lower().isin({"", "nan", "-", "none"})
    ranked = (
        work.loc[~blank]
        .assign(_dept=names.loc[~blank])
        .groupby("_dept", sort=False)
        .apply(lambda part: _deal_count_in_frame(part, deal_n))
        .sort_values(ascending=False)
    )
    dept_rows: list[dict] = []
    for dept in ranked.index:
        short = str(dept)
        dept_mask = names == short
        values = []
        row_total = 0.0
        for bucket in buckets:
            part = work.loc[(work["_bucket"] == bucket) & dept_mask]
            value = _pivot_cell(_deal_count_in_frame(part, deal_n))
            values.append(value)
            row_total += value
        dept_rows.append(
            {
                "label": short,
                "values": values,
                "total": _pivot_cell(row_total),
            }
        )

    if sort == "desc":
        dept_rows.sort(key=lambda row: -float(row["total"]))
    elif sort == "asc":
        dept_rows.sort(key=lambda row: float(row["total"]))
    return dept_rows[:top_n]


def _deals_dynamics_data(df: pd.DataFrame, tile: Tile) -> dict:
    """Широкая таблица: число сделок по периодам (только строка «Совтест»)."""
    prep = _deals_dynamics_prepare(df, tile)
    if prep.get("error"):
        return prep
    work = prep["work"]
    buckets = prep["buckets"]
    columns = prep["columns"]
    deal_n = prep["deal_n"]
    bucket_mode = prep["bucket_mode"]

    company_values: list[float] = []
    for bucket in buckets:
        part = work.loc[work["_bucket"] == bucket]
        company_values.append(_pivot_cell(_deal_count_in_frame(part, deal_n)))

    total = _pivot_cell(sum(company_values))
    rows = [
        {
            "label": _COMPANY_ROW_LABEL,
            "values": list(company_values),
            "total": total,
        }
    ]
    table_columns = _append_span_column(columns, rows, prep["total_label"], [total])

    groups = {_COMPANY_ROW_LABEL: float(total)}
    return {
        "groups": groups,
        "bucket_period": bucket_mode,
        "chart_series": {
            "label": _COMPANY_ROW_LABEL,
            "categories": columns,
            "values": company_values,
        },
        "table": {
            "index_label": "",
            "columns": table_columns,
            "column_kinds": ["count"] * len(table_columns),
            "rows": rows,
        },
    }


def _deals_dynamics_departments_data(df: pd.DataFrame, tile: Tile) -> dict:
    """Таблица и серии для группового графика по подразделениям."""
    prep = _deals_dynamics_prepare(df, tile)
    if prep.get("error"):
        return prep
    work = prep["work"]
    buckets = prep["buckets"]
    columns = prep["columns"]
    deal_n = prep["deal_n"]
    bucket_mode = prep["bucket_mode"]

    group_col = _resolve_group_column(df, tile)
    if not group_col or group_col not in work.columns:
        return {"error": "Не нашёл колонку подразделения"}

    rows = _department_rows_for_dynamics(
        work, group_col, buckets, deal_n, tile.top_n, tile.sort
    )
    if not rows:
        return {"error": "Нет сделок с подразделением"}

    dept_labels = [row["label"] for row in rows]
    series = []
    for col_i, (bucket, col_label) in enumerate(zip(buckets, columns)):
        values = []
        for row in rows:
            values.append(float(row["values"][col_i]))
        series.append({"label": col_label, "values": values})

    table_columns = _append_span_column(
        columns, rows, prep["total_label"], [float(row["total"]) for row in rows]
    )
    groups = {row["label"]: float(row["total"]) for row in rows}
    return {
        "groups": groups,
        "bucket_period": bucket_mode,
        "chart_series": {
            "categories": dept_labels,
            "series": series,
        },
        "table": {
            "index_label": "Подразделение",
            "columns": table_columns,
            "column_kinds": ["count"] * len(table_columns),
            "rows": rows,
        },
    }


def _outcome_share_percent(
    frame: pd.DataFrame, deal_n: str | None, status_col: str
) -> float:
    if frame.empty:
        return 0.0
    total = _deal_count_in_frame(frame, deal_n)
    if total <= 0:
        return 0.0
    status = frame[status_col].astype(str).str.lower()
    lost = status.str.contains("проигран", na=False)
    cancelled = status.str.contains("отмен", na=False) & ~lost
    bad = _deal_count_in_frame(frame.loc[lost | cancelled], deal_n)
    return float(_outcome_percent(int(round(bad)), int(round(total))))


def _department_outcome_share_rows(
    work: pd.DataFrame,
    group_col: str,
    status_col: str,
    buckets: list,
    deal_n: str | None,
    top_n: int,
    sort: str,
) -> list[dict]:
    names = _department_labels(work[group_col].astype(str).str.strip())
    blank = work[group_col].astype(str).str.strip().str.lower().isin({"", "nan", "-", "none"})
    ranked = (
        work.loc[~blank]
        .assign(_dept=names.loc[~blank])
        .groupby("_dept", sort=False)
        .apply(lambda part: _deal_count_in_frame(part, deal_n))
        .sort_values(ascending=False)
    )
    dept_rows: list[dict] = []
    for dept in ranked.index:
        short = str(dept)
        dept_mask = names == short
        values = []
        volume = 0.0
        for bucket in buckets:
            part = work.loc[(work["_bucket"] == bucket) & dept_mask]
            values.append(_outcome_share_percent(part, deal_n, status_col))
            volume += _deal_count_in_frame(part, deal_n)
        dept_rows.append(
            {
                "label": short,
                "values": values,
                "total": volume,
                "span_total": _outcome_share_percent(
                    work.loc[dept_mask], deal_n, status_col
                ),
            }
        )

    if sort == "desc":
        dept_rows.sort(key=lambda row: -float(row["total"]))
    elif sort == "asc":
        dept_rows.sort(key=lambda row: float(row["total"]))
    trimmed = dept_rows[:top_n]
    for row in trimmed:
        row["total"] = _pivot_cell(
            sum(float(v) for v in row["values"]) / max(len(row["values"]), 1)
        )
    return trimmed


def _deals_dynamics_outcome_share_data(df: pd.DataFrame, tile: Tile) -> dict:
    """Доля (отмена + проигрыш) / все сделки, % — Совтест и подразделения."""
    prep = _deals_dynamics_prepare(df, tile)
    if prep.get("error"):
        return prep
    work = prep["work"]
    buckets = prep["buckets"]
    columns = prep["columns"]
    deal_n = prep["deal_n"]
    bucket_mode = prep["bucket_mode"]

    status_col = _status_column(df)
    group_col = _resolve_group_column(df, tile)
    if not status_col:
        return {"error": "Не нашёл колонку статуса"}
    if not group_col:
        return {"error": "Не нашёл колонку подразделения"}

    work = work.copy()
    work[status_col] = df.loc[work.index, status_col]
    work[group_col] = df.loc[work.index, group_col]

    company_values: list[float] = []
    for bucket in buckets:
        part = work.loc[work["_bucket"] == bucket]
        company_values.append(_outcome_share_percent(part, deal_n, status_col))

    company_span = _outcome_share_percent(work, deal_n, status_col)
    rows = [
        {
            "label": _COMPANY_ROW_LABEL,
            "values": company_values,
            "total": _pivot_cell(
                sum(company_values) / max(len(company_values), 1)
            ),
            "span_total": company_span,
        }
    ]
    rows.extend(
        _department_outcome_share_rows(
            work, group_col, status_col, buckets, deal_n, tile.top_n, tile.sort
        )
    )

    chart_rows = sorted(rows, key=lambda row: -float(row["span_total"]))
    categories = [row["label"] for row in chart_rows]
    series = []
    for col_i, col_label in enumerate(columns):
        values = [float(row["values"][col_i]) for row in chart_rows]
        series.append({"label": col_label, "values": values})

    table_columns = _append_span_column(
        columns,
        rows,
        prep["total_label"],
        [float(row.pop("span_total")) for row in rows],
    )
    groups = {row["label"]: float(row["total"]) for row in rows}
    return {
        "groups": groups,
        "bucket_period": bucket_mode,
        "chart_series": {
            "categories": categories,
            "series": series,
        },
        "table": {
            "index_label": "Подразделение",
            "columns": table_columns,
            "column_kinds": ["percent"] * len(table_columns),
            "rows": rows,
        },
    }


def _render_deals_dynamics_chart(series: dict, tile: Tile, catalog: list[str]) -> go.Figure:
    labels = list(series["categories"])
    values = [float(v) for v in series["values"]]
    n = len(labels)
    show_bar_text = n <= 14
    texts = (
        [str(int(v)) if abs(v - round(v)) < 1e-9 else f"{v:.1f}" for v in values]
        if show_bar_text
        else None
    )
    tick_angle = -90 if n > 5 else 0
    fig_width = max(400, min(56 * n, 1100))
    fig = go.Figure(
        go.Bar(
            x=labels,
            y=values,
            name="Сделки",
            marker_color="#4472C4",
            text=texts,
            textposition="outside" if show_bar_text else None,
            textangle=0,
            cliponaxis=False,
            width=0.62,
        )
    )
    subtitle = str(series.get("label") or _COMPANY_ROW_LABEL)
    fig.update_layout(
        title=chart_title_caps(
            _chart_title_with_departments(f"Динамика сделок. {subtitle}", tile, catalog)
        ),
        width=fig_width,
        height=520,
        autosize=False,
        margin=dict(l=16, r=16, t=72, b=96 if tick_angle else 56),
        showlegend=False,
        template="plotly_white",
        bargap=0.28,
    )
    fig.update_xaxes(
        type="category",
        tickangle=tick_angle,
        showgrid=False,
        automargin=True,
        tickfont=dict(size=10),
    )
    if show_bar_text and values:
        peak = max(float(v) for v in values)
        fig.update_yaxes(visible=False, showgrid=False, range=[0, peak * 1.18])
    else:
        fig.update_yaxes(visible=False, showgrid=False)
    return fig


_PERIOD_BAR_COLORS = (
    "#1F4E79",
    "#ED7D31",
    "#A5A5A5",
    "#FFC000",
    "#5B9BD5",
    "#70AD47",
    "#264478",
    "#9E480E",
    "#636363",
    "#997300",
)


def _render_deals_dynamics_departments_chart(
    series: dict, tile: Tile, catalog: list[str]
) -> go.Figure:
    depts = list(series["categories"])
    period_series = list(series["series"])
    n_depts = len(depts)
    n_periods = len(period_series)
    show_bar_text = n_depts * n_periods <= 60

    fig = go.Figure()
    for i, period in enumerate(period_series):
        values = [float(v) for v in period["values"]]
        texts = (
            [str(int(v)) if abs(v - round(v)) < 1e-9 else f"{v:.1f}" for v in values]
            if show_bar_text
            else None
        )
        fig.add_trace(
            go.Bar(
                name=period["label"],
                x=depts,
                y=values,
                marker_color=_PERIOD_BAR_COLORS[i % len(_PERIOD_BAR_COLORS)],
                text=texts,
                textposition="outside" if show_bar_text else None,
                textangle=0,
                cliponaxis=False,
            )
        )

    fig_width = max(560, min(72 * n_depts + 40 * n_periods, 1400))
    fig.update_layout(
        title=chart_title_caps(
            _chart_title_with_departments("Динамика сделок. Службы", tile, catalog)
        ),
        width=fig_width,
        height=560,
        autosize=False,
        barmode="group",
        bargap=0.15,
        bargroupgap=0.08,
        margin=dict(l=16, r=16, t=96, b=72),
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            x=0,
            xanchor="left",
            font=dict(size=10),
        ),
        template="plotly_white",
    )
    fig.update_xaxes(type="category", tickangle=-25 if n_depts > 4 else 0, automargin=True)
    if show_bar_text and period_series:
        peak = max(
            float(v)
            for period in period_series
            for v in period["values"]
        )
        fig.update_yaxes(visible=False, showgrid=False, range=[0, peak * 1.18])
    else:
        fig.update_yaxes(visible=False, showgrid=False)
    return fig


def _render_deals_outcome_share_chart(series: dict, tile: Tile, catalog: list[str]) -> go.Figure:
    depts = [str(label) for label in series["categories"]]
    period_series = list(series["series"])
    n_depts = len(depts)
    n_periods = len(period_series)
    show_bar_text = n_depts * n_periods <= 60

    fig = go.Figure()
    for i, period in enumerate(period_series):
        values = [float(v) for v in period["values"]]
        texts = (
            [f"{int(round(v))}%" for v in values] if show_bar_text else None
        )
        fig.add_trace(
            go.Bar(
                name=period["label"],
                x=depts,
                y=values,
                marker_color=_PERIOD_BAR_COLORS[i % len(_PERIOD_BAR_COLORS)],
                text=texts,
                textposition="outside" if show_bar_text else None,
                textangle=0,
                cliponaxis=False,
            )
        )

    fig_width = max(560, min(72 * n_depts + 40 * n_periods, 1400))
    fig.update_layout(
        title=chart_title_caps(
            _chart_title_with_departments("Доля отмены/проигрыша сделок", tile, catalog)
        ),
        width=fig_width,
        height=560,
        autosize=False,
        barmode="group",
        bargap=0.15,
        bargroupgap=0.08,
        margin=dict(l=16, r=16, t=96, b=72),
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            x=0,
            xanchor="left",
            font=dict(size=10),
        ),
        template="plotly_white",
    )
    fig.update_xaxes(type="category", tickangle=-25 if n_depts > 4 else 0, automargin=True)
    ymax = 100.0
    if show_bar_text and period_series:
        peak = max(float(v) for period in period_series for v in period["values"])
        ymax = max(100.0, peak * 1.18)
    fig.update_yaxes(visible=False, showgrid=False, range=[0, ymax])
    return fig


def _conversion_percent(zk: float, deals: float) -> int:
    if deals <= 0:
        return 0
    return int(round(100.0 * zk / deals))


def _sum_named(frame: pd.DataFrame, col: str | None) -> float:
    if not col or col not in frame.columns:
        return 0.0
    return float(pd.to_numeric(frame[col], errors="coerce").fillna(0).sum())


def _deals_conversion_data(df: pd.DataFrame, tile: Tile) -> dict:
    """Конверсия сделок в заказы: количество ЗК / количество сделок, %."""
    prep = _deals_dynamics_prepare(df, tile)
    if prep.get("error"):
        return prep
    zk_n = _named_column(df, "количество зк")
    if not zk_n:
        return {"error": "Не нашёл колонку количества ЗК"}
    group_col = _resolve_group_column(df, tile)
    work = prep["work"]
    if not group_col or group_col not in work.columns:
        return {"error": "Не нашёл колонку подразделения"}

    buckets = prep["buckets"]
    bucket_mode = prep["bucket_mode"]
    deal_n = prep["deal_n"]
    columns = [_deals_zk_section_title(bucket, bucket_mode) for bucket in buckets]

    def percents(mask: pd.Series) -> list[int]:
        values: list[int] = []
        for bucket in buckets:
            part = work.loc[mask & (work["_bucket"] == bucket)]
            deals = _deal_count_in_frame(part, deal_n)
            zk = _sum_named(part, zk_n)
            values.append(_conversion_percent(zk, deals))
        return values

    company_values = percents(pd.Series(True, index=work.index))
    rows = [
        {
            "label": _COMPANY_ROW_LABEL,
            "values": company_values,
            "total": company_values[-1] if company_values else 0,
        }
    ]

    names = _department_labels(work[group_col].astype(str).str.strip())
    blank = work[group_col].astype(str).str.strip().str.lower().isin({"", "nan", "-", "none"})
    ranked = (
        work.loc[~blank]
        .assign(_dept=names.loc[~blank])
        .groupby("_dept", sort=False)
        .apply(lambda part: _deal_count_in_frame(part, deal_n), include_groups=False)
        .sort_values(ascending=False)
    )
    for dept in ranked.index:
        short = str(dept)
        mask = names == short
        values = percents(mask)
        rows.append(
            {
                "label": short,
                "values": values,
                "total": values[-1] if values else 0,
            }
        )

    period_series = []
    for col_i, label in enumerate(columns):
        period_series.append(
            {
                "period_label": label,
                "points": [
                    {
                        "label": row["label"],
                        "value": float(row["values"][col_i]) if col_i < len(row["values"]) else 0.0,
                        "company": row["label"] == _COMPANY_ROW_LABEL,
                    }
                    for row in rows
                ],
            }
        )
    latest = period_series[-1] if period_series else {"period_label": "", "points": []}
    return {
        "groups": {row["label"]: float(row["total"]) for row in rows},
        "bucket_period": bucket_mode,
        "chart_series": latest,
        "period_series": period_series,
        "table": {
            "index_label": "Подразделение",
            "columns": columns,
            "column_kinds": ["percent"] * len(columns),
            "rows": rows,
        },
    }


_CONVERSION_ABOVE_COLOR = "#548235"
_CONVERSION_OTHER_COLOR = "#5B9BD5"


def _conversion_bar_color(label: str, value: float, company_value: float | None) -> str:
    if label == _COMPANY_ROW_LABEL:
        return COMPANY_CHART_COLOR
    if company_value is not None and value > company_value:
        return _CONVERSION_ABOVE_COLOR
    return _CONVERSION_OTHER_COLOR


def _render_deals_conversion_chart(series: dict, tile: Tile, catalog: list[str]) -> go.Figure:
    points = sorted(series.get("points") or [], key=lambda item: -float(item["value"]))
    labels = [str(item["label"]) for item in points]
    values = [float(item["value"]) for item in points]
    company_value = next(
        (float(item["value"]) for item in points if item.get("company")),
        None,
    )
    colors = [
        _conversion_bar_color(str(item["label"]), value, company_value)
        for item, value in zip(points, values)
    ]
    texts = [f"{int(round(value))}%" for value in values]
    n = max(len(labels), 1)
    fig = go.Figure(
        go.Bar(
            x=labels,
            y=values,
            marker_color=colors,
            text=texts,
            textposition="outside",
            cliponaxis=False,
            width=0.62,
        )
    )
    period = str(series.get("period_label") or "").strip()
    title = "Конверсия сделок в заказы"
    if period:
        title = f"{title} ({period})"
    fig.update_layout(
        title=chart_title_caps(_chart_title_with_departments(title, tile, catalog)),
        width=max(560, min(78 * n, 1280)),
        height=460,
        autosize=False,
        margin=dict(l=16, r=16, t=64, b=72),
        showlegend=False,
        template="plotly_white",
        bargap=0.28,
    )
    fig.update_xaxes(type="category", tickangle=-25 if n > 6 else 0, automargin=True)
    fig.update_yaxes(visible=False, showgrid=False, range=[0, 120])
    return fig


def _bucket_year_part(bucket, mode: str) -> tuple[int, int]:
    if mode == "half":
        year, half = bucket
        return int(year), int(half)
    if mode == "month":
        return int(bucket.year), int(bucket.month)
    return int(bucket.year), int(bucket.quarter)


def _money_values(
    work: pd.DataFrame,
    buckets: list,
    column: str,
    mask: pd.Series,
) -> list[float]:
    values: list[float] = []
    for bucket in buckets:
        part = work.loc[mask & (work["_bucket"] == bucket)]
        values.append(_pivot_cell(_sum_named(part, column)))
    return values


def _deals_money_data(df: pd.DataFrame, tile: Tile) -> dict:
    """Потенциал сделок и сумма заказов по периодам: компания и все подразделения."""
    prep = _deals_dynamics_prepare(df, tile)
    if prep.get("error"):
        return prep
    deal_s = _named_column(df, "сумма по сделке")
    zk_s = _named_column(df, "сумма зк")
    if not deal_s or not zk_s:
        return {"error": "Нет колонок суммы сделки или суммы ЗК"}
    group_col = _resolve_group_column(df, tile)
    work = prep["work"]
    if not group_col or group_col not in work.columns:
        return {"error": "Не нашёл колонку подразделения"}

    buckets = prep["buckets"]
    bucket_mode = prep["bucket_mode"]
    periods = [
        {
            "label": _deals_zk_section_title(bucket, bucket_mode),
            "year": year,
            "part": part,
        }
        for bucket in buckets
        for year, part in [_bucket_year_part(bucket, bucket_mode)]
    ]

    names = _department_labels(work[group_col].astype(str).str.strip())
    blank = work[group_col].astype(str).str.strip().str.lower().isin({"", "nan", "-", "none"})
    ranked = (
        work.loc[~blank]
        .assign(_dept=names.loc[~blank])
        .groupby("_dept", sort=False)
        .apply(lambda part: _sum_named(part, deal_s), include_groups=False)
        .sort_values(ascending=False)
    )

    def rows_for(column: str) -> list[dict]:
        company = _money_values(work, buckets, column, pd.Series(True, index=work.index))
        rows = [
            {
                "label": _COMPANY_ROW_LABEL,
                "company": True,
                "values": company,
            }
        ]
        for dept in ranked.index:
            short = str(dept)
            mask = names == short
            rows.append(
                {
                    "label": short,
                    "company": False,
                    "values": _money_values(work, buckets, column, mask),
                }
            )
        return rows

    groups = {
        row["label"]: float(row["values"][-1] if row["values"] else 0)
        for row in rows_for(deal_s)
    }
    return {
        "groups": groups,
        "bucket_period": bucket_mode,
        "money": {
            "periods": periods,
            "metrics": [
                {"title": "Потенциал сделок", "rows": rows_for(deal_s)},
                {"title": "Сумма заказов", "rows": rows_for(zk_s)},
            ],
        },
    }


def _tile_data(df: pd.DataFrame, tile: Tile) -> dict:
    if tile.source.kind in _DEPT_FILTER_KINDS:
        df = _limit_departments(df, tile)
    kind = tile.source.kind
    if kind == "columns_pattern":
        return _columns_pattern_data(df, tile)
    if kind == "named_columns":
        return _named_columns_data(df, tile)
    if kind == "current_stage":
        return _current_stage_data(df, tile)
    if kind == "period":
        return _period_data(df, tile)
    if kind == "outcome":
        return _outcome_data(df, tile)
    if kind == "halfyear":
        return _halfyear_data(df, tile)
    if kind == "status_summary":
        return _status_summary_data(df, tile)
    if kind == "deal_statuses":
        return _deal_statuses_data(df, tile)
    if kind == "in_work_stages":
        return _in_work_stages_data(df, tile)
    if kind == "stages_deal_list":
        return _stages_deal_list_data(df, tile)
    if kind == "deals_dynamics":
        return _deals_dynamics_data(df, tile)
    if kind == "deals_dynamics_departments":
        return _deals_dynamics_departments_data(df, tile)
    if kind == "deals_dynamics_outcome_share":
        return _deals_dynamics_outcome_share_data(df, tile)
    if kind == "deals_conversion":
        return _deals_conversion_data(df, tile)
    if kind == "deals_money":
        return _deals_money_data(df, tile)
    if kind == "deficit_sales_by_department":
        from services.deficit_sales_table import build_deficit_sales_table

        return build_deficit_sales_table(df, tile)
    if kind == "pivot" or tile.chart_type == "table":
        return _pivot_data(df, tile)
    return _group_data(df, tile)


# ---------------------------------------------------------------------------
# Рендер фигур
# ---------------------------------------------------------------------------

_LAYOUT = dict(
    margin=dict(l=8, r=16, t=8, b=8),
    height=360,
    showlegend=False,
    template="plotly_white",
)


def _render_figure(tile: Tile, data: dict) -> go.Figure:
    labels = list(data["groups"].keys())
    values = list(data["groups"].values())
    if tile.agg == "count":
        scale, suffix = 1.0, ""
    else:
        scale, suffix = _resolve_scale(tile.unit, values)
    scaled = [v / scale for v in values]
    texts = [_fmt_scaled(v, scale, suffix) for v in values]

    if tile.chart_type in ("bar", "hbar"):
        horizontal = tile.chart_type == "hbar"
        if horizontal:
            # plotly рисует hbar снизу вверх — переворачиваем, чтобы максимум был сверху
            labels, scaled, texts = labels[::-1], scaled[::-1], texts[::-1]
        fig = go.Figure(
            go.Bar(
                x=scaled if horizontal else labels,
                y=labels if horizontal else scaled,
                orientation="h" if horizontal else "v",
                text=texts,
                textposition="outside",
                textangle=0,
                cliponaxis=False,
            )
        )
    elif tile.chart_type == "pie":
        fig = go.Figure(go.Pie(labels=labels, values=values, hole=0.35))
        fig.update_layout(showlegend=True, legend=dict(orientation="h", y=-0.15))
    elif tile.chart_type == "area":
        fig = go.Figure(
            go.Scatter(x=labels, y=scaled, mode="lines", fill="tozeroy")
        )
    else:  # line
        fig = go.Figure(go.Scatter(x=labels, y=scaled, mode="lines+markers"))

    if tile.target_line is not None and tile.chart_type in ("bar", "hbar"):
        target_scaled = tile.target_line / scale
        if tile.chart_type == "hbar":
            fig.add_vline(x=target_scaled, line_dash="dash", line_color="tomato")
        else:
            fig.add_hline(y=target_scaled, line_dash="dash", line_color="tomato")

    axis_title = suffix or None
    fig.update_layout(**_LAYOUT)
    if tile.chart_type == "bar":
        fig.update_layout(margin=dict(l=8, r=16, t=48, b=8))
    if tile.chart_type in ("line", "area"):
        fig.update_xaxes(type="category")
    if tile.chart_type == "pie":
        fig.update_layout(
            showlegend=True,
            legend=dict(orientation="h", y=-0.22, font=dict(size=11)),
            margin=dict(l=8, r=16, t=8, b=88),
            height=420,
        )
        fig.update_traces(textinfo="percent", textposition="inside")
    if tile.chart_type == "hbar":
        fig.update_layout(height=max(360, 22 * len(labels) + 48))
        fig.update_xaxes(title_text=axis_title)
        fig.update_yaxes(automargin=True)
    elif tile.chart_type != "pie":
        fig.update_yaxes(title_text=axis_title)
        if tile.chart_type in ("line", "area"):
            fig.update_xaxes(type="category", automargin=True)

    return fig


def _tile_stats(tile: Tile, data: dict) -> dict:
    """Числа для авто-комментариев LLM (ничего не считает сама)."""
    groups = data["groups"]
    items = sorted(groups.items(), key=lambda x: -x[1])
    return {
        "total": sum(groups.values()),
        "top": items[:3],
        "count": len(groups),
    }


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def render_spec(df: pd.DataFrame, spec: DashboardSpec) -> dict:
    """Возвращает {"tabs": [{title, tiles: [{title, chart_type, plotly_json, stats} | {title, error}]}]}."""
    catalog = department_catalog(df)
    rendered_tabs = []
    for tab in spec.tabs:
        tiles = []
        for tile in tab.tiles:
            def emit(payload: dict, tile=tile) -> None:
                if tile.source.kind in _DEPT_FILTER_KINDS:
                    payload["departments"] = catalog
                tiles.append(payload)
            try:
                data = _tile_data(df, tile)
            except Exception as exc:
                logger.warning("Тайл «%s» упал: %s", tile.title, exc)
                emit({"title": tile.title, "error": str(exc)})
                continue

            if "error" in data:
                emit({"title": tile.title, "error": data["error"]})
                continue

            if data.get("sections"):
                payload = {
                    "title": tile.title,
                    "chart_type": "sections",
                    "sections": data["sections"],
                }
                if data.get("bucket_period"):
                    payload["bucket_period"] = data["bucket_period"]
                emit(payload)
                continue

            if tile.source.kind == "stages_deal_list" and data.get("deals_list"):
                emit(
                    {
                        "title": tile.title,
                        "chart_type": "stages_deal_list",
                        "deals_list": data["deals_list"],
                        "filter_meta": data.get("filter_meta"),
                        "departments": catalog,
                    }
                )
                continue

            if tile.source.kind == "deals_money" and data.get("money"):
                emit(
                    {
                        "title": tile.title,
                        "chart_type": "deals_money",
                        "money": data["money"],
                        "bucket_period": data.get("bucket_period"),
                        "stats": _tile_stats(tile, data),
                    }
                )
                continue

            if tile.source.kind in (
                "deals_dynamics",
                "deals_dynamics_departments",
                "deals_dynamics_outcome_share",
                "deals_conversion",
            ) and data.get("table"):
                series = data.get("chart_series") or {}
                chart_type = tile.source.kind
                try:
                    if chart_type == "deals_dynamics_departments":
                        fig = _render_deals_dynamics_departments_chart(series, tile, catalog)
                    elif chart_type == "deals_dynamics_outcome_share":
                        fig = _render_deals_outcome_share_chart(series, tile, catalog)
                    elif chart_type == "deals_conversion":
                        fig = _render_deals_conversion_chart(series, tile, catalog)
                    else:
                        fig = _render_deals_dynamics_chart(series, tile, catalog)
                    plotly_json = fig.to_json()
                except Exception as exc:
                    logger.warning("График динамики «%s» упал: %s", tile.title, exc)
                    plotly_json = None
                period_charts: list[str] = []
                if chart_type == "deals_conversion":
                    for item in data.get("period_series") or []:
                        try:
                            period_charts.append(
                                _render_deals_conversion_chart(item, tile, catalog).to_json()
                            )
                        except Exception as exc:
                            logger.warning("График конверсии «%s» упал: %s", tile.title, exc)
                payload = {
                    "title": tile.title,
                    "chart_type": chart_type,
                    "table": data["table"],
                    "stats": _tile_stats(tile, data),
                }
                if data.get("bucket_period"):
                    payload["bucket_period"] = data["bucket_period"]
                if period_charts:
                    payload["period_charts"] = period_charts
                    payload["plotly_json"] = period_charts[-1]
                elif plotly_json:
                    payload["plotly_json"] = plotly_json
                emit(payload)
                continue

            if tile.chart_type == "table" or data.get("table"):
                emit(
                    {
                        "title": tile.title,
                        "chart_type": "table",
                        "table": data["table"],
                        "stats": _tile_stats(tile, data),
                    }
                )
                continue

            try:
                fig = _render_figure(tile, data)
            except Exception as exc:
                logger.warning("Рендер тайла «%s» упал: %s", tile.title, exc)
                emit({"title": tile.title, "error": str(exc)})
                continue

            emit(
                {
                    "title": tile.title,
                    "chart_type": tile.chart_type,
                    "plotly_json": fig.to_json(),
                    "stats": _tile_stats(tile, data),
                }
            )
        rendered_tabs.append({"title": tab.title, "tiles": tiles})

    return {"tabs": rendered_tabs}
