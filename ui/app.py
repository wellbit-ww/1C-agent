import json
from datetime import datetime

import streamlit as st
import plotly.io as pio

from api_client import (
    ApiClientError,
    chat,
    check_backend,
    get_insights,
    generate_chart,
    get_dashboard,
    get_detailed_table,
    get_full_report,
    upload_file,
)


st.set_page_config(
    page_title="Excel AI Agent",
    page_icon="📊",
    layout="wide",
)


def init_session_state() -> None:
    if "file_id" not in st.session_state:
        st.session_state.file_id = None

    if "messages" not in st.session_state:
        st.session_state.messages = []

    if "uploaded_filename" not in st.session_state:
        st.session_state.uploaded_filename = None

    if "detailed_table" not in st.session_state:
        st.session_state.detailed_table = None

    if "insights" not in st.session_state:
        st.session_state.insights = []

    if "view" not in st.session_state:
        st.session_state.view = "main"

    if "report" not in st.session_state:
        st.session_state.report = None


def render_sidebar(backend_ok: bool, backend_status: str) -> None:
    with st.sidebar:
        st.header("Статус")

        if backend_ok:
            st.success(backend_status)
        else:
            st.error("Backend недоступен")

        st.divider()
        st.caption("Текущий файл")
        st.code(st.session_state.file_id or "Файл не загружен")

        if st.session_state.uploaded_filename:
            st.caption(f"Имя файла: {st.session_state.uploaded_filename}")

        st.metric("Сообщений в чате", len(st.session_state.messages))


def render_upload_block(uploaded_file) -> None:
    st.subheader("1. Загрузка файла")

    if st.button(
        "Загрузить файл",
        type="primary",
        disabled=uploaded_file is None,
    ):
        if uploaded_file is None:
            st.warning("Сначала выберите файл")
            return

        try:
            with st.spinner("Загружаю файл..."):
                result = upload_file(uploaded_file)
        except ApiClientError as exc:
            st.error(f"Ошибка загрузки: {exc}")
            return

        file_id = result.get("file_id")

        if not file_id:
            st.error("Backend не вернул file_id")
            return

        st.session_state.file_id = file_id
        st.session_state.uploaded_filename = uploaded_file.name
        st.session_state.messages = []
        st.session_state.detailed_table = None
        st.session_state.insights = []
        st.session_state.report = None
        st.session_state.view = "main"

        st.success("✅ Файл успешно загружен")
        st.code(f"File ID: {file_id}")
        
        # Load dashboard automatically
        try:
            with st.spinner("Создаю дашборд..."):
                st.session_state.dashboard = get_dashboard(file_id)
                st.session_state.detailed_table = get_detailed_table(file_id)
        except ApiClientError as exc:
            st.error(f"Ошибка создания дашборда: {exc}")

    if st.session_state.file_id:
        st.info(f"Текущий File ID: `{st.session_state.file_id}`")


def _report_type_name(report_type: str) -> str:
    return {
        "sales_pipeline": "Этапы продаж",
        "deficit_report": "Дефицит по КС",
    }.get(report_type, "Универсальный отчёт")


def _generate_report() -> None:
    """Запрашивает полный отчёт у backend и открывает страницу отчёта."""
    try:
        with st.spinner("Формирую подробный отчёт (несколько секунд)..."):
            report = get_full_report(
                st.session_state.file_id,
                st.session_state.uploaded_filename,
            )
    except ApiClientError as exc:
        st.error(f"Ошибка формирования отчёта: {exc}")
        return

    st.session_state.report = report
    # редактируемые поля заполняются сгенерированным текстом
    st.session_state.edit_narrative = report.get("narrative", "")
    st.session_state.edit_insights = "\n".join(
        f"• {insight}" for insight in report.get("insights", [])
    )
    st.session_state.edit_comment = ""
    st.session_state.view = "report"
    st.rerun()


def render_analysis_block(uploaded_file) -> None:
    st.subheader("2. Подробный отчёт")
    st.caption(
        "Отдельная страница с полным разбором файла: KPI, графики, выводы, "
        "качество данных. Текст отчёта можно редактировать и скачать."
    )

    st.button(
        "📄 Сформировать отчёт",
        type="primary",
        disabled=not st.session_state.file_id,
        on_click=_generate_report,
    )


def render_insights_block() -> None:
    st.subheader("3. Инсайты")

    if st.button(
        "Получить инсайты",
        disabled=not st.session_state.file_id,
    ):
        if not st.session_state.file_id:
            st.warning("Сначала загрузите файл")
            return

        try:
            with st.spinner("Собираю автоматические инсайты..."):
                st.session_state.insights = get_insights(
                    st.session_state.file_id
                )
        except ApiClientError as exc:
            st.error(f"Ошибка получения инсайтов: {exc}")
            return

    if st.session_state.insights:
        for insight in st.session_state.insights:
            st.info(insight)


