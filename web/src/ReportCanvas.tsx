import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import GridLayout, { type Layout } from "react-grid-layout";
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
    <div className="overflow-auto rounded-lg border border-line">
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

const GRID_ROW_PX = 28;
const GRID_MARGIN_Y = 12;
/** Высота графика в отчёте — крупнее, читаемые подписи. */
export const REPORT_CHART_PX = 420;

/** Фактическая высота ячейки RGL: h*rowHeight + (h-1)*margin. */
export function gridHToPx(h: number): number {
  return h * GRID_ROW_PX + Math.max(0, h - 1) * GRID_MARGIN_Y;
}

/** Минимальный h, чтобы ячейка была не ниже contentPx (вплотную по низу). */
export function pxToGridH(px: number): number {
  const need = Math.max(1, px);
  const h = Math.ceil((need + GRID_MARGIN_Y) / (GRID_ROW_PX + GRID_MARGIN_Y));
  return Math.max(3, h);
}

export function nextBlockLayout(
  blocks: ReportBlock[],
  opts?: {
    kind?: ReportBlock["kind"];
    hasTable?: boolean;
    hasChart?: boolean;
    rowCount?: number;
  },
): { x: number; y: number; w: number; h: number } {
  const maxY = blocks.reduce((m, b) => Math.max(m, (b.layout?.y ?? 0) + (b.layout?.h ?? 0)), 0);
  const headerPx = 40;
  const padPx = 16;
  let bodyPx = 120;
  if (opts?.kind === "text") {
    bodyPx = 120;
  } else {
    const rows = Math.max(1, opts?.rowCount ?? 6);
    const tablePx = opts?.hasTable ? 34 + rows * 24 + 28 : 0;
    const chartPx = opts?.hasChart ? REPORT_CHART_PX : 0;
    const gap = opts?.hasTable && opts?.hasChart ? 8 : 0;
    bodyPx = tablePx + chartPx + gap;
  }
  return { x: 0, y: maxY, w: 6, h: pxToGridH(headerPx + padPx + bodyPx) };
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
  const canvasRef = useRef<HTMLDivElement>(null);
  const [gridWidth, setGridWidth] = useState(960);
  const userResized = useRef(new Set<string>());
  const docRef = useRef(doc);
  docRef.current = doc;
  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const apply = () => setGridWidth(Math.max(320, el.clientWidth));
    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const layout: Layout[] = useMemo(
    () =>
      doc.blocks.map((b) => ({
        i: b.id,
        x: b.layout?.x ?? 0,
        y: b.layout?.y ?? 0,
        w: b.layout?.w ?? 6,
        h: b.layout?.h ?? 8,
        minW: 3,
        minH: 3,
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
    userResized.current.delete(id);
    onChange({ ...doc, blocks: doc.blocks.filter((b) => b.id !== id) });
  }

  function addTextBlock() {
    const block: ReportBlock = {
      id: newBlockId("text"),
      kind: "text",
      title: "Текст",
      layout: nextBlockLayout(doc.blocks, { kind: "text" }),
      text: "",
    };
    onChange({ ...doc, blocks: [...doc.blocks, block] });
  }

  function applyLayout(next: Layout[]) {
    const byId = new Map(next.map((item) => [item.i, item]));
    const current = docRef.current;
    onChange({
      ...current,
      blocks: current.blocks.map((b) => {
        const item = byId.get(b.id);
        if (!item) return b;
        return {
          ...b,
          layout: { x: item.x, y: item.y, w: item.w, h: item.h },
        };
      }),
    });
  }

  const fitBlockHeight = useCallback(
    (blockId: string, contentPx: number) => {
      if (userResized.current.has(blockId)) return;
      // +2px — чтобы нижняя граница графика не обрезалась рамкой.
      const h = pxToGridH(contentPx + 2);
      const current = docRef.current;
      const block = current.blocks.find((b) => b.id === blockId);
      if (!block || block.layout.h === h) return;
      // Не раздувать блок, если уже почти впритык (лишний ряд пустоты).
      const curPx = gridHToPx(block.layout.h);
      if (curPx >= contentPx && curPx - contentPx < GRID_ROW_PX && block.layout.h <= h) {
        return;
      }
      onChange({
        ...current,
        blocks: current.blocks.map((b) =>
          b.id === blockId ? { ...b, layout: { ...b.layout, h } } : b,
        ),
      });
    },
    [onChange],
  );

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

      <div ref={canvasRef} className="min-h-0 flex-1 overflow-y-auto p-4">
        {doc.blocks.length === 0 ? (
          <div className="flex h-full min-h-[16rem] flex-col items-center justify-center rounded-xl border border-dashed border-line px-6 text-center">
            <p className="text-sm text-zinc-300">Отчёт пока пуст</p>
            <p className="mt-2 max-w-md text-xs text-zinc-500">
              Добавьте таблицы и графики кнопкой «В отчёт» на дашборде или в чате, либо нажмите
              «Добавить текст».
            </p>
          </div>
        ) : (
          <GridLayout
            className="layout"
            layout={layout}
            width={gridWidth}
            cols={12}
            rowHeight={GRID_ROW_PX}
            margin={[GRID_MARGIN_Y, GRID_MARGIN_Y]}
            containerPadding={[0, 0]}
            onDragStop={(next) => applyLayout(next)}
            onResizeStop={(next, _old, item) => {
              userResized.current.add(item.i);
              applyLayout(next);
            }}
            draggableHandle=".report-drag-handle"
            compactType="vertical"
          >
            {doc.blocks.map((block) => (
              <div
                key={block.id}
                className="overflow-hidden rounded-xl border border-line bg-card"
              >
                <ReportBlockInner
                  block={block}
                  busy={busy}
                  onPeriodChange={onPeriodChange}
                  onRemove={() => removeBlock(block.id)}
                  onUpdate={(patch) => updateBlock(block.id, patch)}
                  onFitHeight={(px) => fitBlockHeight(block.id, px)}
                />
              </div>
            ))}
          </GridLayout>
        )}
      </div>
    </div>
  );
}

function ReportBlockInner({
  block,
  busy,
  onPeriodChange,
  onRemove,
  onUpdate,
  onFitHeight,
}: {
  block: ReportBlock;
  busy: boolean;
  onPeriodChange: (blockId: string, period: string) => void;
  onRemove: () => void;
  onUpdate: (patch: Partial<ReportBlock>) => void;
  onFitHeight: (totalPx: number) => void;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const fitRef = useRef(onFitHeight);
  fitRef.current = onFitHeight;

  useEffect(() => {
    const el = rootRef.current;
    if (!el || block.kind === "text") return;
    // offsetHeight = реальный контент, без растягивания под высокую ячейку сетки.
    const measure = () => fitRef.current(el.offsetHeight);
    measure();
    const t1 = window.setTimeout(measure, 80);
    const t2 = window.setTimeout(measure, 280);
    const ro = new ResizeObserver(() => {
      window.requestAnimationFrame(measure);
    });
    ro.observe(el);
    return () => {
      window.clearTimeout(t1);
      window.clearTimeout(t2);
      ro.disconnect();
    };
  }, [block.id, block.kind, block.table, block.plotly_json, block.period, block.text]);

  return (
    <div ref={rootRef} className="h-fit w-full">
      <div className="report-drag-handle flex cursor-move items-start gap-2 border-b border-line px-3 py-2">
        <div className="min-w-0 flex-1">
          {block.kind === "text" ? (
            <input
              type="text"
              value={block.title}
              onChange={(e) => onUpdate({ title: e.target.value })}
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
          onClick={onRemove}
          onMouseDown={(e) => e.stopPropagation()}
        >
          Убрать
        </button>
      </div>
      <div className="p-2">
        {block.kind === "text" ? (
          <textarea
            value={block.text ?? ""}
            onChange={(e) => onUpdate({ text: e.target.value })}
            onMouseDown={(e) => e.stopPropagation()}
            className="min-h-[6rem] w-full resize-y bg-transparent text-sm outline-none"
            placeholder="Напишите что угодно…"
          />
        ) : (
          <div className="space-y-2">
            {block.table ? <ReportTable table={block.table} /> : null}
            {block.plotly_json ? (
              <PlotChart
                json={block.plotly_json}
                compact
                className="h-[420px] w-full min-h-[420px] shrink-0"
              />
            ) : null}
            {!block.table && !block.plotly_json ? (
              <p className="text-xs text-zinc-500">Нет данных для отображения</p>
            ) : null}
          </div>
        )}
      </div>
    </div>
  );
}
