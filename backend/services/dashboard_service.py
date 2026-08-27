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
from models.dashboard_spec import DashboardSpec, Tile
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

    _, profile = get_profile_for_df(df)
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
            num_predict=2000,
        )
    return _spec_llm


_SPEC_PROMPT = """Ты конфигуратор BI-дашборда. По просьбе пользователя собери JSON-спеку дашборда для Excel-таблицы.

Схема (строго):
{{"tabs": [{{"title": "Имя вкладки", "tiles": [{{"title": "...", "chart_type": "bar|hbar|pie|line|area", "source": {{...}}, "agg": "sum|mean|count", "top_n": 10, "unit": "auto|rub|k|mln|mlrd", "target_line": null, "sort": "desc|asc|none"}}]}}]}}

Варианты source:
- {{"kind": "group", "group_column": "<колонка>", "value_column": "<колонка>"}} — группировка по категории
- {{"kind": "period", "period": "month|quarter|year", "value_column": "<колонка>"}} — динамика по дате
- {{"kind": "columns_pattern", "columns_pattern": "(сумма)"}} — агрегат по КАЖДОЙ колонке с таким окончанием (воронка этапов)

Правила:
- Используй ТОЛЬКО колонки из списка ниже, имена копируй посимвольно.
- value_column нужен для agg sum/mean; для agg=count его можно опустить.
- 1–4 вкладки, 1–4 тайла на вкладку.
{mode_block}
Колонки таблицы: {columns}

Запрос пользователя: {request}

Ответ — ТОЛЬКО валидный JSON без markdown и пояснений."""


def _extract_json(text: str) -> str | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group(0) if match else None


def generate_spec_nl(
    df: pd.DataFrame,
    request: str,
    current_spec: DashboardSpec | None = None,
) -> DashboardSpec | None:
    """LLM -> JSON -> pydantic-валидация. При любой ошибке — None (fallback)."""
    if current_spec is not None:
        mode_block = (
            "Это РЕДАКТИРОВАНИЕ: возьми текущую спеку ниже и примени правку пользователя "
            "(добавь/удали/измени тайлы), верни полную новую спеку.\n"
            f"Текущая спека:\n{current_spec.model_dump_json()}\n"
        )
    else:
        mode_block = "Собери спеку с нуля по запросу.\n"

    prompt = _SPEC_PROMPT.format(
        mode_block=mode_block,
        columns=", ".join(df.columns[:60]),
        request=request,
    )
    try:
        raw = _get_spec_llm().invoke(prompt).content
    except Exception as exc:
        raise OllamaUnavailableError(f"Ollama недоступна: {exc}") from exc

    json_text = _extract_json(raw)
    if not json_text:
        logger.warning("LLM не вернула JSON спеки: %.200s", raw)
        return None
    try:
        return DashboardSpec.model_validate_json(json_text)
    except ValidationError as exc:
        logger.warning("LLM вернула невалидную спеку: %s", exc)
        return None


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
