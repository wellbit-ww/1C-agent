"""ИИ-слой дашбордов: NL-генерация/редактирование спек, пины из чата,
авто-комментарии. LLM только производит JSON-спеку — считает всё движок.
"""
import hashlib
import json
import logging
import re

import pandas as pd
from langchain_ollama import ChatOllama
from pydantic import ValidationError

from config import OLLAMA_BASE_URL, ROUTER_MODEL, MAIN_MODEL
from models.dashboard_spec import DashboardSpec, Tab, Tile, TileSource
from services import db_service
from services.exceptions import OllamaUnavailableError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Текущая спека: пользовательская из SQLite или дефолтная из профиля
# ---------------------------------------------------------------------------

def get_current_spec(file_id: str, df: pd.DataFrame) -> DashboardSpec | None:
    saved = db_service.get_dashboard_spec(file_id)
    if saved:
        try:
            return DashboardSpec.model_validate_json(saved)
        except ValidationError:
            logger.warning("Сохранённая спека %s невалидна — беру профиль", file_id)
            db_service.delete_dashboard_spec(file_id)

    from services.report_service import get_profile_for_df
    from services.storage_service import get_original_name

    _, profile = get_profile_for_df(
        df, filename=get_original_name(file_id)
    )
    builder = getattr(profile, "get_dashboard_spec", None)
    if builder is None:
        return None
    spec = builder(df)
    return spec if isinstance(spec, DashboardSpec) else None


def save_spec(file_id: str, spec: DashboardSpec) -> None:
    db_service.save_dashboard_spec(file_id, spec.model_dump_json())


# ---------------------------------------------------------------------------
# Пин из чата
# ---------------------------------------------------------------------------

def pin_tile(file_id: str, df: pd.DataFrame, tile_payload: dict) -> tuple[bool, str]:
    try:
        tile = Tile.model_validate(tile_payload)
    except ValidationError as exc:
        return False, f"Некорректный тайл: {exc.errors()[0]['msg']}"

    spec = get_current_spec(file_id, df)
    if spec is None:
        return False, "Для этого типа файла нет дашборда, куда закрепить график"

    target_tab = spec.tabs[0]
    if any(t.title == tile.title for t in target_tab.tiles):
        return False, "Такой график уже есть на дашборде"
    if len(target_tab.tiles) >= 8:
        return False, "На первой вкладке уже максимум графиков (8)"

    target_tab.tiles.append(tile)
    save_spec(file_id, spec)
    return True, f"График «{tile.title}» закреплён на вкладке «{target_tab.title}»"


# ---------------------------------------------------------------------------
# NL-генерация и редактирование спеки
# ---------------------------------------------------------------------------

_spec_llm: ChatOllama | None = None


def _get_spec_llm() -> ChatOllama:
    global _spec_llm
    if _spec_llm is None:
        _spec_llm = ChatOllama(
            model=ROUTER_MODEL,
            base_url=OLLAMA_BASE_URL,
            temperature=0,
            reasoning=False,
            num_predict=2500,
        )
    return _spec_llm


_SPEC_PROMPT = """Ты конфигуратор BI-дашборда. По просьбе пользователя собери JSON-спеку дашборда для Excel-таблицы.

Схема (строго):
{"tabs": [{"title": "Имя вкладки", "tiles": [{"title": "...", "chart_type": "bar|hbar|pie|line|area", "source": {...}, "agg": "sum|mean|count", "top_n": 10, "unit": "auto|rub|k|mln|mlrd", "target_line": null, "sort": "desc|asc|none"}]}]}

Варианты source:
- {"kind": "group", "group_column": "<колонка>", "value_column": "<колонка>"} — группировка по категории
- {"kind": "period", "period": "month|quarter|year", "value_column": "<колонка>"} — динамика по дате
- {"kind": "current_stage", "columns_pattern": "(сумма)", "value_column": "<колонка суммы сделки>"} — воронка 1С: сделка на ПОСЛЕДНЕМ заполненном этапе
- {"kind": "columns_pattern", "columns_pattern": "(сумма)"} — сумма КАЖДОЙ колонки-этапа (проход через этап)

Правила:
- Используй ТОЛЬКО колонки из списка ниже, имена копируй посимвольно. Не выдумывай колонки (в том числе «инвестиции»), если их нет в списке.
- chart_type ТОЛЬКО: bar, hbar, pie, line, area. Для воронки — hbar + source.kind=current_stage, sort=none. ЗАПРЕЩЕНО chart_type=funnel.
- value_column нужен для agg sum/mean; для agg=count его можно опустить.
- 1–4 вкладки, 1–4 тайла на вкладку.
__MODE_BLOCK__
Колонки таблицы: __COLUMNS__
Запрос пользователя: __REQUEST__

Ответ — ТОЛЬКО валидный JSON без markdown и пояснений."""