def render_visualization_block() -> None:
    st.subheader("4. 📈 Визуализация")

    chart_options = [
        "Авто-график (лучшие категории)",
        "Продажи по регионам",
        "Продажи по менеджерам",
        "Топ клиентов",
        "Продажи по месяцам"
    ]
    
    selected_option = st.selectbox("Что построить?", chart_options)

    if st.button("Построить график", disabled=not st.session_state.file_id):
        if not st.session_state.file_id:
            st.warning("Сначала загрузите файл")
            return

        try:
            with st.spinner("Создаю график..."):
                chart_data = generate_chart(st.session_state.file_id, selected_option)
        except ApiClientError as exc:
            st.error(f"Ошибка построения графика: {exc}")
            return

        if "plotly_json" in chart_data:
            fig = pio.from_json(chart_data["plotly_json"])
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.error("График не был возвращен API.")


def _render_charts_grid(charts: list) -> None:
    for i in range(0, len(charts), 2):
        cols = st.columns(2)
        with cols[0]:
            if "plotly_json" in charts[i]:
                fig = pio.from_json(charts[i]["plotly_json"])
                st.plotly_chart(fig, use_container_width=True)
        if i + 1 < len(charts):
            with cols[1]:
                if "plotly_json" in charts[i + 1]:
                    fig = pio.from_json(charts[i + 1]["plotly_json"])
                    st.plotly_chart(fig, use_container_width=True)


def render_dashboard_block() -> None:
    if st.session_state.get("dashboard"):
        db = st.session_state.dashboard

        report_type = db.get("report_type", "unknown")
        type_name = _report_type_name(report_type)

        st.header(f"Отчет: {type_name}")
        
        metadata = db.get("metadata", {})
        if metadata:
            st.caption(
                f"**Период данных:** {metadata.get('period', 'Не определен')} | "
                f"**Строк:** {metadata.get('rows', 0)} | "
                f"**Колонок:** {metadata.get('columns', 0)}"
            )

        summary = db.get("summary", "")
        if summary:
            st.write("### 📋 Executive Summary")
            st.info(summary)
        
        # KPIs
        if db.get("kpis"):
            st.write("### 📊 Ключевые показатели (KPI)")
            
            # Divide KPIs into rows with max 4 cols each
            kpis = db["kpis"]
            cols_per_row = 4
            for i in range(0, len(kpis), cols_per_row):
                cols = st.columns(cols_per_row)
                for j in range(cols_per_row):
                    if i + j < len(kpis):
                        kpi = kpis[i + j]
                        with cols[j]:
                            st.metric(kpi["label"], kpi["value"])
                            
        # Insights with colors
        if db.get("insights"):
            st.write("### 💡 Ключевые инсайты")
            for insight in db["insights"]:
                # simple heuristic to apply color boxes based on words
                lower_insight = insight.lower()
                if "критическ" in lower_insight or "максимальн" in lower_insight or "дефицит" in lower_insight:
                    st.error(f"🔴 {insight}")
                elif "топ" in lower_insight or "лучший" in lower_insight or "сильный" in lower_insight or "крупнейш" in lower_insight:
                    st.success(f"🟢 {insight}")
                else:
                    st.warning(f"🟡 {insight}")

        # Charts Grid
        if db.get("charts"):
            st.write("### 📈 Графики")
            _render_charts_grid(db["charts"])

        # Detailed Table
        if st.session_state.get("detailed_table"):
            st.write("### 📑 Детализация данных (ТОП-100 строк)")
            st.dataframe(st.session_state.detailed_table, use_container_width=True)


