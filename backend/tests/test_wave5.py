"""Живые сценарии, которые golden на синтетике не ловил."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from services import chat_service
from services.dashboard_engine import render_spec
from services.excel_service import read_excel
from services.exceptions import OllamaUnavailableError
from services.insights_service import _get_date_period
from services.report_service import get_profile_for_df

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"
SALES_ALL = ROOT / "backend" / "uploads" / "bb210e282fb44b19891b3be230564c3c.xlsx"
FORBIDDEN = (
    "Не удалось посчитать",
    "Числовая колонка не найдена",
    "Не удалось сгруппировать",
)
QUESTIONS = (
    "Что в файле?",
    "Сколько строк в таблице?",
    "Какие колонки есть?",
    "Основные выводы",
    "Топ-5",
    "Сколько у Алабуги?",
)
LIVE_FILES = (
    "Дефицит СМЭ_02.12.2024 (1).xlsx",
    "Дефицит СООК_2 декабря.xls",
    "Дефицит СТО_ 231224.xlsx",
    "Отчет ПДО 01.07.2024 к понедельнику в работе.xlsx",
    "Отчет ПДО 20.04.2026 к понедельнику в работе.xlsx",
    "Гарантия 2026.xlsx",
    "Гарантия СТЕ.xlsx",
    "Прогноз продаж СИО+ОАПЛиС на II кв. 2024.xlsx",
    "Заказы поставщикам 02_12_2024.xlsx",
    "Планируемые поступления ОАПЛиС II - й кв. 2024г..xlsx",
    "Входящие запросы_I кв 2024.xlsx",
    "2024.03.12 Состояние дел по договорам ОАПЛИС_дефицит.xlsx",
)


def _ban_llm(monkeypatch):
    def fail(*_a, **_k):
        raise AssertionError("быстрый путь не должен звать роутер")

    def boom(*_a, **_k):
        raise OllamaUnavailableError("banned")

    monkeypatch.setattr(chat_service, "_llm_classify", fail)
    monkeypatch.setattr(chat_service, "classify", fail)
    monkeypatch.setattr(chat_service, "ask_llm", boom)


def _norm(text: str) -> str:
    return "".join(ch.lower() for ch in str(text) if ch.isalnum())


def _related(column: str, label: str) -> bool:
    col_n, lab_n = _norm(column), _norm(label)
    if len(col_n) < 5 or len(lab_n) < 5:
        return False
    if col_n in lab_n or lab_n in col_n:
        return True
    shared = 0
    for left, right in zip(col_n, lab_n):
        if left != right:
            break
        shared += 1
    if shared >= 6:
        return True
    stems = ("сумм", "заказ", "оплат", "долг", "остат", "дефицит", "издел", "выруч")
    return any(stem in col_n and stem in lab_n for stem in stems)


def _kpi_number(kpi: dict) -> float | None:
    raw = kpi.get("raw_value", kpi.get("value"))
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    text = str(raw).replace("\u00a0", " ").replace(" ", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _is_money_kpi(kpi: dict) -> bool:
    label = str(kpi.get("label") or "").lower()
    if any(token in label for token in ("уникальн", "строк", "колонок")):
        return False
    if any(token in label for token in ("руб", "остаток", "оплач", "сумм", "выруч", "дефицит")):
        return True
    number = _kpi_number(kpi)
    return number is not None and number >= 1000


def test_pdo_compare_live_numbers(pdo_df, monkeypatch):
    _ban_llm(monkeypatch)
    text = chat_service.handle_question(pdo_df, "Сравни ССМУ и УПМ")["answer"]
    compact = text.replace(" ", "").replace("\u00a0", "")
    assert "697" in compact
    assert "105" in compact


def test_warranty_alabuga_is_32(warranty_df, monkeypatch):
    _ban_llm(monkeypatch)
    text = chat_service.handle_question(warranty_df, "Сколько у Алабуги?")["answer"]
    assert "32" in text
    for phrase in FORBIDDEN:
        assert phrase not in text


@pytest.mark.parametrize("filename", LIVE_FILES)
def test_money_kpi_matches_named_column(filename):
    path = EXAMPLES / filename
    if not path.exists():
        pytest.skip(f"нет examples/{filename}")
    df = read_excel(str(path))
    _, profile = get_profile_for_df(df, filename=filename)
    money = [kpi for kpi in profile.get_kpis(df) if _is_money_kpi(kpi)]
    if not money:
        pytest.skip(f"нет денежных KPI: {filename}")
    numeric = [
        col
        for col in df.columns
        if pd.to_numeric(df[col], errors="coerce").notna().any()
    ]
    for kpi in money:
        label = str(kpi.get("label") or "")
        expected = _kpi_number(kpi)
        assert expected is not None, label
        matches = []
        related = []
        for col in numeric:
            total = float(pd.to_numeric(df[col], errors="coerce").sum())
            if abs(total - expected) < 1.0:
                matches.append(col)
            if _related(str(col), label):
                related.append(col)
        assert matches, f"{filename}: «{label}»={expected} не равно сумме ни одной колонки"
        if related:
            assert any(col in matches for col in related), (
                f"{filename}: «{label}» совпало с {matches}, а не с {related}"
            )


def test_xls_date_columns_are_datetime():
    files = sorted(EXAMPLES.glob("*.xls"))
    if not files:
        pytest.skip("нет examples/*.xls (локальные выгрузки не в git)")
    assert len(files) == 9, [p.name for p in files]
    dated = 0
    for path in files:
        df = read_excel(str(path))
        named = [
            col
            for col in df.columns
            if any(token in str(col).lower() for token in ("дата", "срок"))
        ]
        typed = [col for col in df.columns if pd.api.types.is_datetime64_any_dtype(df[col])]
        cols = list(dict.fromkeys([*typed, *named]))
        if not cols:
            continue
        dated += 1
        period = _get_date_period(df)
        assert period, f"{path.name}: период не определён"
        for col in cols:
            series = df[col]
            if not pd.api.types.is_datetime64_any_dtype(series):
                parsed = pd.to_datetime(series, errors="coerce", dayfirst=True)
                if parsed.notna().mean() < 0.4:
                    continue
                assert False, f"{path.name}: {col} {series.dtype} — должна быть datetime"
            raw = series.dropna()
            if raw.empty:
                continue
            numeric_as_int = pd.to_numeric(raw, errors="coerce")
            assert not (numeric_as_int <= -1e18).any(), f"{path.name}: {col} содержит int64.min"
    assert dated >= 6, f"датовые колонки нашлись только в {dated} из 9 .xls"


def test_elliptical_followup_not_rowcount(monkeypatch):
    path = SALES_ALL if SALES_ALL.exists() else None
    if path is None:
        pytest.skip("нет Test1kv25-2kv26ALL.xlsx")
    _ban_llm(monkeypatch)
    df = read_excel(str(path))
    first = chat_service.handle_question(
        df, "Сколько сделок было в январе 2025 по отделам?"
    )["answer"]
    history = [
        {"role": "user", "content": "Сколько сделок было в январе 2025 по отделам?"},
        {"role": "assistant", "content": first},
    ]
    text = chat_service.handle_question(
        df, "а за 10 июня 2025?", history=history
    )["answer"]
    assert str(len(df)) not in text.replace(" ", "")
    assert "2954" not in text
    assert "7" in text


@pytest.mark.parametrize("filename", LIVE_FILES)
@pytest.mark.parametrize("question", QUESTIONS)
def test_chat_has_no_forbidden_phrases(filename, question, monkeypatch):
    path = EXAMPLES / filename
    if not path.exists():
        pytest.skip(f"нет examples/{filename}")
    monkeypatch.setattr(chat_service, "_llm_classify", lambda *_a, **_k: None)
    monkeypatch.setattr(chat_service, "classify", lambda *_a, **_k: None)
    monkeypatch.setattr(
        chat_service, "ask_llm", lambda *_a, **_k: (_ for _ in ()).throw(OllamaUnavailableError("banned"))
    )
    df = read_excel(str(path))
    text = chat_service.handle_question(df, question)["answer"]
    for phrase in FORBIDDEN:
        assert phrase not in text, f"{filename} / {question}: {text[:240]}"


def test_month_axis_is_russian():
    from services.data_tools import group_by_month

    frame = pd.DataFrame(
        {
            "дата": ["2025-01-15", "2025-04-02"],
            "сумма": [10, 20],
        }
    )
    groups = group_by_month(frame, "сумма")["groups"]
    assert "янв 2025" in groups
    assert "апр 2025" in groups
    assert "2025-01" not in groups


def test_sme_hides_single_department():
    path = EXAMPLES / "Дефицит СМЭ_02.12.2024 (1).xlsx"
    if not path.exists():
        pytest.skip("нет Дефицит СМЭ")
    df = read_excel(str(path))
    _, profile = get_profile_for_df(df, filename=path.name)
    labels = [str(k.get("label")) for k in profile.get_kpis(df)]
    assert "Подразделения" not in labels
    spec = profile.get_dashboard_spec(df)
    titles = [tile.title for tab in spec.tabs for tile in tab.tiles]
    assert not any("подразделени" in title.lower() for title in titles)


def test_pdo_yes_no_drops_names():
    path = EXAMPLES / "Отчет ПДО 01.07.2024 к понедельнику в работе.xlsx"
    if not path.exists():
        pytest.skip("нет ПДО")
    df = read_excel(str(path))
    _, profile = get_profile_for_df(df, filename=path.name)
    rendered = render_spec(df, profile.get_dashboard_spec(df))
    tile = next(
        item
        for tab in rendered["tabs"]
        for item in tab["tiles"]
        if "тмц" in str(item.get("title") or "").lower()
    )
    fig = json.loads(tile["plotly_json"])
    labels = fig["data"][0].get("x") or fig["data"][0].get("y") or []
    assert set(labels) <= {"Да", "Нет"}
    assert "Да" in labels and "Нет" in labels


def test_family_tabs_and_insights_skip_metadata():
    samples = {
        "Отчет ПДО 01.07.2024 к понедельнику в работе.xlsx": "Производство",
        "Гарантия 2026.xlsx": "Сервис",
        "Прогноз продаж СИО+ОАПЛиС на II кв. 2024.xlsx": "Прогноз",
        "Заказы поставщикам 02_12_2024.xlsx": "Поставщики",
        "Планируемые поступления ОАПЛиС II - й кв. 2024г..xlsx": "Поступления",
        "Входящие запросы_I кв 2024.xlsx": "Запросы",
    }
    for filename, tab_title in samples.items():
        path = EXAMPLES / filename
        if not path.exists():
            pytest.skip(filename)
        df = read_excel(str(path))
        _, profile = get_profile_for_df(df, filename=filename)
        insights = "\n".join(profile.get_insights(df))
        assert "В таблице" not in insights
        assert "пропусков" not in insights
        spec = profile.get_dashboard_spec(df)
        assert any(tab.title == tab_title for tab in spec.tabs), filename


def test_pie_tiles_keep_legend(sales_df):
    from models.dashboard_spec import DashboardSpec, Tab, Tile

    spec = DashboardSpec(
        tabs=[
            Tab(
                title="T",
                tiles=[
                    Tile(
                        title="Топ клиентов",
                        chart_type="pie",
                        source={
                            "kind": "group",
                            "group_column": "компания",
                            "value_column": "сумма по сделке",
                        },
                        top_n=5,
                    )
                ],
            )
        ]
    )
    rendered = render_spec(sales_df, spec)
    pies = [
        tile
        for tab in rendered["tabs"]
        for tile in tab["tiles"]
        if tile.get("chart_type") == "pie" and tile.get("plotly_json")
    ]
    assert pies
    for tile in pies:
        fig = json.loads(tile["plotly_json"])
        assert fig.get("layout", {}).get("showlegend") is True, tile.get("title")
