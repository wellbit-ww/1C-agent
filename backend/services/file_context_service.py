"""Брифинг файла: снимок таблицы → LLM JSON → FileContext в SQLite.

LLM не получает весь Excel — только схему, статистики и 3 строки-образца.
Если Ollama недоступна, сохраняется детерминированная карточка (колонки,
метрики, группировки) — чат и дашборд всё равно имеют контекст.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re

import pandas as pd
from langchain_ollama import ChatOllama
from pydantic import ValidationError

from config import MAIN_MODEL
from models.file_context import ColumnNote, FileContext, SheetBrief
from services import db_service
from services.exceptions import OllamaUnavailableError
from services.llm_service import make_chat_ollama

logger = logging.getLogger(__name__)

_CELL = 48
_MAX_COLS = 24

_PROMPT = """Ты аналитик выгрузок 1С. По снимку таблицы собери JSON-карточку понимания файла.

Схема (строго):
{"title":"короткое имя отчёта","summary":"2–4 предложения: что за файл, что в строке, какие цифры главные","grain":"одна строка = …","report_kind":"человеческий тип","metrics":["точные имена колонок-метрик"],"groupers":["точные имена категориальных колонок"],"date_columns":["колонки дат"],"caveats":["ограничения данных"],"dashboard_ideas":["3–5 запросов дашборда на русском"]}

Правила:
- Имена в metrics/groupers/date_columns копируй ПОСИМВОЛЬНО из снимка. Не выдумывай колонки.
- metrics — суммы, долги, количества, проценты. Не id и не «номер».
- groupers — клиент, менеджер, статус, подразделение, поставщик и т.п.
- Если в снимке несколько листов — назови каждый в summary. metrics/groupers бери только с листа active=true.
- Не выдумывай листы и колонки, которых нет в снимке.
- summary на русском, без markdown.

Снимок:
__SNAPSHOT__

