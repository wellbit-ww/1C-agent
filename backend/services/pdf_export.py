"""Сборка PDF-отчёта из той же структуры, что страница отчёта и Markdown."""
from __future__ import annotations

import io
import logging
import re
from datetime import datetime
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

logger = logging.getLogger(__name__)

_REPORT_TYPE_NAMES = {
    "sales_pipeline": "Этапы продаж",
    "deficit_report": "Дефицит / задолженность",
    "pdo_report": "Отчёт ПДО",
    "warranty": "Гарантия",
    "sales_forecast": "Прогноз продаж",
    "supplier_orders": "Заказы поставщикам",
    "planned_receipts": "Планируемые поступления",
    "incoming_requests": "Входящие запросы",
}

_FONT_REGULAR = [
    Path(r"C:\Windows\Fonts\arial.ttf"),
    Path(r"C:\Windows\Fonts\segoeui.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
]
_FONT_BOLD = [
    Path(r"C:\Windows\Fonts\arialbd.ttf"),
    Path(r"C:\Windows\Fonts\segoeuib.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
]


class PdfExportError(RuntimeError):
    pass


def report_type_name(report_type: str) -> str:
    return _REPORT_TYPE_NAMES.get(report_type or "", "Универсальный отчёт")


def _first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.is_file():
            return path
    return None


def _plain(text: str) -> str:
    if not text:
        return ""
    cleaned = str(text).replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"\*\*(.+?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"^#+\s*", "", cleaned, flags=re.M)
    cleaned = cleaned.replace("`", "")
    return cleaned.strip()


def _soft_wrap(text: str, chunk: int = 42) -> str:
    """fpdf не переносит слово длиннее ширины страницы — режем сами."""
    pieces: list[str] = []
    for raw in (text or "").split(" "):
        word = raw
        while len(word) > chunk:
            pieces.append(word[:chunk])
            word = word[chunk:]
        pieces.append(word)
    return " ".join(pieces)


def _insights_text(insights) -> str:
    if insights is None:
        return ""
    if isinstance(insights, str):
        return insights.strip()
    lines = [str(item).strip() for item in insights if str(item).strip()]
    return "\n".join(f"• {line.lstrip('•').strip()}" for line in lines)


class _ReportPdf(FPDF):
    def __init__(self, subtitle: str):
        super().__init__(format="A4")
        self.subtitle = subtitle
        self.set_auto_page_break(auto=True, margin=18)

    def header(self) -> None:
        if self.page_no() == 1:
            return
        self.set_font("Report", size=8)
        self.set_text_color(100, 100, 100)
        self.cell(0, 8, self.subtitle, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_draw_color(200, 200, 200)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(4)
        self.set_text_color(0, 0, 0)

    def footer(self) -> None:
        self.set_y(-14)
        self.set_font("Report", size=8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 8, f"Стр. {self.page_no()}/{{nb}}", align="C")
        self.set_text_color(0, 0, 0)


def _heading(pdf: _ReportPdf, title: str) -> None:
    pdf.ln(3)
    pdf.set_font("Report", style="B", size=13)
    pdf.set_text_color(20, 55, 110)
    pdf.cell(0, 8, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_draw_color(20, 55, 110)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(3)
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Report", size=10)


def _body(pdf: _ReportPdf, text: str) -> None:
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Report", size=10)
    pdf.multi_cell(0, 5.5, _soft_wrap(_plain(text) or "—"))
    pdf.ln(1)


def _need_page(pdf: _ReportPdf, extra_h: float) -> None:
    if pdf.get_y() + extra_h > pdf.h - pdf.b_margin - 6:
        pdf.add_page()


def _png_size_mm(png: bytes, width_mm: float) -> tuple[float, float]:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(png)) as image:
            w_px, h_px = image.size
        if w_px <= 0:
            return width_mm, width_mm * 0.5
        return width_mm, width_mm * h_px / w_px
    except Exception:
        return width_mm, width_mm * 0.5


def _mpl_png(plotly_json: str) -> bytes | None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import plotly.io as pio
    except Exception:
        return None
    plt.rcParams["font.sans-serif"] = ["Arial", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    try:
        fig = pio.from_json(plotly_json)
        traces = list(fig.data or [])
        if not traces:
            return None
        mpl, ax = plt.subplots(figsize=(8.2, 3.8), dpi=120)
        trace = traces[0]
        kind = getattr(trace, "type", "") or ""
        if kind == "pie":
            labels = [str(x) for x in (trace.labels or [])]
            values = [float(v) for v in (trace.values or [])]
            ax.pie(values, labels=labels[:8], autopct="%1.0f%%")
        elif kind == "bar" and getattr(trace, "orientation", "v") == "h":
            labels = [str(y) for y in (trace.y or [])]
            values = [float(x) for x in (trace.x or [])]
            ax.barh(labels, values)
        elif kind == "bar":
            labels = [str(x) for x in (trace.x or [])]
            values = [float(y) for y in (trace.y or [])]
            ax.bar(labels, values)
            if len(labels) > 4:
                plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
        else:
            xs = list(trace.x or [])
            ys = [float(y) for y in (trace.y or [])]
            if getattr(trace, "fill", None):
                ax.fill_between(range(len(ys)), ys, alpha=0.35)
                ax.plot(range(len(ys)), ys)
                ax.set_xticks(range(len(xs)))
                ax.set_xticklabels([str(x) for x in xs], rotation=30, ha="right")
            else:
                ax.plot(xs, ys, marker="o")
                if len(xs) > 4:
                    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
        ax.grid(axis="y", linestyle=":", alpha=0.4)
        mpl.tight_layout()
        buf = io.BytesIO()
        mpl.savefig(buf, format="png")
        plt.close(mpl)
        return buf.getvalue()
    except Exception as exc:
        logger.warning("matplotlib-график не собрался: %s", exc)
        try:
            plt.close("all")
        except Exception:
            pass
        return None


def chart_to_png(item: dict) -> bytes | None:
    if item.get("png"):
        raw = item["png"]
        return raw if isinstance(raw, (bytes, bytearray)) else None
    plotly_json = item.get("plotly_json")
    if not plotly_json:
        return None
    try:
        import plotly.io as pio

        fig = pio.from_json(plotly_json)
        fig.update_layout(
            template="plotly_white",
            title=None,
            width=920,
            height=420,
            margin=dict(l=56, r=28, t=20, b=72),
            font=dict(family="Arial", size=13),
        )
        return fig.to_image(format="png", scale=1.3)
    except Exception as exc:
        logger.warning("kaleido не собрал график, запасной путь: %s", exc)
        return _mpl_png(str(plotly_json))


def _tile_caption(item: dict) -> str:
    stats = item.get("stats") or {}
    top = stats.get("top") or []
    if not top:
        return ""
    bits = []
    for name, value in top[:3]:
        try:
            bits.append(f"{name} ({value:,.0f})".replace(",", " "))
        except (TypeError, ValueError):
            bits.append(str(name))
    return "Лидеры: " + "; ".join(bits)


def _embed_tiles(pdf: _ReportPdf, tiles: list[dict]) -> None:
    ready = [t for t in tiles if isinstance(t, dict)][:8]
    i = 0
    while i < len(ready):
        pair = ready[i : i + 2]
        usable = pdf.epw
        gap = 4
        col_w = usable if len(pair) == 1 else (usable - gap) / 2
        pngs = [chart_to_png(tile) for tile in pair]
        heights = []
        for png in pngs:
            if png:
                _, h = _png_size_mm(png, col_w)
                heights.append(min(h, 78))
            else:
                heights.append(18)
        block_h = max(heights) + 16
        _need_page(pdf, block_h)
        y0 = pdf.get_y()
        max_bottom = y0
        for col, tile in enumerate(pair):
            x = pdf.l_margin + col * (col_w + gap)
            pdf.set_xy(x, y0)
            pdf.set_font("Report", style="B", size=10)
            pdf.set_text_color(20, 55, 110)
            pdf.multi_cell(col_w, 5, _soft_wrap(str(tile.get("title") or "График"), 36))
            pdf.set_text_color(0, 0, 0)
            y_img = pdf.get_y() + 1
            png = pngs[col]
            if tile.get("error") and not png:
                pdf.set_xy(x, y_img)
                pdf.set_font("Report", size=9)
                pdf.multi_cell(col_w, 4.5, _soft_wrap(f"Не построен: {tile['error']}", 36))
                max_bottom = max(max_bottom, pdf.get_y())
                continue
            if png:
                w_mm, h_mm = _png_size_mm(png, col_w)
                h_mm = min(h_mm, 78)
                pdf.image(io.BytesIO(png), x=x, y=y_img, w=col_w, h=h_mm)
                bottom = y_img + h_mm
                caption = _tile_caption(tile)
                if caption:
                    pdf.set_xy(x, bottom + 1)
                    pdf.set_font("Report", size=8)
                    pdf.set_text_color(80, 80, 80)
                    pdf.multi_cell(col_w, 4, _soft_wrap(caption, 40))
                    pdf.set_text_color(0, 0, 0)
                    bottom = pdf.get_y()
                max_bottom = max(max_bottom, bottom)
            else:
                pdf.set_xy(x, y_img)
                pdf.set_font("Report", size=9)
                pdf.multi_cell(col_w, 4.5, "График не удалось встроить.")
                max_bottom = max(max_bottom, pdf.get_y())
        pdf.set_y(max_bottom + 4)
        i += 2


def _tabs_from_report(report: dict, dashboard_tabs: list | None) -> list[dict]:
    if dashboard_tabs:
        return [t for t in dashboard_tabs if isinstance(t, dict)]
    charts = [
        c
        for c in (report.get("charts") or [])
        if isinstance(c, dict) and (c.get("plotly_json") or c.get("png"))
    ]
    if charts:
        return [{"title": "Графики", "tiles": charts}]
    return []


def _embed_dashboard(
    pdf: _ReportPdf,
    report: dict,
    dashboard_tabs: list | None,
    comments: dict | None,
) -> None:
    tabs = _tabs_from_report(report, dashboard_tabs)
    if not tabs:
        titles = [
            str(c.get("title") or "").strip()
            for c in (report.get("charts") or [])
            if isinstance(c, dict) and str(c.get("title") or "").strip()
        ]
        if titles:
            _heading(pdf, "Графики отчёта")
            _body(pdf, "\n".join(f"• {title}" for title in titles[:12]))
        return

    _heading(pdf, "Дашборд")
    notes = comments or {}
    for tab in tabs[:8]:
        title = str(tab.get("title") or "Вкладка").strip() or "Вкладка"
        pdf.set_font("Report", style="B", size=11)
        pdf.set_text_color(20, 55, 110)
        pdf.multi_cell(0, 6, _soft_wrap(f"Вкладка: {title}", 48))
        pdf.set_text_color(0, 0, 0)
        comment = notes.get(title)
        if comment:
            pdf.set_font("Report", size=9)
            _body(pdf, str(comment))
        tiles = [t for t in (tab.get("tiles") or []) if isinstance(t, dict)]
        if not tiles:
            _body(pdf, "На вкладке нет графиков.")
            continue
        _embed_tiles(pdf, tiles)


def load_dashboard_tabs(file_id: str, df) -> list[dict]:
    """Текущая спека дашборда, уже посчитанная pandas → plotly."""
    from services import dashboard_service
    from services.dashboard_engine import render_spec

    spec = dashboard_service.get_current_spec(file_id, df)
    if spec is None:
        return []
    return list(render_spec(df, spec).get("tabs") or [])


def render_report_pdf(
    report: dict,
    *,
    narrative: str | None = None,
    insights: str | list | None = None,
    comment: str | None = None,
    dashboard_tabs: list | None = None,
    dashboard_comments: dict | None = None,
    generated_at: datetime | None = None,
    compress: bool = True,
) -> bytes:
    """Собрать PDF. Правки резюме/выводов/комментария перекрывают поля отчёта."""
    regular = _first_existing(_FONT_REGULAR)
    if regular is None:
        raise PdfExportError(
            "Нет шрифта с кириллицей (Arial / DejaVu / Liberation). "
            "PDF отчёта недоступен на этой машине."
        )
    bold = _first_existing(_FONT_BOLD) or regular
    when = generated_at or datetime.now()
    metadata = report.get("metadata") or {}
    filename = metadata.get("filename") or "без имени"
    type_name = report_type_name(report.get("report_type", ""))
    subtitle = f"Аналитический отчёт · {filename}"

    pdf = _ReportPdf(subtitle)
    pdf.compress = compress
    pdf.alias_nb_pages()
    pdf.add_font("Report", style="", fname=str(regular))
    pdf.add_font("Report", style="B", fname=str(bold))
    pdf.set_margins(16, 16, 16)
    pdf.add_page()

    pdf.set_font("Report", style="B", size=18)
    pdf.set_text_color(20, 55, 110)
    pdf.multi_cell(0, 9, _soft_wrap(f"Аналитический отчёт: {filename}", 48))
    pdf.set_text_color(80, 80, 80)
    pdf.set_font("Report", size=9)
    pdf.cell(
        0,
        6,
        f"Сформирован: {when:%d.%m.%Y %H:%M}  ·  {type_name}",
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Report", size=10)
    pdf.ln(2)
    pdf.multi_cell(
        0,
        5.5,
        f"Строк: {metadata.get('rows', 0)}  ·  "
        f"Колонок: {metadata.get('columns', 0)}  ·  "
        f"Период: {metadata.get('period', 'Не определен')}",
    )

    _heading(pdf, "Резюме")
    summary = narrative if narrative is not None else (report.get("narrative") or report.get("summary") or "")
    _body(pdf, summary)

    kpis = list(report.get("kpis") or [])
    if kpis:
        _heading(pdf, "Ключевые показатели")
        pdf.set_font("Report", size=10)
        for kpi in kpis:
            label = _soft_wrap(str(kpi.get("label") or ""), 32)
            value = _soft_wrap(str(kpi.get("value") or ""), 28)
            pdf.set_x(pdf.l_margin)
            pdf.set_font("Report", style="B", size=10)
            pdf.cell(70, 6, label[:50])
            pdf.set_font("Report", size=10)
            pdf.multi_cell(0, 6, value)

    _embed_dashboard(pdf, report, dashboard_tabs, dashboard_comments)

    insight_block = _insights_text(
        insights if insights is not None else report.get("insights")
    )
    _heading(pdf, "Выводы")
    _body(pdf, insight_block)

    comment_text = comment if comment is not None else ""
    if _plain(comment_text):
        _heading(pdf, "Комментарий аналитика")
        _body(pdf, comment_text)

    quality = report.get("data_quality") or {}
    _heading(pdf, "Качество данных")
    _body(
        pdf,
        f"Всего ячеек: {quality.get('total_cells', 0)}\n"
        f"Пропусков: {quality.get('null_cells', 0)} ({quality.get('null_pct', 0)}%)\n"
        f"Дубликатов строк: {quality.get('duplicates', 0)}",
    )
    for worst in quality.get("worst_columns") or []:
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(
            0,
            5.5,
            _soft_wrap(
                f"Колонка «{worst.get('column')}»: {worst.get('nulls')} "
                f"пропусков ({worst.get('pct')}%)"
            ),
        )

    columns = list(report.get("columns_overview") or [])[:30]
    if columns:
        _heading(pdf, "Структура данных")
        pdf.set_font("Report", size=8)
        for col in columns:
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(
                0,
                4.5,
                _soft_wrap(
                    f"{col.get('column')} — {col.get('type')}; "
                    f"заполнено {col.get('filled')}; "
                    f"уникальных {col.get('unique')}; {col.get('stats') or '—'}"
                ),
            )

    out = pdf.output()
    return bytes(out)


import hashlib
import threading

_pdf_lock = threading.Lock()
_pdf_cache: dict[str, bytes] = {}
_PDF_CACHE_LIMIT = 8


def cached_report_pdf(
    file_id: str,
    df,
    *,
    filename: str | None,
    narrative: str | None,
    insights: str | None,
    comment: str | None,
) -> bytes:
    """PDF с кэшем по file_id + data_hash + спека + правки текста."""
    from services import db_service
    from services.file_context_service import data_hash
    from services.report_service import get_full_report

    spec = db_service.get_dashboard_spec(file_id) or ""
    key_src = "\0".join(
        [
            file_id,
            data_hash(df),
            spec,
            filename or "",
            narrative or "",
            insights or "",
            comment or "",
        ]
    )
    key = hashlib.sha256(key_src.encode("utf-8", "replace")).hexdigest()
    with _pdf_lock:
        hit = _pdf_cache.get(key)
    if hit is not None:
        return hit

    report = get_full_report(df, filename)
    pdf_bytes = render_report_pdf(
        report,
        narrative=narrative,
        insights=insights,
        comment=comment,
        dashboard_tabs=load_dashboard_tabs(file_id, df),
    )
    with _pdf_lock:
        if key not in _pdf_cache:
            while len(_pdf_cache) >= _PDF_CACHE_LIMIT:
                _pdf_cache.pop(next(iter(_pdf_cache)))
            _pdf_cache[key] = pdf_bytes
    return pdf_bytes