_FUNNEL_HINT = re.compile(r"воронк|этап", re.I)

_CHART_ALIASES = {
    "funnel": "hbar",
    "воронка": "hbar",
    "histogram": "bar",
    "hist": "bar",
    "column": "bar",
    "columns": "bar",
    "donut": "pie",
    "doughnut": "pie",
    "scatter": "bar",
    "table": "bar",
    "kpi": "bar",
    "horizontalbar": "hbar",
    "horizontal_bar": "hbar",
}

_KIND_ALIASES = {
    "funnel": "current_stage",
    "stages": "current_stage",
    "pipeline": "current_stage",
    "воронка": "current_stage",
    "currentstage": "current_stage",
    "current_stage": "current_stage",
    "aggregate": "group",
    "groupby": "group",
    "category": "group",
    "time": "period",
    "timeseries": "period",
    "trend": "period",
}

_AGG_ALIASES = {
    "average": "mean",
    "avg": "mean",
    "total": "sum",
    "len": "count",
    "size": "count",
}

_VALID_CHARTS = {"bar", "hbar", "pie", "line", "area"}
_VALID_AGGS = {"sum", "mean", "count"}
_VALID_KINDS = {"group", "columns_pattern", "period", "current_stage"}
_VALID_PERIODS = {"month", "quarter", "year"}
_VALID_UNITS = {"auto", "rub", "k", "mln", "mlrd"}
_VALID_SORTS = {"desc", "asc", "none"}

_GROUP_HINTS = (
    ("менеджер", "менеджер"),
    ("клиент", "клиент"),
    ("компани", "компани"),
    ("подраздел", "подраздел"),
    ("поставщик", "поставщик"),
    ("город", "город"),
    ("регион", "регион"),
)


def _extract_json(text: str) -> str | None:
    if not text:
        return None
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    blob = fence.group(1) if fence else cleaned
    start = blob.find("{")
    if start < 0:
        return None
    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(blob[start:])
        return json.dumps(obj, ensure_ascii=False)
    except json.JSONDecodeError:
        pass
    candidate = re.sub(r",\s*([}\]])", r"\1", blob[start:])
    match = re.search(r"\{.*\}", candidate, re.DOTALL)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
        return json.dumps(obj, ensure_ascii=False)
    except json.JSONDecodeError:
        return match.group(0)


def _normalize_chart(value) -> str:
    v = str(value or "bar").lower().strip().replace("-", "_").replace(" ", "")
    if v in _VALID_CHARTS:
        return v
    if v in _CHART_ALIASES:
        return _CHART_ALIASES[v]
    if "pie" in v or "donut" in v:
        return "pie"
    if "area" in v:
        return "area"
    if "line" in v:
        return "line"
    if "hbar" in v or "horizontal" in v:
        return "hbar"
    return "bar"


def _best_column(df: pd.DataFrame, name) -> str | None:
    if not name:
        return None
    name = str(name).strip()
    if name in df.columns:
        return name
    lowered = name.lower()
    for col in df.columns:
        if str(col).strip().lower() == lowered:
            return col
    matches = [
        col for col in df.columns
        if lowered in str(col).lower() or str(col).lower() in lowered
    ]
    if not matches:
        return None
    return min(matches, key=lambda col: abs(len(str(col)) - len(name)))


def _has_pattern(df: pd.DataFrame, pattern: str) -> bool:
    return any(str(c).endswith(pattern) for c in df.columns)