def _build_report_markdown() -> str:
    rep = st.session_state.report
    metadata = rep.get("metadata", {})
    quality = rep.get("data_quality", {})

    md: list[str] = []
    md.append(f"# Аналитический отчёт: {metadata.get('filename') or 'без имени'}")
    md.append(f"_Сформирован: {datetime.now():%d.%m.%Y %H:%M}_\n")
    md.append(
        f"**Тип отчёта:** {_report_type_name(rep.get('report_type', 'unknown'))}  |  "
        f"**Строк:** {metadata.get('rows', 0)}  |  "
        f"**Колонок:** {metadata.get('columns', 0)}  |  "
        f"**Период:** {metadata.get('period', 'Не определен')}"
    )

    md.append("\n## Резюме\n")
    md.append(st.session_state.get("edit_narrative", ""))

    if rep.get("kpis"):
        md.append("\n## Ключевые показатели\n")
        for kpi in rep["kpis"]:
            md.append(f"- **{kpi['label']}:** {kpi['value']}")

    md.append("\n## Выводы\n")
    md.append(st.session_state.get("edit_insights", ""))

    comment = st.session_state.get("edit_comment", "").strip()
    if comment:
        md.append("\n## Комментарий аналитика\n")
        md.append(comment)

    md.append("\n## Качество данных\n")
    md.append(
        f"- Всего ячеек: {quality.get('total_cells', 0)}\n"
        f"- Пропусков: {quality.get('null_cells', 0)} ({quality.get('null_pct', 0)}%)\n"
        f"- Дубликатов строк: {quality.get('duplicates', 0)}"
    )
    for worst in quality.get("worst_columns", []):
        md.append(
            f"- Колонка «{worst['column']}»: {worst['nulls']} пропусков ({worst['pct']}%)"
        )

    if rep.get("columns_overview"):
        md.append("\n## Структура данных\n")
        md.append("| Колонка | Тип | Заполнено | Уникальных | Статистика |")
        md.append("|---|---|---|---|---|")
        for col in rep["columns_overview"]:
            md.append(
                f"| {col['column']} | {col['type']} | {col['filled']} | "
                f"{col['unique']} | {col['stats']} |"
            )

    return "\n".join(md)


def render_report_page() -> None:
    rep = st.session_state.report
    metadata = rep.get("metadata", {})

    top_left, top_right = st.columns([3, 1])
    with top_left:
        if st.button("← Назад к основному экрану"):
            st.session_state.view = "main"
            st.rerun()
    with top_right:
        st.button(
            "🔄 Пересоздать (сбросит правки)",
            on_click=_generate_report,
        )

    st.title(f"📄 Отчёт: {_report_type_name(rep.get('report_type', 'unknown'))}")
    st.caption(
        f"**Файл:** {metadata.get('filename') or '—'} | "
        f"**Период данных:** {metadata.get('period', 'Не определен')} | "
        f"**Строк:** {metadata.get('rows', 0)} | "
        f"**Колонок:** {metadata.get('columns', 0)}"
    )

    # --- Редактируемое резюме ---
    st.write("### 📋 Резюме (редактируемое)")
    st.text_area(
        "Текст резюме",
        key="edit_narrative",
        height=420,
        label_visibility="collapsed",
    )
    with st.expander("Предпросмотр"):
        st.markdown(st.session_state.get("edit_narrative", "").replace("\n", "  \n"))

    # --- KPI ---
    if rep.get("kpis"):
        st.write("### 📊 Ключевые показатели")
        kpis = rep["kpis"]
        cols_per_row = 4
        for i in range(0, len(kpis), cols_per_row):
            cols = st.columns(cols_per_row)
            for j in range(cols_per_row):
                if i + j < len(kpis):
                    with cols[j]:
                        st.metric(kpis[i + j]["label"], kpis[i + j]["value"])

    # --- Графики ---
    if rep.get("charts"):
        st.write("### 📈 Графики")
        _render_charts_grid(rep["charts"])

    # --- Редактируемые выводы ---
    st.write("### 💡 Выводы (редактируемые)")
    st.text_area(
        "Каждая строка — отдельный вывод",
        key="edit_insights",
        height=160,
        label_visibility="collapsed",
    )

    # --- Свободный комментарий ---
    st.write("### ✍️ Комментарий аналитика")
    st.text_area(
        "Добавьте свои замечания — они попадут в итоговый файл",
        key="edit_comment",
        height=120,
        label_visibility="collapsed",
    )

    # --- Качество данных ---
    quality = rep.get("data_quality", {})
    st.write("### 🧹 Качество данных")
    q_cols = st.columns(4)
    q_cols[0].metric("Всего ячеек", quality.get("total_cells", 0))
    q_cols[1].metric(
        "Пропуски",
        f"{quality.get('null_cells', 0)} ({quality.get('null_pct', 0)}%)",
    )
    q_cols[2].metric("Дубликаты строк", quality.get("duplicates", 0))
    q_cols[3].metric("Заполненность", f"{100 - quality.get('null_pct', 0):.1f}%")

    if quality.get("worst_columns"):
        st.dataframe(
            [
                {
                    "Колонка": w["column"],
                    "Пропусков": w["nulls"],
                    "Доля, %": w["pct"],
                }
                for w in quality["worst_columns"]
            ],
            use_container_width=True,
            hide_index=True,
        )

    # --- Структура данных ---
    if rep.get("columns_overview"):
        with st.expander("🧱 Структура данных (обзор всех колонок)"):
            st.dataframe(
                [
                    {
                        "Колонка": c["column"],
                        "Тип": c["type"],
                        "Заполнено": c["filled"],
                        "Уникальных": c["unique"],
                        "Статистика": c["stats"],
                    }
                    for c in rep["columns_overview"]
                ],
                use_container_width=True,
                hide_index=True,
            )

    # --- Образец данных ---
    if rep.get("sample"):
        with st.expander("📑 Образец данных (первые 20 строк)"):
            st.dataframe(rep["sample"], use_container_width=True)

    # --- Скачивание ---
    st.divider()
    st.download_button(
        "⬇️ Скачать отчёт (Markdown, с вашими правками)",
        data=_build_report_markdown(),
        file_name=f"report_{datetime.now():%Y%m%d_%H%M%S}.md",
        mime="text/markdown",
        type="primary",
    )


