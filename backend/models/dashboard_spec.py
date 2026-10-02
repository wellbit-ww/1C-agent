"""Dashboard Spec — декларативное описание дашборда.

Спека генерируется профилем отчёта, LLM или редактором UI, а исполняется
всегда детерминированно (dashboard_engine + pandas/plotly).
"""
from typing import Literal

from pydantic import BaseModel, Field

ChartType = Literal["bar", "hbar", "pie", "line", "area", "table"]
AggType = Literal["sum", "mean", "count"]
PeriodType = Literal["month", "quarter", "year", "half"]
UnitType = Literal["auto", "rub", "k", "mln", "mlrd"]


class TileSource(BaseModel):
    """Откуда брать данные для тайла.

    group — группировка по категориальной колонке (семантика или явное имя);
    columns_pattern — агрегат по КАЖДОЙ колонке с таким окончанием
    (сколько прошло через этап, если в 1С заполнены все пройденные стадии);
    named_columns — сумма каждой явно названной колонки (блоки «К оплате»);
    current_stage — воронка «как в 1С»: сделка целиком на ПОСЛЕДНЕМ
    заполненном этапе (сумма сделки или число сделок);
    period — динамика по дате;
    pivot — таблица: категория × период (кварталы по подразделениям);
    halfyear — блоки «Сделки и ЗК» по полугодиям/кварталам/месяцам (period: half | quarter | month);
    status_summary — сводка сделок и ЗК по статусам (в работе, выиграна, …);
    deals_dynamics — вкладка «Динамика»: число сделок по периодам (таблица + график под ней);
    deals_dynamics_departments — то же по всем подразделениям (групповые столбцы по периодам);
    deals_dynamics_outcome_share — доля отменённых и проигранных сделок, % (Совтест + подразделения);
    deals_conversion — вкладка «Конверсия»: заказы (ЗК) / сделки, % по компании и всем подразделениям;
    deals_money — вкладка «Деньги»: потенциал сделок и сумма заказов, сравнение с тем же периодом год назад;
    deal_statuses — вкладка «Этапы продаж»: статусы сделок и заказов по периодам;
    in_work_stages — сделки со статусом «В работе» по текущему этапу.
    """

    kind: Literal[
        "group",
        "columns_pattern",
        "named_columns",
        "period",
        "current_stage",
        "pivot",
        "outcome",
        "halfyear",
        "status_summary",
        "deals_dynamics",
        "deals_dynamics_departments",
        "deals_dynamics_outcome_share",
        "deals_conversion",
        "deals_money",
        "deal_statuses",
        "in_work_stages",
    ]
    group_semantic: str | None = None
    group_column: str | None = None
    value_semantic: str | None = None
    value_column: str | None = None
    columns_pattern: str | None = None
    column_names: list[str] | None = None
    period: PeriodType | None = None
    departments: list[str] | None = None
    variant: Literal["full", "counts"] | None = None


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
