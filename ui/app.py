import json
import streamlit as st
import plotly.io as pio

from api_client import (
    ApiClientError,
    analyze_file,
    chat,
    check_backend,
    get_insights,
    generate_chart,
    get_dashboard,
    get_detailed_table,
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


def render_analysis_block(uploaded_file) -> None:
    st.subheader("2. Автоанализ")

    if st.button(
        "Проанализировать файл",
        disabled=uploaded_file is None,
    ):
        if uploaded_file is None:
            st.warning("Сначала загрузите файл")
            return

        try:
            with st.spinner("Анализирую файл через модель..."):
                result = analyze_file(uploaded_file)
        except ApiClientError as exc:
            st.error(f"Ошибка анализа: {exc}")
            return

        description = result.get("description", "Описание не получено")

        st.markdown("## Описание файла")
        st.info(description)


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


def render_dashboard_block() -> None:
    if st.session_state.get("dashboard"):
        db = st.session_state.dashboard
        
        report_type = db.get("report_type", "unknown")
        type_name = "Этапы продаж" if report_type == "sales_pipeline" else "Дефицит" if report_type == "deficit_report" else "Неизвестный"
        
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
            charts = db["charts"]
            for i in range(0, len(charts), 2):
                cols = st.columns(2)
                # First chart in row
                with cols[0]:
                    if "plotly_json" in charts[i]:
                        fig = pio.from_json(charts[i]["plotly_json"])
                        st.plotly_chart(fig, use_container_width=True)
                # Second chart in row (if exists)
                if i + 1 < len(charts):
                    with cols[1]:
                        if "plotly_json" in charts[i+1]:
                            fig = pio.from_json(charts[i+1]["plotly_json"])
                            st.plotly_chart(fig, use_container_width=True)

        # Detailed Table
        if st.session_state.get("detailed_table"):
            st.write("### 📑 Детализация данных (ТОП-100 строк)")
            st.dataframe(st.session_state.detailed_table, use_container_width=True)


def render_chat_block() -> None:
    st.subheader("Чат")

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    user_question = st.chat_input("Задайте вопрос по Excel-файлу")

    if not user_question:
        return

    if not st.session_state.file_id:
        st.warning("Сначала загрузите файл")
        return

    st.session_state.messages.append(
        {
            "role": "user",
            "content": user_question,
        }
    )

    with st.chat_message("user"):
        st.markdown(user_question)

    try:
        with st.spinner("Excel AI Agent думает..."):
            answer = chat(
                st.session_state.file_id,
                user_question,
            )
    except ApiClientError as exc:
        answer = f"Ошибка: {exc}"

    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": answer,
        }
    )

    with st.chat_message("assistant"):
        st.markdown(answer)


def main() -> None:
    init_session_state()

    backend_ok, backend_status = check_backend()
    render_sidebar(backend_ok, backend_status)

    st.title("Excel AI Agent")
    st.caption("Локальный интерфейс для тестирования FastAPI + Pandas + Ollama агента")

    if not backend_ok:
        st.error("❌ Backend недоступен")
        st.stop()

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
