import json
from datetime import datetime

import streamlit as st
import plotly.io as pio

from api_client import (
    ApiClientError,
    chat,
    check_backend,
    dashboard_comments,
    dashboard_edit,
    dashboard_generate,
    dashboard_pin,
    dashboard_save_spec,
    get_insights,
    generate_chart,
    get_dashboard,
    get_detailed_table,
    get_full_report,
    get_history,
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

    if "dashboard_comments" not in st.session_state:
        st.session_state.dashboard_comments = {}


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
        # если этот файл уже загружали раньше — подтягиваем прошлую переписку
        try:
            history = get_history(file_id)
            st.session_state.messages = [
                {
                    "role": m["role"],
                    "content": m["content"],
                    "charts": m.get("charts", []),
                }
                for m in history.get("messages", [])
            ]
        except ApiClientError:
            st.session_state.messages = []
        st.session_state.detailed_table = None
        st.session_state.insights = []
        st.session_state.report = None
        st.session_state.dashboard_comments = {}
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


_CHART_TYPES = ["bar", "hbar", "pie", "line", "area"]
_AGGS = ["sum", "mean", "count"]
_UNITS = ["auto", "rub", "k", "mln", "mlrd"]


def _apply_dashboard_result(result: dict) -> None:
    """Обновить session_state.dashboard новыми вкладками/спекой."""
    db = st.session_state.dashboard or {}
    db["tabs"] = result.get("tabs", [])
    if "spec" in result:
        db["spec"] = result["spec"]
    st.session_state.dashboard = db
    st.session_state.dashboard_comments = {}


def _render_dashboard_toolbar(db: dict) -> None:
    """NL-генерация/правка дашборда + комментарии ИИ + простой редактор."""
    file_id = st.session_state.file_id

    col_input, col_gen, col_edit, col_comments = st.columns([5, 2, 2, 2])
    request_text = col_input.text_input(
        "Что изменить в дашборде?",
        placeholder="Например: собери дашборд по менеджерам с воронкой",
        key="dash_nl_request",
        label_visibility="collapsed",
    )
    if col_gen.button("✨ Сгенерировать", use_container_width=True, disabled=not request_text):
        try:
            with st.spinner("ИИ собирает дашборд..."):
                _apply_dashboard_result(dashboard_generate(file_id, request_text))
            st.rerun()
        except ApiClientError as exc:
            st.error(f"Генерация не удалась: {exc}")
    if col_edit.button("🛠 Применить правку", use_container_width=True, disabled=not request_text):
        try:
            with st.spinner("ИИ редактирует дашборд..."):
                _apply_dashboard_result(dashboard_edit(file_id, request_text))
            st.rerun()
        except ApiClientError as exc:
            st.error(f"Правка не удалась: {exc}")
    if col_comments.button("💬 Комментарии ИИ", use_container_width=True):
        try:
            with st.spinner("ИИ анализирует вкладки..."):
                result = dashboard_comments(file_id)
            st.session_state.dashboard_comments = result.get("comments", {})
        except ApiClientError as exc:
            st.error(f"Комментарии недоступны: {exc}")

    _render_spec_editor(db)


def _render_spec_editor(db: dict) -> None:
    spec = db.get("spec")
    if not spec:
        return
    column_names = db.get("metadata", {}).get("column_names", [])

    with st.expander("🧩 Простой редактор тайлов"):
        st.caption("Настройки применяются после кнопки «Сохранить дашборд».")
        for tab_i, tab in enumerate(spec.get("tabs", [])):
            st.markdown(f"**Вкладка: {tab['title']}**")
            for tile_i, tile in enumerate(tab.get("tiles", [])):
                key = f"ed_{tab_i}_{tile_i}"
                cols = st.columns([3, 1.4, 1.4, 1.2, 1.4, 0.8])
                tile["title"] = cols[0].text_input(
                    "Название", tile["title"], key=f"{key}_t", label_visibility="collapsed"
                )
                tile["chart_type"] = cols[1].selectbox(
                    "Тип", _CHART_TYPES,
                    index=_CHART_TYPES.index(tile.get("chart_type", "bar")),
                    key=f"{key}_c", label_visibility="collapsed",
                )
                tile["agg"] = cols[2].selectbox(
                    "Агрегат", _AGGS,
                    index=_AGGS.index(tile.get("agg", "sum")),
                    key=f"{key}_a", label_visibility="collapsed",
                )
                tile["top_n"] = cols[3].number_input(
                    "Топ-N", 1, 50, int(tile.get("top_n", 10)),
                    key=f"{key}_n", label_visibility="collapsed",
                )
                tile["unit"] = cols[4].selectbox(
                    "Единицы", _UNITS,
                    index=_UNITS.index(tile.get("unit", "auto")),
                    key=f"{key}_u", label_visibility="collapsed",
                )
                tile["_delete"] = cols[5].checkbox("🗑", key=f"{key}_d")

            with st.popover("＋ Добавить график", use_container_width=False):
                new_title = st.text_input("Название", key=f"add_{tab_i}_title")
                new_type = st.selectbox("Тип", _CHART_TYPES, key=f"add_{tab_i}_type")
                new_kind = st.selectbox(
                    "Источник", ["group", "period", "columns_pattern"], key=f"add_{tab_i}_kind"
                )
                new_group = new_value = new_period = new_pattern = None
                if new_kind == "group":
                    new_group = st.selectbox("Колонка группировки", column_names, key=f"add_{tab_i}_g")
                if new_kind in ("group", "period"):
                    new_value = st.selectbox(
                        "Метрика (пусто = count)", ["—"] + column_names, key=f"add_{tab_i}_v"
                    )
                if new_kind == "period":
                    new_period = st.selectbox("Период", ["month", "quarter", "year"], key=f"add_{tab_i}_p")
                if new_kind == "columns_pattern":
                    new_pattern = st.text_input("Окончание колонок", "(сумма)", key=f"add_{tab_i}_pat")
                if st.button("Добавить", key=f"add_{tab_i}_btn"):
                    source = {"kind": new_kind}
                    if new_group:
                        source["group_column"] = new_group
                    if new_value and new_value != "—":
                        source["value_column"] = new_value
                    if new_period:
                        source["period"] = new_period
                    if new_pattern:
                        source["columns_pattern"] = new_pattern
                    tab.setdefault("tiles", []).append(
                        {
                            "title": new_title or "Новый график",
                            "chart_type": new_type,
                            "source": source,
                            "agg": "sum",
                            "top_n": 10,
                            "unit": "auto",
                            "target_line": None,
                            "sort": "desc",
                        }
                    )
                    st.rerun()

        if st.button("💾 Сохранить дашборд", type="primary"):
            clean_tabs = []
            for tab in spec.get("tabs", []):
                tiles = [
                    {k: v for k, v in tile.items() if not k.startswith("_")}
                    for tile in tab.get("tiles", [])
                    if not tile.get("_delete")
                ]
                clean_tabs.append({"title": tab["title"], "tiles": tiles})
            try:
                with st.spinner("Сохраняю и перерисовываю..."):
                    _apply_dashboard_result(
                        dashboard_save_spec(st.session_state.file_id, {"tabs": clean_tabs})
                    )
                st.rerun()
            except ApiClientError as exc:
                st.error(f"Не сохранилось: {exc}")


def _render_tabs(tabs: list) -> None:
    """Вкладочный дашборд v2: st.tabs + сетка тайлов по 2 в ряд."""
    comments = st.session_state.get("dashboard_comments", {})
    tab_titles = [t["title"] for t in tabs]
    for tab_obj, tab_data in zip(st.tabs(tab_titles), tabs):
        with tab_obj:
            comment = comments.get(tab_data["title"])
            if comment:
                st.info(f"💬 {comment}")
            tiles = [t for t in tab_data.get("tiles", [])]
            for i in range(0, len(tiles), 2):
                cols = st.columns(2)
                for j, col in enumerate(cols):
                    idx = i + j
                    if idx >= len(tiles):
                        break
                    tile = tiles[idx]
                    with col:
                        st.markdown(f"**{tile['title']}**")
                        if "error" in tile:
                            st.warning(f"Не удалось построить: {tile['error']}")
                        elif "plotly_json" in tile:
                            fig = pio.from_json(tile["plotly_json"])
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

        # Charts: вкладки v2 или плоский список (fallback)
        if db.get("tabs"):
            st.write("### 📈 Дашборд")
            _render_dashboard_toolbar(db)
            _render_tabs(db["tabs"])
        elif db.get("charts"):
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


def _render_message_charts(message: dict, msg_idx: int = 0) -> None:
    for chart_i, chart in enumerate(message.get("charts", [])):
        if "plotly_json" in chart:
            fig = pio.from_json(chart["plotly_json"])
            st.plotly_chart(fig, use_container_width=True)
        pin_spec = chart.get("pin_spec")
        # пин доступен, когда у текущего файла есть вкладочный дашборд
        if pin_spec and st.session_state.get("dashboard", {}).get("tabs"):
            if st.button(
                "📌 На дашборд",
                key=f"pin_{msg_idx}_{chart_i}",
                help="Закрепить этот график на первой вкладке дашборда",
            ):
                try:
                    result = dashboard_pin(st.session_state.file_id, pin_spec)
                    st.toast(result.get("message", "Закреплено"), icon="📌")
                    with st.spinner("Обновляю дашборд..."):
                        st.session_state.dashboard = get_dashboard(st.session_state.file_id)
                except ApiClientError as exc:
                    st.warning(str(exc))


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
    for msg_idx, message in enumerate(st.session_state.messages):
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            _render_message_charts(message, msg_idx)

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
        _render_message_charts(
            {"charts": charts}, msg_idx=len(st.session_state.messages)
        )


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
