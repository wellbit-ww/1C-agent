"""Карточка понимания файла: что в выгрузке и как с ней работать.

Считается один раз (детерминированно + LLM) и живёт в SQLite рядом с file_id.
Чат и генерация дашборда читают её как контекст — LLM не перечитывает Excel.
"""
from pydantic import BaseModel, Field


class SheetBrief(BaseModel):
    name: str
    rows: int = 0
    n_columns: int = 0
    columns: list[str] = Field(default_factory=list)
    active: bool = False


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
                cols = ", ".join(f"«{c}»" for c in sheet.columns[:8])
                lines.append(
                    f"- «{sheet.name}»{mark}: {sheet.rows} строк, {sheet.n_columns} колонок"
                    + (f", колонки: {cols}" if cols else "")
                )
        if self.metrics:
            lines.append("Метрики: " + ", ".join(f"«{m}»" for m in self.metrics[:8]))
        if self.groupers:
            lines.append("Группировки: " + ", ".join(f"«{g}»" for g in self.groupers[:8]))
        if self.date_columns:
            lines.append("Даты: " + ", ".join(f"«{c}»" for c in self.date_columns[:4]))
        if self.caveats:
            lines.append("Ограничения: " + "; ".join(self.caveats[:4]))
        return "\n".join(lines)