def _coerce_tile(tile: dict, df: pd.DataFrame) -> dict | None:
    if not isinstance(tile, dict):
        return None
    title = str(tile.get("title") or "График")[:120]
    chart = _normalize_chart(tile.get("chart_type"))
    source = tile.get("source") if isinstance(tile.get("source"), dict) else {}
    kind_raw = str(source.get("kind") or "group").lower().strip()
    kind = _KIND_ALIASES.get(kind_raw, kind_raw)
    if kind not in _VALID_KINDS:
        if "current" in kind_raw or "funnel" in kind_raw or "ворон" in kind_raw:
            kind = "current_stage"
        elif source.get("columns_pattern"):
            kind = "columns_pattern"
        elif source.get("period"):
            kind = "period"
        else:
            kind = "group"

    src: dict = {"kind": kind}
    if kind in ("columns_pattern", "current_stage"):
        pattern = str(source.get("columns_pattern") or "(сумма)")
        if not _has_pattern(df, pattern):
            pattern = next(
                (p for p in ("(сумма)", "(количество)") if _has_pattern(df, p)),
                None,
            )
        if not pattern:
            return None
        src["columns_pattern"] = pattern
        if kind == "current_stage":
            value_column = _best_column(df, source.get("value_column"))
            if value_column:
                src["value_column"] = value_column
            if source.get("value_semantic"):
                src["value_semantic"] = source["value_semantic"]
        if chart not in ("bar", "hbar"):
            chart = "hbar"
    elif kind == "period":
        period = str(source.get("period") or "month").lower()
        src["period"] = period if period in _VALID_PERIODS else "month"
        value_column = _best_column(df, source.get("value_column"))
        if value_column:
            src["value_column"] = value_column
        if source.get("value_semantic"):
            src["value_semantic"] = source["value_semantic"]
        if "value_column" not in src and "value_semantic" not in src:
            return None
    else:
        group_column = _best_column(df, source.get("group_column"))
        if group_column:
            src["group_column"] = group_column
        elif source.get("group_semantic"):
            src["group_semantic"] = source["group_semantic"]
        else:
            return None
        value_column = _best_column(df, source.get("value_column"))
        if value_column:
            src["value_column"] = value_column
        if source.get("value_semantic"):
            src["value_semantic"] = source["value_semantic"]

    agg = _AGG_ALIASES.get(str(tile.get("agg") or "sum").lower(), str(tile.get("agg") or "sum").lower())
    if agg not in _VALID_AGGS:
        agg = "sum"
    unit = str(tile.get("unit") or "auto").lower()
    if unit not in _VALID_UNITS:
        unit = "auto"
    sort = str(tile.get("sort") or "desc").lower()
    if sort not in _VALID_SORTS:
        sort = "desc"
    try:
        top_n = int(tile.get("top_n") or 10)
    except (TypeError, ValueError):
        top_n = 10

    out = {
        "title": title,
        "chart_type": chart,
        "source": src,
        "agg": agg,
        "top_n": max(1, min(50, top_n)),
        "unit": unit,
        "sort": sort,
    }
    if tile.get("target_line") is not None:
        try:
            out["target_line"] = float(tile["target_line"])
        except (TypeError, ValueError):
            pass
    return out


def _coerce_spec_dict(data: dict, df: pd.DataFrame) -> dict:
    if not isinstance(data, dict):
        return {"tabs": []}
    if "tabs" not in data and ("tiles" in data or "title" in data):
        data = {"tabs": [data]}
    tabs_in = data.get("tabs")
    if not isinstance(tabs_in, list):
        return {"tabs": []}
    tabs = []
    for tab in tabs_in:
        if not isinstance(tab, dict):
            continue
        tiles_in = tab.get("tiles") or []
        if not isinstance(tiles_in, list):
            continue
        tiles = [t for t in (_coerce_tile(tile, df) for tile in tiles_in) if t]
        if tiles:
            tabs.append({
                "title": str(tab.get("title") or "Обзор")[:80],
                "tiles": tiles[:8],
            })
    data["tabs"] = tabs[:8]
    return data


def _build_spec_prompt(
    df: pd.DataFrame,
    request: str,
    current_spec: DashboardSpec | None,
) -> str:
    if current_spec is not None:
        mode_block = (
            "Это РЕДАКТИРОВАНИЕ: возьми текущую спеку ниже и примени правку пользователя "
            "(добавь/удали/измени тайлы), верни полную новую спеку.\n"
            f"Текущая спека:\n{current_spec.model_dump_json()}\n"
        )
    else:
        mode_block = "Собери спеку с нуля по запросу.\n"
    return (
        _SPEC_PROMPT
        .replace("__MODE_BLOCK__", mode_block)
        .replace("__COLUMNS__", ", ".join(str(c) for c in df.columns[:60]))
        .replace("__REQUEST__", request or "")
    )


