"""Фиксированные цвета коммерческих служб на всех графиках воронки."""

COMPANY_CHART_COLOR = "#C00000"
DEPARTMENT_CHART_FALLBACK = "#A5A5A5"

# Палитра в стиле Excel: достаточный контраст между соседними службами.
DEPARTMENT_CHART_COLORS: dict[str, str] = {
    "СИО": "#4472C4",
    "ОАПЛиС": "#7030A0",
    "СТО": "#70AD47",
    "СМЭ": "#FFC000",
    "СТЕ": "#ED7D31",
    "СООК": "#00B0F0",
    "Сервис": "#17A2B8",
}

_LABEL_ALIASES: dict[str, str] = {
    "сс": "Сервис",
    "сервис": "Сервис",
    "сервисная служба": "Сервис",
}


def department_chart_color(label: str) -> str:
    text = str(label).strip()
    if text == "Совтест":
        return COMPANY_CHART_COLOR
    key = _LABEL_ALIASES.get(text.lower(), text)
    return DEPARTMENT_CHART_COLORS.get(key, DEPARTMENT_CHART_FALLBACK)
