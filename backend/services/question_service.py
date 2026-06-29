def detect_intent(question: str):
    q = question.lower()

    if any(
        marker in q
        for marker in (
            "инсайт",
            "инсайты",
            "основные выводы",
            "что интересного",
            "что важного",
        )
    ):
        return "insights"

    # Chart intents explicitly requested
    if "график" in q or "диаграмм" in q:
        if "регион" in q:
            return "chart_regions"
        if "месяц" in q or "динамик" in q:
            return "chart_monthly"
        if "клиент" in q:
            return "chart_clients"
        if "менеджер" in q:
            return "chart_managers"
        if "год" in q:
            return "chart_yearly"
        if "квартал" in q:
            return "chart_quarterly"

    # Specific phrases from requirements mapped to charts
    if "продажи по регионам" in q:
        return "chart_regions"
    
    if "продажи по менеджерам" in q:
        return "chart_managers"
        
    if "топ клиентов" in q:
        return "chart_clients"
        
    if "продажи по месяцам" in q or "динамика выручки" in q:
        return "chart_monthly"
        
    if "распределение выручки" in q:
        return "chart_revenue"

    if any(
        marker in q
        for marker in (
            "по месяц",
            "по месяцам",
            "динамик",
            "тренд",
        )
    ) and any(
        marker in q
        for marker in ("продаж", "выручк", "доход", "sales", "revenue")
    ):
        return "trend_month"

    if any(
        marker in q
        for marker in ("по год", "по годам", "ежегодн")
    ):
        return "trend_year"

    if any(
        marker in q
        for marker in ("по квартал", "квартал", "quarter")
    ):
        return "trend_quarter"

    if any(
        marker in q
        for marker in ("лучш", "топ", "лидер", "больше всего")
    ) and "клиент" in q:
        return "top_client"

    if any(
        marker in q
        for marker in ("лучш", "топ", "лидер", "больше всего")
    ) and "менеджер" in q:
        return "top_manager"

    if any(
        marker in q
        for marker in ("лучш", "топ", "лидер", "больше всего", "больше выручк")
    ) and "регион" in q:
        return "top_region"

    if "уникальн" in q or "сколько клиентов" in q or "сколько покупател" in q:
        return "unique_count"

    if any(
        marker in q
        for marker in ("пропуск", "пуст", "null", "nan")
    ):
        return "null_count"

    if "дубликат" in q or "повтор" in q:
        return "duplicates_count"

    if any(
        marker in q
        for marker in ("по регион", "по менеджер", "по клиент", "группир")
    ):
        if "средн" in q:
            return "group_mean"

        if any(
            marker in q
            for marker in ("сколько", "количеств", "число записей")
        ):
            return "group_count"

        return "group_sum"

    if "сколько строк" in q:
        return "row_count"

    if "сколько записей" in q:
        return "row_count"

    if "сколько колонок" in q:
        return "column_count"

    if "какие колонки" in q:
        return "columns"

    if "список колонок" in q:
        return "columns"

    if "общая сумма" in q or "выручк" in q or "сумма продаж" in q:
        return "sum"

    if "средн" in q:
        return "mean"

    if "максимальн" in q or "максимум" in q:
        return "max"

    if "минимальн" in q or "минимум" in q:
        return "min"

    return "unknown"
