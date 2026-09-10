"""Универсальный дашборд: спека и KPI из колонок любого 1С-файла.

Не заменяет профили sales_pipeline / deficit — только когда нет узкой спеки.
"""
import pandas as pd

from models.dashboard_spec import DashboardSpec, Tab, Tile, TileSource

_ID_MARKERS = ("unnamed", "№", "номер п", "n/п")
_SKIP_GROUP = ("примечан", "комментар", "unnamed", "номер")
_AMOUNT_MARKERS = (
    "сумма",
    "долг",
    "дефицит",
    "выручк",
    "оплат",
    "стоим",
    "цена",
    "поступлен",
    "задолжен",
    "проект",
)
_PREFERRED_GROUPERS = (
    ("заказчик", "клиент", "контрагент", "отправитель", "компания"),
    ("менеджер", "ответствен", "инженер", "продавец"),
    ("подразделение", "отдел", "служба"),
    ("статус", "состояние", "приоритет"),
    ("поставщик", "производитель"),
    ("номенклатура", "оборудован"),
    ("этап",),
)


def _is_id_column(name: str) -> bool:
    n = str(name).lower().strip()
    if n.startswith("unnamed"):
        return True
    if n in {"№", "№ п/п", "n", "код"}:
        return True
    return n.startswith("№") and len(n) < 12


def _numeric_columns(df: pd.DataFrame) -> list[str]:
    n = max(len(df), 1)
    cols = []
    for c in df.select_dtypes(include="number").columns:
        if _is_id_column(c):
            continue
        name = str(c).lower()
        if name in {"номер"} or name.startswith("номер "):
            continue
        if n > 20 and df[c].nunique(dropna=True) > 0.85 * n:
            continue
        cols.append(c)
    return cols


def pick_metrics(df: pd.DataFrame) -> list[str]:
    nums = _numeric_columns(df)
    preferred = [
        c for c in nums if any(m in str(c).lower() for m in _AMOUNT_MARKERS)
    ]
    rub = [c for c in preferred if any(h in str(c).lower() for h in ("руб", "₽"))]
    if rub:
        preferred = rub + [c for c in preferred if c not in rub]
    rest = [c for c in nums if c not in preferred]
    return (preferred + rest)[:4]


def pick_groupers(df: pd.DataFrame) -> list[str]:
    n = max(len(df), 1)
    candidates: list[str] = []
    for col in df.columns:
        name = str(col).lower()
        if _is_id_column(name) or any(s in name for s in _SKIP_GROUP):
            continue
        if any(s in name for s in ("дата", "срок")):
            continue
        if pd.api.types.is_numeric_dtype(df[col]):
            continue
        unique = df[col].dropna().astype(str).nunique()
        if unique < 2:
            continue
        # в коротких таблицах (договоры, дефицит службы) заказчик может быть уникален в каждой строке
        if n > 25 and unique > min(80, int(0.7 * n) or 80):
            continue
        candidates.append(col)

    ordered: list[str] = []
    for aliases in _PREFERRED_GROUPERS:
        for col in candidates:
            name = str(col).lower()
            if col not in ordered and any(a in name for a in aliases):
                ordered.append(col)
                break
    for col in candidates:
        if col not in ordered:
            ordered.append(col)
    return ordered[:5]


def generic_kpis(df: pd.DataFrame) -> list[dict]:
    kpis = [{"label": "Строк", "value": len(df)}]
    metrics = pick_metrics(df)
    if metrics:
        total = pd.to_numeric(df[metrics[0]], errors="coerce").sum()
        kpis.append({"label": f"Итого «{metrics[0]}»", "value": _fmt(total)})
    groupers = pick_groupers(df)
    if groupers:
        kpis.append(
            {
                "label": f"Уникальных «{groupers[0]}»",
                "value": int(df[groupers[0]].nunique()),
            }
        )
    kpis.append({"label": "Колонок", "value": len(df.columns)})
    return kpis


def _fmt(value: float) -> str:
    if pd.isna(value):
        return "—"
    if float(value).is_integer():
        return f"{int(value):,}".replace(",", " ")
    return f"{float(value):,.2f}".replace(",", " ")


def build_generic_spec(df: pd.DataFrame) -> DashboardSpec | None:
    metrics = pick_metrics(df)
    groupers = pick_groupers(df)
    tiles: list[Tile] = []

    if groupers and metrics:
        tiles.append(
            Tile(
                title=f"{metrics[0]} по «{groupers[0]}»",
                chart_type="hbar",
                source=TileSource(
                    kind="group",
                    group_column=groupers[0],
                    value_column=metrics[0],
                ),
                agg="sum",
                top_n=10,
                unit="auto",
            )
        )
        if len(groupers) > 1:
            tiles.append(
                Tile(
                    title=f"Доля «{metrics[0]}» по «{groupers[1]}»",
                    chart_type="pie",
                    source=TileSource(
                        kind="group",
                        group_column=groupers[1],
                        value_column=metrics[0],
                    ),
                    agg="sum",
                    top_n=8,
                    unit="auto",
                )
            )
        if len(metrics) > 1:
            tiles.append(
                Tile(
                    title=f"{metrics[1]} по «{groupers[0]}»",
                    chart_type="bar",
                    source=TileSource(
                        kind="group",
                        group_column=groupers[0],
                        value_column=metrics[1],
                    ),
                    agg="sum",
                    top_n=10,
                    unit="auto",
                )
            )
        if len(groupers) > 2:
            tiles.append(
                Tile(
                    title=f"Количество по «{groupers[2]}»",
                    chart_type="bar",
                    source=TileSource(kind="group", group_column=groupers[2]),
                    agg="count",
                    top_n=12,
                    unit="auto",
                )
            )
    elif groupers:
        tiles.append(
            Tile(
                title=f"Количество по «{groupers[0]}»",
                chart_type="pie",
                source=TileSource(kind="group", group_column=groupers[0]),
                agg="count",
                top_n=8,
            )
        )
        if len(groupers) > 1:
            tiles.append(
                Tile(
                    title=f"Количество по «{groupers[1]}»",
                    chart_type="hbar",
                    source=TileSource(kind="group", group_column=groupers[1]),
                    agg="count",
                    top_n=10,
                )
            )
    elif metrics:
        return None

    if not tiles:
        return None

    tabs = [Tab(title="Обзор", tiles=tiles[:4])]

    from services import data_tools

    dates = data_tools.detect_date_columns(df).get("columns") or []
    if dates and metrics:
        tabs.append(
            Tab(
                title="Динамика",
                tiles=[
                    Tile(
                        title=f"Динамика «{metrics[0]}» по месяцам",
                        chart_type="area",
                        source=TileSource(
                            kind="period",
                            period="month",
                            value_column=metrics[0],
                        ),
                        unit="auto",
                        sort="none",
                    )
                ],
            )
        )

    return DashboardSpec(tabs=tabs)
