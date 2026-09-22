"""Движок дашбордов: Dashboard Spec -> набор plotly-фигур.

Всё исполнение детерминированное: pandas считает, plotly рисует.
LLM может только генерировать/редактировать спеку — никогда не считает.
"""
import logging

import pandas as pd
import plotly.graph_objects as go

from models.dashboard_spec import DashboardSpec, Tab, Tile
from services import data_tools
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
            return text.split(sep)[-1].strip()
    return text


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

    numeric = df[matched].apply(pd.to_numeric, errors="coerce")
    stage_idx = _last_filled_stage_index(numeric)
    labels = _stage_labels(matched, pattern)
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
    work[group_col] = names.loc[~blank]
    work[date_col] = pd.to_datetime(work[date_col], errors="coerce", dayfirst=True)
    work = work.dropna(subset=[date_col, group_col])
    if work.empty:
        return {"error": "Нет сделок с датой начала и подразделением"}

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
        if freq != "Y" and year_periods:
            columns.append(str(year))
            year_vals = [_pivot_cell(sum(row[start_len:])) for row in matrix]
            for i, value in enumerate(year_vals):
                matrix[i].append(value)
            col_totals.append(_pivot_cell(sum(year_vals)))
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


def _tile_data(df: pd.DataFrame, tile: Tile) -> dict:
    kind = tile.source.kind
    if kind == "columns_pattern":
        return _columns_pattern_data(df, tile)
    if kind == "named_columns":
        return _named_columns_data(df, tile)
    if kind == "current_stage":
        return _current_stage_data(df, tile)
    if kind == "period":
        return _period_data(df, tile)
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
    rendered_tabs = []
    for tab in spec.tabs:
        tiles = []
        for tile in tab.tiles:
            try:
                data = _tile_data(df, tile)
            except Exception as exc:
                logger.warning("Тайл «%s» упал: %s", tile.title, exc)
                tiles.append({"title": tile.title, "error": str(exc)})
                continue

            if "error" in data:
                tiles.append({"title": tile.title, "error": data["error"]})
                continue

            if tile.chart_type == "table" or data.get("table"):
                tiles.append(
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
                tiles.append({"title": tile.title, "error": str(exc)})
                continue

            tiles.append(
                {
                    "title": tile.title,
                    "chart_type": tile.chart_type,
                    "plotly_json": fig.to_json(),
                    "stats": _tile_stats(tile, data),
                }
            )
        rendered_tabs.append({"title": tab.title, "tiles": tiles})

    return {"tabs": rendered_tabs}