_CHAT_SUGGESTIONS = {
    "sales_pipeline": [
        "Общая выручка",
        "Топ-5 клиентов",
        "Круговая диаграмма продаж по менеджерам",
        "Динамика выручки по месяцам",
    ],
    "deficit_report": [
        "Общий дефицит",
        "Топ-5 заказчиков",
        "Круговая диаграмма дефицита по подразделениям",
        "Сколько уникальных клиентов?",
    ],
}
_DEFAULT_CHAT_SUGGESTIONS = [
    "Сколько строк в таблице?",
    "Какие колонки есть?",
    "Основные выводы",
]


def _render_message_charts(message: dict) -> None:
    for chart in message.get("charts", []):
        if "plotly_json" in chart:
            fig = pio.from_json(chart["plotly_json"])
            st.plotly_chart(fig, use_container_width=True)


def render_chat_block() -> None:
    st.subheader("Чат")
    st.caption(
        "Спросите про данные или попросите диаграмму: "
        "«круговая диаграмма дефицита по подразделениям», «динамика выручки по месяцам»"
    )

    # Чипы-подсказки под тип отчёта
    dashboard = st.session_state.get("dashboard") or {}
    suggestions = _CHAT_SUGGESTIONS.get(
        dashboard.get("report_type"),
        _DEFAULT_CHAT_SUGGESTIONS,
    )
    disabled = not st.session_state.file_id
    chip_cols = st.columns(len(suggestions))
    for i, suggestion in enumerate(suggestions):
        if chip_cols[i].button(
            suggestion,
            disabled=disabled,
            use_container_width=True,
        ):
            st.session_state.pending_question = suggestion

    # История
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            _render_message_charts(message)

    pending = st.session_state.get("pending_question")
    st.session_state.pending_question = None

    user_question = st.chat_input("Задайте вопрос по Excel-файлу")
    question = pending or user_question

    if not question:
        return

    if not st.session_state.file_id:
        st.warning("Сначала загрузите файл")
        return

    st.session_state.messages.append(
        {
            "role": "user",
            "content": question,
        }
    )

    with st.chat_message("user"):
        st.markdown(question)

    try:
        with st.spinner("Excel AI Agent думает..."):
            result = chat(
                st.session_state.file_id,
                question,
            )
    except ApiClientError as exc:
        result = {"answer": f"Ошибка: {exc}", "charts": []}

    answer = result.get("answer", "Ответ не получен")
    charts = [c for c in result.get("charts", []) if "plotly_json" in c]

    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": answer,
            "charts": charts,
        }
    )

    with st.chat_message("assistant"):
        st.markdown(answer)
        for chart in charts:
            fig = pio.from_json(chart["plotly_json"])
            st.plotly_chart(fig, use_container_width=True)


def main() -> None:
    init_session_state()

    backend_ok, backend_status = check_backend()
    render_sidebar(backend_ok, backend_status)

    st.title("Excel AI Agent")
    st.caption("Локальный интерфейс для тестирования FastAPI + Pandas + Ollama агента")

    if not backend_ok:
        st.error("❌ Backend недоступен")
        st.stop()

    # Отдельная страница подробного отчёта
    if st.session_state.view == "report":
        if st.session_state.report is not None:
            render_report_page()
        else:
            st.session_state.view = "main"
            st.rerun()
        return

    uploaded_file = st.file_uploader(
        "Выберите Excel-файл",
        type=["xlsx", "xls"],
    )

    left_col, right_col = st.columns(2)

    with left_col:
        render_upload_block(uploaded_file)

    with right_col:
        render_analysis_block(uploaded_file)

    st.divider()
    if st.session_state.get("dashboard"):
        render_dashboard_block()
    else:
        render_insights_block()
        
    st.divider()
    render_visualization_block()
    
    st.divider()
    render_chat_block()


if __name__ == "__main__":
    main()