def build_spec_from_request(df: pd.DataFrame, request: str) -> DashboardSpec | None:
    """Детерминированная спека по ключевым словам запроса (без LLM)."""
    q = (request or "").lower()
    tabs: list[Tab] = []

    if _FUNNEL_HINT.search(q) and _has_pattern(df, "(сумма)"):
        tiles = [
            Tile(
                title="Сделки на текущем этапе — сумма",
                chart_type="hbar",
                source=TileSource(
                    kind="current_stage",
                    columns_pattern="(сумма)",
                    value_semantic="revenue",
                ),
                unit="auto",
                sort="none",
            ),
            Tile(
                title="Сделки на текущем этапе — количество",
                chart_type="hbar",
                source=TileSource(kind="current_stage", columns_pattern="(сумма)"),
                agg="count",
                sort="none",
            ),
        ]
        tabs.append(Tab(title="Воронка", tiles=tiles))

    from services.generic_dashboard import pick_groupers, pick_metrics

    metrics = pick_metrics(df)
    groupers = pick_groupers(df)
    overview: list[Tile] = []
    used: set[str] = set()
    want_pie = any(w in q for w in ("кругов", "пирог", "donut"))

    for word, needle in _GROUP_HINTS:
        if word not in q:
            continue
        col = next((c for c in df.columns if needle in str(c).lower()), None)
        if col is None or col in used:
            continue
        used.add(str(col))
        if metrics:
            overview.append(
                Tile(
                    title=f"{metrics[0]} по «{col}»",
                    chart_type="pie" if want_pie and len(overview) == 0 else "hbar",
                    source=TileSource(
                        kind="group",
                        group_column=col,
                        value_column=metrics[0],
                    ),
                    agg="sum",
                    top_n=10,
                    unit="auto",
                )
            )
        else:
            overview.append(
                Tile(
                    title=f"Количество по «{col}»",
                    chart_type="pie" if want_pie else "bar",
                    source=TileSource(kind="group", group_column=col),
                    agg="count",
                    top_n=10,
                )
            )

    if "инвест" in q:
        inv_col = next((c for c in df.columns if "инвест" in str(c).lower()), None)
        if inv_col and groupers:
            g = next((c for c in groupers if c != inv_col), groupers[0])
            overview.append(
                Tile(
                    title=f"{inv_col} по «{g}»",
                    chart_type="hbar",
                    source=TileSource(kind="group", group_column=g, value_column=inv_col),
                    agg="sum",
                    top_n=10,
                    unit="auto",
                )
            )

    if overview:
        tabs.append(Tab(title="Обзор", tiles=overview[:4]))

    if any(w in q for w in ("динамик", "месяц", "квартал")):
        from services import data_tools

        dates = data_tools.detect_date_columns(df).get("columns") or []
        if dates and metrics:
            tabs.append(
                Tab(
                    title="Динамика",
                    tiles=[
                        Tile(
                            title=f"Динамика «{metrics[0]}» по месяцам",
                            chart_type="area",
                            source=TileSource(
                                kind="period",
                                period="month",
                                value_column=metrics[0],
                            ),
                            unit="auto",
                            sort="none",
                        )
                    ],
                )
            )

    if not tabs:
        return None
    return DashboardSpec(tabs=tabs[:8])


def generate_spec_nl(
    df: pd.DataFrame,
    request: str,
    current_spec: DashboardSpec | None = None,
) -> DashboardSpec | None:
    """LLM -> JSON -> coerce -> pydantic. При любой ошибке разбора — None."""
    prompt = _build_spec_prompt(df, request, current_spec)
    try:
        raw = _get_spec_llm().invoke(prompt).content
    except Exception as exc:
        raise OllamaUnavailableError(f"Ollama недоступна: {exc}") from exc

    json_text = _extract_json(raw)
    if not json_text:
        logger.warning("LLM не вернула JSON спеки: %.200s", raw)
        return None
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        logger.warning("LLM вернула не-JSON спеку: %.200s", json_text)
        return None
    if not isinstance(data, dict):
        return None
    data = _coerce_spec_dict(data, df)
    try:
        return DashboardSpec.model_validate(data)
    except ValidationError as exc:
        logger.warning("LLM вернула невалидную спеку: %s", exc)
        return None


