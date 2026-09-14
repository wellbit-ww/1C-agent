"""Карточка понимания файла: что в выгрузке и как с ней работать.

Считается один раз (детерминированно + LLM) и живёт в SQLite рядом с file_id.
Чат и генерация дашборда читают её как контекст — LLM не перечитывает Excel.
"""
from pydantic import BaseModel, Field


class ColumnNote(BaseModel):
    name: str
    role: str = "text"
    meaning: str = ""


class SheetBrief(BaseModel):
    name: str
    rows: int = 0
    n_columns: int = 0
    columns: list[str] = Field(default_factory=list)
    active: bool = False
    sample: list[dict] = Field(default_factory=list)
    role: str = "data"
    role_label: str = ""
    facts: list[str] = Field(default_factory=list)
    grain_note: str = ""


class FileContext(BaseModel):
    title: str = ""
    summary: str = ""
    grain: str = ""
    report_kind: str = ""
    metrics: list[str] = Field(default_factory=list)
    groupers: list[str] = Field(default_factory=list)
    date_columns: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    dashboard_ideas: list[str] = Field(default_factory=list)
    sheets: list[SheetBrief] = Field(default_factory=list)
    active_sheet: str = ""
    facts: list[str] = Field(default_factory=list)
    column_notes: list[ColumnNote] = Field(default_factory=list)
    entity_hints: list[str] = Field(default_factory=list)
    llm_ready: bool = False

    def prompt_block(self) -> str:
        """Компактный текст для системного контекста LLM."""
        if not self.summary and not self.metrics:
            return ""
        lines = ["Понимание этого файла (используй только эти колонки):"]
        if self.title:
            lines.append(f"Название: {self.title}")
        if self.report_kind:
            lines.append(f"Тип: {self.report_kind}")
        if self.grain:
            lines.append(f"Зерно: {self.grain}")
        if self.summary:
            lines.append(self.summary)
        if len(self.sheets) > 1:
            lines.append("Листы книги:")
            for sheet in self.sheets[:8]:
                mark = " — рабочий лист дашборда и чата" if sheet.active else ""
                role = sheet.role_label or sheet.role
                role_bit = f" [{role}]" if role else ""
                cols = ", ".join(f"«{c}»" for c in sheet.columns[:8])
                lines.append(
                    f"- «{sheet.name}»{mark}{role_bit}: {sheet.rows} строк, {sheet.n_columns} колонок"
                    + (f", колонки: {cols}" if cols else "")
                )
                if sheet.grain_note:
                    lines.append(f"  {sheet.grain_note}")
                for fact in list(sheet.facts or [])[:3]:
                    if fact and fact != sheet.grain_note:
                        lines.append(f"  {fact}")
        if self.metrics:
            lines.append("Метрики: " + ", ".join(f"«{m}»" for m in self.metrics[:8]))
        if self.groupers:
            lines.append("Группировки: " + ", ".join(f"«{g}»" for g in self.groupers[:8]))
        if self.date_columns:
            lines.append("Даты: " + ", ".join(f"«{c}»" for c in self.date_columns[:4]))
        if self.caveats:
            lines.append("Ограничения: " + "; ".join(self.caveats[:4]))
        if self.facts:
            lines.append("Посчитанные факты: " + "; ".join(self.facts[:16]))
        return "\n".join(lines)

    def router_block(self) -> str:
        """Короткий контекст для малой модели-роутера. Без сампли витрин."""
        lines: list[str] = []
        if self.title:
            lines.append(f"Название: {self.title}")
        if self.report_kind:
            lines.append(f"Тип: {self.report_kind}")
        if self.grain:
            lines.append(f"Зерно: {self.grain}")
        if self.summary:
            lines.append(self.summary[:240])
        if self.sheets:
            bits = []
            for sheet in self.sheets[:8]:
                role = sheet.role_label or sheet.role
                mark = "*" if sheet.active else ""
                label = f"«{sheet.name}»{mark}"
                if role:
                    label += f"[{role}]"
                bits.append(label)
            lines.append("Листы: " + ", ".join(bits))
        if self.metrics:
            lines.append("Метрики: " + ", ".join(f"«{m}»" for m in self.metrics[:6]))
        if self.groupers:
            lines.append("Группы: " + ", ".join(f"«{g}»" for g in self.groupers[:4]))
        if self.facts:
            lines.append("Факты: " + "; ".join(self.facts[:6]))
        return "\n".join(lines)
