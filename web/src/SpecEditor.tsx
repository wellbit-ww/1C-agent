import { useEffect, useState } from "react";
import type { DashSpec, TileSpec } from "./types";

const CHART_TYPES = ["bar", "hbar", "pie", "line", "area"];
const AGGS = ["sum", "mean", "count"];
const UNITS = ["auto", "rub", "k", "mln", "mlrd"];
const KINDS = ["group", "period", "columns_pattern", "named_columns", "current_stage"];

type Props = {
  spec: DashSpec;
  columns: string[];
  busy: boolean;
  onSave: (spec: DashSpec) => void;
};

export function SpecEditor({ spec, columns, busy, onSave }: Props) {
  const [draft, setDraft] = useState<DashSpec>(spec);

  useEffect(() => {
    setDraft(structuredClone(spec));
  }, [spec]);

  function patchTile(tabI: number, tileI: number, patch: Partial<TileSpec>) {
    setDraft((cur) => {
      const next = structuredClone(cur);
      next.tabs[tabI].tiles[tileI] = { ...next.tabs[tabI].tiles[tileI], ...patch };
      return next;
    });
  }

  function removeTile(tabI: number, tileI: number) {
    setDraft((cur) => {
      const next = structuredClone(cur);
      next.tabs[tabI].tiles.splice(tileI, 1);
      return next;
    });
  }

  function addTile(tabI: number) {
    setDraft((cur) => {
      const next = structuredClone(cur);
      const col = columns[0];
      next.tabs[tabI].tiles.push({
        title: "Новый график",
        chart_type: "bar",
        agg: "sum",
        top_n: 10,
        unit: "auto",
        sort: "desc",
        source: col ? { kind: "group", group_column: col } : { kind: "group", group_semantic: "manager" },
      });
      return next;
    });
  }

  return (
    <details className="mb-4 rounded-xl border border-line bg-card">
      <summary className="cursor-pointer px-3 py-2 text-sm text-zinc-300">
        Редактор тайлов
      </summary>
      <div className="space-y-4 border-t border-line px-3 py-3">
        {draft.tabs.map((tab, tabI) => (
          <div key={`${tab.title}-${tabI}`}>
            <div className="mb-2 flex items-center justify-between">
              <div className="text-xs font-medium text-zinc-400">{tab.title}</div>
              <button
                type="button"
                className="text-xs text-accent hover:underline"
                onClick={() => addTile(tabI)}
              >
                + график
              </button>
            </div>
            <div className="space-y-2">
              {tab.tiles.map((tile, tileI) => (
                <div key={tileI} className="grid grid-cols-12 gap-1">
                  <input
                    value={tile.title}
                    onChange={(e) => patchTile(tabI, tileI, { title: e.target.value })}
                    className="col-span-4 rounded-lg border border-line bg-bg px-2 py-1 text-xs outline-none"
                  />
                  <select
                    value={tile.chart_type}
                    onChange={(e) => patchTile(tabI, tileI, { chart_type: e.target.value })}
                    className="col-span-2 rounded-lg border border-line bg-bg px-1 py-1 text-xs"
                  >
                    {CHART_TYPES.map((t) => (
                      <option key={t}>{t}</option>
                    ))}
                  </select>
                  <select
                    value={tile.agg}
                    onChange={(e) => patchTile(tabI, tileI, { agg: e.target.value })}
                    className="col-span-2 rounded-lg border border-line bg-bg px-1 py-1 text-xs"
                  >
                    {AGGS.map((t) => (
                      <option key={t}>{t}</option>
                    ))}
                  </select>
                  <input
                    type="number"
                    min={1}
                    max={50}
                    value={tile.top_n}
                    onChange={(e) => patchTile(tabI, tileI, { top_n: Number(e.target.value) || 10 })}
                    className="col-span-1 rounded-lg border border-line bg-bg px-1 py-1 text-xs"
                  />
                  <select
                    value={tile.unit}
                    onChange={(e) => patchTile(tabI, tileI, { unit: e.target.value })}
                    className="col-span-2 rounded-lg border border-line bg-bg px-1 py-1 text-xs"
                  >
                    {UNITS.map((t) => (
                      <option key={t}>{t}</option>
                    ))}
                  </select>
                  <button
                    type="button"
                    className="col-span-1 text-xs text-zinc-500 hover:text-red-300"
                    onClick={() => removeTile(tabI, tileI)}
                  >
                    ×
                  </button>
                  <select
                    value={String(tile.source.kind ?? "group")}
                    onChange={(e) =>
                      patchTile(tabI, tileI, {
                        source: { ...tile.source, kind: e.target.value },
                      })
                    }
                    className="col-span-3 rounded-lg border border-line bg-bg px-1 py-1 text-xs"
                  >
                    {KINDS.map((t) => (
                      <option key={t}>{t}</option>
                    ))}
                  </select>
                  {columns.length > 0 && (
                    <select
                      value={String(tile.source.group_column ?? "")}
                      onChange={(e) =>
                        patchTile(tabI, tileI, {
                          source: { ...tile.source, group_column: e.target.value || undefined },
                        })
                      }
                      className="col-span-9 rounded-lg border border-line bg-bg px-1 py-1 text-xs"
                    >
                      <option value="">группировка: авто</option>
                      {columns.map((c) => (
                        <option key={c} value={c}>
                          {c}
                        </option>
                      ))}
                    </select>
                  )}
                </div>
              ))}
            </div>
          </div>
        ))}
        <button
          type="button"
          disabled={busy}
          onClick={() => onSave(draft)}
          className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-bg disabled:opacity-40"
        >
          Сохранить дашборд
        </button>
      </div>
    </details>
  );
}
