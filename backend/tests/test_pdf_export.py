"""Экспорт аналитического отчёта в PDF."""
import io

from services.pdf_export import render_report_pdf


def _sample_report() -> dict:
    return {
        "report_type": "deficit_report",
        "metadata": {
            "filename": "deficit.xlsx",
            "rows": 10,
            "columns": 4,
            "period": "2024",
        },
        "narrative": "Краткое резюме про дефицит по договорам.",
        "kpis": [{"label": "Общий дефицит", "value": "1 000"}],
        "insights": ["Первый вывод по данным"],
        "data_quality": {
            "total_cells": 40,
            "null_cells": 2,
            "null_pct": 5.0,
            "duplicates": 0,
            "worst_columns": [{"column": "комментарий", "nulls": 2, "pct": 20.0}],
        },
        "columns_overview": [
            {
                "column": "заказчик",
                "type": "Текст",
                "filled": 10,
                "unique": 3,
                "stats": "Альфа (4)",
            }
        ],
        "charts": [{"title": "ТОП заказчиков"}],
    }


def test_pdf_magic_and_size():
    pdf = render_report_pdf(_sample_report())
    assert pdf.startswith(b"%PDF")
    assert b"%%EOF" in pdf[-64:]
    assert len(pdf) > 1000


def test_pdf_user_edits_change_bytes():
    base = render_report_pdf(_sample_report())
    edited = render_report_pdf(
        _sample_report(),
        narrative="Секретная правка резюме XYZ " * 15,
        insights="Вывод аналитика ABC",
        comment="Комментарий пользователя 123",
    )
    assert edited.startswith(b"%PDF")
    assert base != edited
    assert len(edited) >= len(base)


def test_pdf_wraps_long_column_names():
    report = _sample_report()
    report["columns_overview"] = [
        {
            "column": "НеоплаченныйОстатокПоРасчётнымДокументамБезПробелов" * 2,
            "type": "Число",
            "filled": 10,
            "unique": 10,
            "stats": "мин 0 · макс 999999",
        }
    ]
    pdf = render_report_pdf(report)
    assert pdf.startswith(b"%PDF")


def test_pdf_embeds_dashboard_images():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    buf = io.BytesIO()
    fig, ax = plt.subplots(figsize=(3, 2), dpi=72)
    ax.bar(["A", "B"], [3, 5])
    fig.savefig(buf, format="png")
    plt.close(fig)
    png = buf.getvalue()

    pdf = render_report_pdf(
        _sample_report(),
        dashboard_tabs=[
            {
                "title": "Обзор",
                "tiles": [
                    {"title": "ТОП клиентов", "png": png, "stats": {"top": [("Альфа", 100)]}},
                    {"title": "Ошибка тайла", "error": "нет колонки"},
                ],
            }
        ],
        dashboard_comments={"Обзор": "Краткий комментарий вкладки"},
    )
    assert pdf.startswith(b"%PDF")
    assert b"/XObject" in pdf or b"/Image" in pdf
    assert len(pdf) > len(render_report_pdf(_sample_report()))