Ответ — ТОЛЬКО JSON без markdown."""


_brief_llm: ChatOllama | None = None


def _get_brief_llm() -> ChatOllama:
    global _brief_llm
    if _brief_llm is None:
        _brief_llm = make_chat_ollama(model=MAIN_MODEL, num_predict=1800)
    return _brief_llm


def data_hash(df: pd.DataFrame) -> str:
    """Хэш схемы и содержимого: смена значений в ячейках инвалидирует карточку."""
    hasher = hashlib.md5()
    hasher.update(str(tuple(df.shape)).encode())
    hasher.update("\0".join(map(str, df.columns)).encode("utf-8", "replace"))
    hasher.update("\0".join(str(t) for t in df.dtypes).encode())
    if len(df):
        positions = list(dict.fromkeys([0, len(df) // 2, len(df) - 1]))
        sample = df.iloc[positions]
        hasher.update(sample.to_csv(index=False).encode("utf-8", "replace"))
    return hasher.hexdigest()


def _cell(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).replace("\n", " ").strip()
    return text[:_CELL] + ("…" if len(text) > _CELL else "")


def _num(value) -> float | None:
    try:
        if pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def catalog_sheets(workbook: dict[str, pd.DataFrame]) -> list[dict]:
    """Компактные карточки всех непустых листов. Первый — рабочий (дашборд/чат)."""
    cards: list[dict] = []
    for index, (name, frame) in enumerate(list(workbook.items())[:8]):
        columns = [str(c) for c in frame.columns[:24]]
        card: dict = {
            "name": str(name),
            "rows": int(len(frame)),
            "n_columns": int(len(frame.columns)),
            "columns": columns,
            "active": index == 0,
        }
        if index > 0:
            keep = set(columns[:10])
            card["sample"] = [
                {str(k): _cell(v) for k, v in row.items() if str(k) in keep}
                for row in frame.head(2).to_dict(orient="records")
            ]
        cards.append(card)
    return cards


def _sheet_cards(
    df: pd.DataFrame,
    filename: str | None,
    workbook: dict[str, pd.DataFrame] | None,
    saved_sheets: list | None,
) -> list[dict]:
    if workbook:
        return catalog_sheets(workbook)
    if saved_sheets:
        cards = []
        for raw in saved_sheets:
            if hasattr(raw, "model_dump"):
                cards.append(raw.model_dump())
            elif isinstance(raw, dict):
                cards.append(raw)
        if cards:
            return cards
    return [{
        "name": filename or "лист1",
        "rows": int(len(df)),
        "n_columns": int(len(df.columns)),
        "columns": [str(c) for c in df.columns[:24]],
        "active": True,
    }]


def compute_facts(
    df: pd.DataFrame,
    metrics: list[str],
    groupers: list[str],
) -> list[str]:
    """Короткие цифры pandas для карточки и чата. LLM их не считает."""
    from services.insights_service import _format_number

    facts = [f"Строк: {len(df)}", f"Колонок: {len(df.columns)}"]
    if len(df):
        empty = round(float(df.isna().mean().mean()) * 100, 1)
        facts.append(f"Пустых ячеек: {empty}%")
    known = {str(c) for c in df.columns}
    for name in metrics[:2]:
        if name not in known:
            continue
        total = pd.to_numeric(df[name], errors="coerce").sum()
        facts.append(f"Сумма «{name}»: {_format_number(float(total))}")
    for name in groupers[:2]:
        if name not in known:
            continue
        top = df[name].dropna().astype(str).value_counts().head(3)
        if top.empty:
            continue
        listing = ", ".join(f"{k} ({int(v)})" for k, v in top.items())
        facts.append(f"Топ «{name}»: {listing}")
    return facts[:8]


def build_column_notes(
    df: pd.DataFrame,
    metrics: list[str],
    groupers: list[str],
    dates: list[str],
) -> list[ColumnNote]:
    notes: list[ColumnNote] = []
    metric_set = set(metrics)
    grouper_set = set(groupers)
    date_set = set(dates)
    for col in list(df.columns)[:20]:
        name = str(col)
        lower = name.lower()
        if name in metric_set:
            role, meaning = "metric", "числовая метрика"
        elif str(col).endswith("(сумма)"):
            role, meaning = "stage", "этап воронки"
        elif name in date_set or "дата" in lower:
            role, meaning = "date", "дата"
        elif name in grouper_set:
            role, meaning = "grouper", "разрез"
        elif "номер" in lower or lower in {"№", "n", "код"}:
            role, meaning = "id", "идентификатор"
        else:
            continue
        notes.append(ColumnNote(name=name, role=role, meaning=meaning))
    return notes[:12]


def build_snapshot(
    df: pd.DataFrame,
    filename: str | None = None,
    report_type: str | None = None,
    workbook: dict[str, pd.DataFrame] | None = None,
    saved_sheets: list | None = None,
) -> dict:
    from services import data_tools
    from services.generic_dashboard import pick_groupers, pick_metrics
    from services.report_detector import detect_report_type

    report_type = report_type or detect_report_type(df, filename=filename)
    metrics_guess = [str(c) for c in pick_metrics(df)]
    groupers_guess = [str(c) for c in pick_groupers(df)]
    dates_guess = [str(c) for c in (data_tools.detect_date_columns(df).get("columns") or [])]

    columns = []
    for col in list(df.columns)[:_MAX_COLS]:
        series = df[col]
        entry: dict = {
            "name": str(col),
            "null_pct": round(float(series.isna().mean()) * 100, 1),
        }
        if pd.api.types.is_numeric_dtype(series):
            nums = pd.to_numeric(series, errors="coerce")
            entry["sum"] = _num(nums.sum())
        else:
            top = series.dropna().astype(str).value_counts().head(2)
            entry["examples"] = [_cell(v) for v in top.index]
        columns.append(entry)

    sample = [
        {str(k): _cell(v) for k, v in row.items()}
        for row in df.head(3).to_dict(orient="records")
    ]
    slim_sample = []
    keep = {c["name"] for c in columns[:12]}
    for row in sample:
        slim_sample.append({k: v for k, v in row.items() if k in keep})

    sheet_cards = _sheet_cards(df, filename, workbook, saved_sheets)
    active = next((s for s in sheet_cards if s.get("active")), sheet_cards[0])
    facts = compute_facts(df, metrics_guess, groupers_guess)
    return {
        "filename": filename or "",
        "sheets": sheet_cards,
        "active_sheet": active.get("name", ""),
        "facts": facts,
        "rows": int(len(df)),
        "n_columns": int(len(df.columns)),
        "report_type": report_type,
        "metrics_guess": metrics_guess,
        "groupers_guess": groupers_guess,
        "dates_guess": dates_guess,
        "has_stage_funnel": any(str(c).endswith("(сумма)") for c in df.columns),
        "columns": columns,
        "sample": slim_sample,
    }


def _existing_names(df: pd.DataFrame) -> set[str]:
    return {str(c) for c in df.columns}


def _filter_names(names: list, known: set[str]) -> list[str]:
    out: list[str] = []
    lowered = {n.lower(): n for n in known}
    for raw in names or []:
        name = str(raw).strip()
        if name in known:
            if name not in out:
                out.append(name)
            continue
        match = lowered.get(name.lower())
        if match and match not in out:
            out.append(match)
    return out


def deterministic_context(
    df: pd.DataFrame,
    filename: str | None = None,
    report_type: str | None = None,
    workbook: dict[str, pd.DataFrame] | None = None,
    saved_sheets: list | None = None,
) -> FileContext:
    snap = build_snapshot(
        df,
        filename=filename,
        report_type=report_type,
        workbook=workbook,
        saved_sheets=saved_sheets,
    )
    kind_map = {
        "sales_pipeline": "Этапы продаж (воронка сделок)",
        "deficit_report": "Дефицит / задолженность",
        "pdo_report": "Отчёт ПДО",
        "warranty": "Гарантия",
        "sales_forecast": "Прогноз продаж",
        "supplier_orders": "Заказы поставщикам",
        "planned_receipts": "Планируемые поступления",
        "incoming_requests": "Входящие запросы",
    }
    kind = kind_map.get(snap["report_type"], "Универсальный отчёт 1С")
    grain = "одна строка = запись выгрузки"
    if snap["has_stage_funnel"]:
        grain = "одна строка = сделка на текущем этапе воронки"
    metrics = snap["metrics_guess"][:6]
    groupers = snap["groupers_guess"][:6]
    ideas = []
    if metrics and groupers:
        ideas.append(f"{metrics[0]} по «{groupers[0]}»")
    if groupers:
        ideas.append(f"Круговая по «{groupers[0]}»")
    if snap["dates_guess"] and metrics:
        ideas.append(f"Динамика «{metrics[0]}» по месяцам")
    if snap["has_stage_funnel"]:
        ideas.append("Собери дашборд с воронкой")
    fname = snap["filename"] or "выгрузка"
    summary = (
        f"Файл «{fname}»: {snap['rows']} строк, {snap['n_columns']} колонок, тип «{kind}». "
        f"{grain.capitalize()}."
    )
    if metrics:
        summary += f" Главные метрики: {', '.join(metrics[:3])}."
    if groupers:
        summary += f" Разрезы: {', '.join(groupers[:3])}."
    sheet_models = [SheetBrief.model_validate(card) for card in snap["sheets"]]
    extra = [s.name for s in sheet_models if not s.active]
    caveats: list[str] = []
    if extra:
        summary += (
            f" В книге {len(sheet_models)} листа: "
            + ", ".join(f"«{s.name}»" for s in sheet_models)
            + f". Дашборд и чат считают по «{snap['active_sheet']}»."
        )
        caveats.append(
            "Остальные листы есть в брифинге, но цифры дашборда считаются "
            f"по листу «{snap['active_sheet']}»: " + ", ".join(f"«{n}»" for n in extra)
        )
    facts = list(snap.get("facts") or [])
    notes = build_column_notes(
        df,
        metrics,
        groupers,
        snap["dates_guess"][:4],
    )
    return FileContext(
        title=fname,
        summary=summary,
        grain=grain,
        report_kind=kind,
        metrics=metrics,
        groupers=groupers,
        date_columns=snap["dates_guess"][:4],
        caveats=caveats,
        dashboard_ideas=ideas[:5],
        sheets=sheet_models,
        active_sheet=str(snap.get("active_sheet") or ""),
        facts=facts,
        column_notes=notes,
        llm_ready=False,
    )


def _extract_json_obj(text: str) -> dict | None:
    if not text:
        return None
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    start = cleaned.find("{")
    if start < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(cleaned[start:])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned[start:], re.DOTALL)
        if not match:
            return None
        try:
            obj = json.loads(match.group(0))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None


def _from_llm_dict(data: dict, df: pd.DataFrame, fallback: FileContext) -> FileContext:
    known = _existing_names(df)
    try:
        ctx = FileContext.model_validate(data)
    except ValidationError:
        return fallback
    ctx.metrics = _filter_names(ctx.metrics, known) or fallback.metrics
    ctx.groupers = _filter_names(ctx.groupers, known) or fallback.groupers
    ctx.date_columns = _filter_names(ctx.date_columns, known) or fallback.date_columns
    ctx.summary = (ctx.summary or fallback.summary).strip()
    ctx.title = (ctx.title or fallback.title).strip()
    ctx.grain = (ctx.grain or fallback.grain).strip()
    ctx.report_kind = (ctx.report_kind or fallback.report_kind).strip()
    ctx.caveats = [str(x)[:200] for x in (ctx.caveats or [])[:6]]
    ctx.dashboard_ideas = [str(x)[:120] for x in (ctx.dashboard_ideas or [])[:6]] or fallback.dashboard_ideas
    ctx.sheets = fallback.sheets
    ctx.active_sheet = fallback.active_sheet
    ctx.facts = fallback.facts or ctx.facts
    ctx.column_notes = fallback.column_notes or ctx.column_notes
    if fallback.caveats and not any(
        "лист" in str(c).lower() for c in ctx.caveats
    ):
        ctx.caveats = (ctx.caveats + fallback.caveats)[:6]
    ctx.llm_ready = True
    return ctx


def enrich_with_llm(
    df: pd.DataFrame,
    fallback: FileContext,
    filename: str | None = None,
    workbook: dict[str, pd.DataFrame] | None = None,
) -> FileContext:
    snap = build_snapshot(
        df,
        filename=filename,
        workbook=workbook,
        saved_sheets=[s.model_dump() for s in fallback.sheets] if fallback.sheets else None,
    )
    payload = json.dumps(snap, ensure_ascii=False, default=str)
    prompt = _PROMPT.replace("__SNAPSHOT__", payload[:12000])
    try:
        llm = _get_brief_llm()
        raw = llm.invoke(prompt).content
    except Exception as exc:
        raise OllamaUnavailableError(f"Ollama недоступна: {exc}") from exc
    parsed = _extract_json_obj(raw)
    if not parsed:
        logger.warning("Брифинг LLM не JSON, повтор: %.200s", raw)
        retry = prompt + "\nПовтори ответ: только один JSON-объект, без markdown и без текста вокруг."
        try:
            raw = llm.invoke(retry).content
        except Exception as exc:
            raise OllamaUnavailableError(f"Ollama недоступна: {exc}") from exc
        parsed = _extract_json_obj(raw)
    if not parsed:
        logger.warning("Брифинг LLM не JSON: %.200s", raw)
        return fallback
    return _from_llm_dict(parsed, df, fallback)


def get_context(file_id: str) -> FileContext | None:
    row = db_service.get_file_context(file_id)
    if not row:
        return None
    try:
        return FileContext.model_validate_json(row["context_json"])
    except ValidationError:
        logger.warning("Карточка файла %s повреждена", file_id)
        return None


def ensure_context(
    file_id: str,
    df: pd.DataFrame,
    filename: str | None = None,
    *,
    use_llm: bool = False,
    workbook: dict[str, pd.DataFrame] | None = None,
) -> FileContext:
    """Вернуть карточку файла; при use_llm=True дополнить разбором модели."""
    digest = data_hash(df)
    saved = db_service.get_file_context(file_id)
    saved_ctx: FileContext | None = None
    if saved and saved.get("data_hash") == digest:
        try:
            saved_ctx = FileContext.model_validate_json(saved["context_json"])
        except ValidationError:
            saved_ctx = None
        if saved_ctx is not None and (saved_ctx.llm_ready or not use_llm):
            return saved_ctx

    saved_sheets = list(saved_ctx.sheets) if saved_ctx and saved_ctx.sheets else None
    ctx = deterministic_context(
        df,
        filename=filename,
        workbook=workbook,
        saved_sheets=saved_sheets,
    )
    if use_llm:
        try:
            ctx = enrich_with_llm(df, ctx, filename=filename, workbook=workbook)
        except OllamaUnavailableError as exc:
            logger.warning("Брифинг без LLM: %s", exc)

    db_service.save_file_context(file_id, digest, ctx.model_dump_json())
    return ctx
