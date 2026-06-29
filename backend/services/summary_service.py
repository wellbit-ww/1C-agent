import pandas as pd

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
