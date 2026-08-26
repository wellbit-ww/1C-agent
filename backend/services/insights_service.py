import pandas as pd

from services.data_tools import (
    detect_date_columns,
    get_duplicates_count,
    get_null_count,
    get_sum,
    get_top_n,
    group_sum,
)


def _format_number(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value):,}".replace(",", " ")

    return f"{float(value):,.2f}".replace(",", " ")


def _get_date_period(df: pd.DataFrame) -> str | None:
    detected = detect_date_columns(df)["columns"]

    if not detected:
        return None

    date_col = detected[0]
    dates = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True).dropna()

    if dates.empty:
        return None

    min_date = dates.min()
    max_date = dates.max()

    if min_date.year == max_date.year and min_date.month == max_date.month:
        return f"{min_date.strftime('%m.%Y')}"

    return (
        f"{min_date.strftime('%d.%m.%Y')} — "
        f"{max_date.strftime('%d.%m.%Y')}"
    )


def get_basic_insights(df: pd.DataFrame) -> list[str]:
    insights: list[str] = []

    insights.append(f"В таблице {len(df)} строк и {len(df.columns)} колонок")

    revenue_result = get_sum(df, "общая выручка")

    if "value" in revenue_result:
        insights.append(
            "Общая выручка: "
            f"{_format_number(revenue_result['value'])}"
        )

    for semantic, label in (
        ("client", "клиент"),
        ("manager", "менеджер"),
        ("region", "регион"),
    ):
        top_result = get_top_n(df, f"лучший {label}", semantic=semantic, n=1)

        if "groups" in top_result and top_result["groups"]:
            top_name = next(iter(top_result["groups"]))
            top_value = top_result["groups"][top_name]
            insights.append(
                f"Топ {label}: {top_name} "
                f"({_format_number(top_value)})"
            )

    region_groups = group_sum(df, "продажи по регионам")

    if "groups" in region_groups and region_groups["groups"]:
        top_region = next(iter(region_groups["groups"]))
        top_region_value = region_groups["groups"][top_region]
        insights.append(
            f"Лучший регион: {top_region} "
            f"({_format_number(top_region_value)})"
        )

    period = _get_date_period(df)

    if period:
        insights.append(f"Период данных: {period}")

    null_result = get_null_count(df)

    if null_result.get("value", 0) > 0:
        insights.append(
            f"Обнаружено {null_result['value']} пропусков"
        )
    else:
        insights.append("Пропуски в данных не обнаружены")

    duplicates_result = get_duplicates_count(df)

    if duplicates_result.get("value", 0) > 0:
        insights.append(
            f"Обнаружено {duplicates_result['value']} дубликатов"
        )
    else:
        insights.append("Дубликаты в данных не обнаружены")

    return insights
