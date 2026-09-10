import pandas as pd
from datetime import datetime


_REPORT_TYPE_NAMES = {
    "sales_pipeline": "Этапы продаж (воронка сделок)",
    "deficit_report": "Дефицит по коммерческим службам (задолженность)",
    "unknown": "Универсальный табличный отчёт",
}

_TYPE_INTRO = {
    "sales_pipeline": (
        "Отчёт отражает воронку продаж: распределение сделок по подразделениям, "
        "каналам привлечения, менеджерам и этапам продаж."
    ),
    "deficit_report": (
        "Отчёт отражает дефицит (задолженность) по заказам заказчиков "
        "в разрезе подразделений и ответственных менеджеров."
    ),
    "unknown": "Автоматический анализ табличных данных произвольной структуры.",
}

_TYPE_RECOMMENDATIONS = {
    "sales_pipeline": [
        "Оцените концентрацию выручки: если топ-3 клиента дают большую часть оборота, "
        "снизьте зависимость от них за счёт развития среднего сегмента.",
        "Разберите сделки с незаполненными каналом и источником привлечения — без этого "
        "невозможно корректно оценить эффективность маркетинга.",
        "Проверьте сделки, задержанные на ранних этапах: именно там теряется потенциальная выручка.",
    ],
    "deficit_report": [
        "Сфокусируйте усилия по взысканию на подразделении и заказчиках с наибольшим дефицитом — "
        "это даст максимальный эффект.",
        "Сверьте даты реализации с текущей датой для оценки просрочки по каждой сделке.",
        "Сделки в валюте контролируйте отдельно: возможна курсовая переоценка суммы дефицита.",
    ],
    "unknown": [
        "Устраните пропуски в наиболее проблемных колонках — они искажают статистику.",
        "Проверьте дубликаты строк: они завышают итоговые показатели.",
    ],
}


def generate_summary(report_type: str, kpis: list[dict], insights: list[str]) -> str:
    if report_type == "deficit_report":
        total_deficit = next((k["formatted"] for k in kpis if k["name"] == "total_deficit"), "0")
        unique_cust = next((k["formatted"] for k in kpis if k["name"] == "unique_customers"), "0")
        
        lines = []
        lines.append(f"За анализируемый период выявлен общий дефицит на сумму {total_deficit} рублей.")
        lines.append(f"Количество уникальных заказчиков: {unique_cust}.")
        
        # Adding some generic logic to include top insights
        if insights:
            lines.append("\nКлючевые факты:")
            for i, ins in enumerate(insights, 1):
                lines.append(f"{i}. {ins}")
        return "\n".join(lines)
        
    elif report_type == "sales_pipeline":
        total_rev = next((k["formatted"] for k in kpis if k["name"] == "total_revenue"), "0")
        avg_check = next((k["formatted"] for k in kpis if k["name"] == "average_check"), "0")
        
        lines = []
        lines.append(f"Общая выручка составила {total_rev} рублей.")
        lines.append(f"Средний чек составил {avg_check} рублей.")
        
        if insights:
            lines.append("\nКлючевые факты:")
            for i, ins in enumerate(insights, 1):
                lines.append(f"{i}. {ins}")
        return "\n".join(lines)
        
    return "Отчет успешно проанализирован."


def build_report_narrative(
    report_type: str,
    kpis: list[dict],
    insights: list[str],
    metadata: dict,
    quality: dict,
) -> str:
    """Подробный текстовый отчёт (markdown) — редактируется пользователем в UI."""
    type_name = _REPORT_TYPE_NAMES.get(report_type, _REPORT_TYPE_NAMES["unknown"])
    lines: list[str] = []

    lines.append(f"Отчёт сформирован автоматически {datetime.now():%d.%m.%Y, %H:%M}.")
    lines.append(f"Тип отчёта: {type_name}.")
    if metadata.get("filename"):
        lines.append(f"Источник данных: файл «{metadata['filename']}».")
    lines.append(
        f"Объём данных: {metadata.get('rows', 0)} записей, {metadata.get('columns', 0)} колонок."
    )
    if metadata.get("period") and metadata["period"] != "Не определен":
        lines.append(f"Период данных: {metadata['period']}.")
    lines.append("")
    lines.append(_TYPE_INTRO.get(report_type, _TYPE_INTRO["unknown"]))

    if kpis:
        lines.append("")
        lines.append("КЛЮЧЕВЫЕ ПОКАЗАТЕЛИ")
        for kpi in kpis:
            label = kpi.get("label", kpi.get("name", "Показатель"))
            value = kpi.get("formatted", kpi.get("value", ""))
            lines.append(f"• {label}: {value}")

    if insights:
        lines.append("")
        lines.append("ОСНОВНЫЕ ВЫВОДЫ")
        for i, insight in enumerate(insights, 1):
            lines.append(f"{i}. {insight}")

    lines.append("")
    lines.append("КАЧЕСТВО ДАННЫХ")
    lines.append(
        f"Заполнено {100 - quality.get('null_pct', 0):.1f}% ячеек "
        f"(пропусков: {quality.get('null_cells', 0)} из {quality.get('total_cells', 0)}). "
        f"Полных дубликатов строк: {quality.get('duplicates', 0)}."
    )
    worst = [w for w in quality.get("worst_columns", []) if w.get("nulls", 0) > 0]
    if worst:
        problem = ", ".join(f"«{w['column']}» ({w['pct']}%)" for w in worst[:3])
        lines.append(f"Наибольшая доля пропусков в колонках: {problem}.")

    recommendations = _TYPE_RECOMMENDATIONS.get(report_type, _TYPE_RECOMMENDATIONS["unknown"])
    lines.append("")
    lines.append("РЕКОМЕНДАЦИИ")
    for rec in recommendations:
        lines.append(f"• {rec}")

    return "\n".join(lines)
