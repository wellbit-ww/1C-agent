import pandas as pd

from services.sales_brief_summary import build_sales_brief_summary


def test_sales_brief_summary_template():
    df = pd.DataFrame(
        {
            "подразделение": [
                "Служба испытательного оборудования",
                "Служба испытательного оборудования",
                "Сервисная служба",
            ],
            "дата начала сделки": pd.to_datetime(["2026-07-01", "2026-08-01", "2026-07-15"]),
            "количество сделок": [10, 5, 20],
            "сумма по сделке": [100.0, 50.0, 200.0],
            "количество зк": [4, 2, 10],
        }
    )
    text = build_sales_brief_summary(df)
    assert "**Заведено сделок**" in text
    assert "**Общий потенциал сделок**" in text
    assert "\n\n" in text
    assert "Конверсия сделок в заказы" in text
    assert "КП" not in text or "конверсии" in text
    assert "СИО" in text
    assert "без Сервиса и СООК" in text
    assert "с учётом Сервиса и СООК" in text
