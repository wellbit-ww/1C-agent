import { useMemo, useState, type ReactNode } from "react";
import type { DashSpec, TileSpec } from "./types";

export type ChartEditorMode =
  | { mode: "edit"; tabI: number; tileI: number }
  | { mode: "add"; tabI: number };

const CHARTS: { id: string; label: string }[] = [
  { id: "bar", label: "Столбцы" },
  { id: "hbar", label: "Горизонтально" },
  { id: "pie", label: "Круг" },
  { id: "line", label: "Линия" },
  { id: "area", label: "Область" },
];

const AGGS: { id: string; label: string }[] = [
  { id: "sum", label: "Сумма" },
  { id: "count", label: "Количество" },
  { id: "mean", label: "Среднее" },
];

const UNITS: { id: string; label: string }[] = [
  { id: "auto", label: "Авто" },
  { id: "rub", label: "Рубли" },
  { id: "k", label: "Тысячи" },
  { id: "mln", label: "Миллионы" },
  { id: "mlrd", label: "Миллиарды" },
];

const PERIODS: { id: string; label: string }[] = [
  { id: "month", label: "По месяцам" },
  { id: "quarter", label: "По кварталам" },
  { id: "year", label: "По годам" },
];

const INTENTS: { id: string; label: string; hint: string }[] = [
  {
    id: "group",
    label: "Разбить по колонке",
    hint: "Топ заказчиков, отделов или ответственных",
  },
  {
    id: "period",
    label: "Динамика по времени",
    hint: "Как сумма меняется по месяцам",
  },
  {
    id: "named_columns",
    label: "Сравнить суммы",
    hint: "Несколько денежных колонок рядом",
  },
  {
    id: "current_stage",
    label: "Воронка этапов",
    hint: "Сделки на текущей стадии, как в 1С",
  },
];

const MAX_TILES = 8;
const selectClass =
  "w-full rounded-lg border border-line bg-bg px-2 py-1.5 text-sm outline-none focus:border-accent/60";

type Props = {
  spec: DashSpec;
  columns: string[];
  busy: boolean;
  mode: ChartEditorMode;
  onClose: () => void;
  onSave: (spec: DashSpec) => void;
};

function intentOf(tile: TileSpec): string {
  const kind = tile.source.kind || "group";
  if (kind === "columns_pattern") return "columns_pattern";
  if (INTENTS.some((item) => item.id === kind)) return kind;
  return "group";
}

function blankTile(intent: string, columns: string[]): TileSpec {
  if (intent === "period") {
    return {
      title: "Динамика по месяцам",
      chart_type: "area",
      agg: "sum",
      top_n: 12,
      unit: "auto",
      sort: "none",
      source: { kind: "period", period: "month" },
    };
  }
  if (intent === "named_columns") {
    return {
      title: "Сравнение сумм",
      chart_type: "bar",
      agg: "sum",
      top_n: 10,
      unit: "auto",
      sort: "none",
      source: { kind: "named_columns", column_names: columns.slice(0, 3) },
    };
  }
  if (intent === "current_stage") {
    return {
      title: "Воронка этапов",
      chart_type: "bar",
      agg: "sum",
      top_n: 12,
      unit: "auto",
      sort: "none",
      source: { kind: "current_stage" },
    };
  }
  return {
    title: "Топ по колонке",
    chart_type: "hbar",
    agg: "sum",
    top_n: 10,
    unit: "auto",
    sort: "desc",
    source: { kind: "group", group_column: columns[0] },
  };
}

function cloneSpec(spec: DashSpec): DashSpec {
  return structuredClone(spec);
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <div className="mb-1 text-xs text-zinc-400">{label}</div>
      {children}
    </label>
  );
}

