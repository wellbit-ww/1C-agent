import { useMemo, useState } from "react";
import GridLayout, { WidthProvider, type Layout } from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";
import { PlotChart } from "./PlotChart";
import type { PivotTable, ReportBlock, ReportDoc } from "./types";

function formatCell(value: number | null | undefined, kind?: string) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  const n = Number(value);
  if (kind === "percent" || kind === "percent1") {
    return `${kind === "percent1" ? n.toFixed(1).replace(".", ",") : Math.round(n)}%`;
  }
  if (kind === "count") return String(Math.round(n));
  return n.toLocaleString("ru-RU", { maximumFractionDigits: 1 });
}

function ReportTable({ table }: { table: PivotTable }) {
  return (
    <div className="max-h-full overflow-auto rounded-lg border border-line">
      <table className="min-w-full border-collapse text-right text-xs">
        <thead className="sticky top-0 bg-panel text-zinc-200">
          <tr>
            <th className="border-b border-line px-2 py-1.5 text-left font-medium">
              {table.index_label || ""}
            </th>
            {table.columns.map((col, i) => (
              <th key={`${i}-${col}`} className="border-b border-line px-2 py-1.5 font-medium whitespace-nowrap">
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row) => (
            <tr key={row.label} className="border-t border-line">
              <th className="px-2 py-1.5 text-left font-medium text-zinc-200">{row.label}</th>
              {row.values.map((value, i) => (
                <td key={`${row.label}-${i}`} className="px-2 py-1.5 tabular-nums text-zinc-300">
                  {formatCell(value as number, table.column_kinds?.[i])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
        {table.totals && table.totals.length > 0 && (
          <tfoot>
            <tr className="border-t border-line bg-panel font-medium">
              <th className="px-2 py-1.5 text-left text-zinc-100">{table.totals_label || "Итого"}</th>
              {table.totals.map((value, i) => (
                <td key={`t-${i}`} className="px-2 py-1.5 tabular-nums text-accent">
                  {formatCell(value, table.column_kinds?.[i])}
                </td>
              ))}
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}

const Grid = WidthProvider(GridLayout);

const PERIOD_OPTIONS = [
  { id: "month", label: "Месяц" },
  { id: "quarter", label: "Квартал" },
  { id: "half", label: "Полугодие" },
] as const;

const PERIOD_CAPABLE = new Set([
  "halfyear",
  "pivot",
  "outcome",
  "deal_statuses",
  "in_work_stages",
  "deals_dynamics",
  "deals_dynamics_departments",
  "deals_dynamics_outcome_share",
  "deals_conversion",
  "deals_money",
  "deficit_sales_by_department",
  "deficit_sales_summary",
  "deficit_sales_forecast_vitrine",
  "deficit_sales_fulfillment_board",
  "deficit_sales_share",
  "deficit_payment_receipt",
  "deficit_payment_by_department",
  "deficit_payment_fulfillment_board",
  "deficit_payment_share",
]);

export function nextBlockLayout(blocks: ReportBlock[]): { x: number; y: number; w: number; h: number } {
  const maxY = blocks.reduce((m, b) => Math.max(m, (b.layout?.y ?? 0) + (b.layout?.h ?? 0)), 0);
  return { x: 0, y: maxY, w: 6, h: 10 };
}

export function newBlockId(prefix = "block"): string {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}

type SavedMeta = { id: string; title: string; updated_at?: string };

type Props = {
  doc: ReportDoc;
  busy: boolean;
  library: SavedMeta[];
  onChange: (doc: ReportDoc) => void;
  onSave: () => void;
  onExcel: () => void;
  onClear: () => void;
  onBack: () => void;
  onPeriodChange: (blockId: string, period: string) => void;
  onOpenSaved: (id: string) => void;
  onDeleteSaved: (id: string) => void;
  onRefreshLibrary: () => void;
};

export function ReportCanvas({
  doc,
  busy,
  library,
  onChange,
  onSave,
  onExcel,
  onClear,
  onBack,
  onPeriodChange,
  onOpenSaved,
  onDeleteSaved,
  onRefreshLibrary,
}: Props) {
  const [showLibrary, setShowLibrary] = useState(false);
  const layout: Layout[] = useMemo(
    () =>
      doc.blocks.map((b) => ({
        i: b.id,
        x: b.layout?.x ?? 0,
        y: b.layout?.y ?? 0,
        w: b.layout?.w ?? 6,
        h: b.layout?.h ?? 8,
        minW: 3,
        minH: 4,
      })),
    [doc.blocks],
  );

  function updateBlock(id: string, patch: Partial<ReportBlock>) {
    onChange({
      ...doc,
      blocks: doc.blocks.map((b) => (b.id === id ? { ...b, ...patch } : b)),
    });
  }

  function removeBlock(id: string) {
    onChange({ ...doc, blocks: doc.blocks.filter((b) => b.id !== id) });
  }

  function addTextBlock() {
    const block: ReportBlock = {
      id: newBlockId("text"),
      kind: "text",
      title: "Текст",
      layout: nextBlockLayout(doc.blocks),
      text: "",
    };
    onChange({ ...doc, blocks: [...doc.blocks, block] });
  }

  function onLayoutChange(next: Layout[]) {
    const byId = new Map(next.map((item) => [item.i, item]));
    onChange({
      ...doc,
      blocks: doc.blocks.map((b) => {
        const item = byId.get(b.id);
        if (!item) return b;
        return {
          ...b,
          layout: { x: item.x, y: item.y, w: item.w, h: item.h },
        };
      }),
    });
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-line bg-panel px-4 py-3">
        <button type="button" className="text-sm text-zinc-400 hover:text-zinc-100" onClick={onBack}>
          К дашборду
        </button>
        <input
          type="text"
          value={doc.title}
          disabled={busy}
          onChange={(e) => onChange({ ...doc, title: e.target.value })}
          className="min-w-[12rem] flex-1 rounded-lg border border-line bg-bg px-3 py-1.5 text-sm outline-none focus:border-accent/60"
          aria-label="Название отчёта"
          placeholder="Название отчёта"
        />
        <button
          type="button"
          disabled={busy}
          className="rounded-lg border border-line px-3 py-1.5 text-sm text-zinc-300 hover:border-accent/50 hover:text-accent disabled:opacity-50"
          onClick={addTextBlock}
        >
          Добавить текст
        </button>
        <button
          type="button"
          disabled={busy}
          className="rounded-lg border border-line px-3 py-1.5 text-sm text-zinc-300 hover:border-accent/50 hover:text-accent disabled:opacity-50"
          onClick={() => {
            setShowLibrary((v) => !v);
            onRefreshLibrary();
          }}
        >
          Библиотека
        </button>
        <button
          type="button"
          disabled={busy || doc.blocks.length === 0}
          className="rounded-lg border border-line px-3 py-1.5 text-sm text-zinc-300 hover:text-red-300 disabled:opacity-50"
          onClick={onClear}
        >
          Очистить
        </button>
        <button
          type="button"
          disabled={busy}
          className="rounded-lg border border-line px-3 py-1.5 text-sm text-zinc-300 hover:border-accent/50 hover:text-accent disabled:opacity-50"
          onClick={onExcel}
        >
          В Excel
        </button>
        <button
          type="button"
          disabled={busy}
          className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-bg disabled:opacity-50"
          onClick={onSave}
        >
          Сохранить
        </button>
      </div>

      {showLibrary && (
        <div className="shrink-0 border-b border-line bg-card px-4 py-3">
          <div className="mb-2 text-xs text-zinc-500">Сохранённые отчёты</div>
          {library.length === 0 ? (
            <p className="text-sm text-zinc-500">Пока пусто — сохраните текущий отчёт.</p>
          ) : (
            <ul className="flex flex-col gap-1">
              {library.map((item) => (
                <li key={item.id} className="flex items-center gap-2 text-sm">
                  <button
                    type="button"
                    className="min-w-0 flex-1 truncate text-left text-zinc-200 hover:text-accent"
                    onClick={() => onOpenSaved(item.id)}
                  >
                    {item.title}
                  </button>
                  <button
                    type="button"
                    className="text-[11px] text-zinc-500 hover:text-red-300"
                    onClick={() => onDeleteSaved(item.id)}
                  >
                    Удалить
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {doc.blocks.length === 0 ? (
          <div className="flex h-full min-h-[16rem] flex-col items-center justify-center rounded-xl border border-dashed border-line px-6 text-center">
            <p className="text-sm text-zinc-300">Отчёт пока пуст</p>
            <p className="mt-2 max-w-md text-xs text-zinc-500">
              Добавьте таблицы и графики кнопкой «В отчёт» на дашборде или в чате, либо нажмите
              «Добавить текст».
            </p>
          </div>
        ) : (
          <Grid
            className="layout"
            layout={layout}
            cols={12}
            rowHeight={28}
            margin={[12, 12]}
            containerPadding={[0, 0]}
            onLayoutChange={onLayoutChange}
            draggableHandle=".report-drag-handle"
            compactType="vertical"
          >
            {doc.blocks.map((block) => (
              <div
                key={block.id}
                className="flex min-h-0 flex-col overflow-hidden rounded-xl border border-line bg-card"
              >
                <div className="report-drag-handle flex cursor-move items-start gap-2 border-b border-line px-3 py-2">
                  <div className="min-w-0 flex-1">
                    {block.kind === "text" ? (
                      <input
                        type="text"
                        value={block.title}
                        onChange={(e) => updateBlock(block.id, { title: e.target.value })}
                        className="w-full bg-transparent text-sm font-medium outline-none"
                        onMouseDown={(e) => e.stopPropagation()}
                      />
                    ) : (
                      <div className="text-sm font-medium">{block.title}</div>
                    )}
                  </div>
                  {block.source?.kind && PERIOD_CAPABLE.has(block.source.kind) && (
                    <select
                      value={block.period || block.source.period || "quarter"}
                      disabled={busy}
                      onChange={(e) => onPeriodChange(block.id, e.target.value)}
                      onMouseDown={(e) => e.stopPropagation()}
                      className="rounded-md border border-line bg-bg px-2 py-0.5 text-[11px] text-zinc-300 outline-none"
                      aria-label="Период блока"
                    >
                      {PERIOD_OPTIONS.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.label}
                        </option>
                      ))}
                    </select>
                  )}
                  <button
                    type="button"
                    className="shrink-0 text-[11px] text-zinc-500 hover:text-red-300"
                    onClick={() => removeBlock(block.id)}
                    onMouseDown={(e) => e.stopPropagation()}
                  >
                    Убрать
                  </button>
                </div>
                <div className="min-h-0 flex-1 overflow-auto p-3">
                  {block.kind === "text" ? (
                    <textarea
                      value={block.text ?? ""}
                      onChange={(e) => updateBlock(block.id, { text: e.target.value })}
                      onMouseDown={(e) => e.stopPropagation()}
                      className="h-full min-h-[6rem] w-full resize-none bg-transparent text-sm outline-none"
                      placeholder="Напишите что угодно…"
                    />
                  ) : (
                    <div className="space-y-3">
                      {block.table ? <ReportTable table={block.table} /> : null}
                      {block.plotly_json ? (
                        <PlotChart json={block.plotly_json} className="h-[18rem] w-full" />
                      ) : null}
                      {!block.table && !block.plotly_json ? (
                        <p className="text-xs text-zinc-500">Нет данных для отображения</p>
                      ) : null}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </Grid>
        )}
      </div>
    </div>
  );
}
