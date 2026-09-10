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
    if unit == "auto":
        return _auto_scale(max(values) if values else 0.0)
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

    if tile.sort == "desc":
        grouped = grouped.sort_values(ascending=False)
    elif tile.sort == "asc":
        grouped = grouped.sort_values(ascending=True)

    grouped = grouped.head(tile.top_n)
    if grouped.empty:
        return {"error": "Нет данных для тайла"}

    return {
        "groups": {str(k): float(v) for k, v in grouped.to_dict().items()},
        "group_column": group_col,
        "value_column": value_col,
    }


def _columns_pattern_data(df: pd.DataFrame, tile: Tile) -> dict:
    """Агрегат по каждой колонке, оканчивающейся на pattern (проход через этап)."""
    pattern = tile.source.columns_pattern or ""
    matched = [c for c in df.columns if str(c).endswith(pattern)]
    if not matched:
        return {"error": f"Нет колонок с шаблоном «{pattern}»"}

    values: dict[str, float] = {}
    for col in matched:
        series = pd.to_numeric(df[col], errors="coerce")
        if tile.agg == "mean":
            value = series.mean()
        elif tile.agg == "count":
            value = series.notna().sum()
        else:
            value = series.sum()
        if pd.isna(value):
            continue
        label = col[: -len(pattern)].strip() if pattern else str(col)
        values[label] = float(value)

    if not values:
        return {"error": f"Колонки по шаблону «{pattern}» пусты"}

    items = list(values.items())
    if tile.sort == "desc":
        items.sort(key=lambda x: -x[1])
    elif tile.sort == "asc":
        items.sort(key=lambda x: x[1])
    # sort == "none": порядок колонок = бизнес-порядок этапов воронки

    return {"groups": dict(items), "group_column": pattern, "value_column": None}


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


def _tile_data(df: pd.DataFrame, tile: Tile) -> dict:
    kind = tile.source.kind
    if kind == "columns_pattern":
        return _columns_pattern_data(df, tile)
    if kind == "current_stage":
        return _current_stage_data(df, tile)
    if kind == "period":
        return _period_data(df, tile)
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
                textposition="outside" if horizontal else "auto",
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
    if tile.chart_type == "hbar":
        fig.update_xaxes(title_text=axis_title)
        fig.update_yaxes(automargin=True)
    elif tile.chart_type != "pie":
        fig.update_yaxes(title_text=axis_title)

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