export function ChartEditor({ spec, columns, busy, mode, onClose, onSave }: Props) {
  const [draft, setDraft] = useState<DashSpec>(() => cloneSpec(spec));
  const [picked, setPicked] = useState(mode.mode === "edit");
  const [tileI, setTileI] = useState(mode.mode === "edit" ? mode.tileI : -1);

  const tabI = mode.tabI;
  const tab = draft.tabs[tabI];
  const tile = tileI >= 0 ? tab?.tiles[tileI] : undefined;
  const intent = tile ? intentOf(tile) : "group";
  const canAdd = (tab?.tiles.length ?? 0) < MAX_TILES;

  const summary = useMemo(() => {
    if (!tile) return "";
    const chart = CHARTS.find((c) => c.id === tile.chart_type)?.label ?? tile.chart_type;
    const agg = AGGS.find((a) => a.id === tile.agg)?.label ?? tile.agg;
    if (intent === "period") {
      const period = PERIODS.find((p) => p.id === (tile.source.period || "month"))?.label;
      return `${agg}, ${period}, ${chart}`;
    }
    if (intent === "named_columns") {
      const n = tile.source.column_names?.length ?? 0;
      return `${n} колонок, ${chart}`;
    }
    if (intent === "current_stage") return `${agg} по текущему этапу, ${chart}`;
    const by = tile.source.group_column || "авто";
    return `${agg} по «${by}», ${chart}`;
  }, [tile, intent]);

  function patch(next: Partial<TileSpec>, source?: TileSpec["source"]) {
    if (tileI < 0) return;
    setDraft((cur) => {
      const copy = cloneSpec(cur);
      const current = copy.tabs[tabI].tiles[tileI];
      copy.tabs[tabI].tiles[tileI] = {
        ...current,
        ...next,
        source: source ?? current.source,
      };
      return copy;
    });
  }

  function pickTemplate(id: string) {
    if (!tab || !canAdd) return;
    setDraft((cur) => {
      const copy = cloneSpec(cur);
      copy.tabs[tabI].tiles.push(blankTile(id, columns));
      return copy;
    });
    setTileI(tab.tiles.length);
    setPicked(true);
  }

  function remove() {
    if (mode.mode !== "edit" || tileI < 0) return;
    const copy = cloneSpec(draft);
    copy.tabs[tabI].tiles.splice(tileI, 1);
    onSave(copy);
  }

  if (!tab) {
    return (
      <aside className="flex h-full w-80 shrink-0 flex-col border-l border-line bg-panel p-4">
        <p className="text-sm text-zinc-400">Нет вкладки для графика.</p>
        <button type="button" className="mt-3 text-sm text-accent" onClick={onClose}>
          Закрыть
        </button>
      </aside>
    );
  }

  return (
    <aside className="flex h-full w-80 shrink-0 flex-col border-l border-line bg-panel">
      <div className="flex items-start justify-between gap-2 border-b border-line px-4 py-3">
        <div>
          <div className="text-sm font-medium">
            {mode.mode === "add" && !picked ? "Новый график" : "Настройка графика"}
          </div>
          <div className="text-xs text-zinc-500">{tab.title}</div>
        </div>
        <button type="button" className="text-xs text-zinc-500 hover:text-zinc-200" onClick={onClose}>
          Закрыть
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {!picked ? (
          <div className="space-y-2">
            <p className="mb-3 text-sm text-zinc-400">Что показать на графике?</p>
            {!canAdd && (
              <p className="rounded-lg border border-line px-3 py-2 text-xs text-amber-300">
                На вкладке уже {MAX_TILES} графиков — уберите один, чтобы добавить новый.
              </p>
            )}
            {INTENTS.map((item) => (
              <button
                key={item.id}
                type="button"
                disabled={!canAdd}
                onClick={() => pickTemplate(item.id)}
                className="w-full rounded-xl border border-line bg-card px-3 py-3 text-left hover:border-accent/50 disabled:opacity-40"
              >
                <div className="text-sm">{item.label}</div>
                <div className="mt-0.5 text-xs text-zinc-500">{item.hint}</div>
              </button>
            ))}
          </div>
        ) : tile ? (
          <div className="space-y-3">
            {summary && <p className="text-xs text-zinc-500">{summary}</p>}
            <Field label="Название">
              <input
                value={tile.title}
                onChange={(e) => patch({ title: e.target.value })}
                className={selectClass}
              />
            </Field>
            <div>
              <div className="mb-1 text-xs text-zinc-400">Как показать</div>
              <div className="grid grid-cols-2 gap-1.5">
                {CHARTS.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => patch({ chart_type: item.id })}
                    className={
                      tile.chart_type === item.id
                        ? "rounded-lg bg-accent-dim px-2 py-1.5 text-xs text-accent"
                        : "rounded-lg border border-line px-2 py-1.5 text-xs text-zinc-300 hover:text-zinc-100"
                    }
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            </div>
            {intent === "group" && (
              <>
                <Field label="Разбить по">
                  <select
                    value={tile.source.group_column ?? ""}
                    onChange={(e) =>
                      patch({}, { ...tile.source, kind: "group", group_column: e.target.value || undefined })
                    }
                    className={selectClass}
                  >
                    <option value="">Подставить автоматически</option>
                    {columns.map((col) => (
                      <option key={col} value={col}>
                        {col}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Считать">
                  <select
                    value={tile.agg}
                    onChange={(e) => patch({ agg: e.target.value })}
                    className={selectClass}
                  >
                    {AGGS.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.label}
                      </option>
                    ))}
                  </select>
                </Field>
                {tile.agg !== "count" && (
                  <Field label="Из какой суммы">
                    <select
                      value={tile.source.value_column ?? ""}
                      onChange={(e) =>
                        patch(
                          {},
                          { ...tile.source, value_column: e.target.value || undefined },
                        )
                      }
                      className={selectClass}
                    >
                      <option value="">Подставить автоматически</option>
                      {columns.map((col) => (
                        <option key={col} value={col}>
                          {col}
                        </option>
                      ))}
                    </select>
                  </Field>
                )}
              </>
            )}
            {intent === "period" && (
              <>
                <Field label="Период">
                  <select
                    value={tile.source.period || "month"}
                    onChange={(e) =>
                      patch({}, { ...tile.source, kind: "period", period: e.target.value })
                    }
                    className={selectClass}
                  >
                    {PERIODS.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.label}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Какую сумму">
                  <select
                    value={tile.source.value_column ?? ""}
                    onChange={(e) =>
                      patch({}, { ...tile.source, value_column: e.target.value || undefined })
                    }
                    className={selectClass}
                  >
                    <option value="">Подставить автоматически</option>
                    {columns.map((col) => (
                      <option key={col} value={col}>
                        {col}
                      </option>
                    ))}
                  </select>
                </Field>
              </>
            )}
            {intent === "named_columns" && (
              <div>
                <div className="mb-1 text-xs text-zinc-400">Какие колонки сравнить</div>
                <div className="max-h-48 space-y-1 overflow-y-auto rounded-lg border border-line p-2">
                  {columns.map((col) => {
                    const pickedCols = tile.source.column_names ?? [];
                    const on = pickedCols.includes(col);
                    return (
                      <label key={col} className="flex items-start gap-2 text-xs text-zinc-300">
                        <input
                          type="checkbox"
                          checked={on}
                          onChange={() => {
                            const next = on
                              ? pickedCols.filter((name) => name !== col)
                              : [...pickedCols, col];
                            patch({}, { ...tile.source, kind: "named_columns", column_names: next });
                          }}
                        />
                        <span className="break-all">{col}</span>
                      </label>
                    );
                  })}
                </div>
              </div>
            )}
            {intent === "current_stage" && (
              <Field label="Считать">
                <select
                  value={tile.agg}
                  onChange={(e) => patch({ agg: e.target.value })}
                  className={selectClass}
                >
                  {AGGS.filter((item) => item.id !== "mean").map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </Field>
            )}
            {intent === "columns_pattern" && (
              <Field label="Окончание имени колонки">
                <input
                  value={tile.source.columns_pattern ?? ""}
                  onChange={(e) =>
                    patch({}, { ...tile.source, kind: "columns_pattern", columns_pattern: e.target.value })
                  }
                  className={selectClass}
                />
              </Field>
            )}
            {intent !== "named_columns" && intent !== "current_stage" && (
              <Field label="Сколько позиций показать">
                <input
                  type="number"
                  min={1}
                  max={50}
                  value={tile.top_n}
                  onChange={(e) => patch({ top_n: Number(e.target.value) || 10 })}
                  className={selectClass}
                />
              </Field>
            )}
            {tile.agg !== "count" && (
              <Field label="Единицы">
                <select
                  value={tile.unit}
                  onChange={(e) => patch({ unit: e.target.value })}
                  className={selectClass}
                >
                  {UNITS.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </Field>
            )}
          </div>
        ) : (
          <p className="text-sm text-zinc-400">График не найден — закройте панель.</p>
        )}
      </div>

      {picked && tile && (
        <div className="space-y-2 border-t border-line p-4">
          <button
            type="button"
            disabled={busy}
            onClick={() => onSave(draft)}
            className="w-full rounded-lg bg-accent px-3 py-2 text-sm font-medium text-bg disabled:opacity-40"
          >
            Сохранить график
          </button>
          {mode.mode === "edit" && (
            <button
              type="button"
              disabled={busy}
              onClick={remove}
              className="w-full rounded-lg border border-line px-3 py-2 text-sm text-zinc-400 hover:text-red-300 disabled:opacity-40"
            >
              Удалить график
            </button>
          )}
        </div>
      )}
    </aside>
  );
}