def assemble_spec(
    df: pd.DataFrame,
    request: str,
    current_spec: DashboardSpec | None = None,
    fallback: DashboardSpec | None = None,
) -> tuple[DashboardSpec | None, str | None]:
    """Собрать спеку: ключевые слова / LLM / профиль. warning — если это запасной вариант."""
    if current_spec is None and _FUNNEL_HINT.search(request or ""):
        keyword_spec = build_spec_from_request(df, request)
        if keyword_spec is not None:
            return keyword_spec, None

    spec = None
    try:
        spec = generate_spec_nl(df, request, current_spec)
    except OllamaUnavailableError:
        logger.warning("Ollama недоступна при сборке дашборда — используем запасной вариант")

    if spec is not None:
        return spec, None

    if current_spec is not None:
        return current_spec, "ИИ не смог применить правку — дашборд без изменений"

    keyword_spec = build_spec_from_request(df, request)
    if keyword_spec is not None:
        return keyword_spec, "ИИ не собрал спеку — дашборд собран по запросу автоматически"

    if fallback is not None:
        return fallback, "ИИ не собрал спеку — показан исходный дашборд"

    from services.generic_dashboard import build_generic_spec

    generic = build_generic_spec(df)
    if generic is not None:
        return generic, "ИИ не собрал спеку — собран универсальный дашборд"
    return None, None


# ---------------------------------------------------------------------------
# Авто-комментарии к вкладкам (кэш по хэшу данных)
# ---------------------------------------------------------------------------

_comments_cache: dict[str, dict] = {}


def _data_hash(df: pd.DataFrame) -> str:
    basis = f"{df.shape}|{','.join(map(str, df.columns))}"
    return hashlib.md5(basis.encode()).hexdigest()


def generate_comments(file_id: str, df: pd.DataFrame, rendered_tabs: list[dict]) -> dict:
    """Возвращает {tab_title: comment}. Кэш: (file_id, хэш данных)."""
    key = f"{file_id}:{_data_hash(df)}"
    if key in _comments_cache:
        return _comments_cache[key]

    blocks = []
    for tab in rendered_tabs:
        lines = [f"Вкладка «{tab['title']}»:"]
        for tile in tab.get("tiles", []):
            if "stats" not in tile:
                continue
            stats = tile["stats"]
            top = ", ".join(f"{n}={v:,.0f}" for n, v in stats["top"])
            lines.append(
                f"- {tile['title']}: всего {stats['total']:,.0f}, топ: {top}"
            )
        blocks.append("\n".join(lines))

    if not blocks:
        return {}

    prompt = (
        "Ты аналитик продаж. Ниже — статистика по вкладкам дашборда.\n"
        "Для КАЖДОЙ вкладки напиши 2–3 содержательных вывода на русском "
        "(динамика, лидеры, аномалии, на что обратить внимание).\n"
        "Формат ответа — строго JSON: {\"<имя вкладки>\": \"текст выводов\", ...}\n\n"
        + "\n\n".join(blocks)
    )
    try:
        raw = _get_comments_llm().invoke(prompt).content
    except Exception as exc:
        raise OllamaUnavailableError(f"Ollama недоступна: {exc}") from exc

    json_text = _extract_json(raw)
    comments: dict = {}
    if json_text:
        try:
            parsed = json.loads(json_text)
            comments = {str(k): str(v) for k, v in parsed.items()}
        except (json.JSONDecodeError, AttributeError):
            logger.warning("Комментарии LLM не распарсились: %.200s", raw)
    if not comments:
        comments = {"Общее": raw.strip()[:500]}

    if len(_comments_cache) > 50:
        _comments_cache.clear()
    _comments_cache[key] = comments
    return comments


_comments_llm: ChatOllama | None = None


def _get_comments_llm() -> ChatOllama:
    global _comments_llm
    if _comments_llm is None:
        _comments_llm = ChatOllama(
            model=MAIN_MODEL,
            base_url=OLLAMA_BASE_URL,
            temperature=0.3,
            reasoning=False,
            num_predict=800,
        )
    return _comments_llm
