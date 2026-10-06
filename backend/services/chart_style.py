"""Единый стиль заголовков Plotly-графиков на дашбордах."""

CHART_TITLE_FONT_SIZE = 14


def chart_title_layout(text: str) -> dict:
    """Заголовок по центру, размер шрифта как у всех диаграмм."""
    return dict(
        text=text,
        x=0.5,
        xanchor="center",
        font=dict(size=CHART_TITLE_FONT_SIZE),
    )


def chart_title_caps(text: str) -> dict:
    """Заголовок столбчатых диаграмм воронки продаж — прописные буквы."""
    return chart_title_layout(text.upper())
