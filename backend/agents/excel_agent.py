from services.excel_service import read_excel
from services.analysis_service import get_basic_info
from services.llm_service import ask_llm
from services.cache_service import get_dataframe, set_dataframe
from services.question_service import detect_intent
from services.insights_service import get_basic_insights
from services.data_tools import (
    group_sum,
    get_top_n,
    group_by_month,
    group_by_year,
    group_by_quarter,
)
from services.chart_service import (
    create_bar_chart,
    create_line_chart,
    create_top_clients_chart,
    create_region_chart,
    create_manager_chart,
    create_monthly_trend_chart,
)


class ExcelAgent:

    def _load_dataframe(self, file_id: str, file_path: str):
        df = get_dataframe(file_id)

        if df is None:
            df = read_excel(file_path)
            set_dataframe(file_id, df)

        return df

    def analyze_file(self, file_path: str):
        df = read_excel(file_path)
        info = get_basic_info(df)

        prompt = f"""
Ты аналитик данных.

Вот информация об Excel-файле:

{info}

Опиши:
1. Что находится в файле.
2. Какие данные содержит таблица.
3. Для чего файл может использоваться.

Ответь кратко на русском языке.
"""

        return ask_llm(prompt)

    def get_insights(self, file_id: str, file_path: str) -> list[str]:
        df = self._load_dataframe(file_id, file_path)
        return get_basic_insights(df)

    def generate_chart(self, file_id: str, file_path: str, question: str) -> dict:
        df = self._load_dataframe(file_id, file_path)
        intent = detect_intent(question)
        
        if intent == "chart_regions":
            data = group_sum(df, "продажи по регионам")
            return create_region_chart(data)
            
        elif intent == "chart_clients":
            data = get_top_n(df, "лучший клиент", semantic="client", n=10)
            return create_top_clients_chart(data)
            
        elif intent == "chart_managers":
            data = group_sum(df, "продажи по менеджерам")
            return create_manager_chart(data)
            
        elif intent == "chart_monthly":
            data = group_by_month(df, "динамика по месяцам")
            return create_monthly_trend_chart(data)
            
        elif intent == "chart_yearly":
            data = group_by_year(df, "динамика по годам")
            return create_line_chart(data, title="Динамика по годам")
            
        elif intent == "chart_quarterly":
            data = group_by_quarter(df, "динамика по кварталам")
            return create_line_chart(data, title="Динамика по кварталам")
            
        elif intent == "chart_revenue" or intent == "chart_sales" or "авто-график" in question.lower():
            # Автоматически находим любую текстовую и любую числовую
            cat_cols = df.select_dtypes(include=['object', 'string', 'category']).columns.tolist()
            num_cols = df.select_dtypes(include=['number']).columns.tolist()
            
            if cat_cols and num_cols:
                # Группируем по первой попавшейся категории и суммируем первое числовое
                group_col = cat_cols[0]
                val_col = num_cols[0]
                grouped = df.groupby(group_col)[val_col].sum().sort_values(ascending=False).head(10)
                data = {
                    "groups": grouped.to_dict(),
                    "group_column": group_col,
                    "value_column": val_col
                }
                return create_bar_chart(data, title=f"ТОП-10: {group_col} (по {val_col})")
            else:
                return {"error": "В таблице нет текстовых и числовых колонок для автоматического графика."}
            
        else:
            return {"error": "Не удалось распознать запрос для построения графика."}

    def get_dashboard(self, file_id: str, file_path: str) -> dict:
        df = self._load_dataframe(file_id, file_path)
        from pathlib import Path
        from services.report_service import get_profile_for_df
        from services.storage_service import get_original_name

        filename = get_original_name(file_id) or Path(file_path).name
        report_type, profile = get_profile_for_df(df, filename=filename)

        summary = ""
        if hasattr(profile, "get_summary"):
            summary = profile.get_summary(df)

        from services.insights_service import _get_date_period
        period = _get_date_period(df) or "Не определен"

        result = {
            "report_type": report_type,
            "summary": summary,
            "kpis": profile.get_kpis(df),
            "charts": [],
            "insights": profile.get_insights(df),
            "metadata": {
                "rows": len(df),
                "columns": len(df.columns),
                "period": period,
                "column_names": [str(c) for c in df.columns],
            }
        }

        # вкладочный дашборд (v2): сначала пользовательская спека из SQLite,
        # иначе дефолтная спека профиля; без спек — старый плоский список
        from services import dashboard_service

        spec = dashboard_service.get_current_spec(file_id, df)
        if spec is not None:
            from services.dashboard_engine import render_spec
            result["tabs"] = render_spec(df, spec)["tabs"]
            result["spec"] = spec.model_dump()
        else:
            result["charts"] = profile.get_charts(df)

        from services.file_context_service import ensure_context, get_context

        try:
            ctx = get_context(file_id) or ensure_context(
                file_id, df, filename=filename, use_llm=False
            )
            result["file_context"] = ctx.model_dump()
        except Exception:
            pass

        return result

    def handle_chat_message(
        self,
        file_id: str,
        file_path: str,
        question: str,
        history: list[dict] | None = None,
    ) -> dict:
        """Новый обработчик чата: возвращает {"answer": str, "charts": [...]}."""
        df = self._load_dataframe(file_id, file_path)
        from services.chat_service import handle_question
        from services.file_context_service import get_context

        return handle_question(
            df, question, history=history, file_context=get_context(file_id)
        )
