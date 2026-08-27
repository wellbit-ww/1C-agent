"""Dashboard Spec — декларативное описание дашборда.

Спека генерируется профилем отчёта, LLM или редактором UI, а исполняется
всегда детерминированно (dashboard_engine + pandas/plotly).
"""
from typing import Literal

from pydantic import BaseModel, Field

ChartType = Literal["bar", "hbar", "pie", "line", "area"]
AggType = Literal["sum", "mean", "count"]
PeriodType = Literal["month", "quarter", "year"]
UnitType = Literal["auto", "rub", "k", "mln", "mlrd"]


class TileSource(BaseModel):
    """Откуда брать данные для тайла.

    group — группировка по категориальной колонке (семантика или явное имя);
    columns_pattern — воронка: агрегат по КАЖДОЙ колонке, чьё имя
    заканчивается на pattern (например «(сумма)» в отчёте этапов продаж);
    period — динамика по дате.
    """

    kind: Literal["group", "columns_pattern", "period"]
    group_semantic: str | None = None
    group_column: str | None = None
    value_semantic: str | None = None
    value_column: str | None = None
    columns_pattern: str | None = None
    period: PeriodType | None = None


class Tile(BaseModel):
    title: str
    chart_type: ChartType = "bar"
    source: TileSource
    agg: AggType = "sum"
    top_n: int = Field(default=10, ge=1, le=50)
    unit: UnitType = "auto"
    target_line: float | None = None
    sort: Literal["desc", "asc", "none"] = "desc"


class Tab(BaseModel):
    title: str
    tiles: list[Tile] = Field(default_factory=list, max_length=8)


class DashboardSpec(BaseModel):
    tabs: list[Tab] = Field(min_length=1, max_length=8)
