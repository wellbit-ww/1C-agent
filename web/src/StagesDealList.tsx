import { useEffect, useMemo, useState } from "react";
import type { DealsListFilterMeta, DealsListPayload, TileSpec } from "./types";

type HtmlCell = { text: string; num?: string; format?: string };

export type DealsListFiltersPatch = {
  stages?: string[];
  departments?: string[] | undefined;
  managers?: string[] | undefined;
  statuses?: string[] | undefined;
  min_potential?: number | null;
  list_sort_column?: string | null;
  sort?: "asc" | "desc" | "none";
};

type Props = {
  dealsList: DealsListPayload;
  filterMeta: DealsListFilterMeta;
  deptCatalog: string[];
  specSource: TileSpec["source"];
  specSort?: string;
  busy: boolean;
  onApply: (patch: DealsListFiltersPatch) => void;
};

function formatPotential(value: number) {
  return Math.round(value).toLocaleString("ru-RU");
}

function toggleInList(list: string[], item: string, on: boolean, fullCatalog: string[]): string[] {
  const current = list.length ? list : fullCatalog;
  const next = on ? current.filter((x) => x !== item) : [...current, item];
  if (next.length === 0) return list;
  return next;
}

function allSelected(picked: string[] | undefined, catalog: string[]) {
  return !picked?.length || catalog.every((item) => picked.includes(item));
}

export function dealsListToGrid(dealsList: DealsListPayload): {
  plain: string[][];
  html: HtmlCell[][];
} {
  const plain: string[][] = [dealsList.columns];
  const html: HtmlCell[][] = [dealsList.columns.map((text) => ({ text }))];
  const potentialCol = dealsList.columns.indexOf("Потенциал");
  const potCol = potentialCol >= 0 ? potentialCol : dealsList.columns.length - 1;
  for (const row of dealsList.rows) {
    const cells = row.cells.map((cell, i) => {
      if (i === potCol && typeof cell === "number") {
        const n = Math.round(cell);
        const literal = String(n);
        return {
          plain: formatPotential(n),
          html: { text: formatPotential(n), num: literal, format: "#,##0" } as HtmlCell,
        };
      }
      const text = String(cell ?? "");
      return { plain: text, html: { text } as HtmlCell };
    });
    plain.push(cells.map((c) => c.plain));
    html.push(cells.map((c) => c.html));
  }
  return { plain, html };
}

export function StagesDealListView({
  dealsList,
  filterMeta,
  deptCatalog,
  specSource,
  specSort,
  busy,
  onApply,
}: Props) {
  const resolvedStages = filterMeta.resolved_stages.length
    ? filterMeta.resolved_stages
    : filterMeta.default_stages;

  const statusOptions = filterMeta.statuses ?? [];

  const [stages, setStages] = useState<string[]>(specSource.stages?.length ? specSource.stages : resolvedStages);
  const [departments, setDepartments] = useState<string[] | undefined>(specSource.departments);
  const [managers, setManagers] = useState<string[] | undefined>(specSource.managers);
  const [statuses, setStatuses] = useState<string[] | undefined>(specSource.statuses);
  const [minPotential, setMinPotential] = useState<string>(
    specSource.min_potential != null && specSource.min_potential > 0
      ? String(Math.round(specSource.min_potential))
      : "",
  );

  useEffect(() => {
    setStages(specSource.stages?.length ? specSource.stages : resolvedStages);
    setDepartments(specSource.departments);
    setManagers(specSource.managers);
    setStatuses(specSource.statuses);
    setMinPotential(
      specSource.min_potential != null && specSource.min_potential > 0
        ? String(Math.round(specSource.min_potential))
        : "",
    );
  }, [
    specSource.stages,
    specSource.departments,
    specSource.managers,
    specSource.statuses,
    specSource.min_potential,
    resolvedStages.join("|"),
  ]);

  const managerOptions = useMemo(() => filterMeta.managers, [filterMeta.managers]);

  const sortColumn = specSource.list_sort_column || filterMeta.sort_column || "Потенциал";
  const sortDir: "asc" | "desc" =
    specSort === "asc" ? "asc" : specSort === "desc" ? "desc" : filterMeta.sort_dir === "asc" ? "asc" : "desc";

  const potentialCol =
    dealsList.columns.indexOf("Потенциал") >= 0
      ? dealsList.columns.indexOf("Потенциал")
      : dealsList.columns.length - 1;

  function buildFilterPatch(extra?: DealsListFiltersPatch): DealsListFiltersPatch {
    const min = minPotential.trim() ? Number(minPotential.replace(/\s/g, "")) : null;
    return {
      stages: stages.length ? stages : resolvedStages,
      departments: allSelected(departments, deptCatalog) ? undefined : departments,
      managers: allSelected(managers, managerOptions) ? undefined : managers,
      statuses: allSelected(statuses, statusOptions) ? undefined : statuses,
      min_potential: min != null && !Number.isNaN(min) && min > 0 ? min : null,
      ...extra,
    };
  }

  function apply() {
    onApply(buildFilterPatch());
  }

  function onColumnSort(col: string) {
    const nextDir: "asc" | "desc" = col === sortColumn && sortDir === "asc" ? "desc" : "asc";
    onApply(
      buildFilterPatch({
        list_sort_column: col,
        sort: nextDir,
      }),
    );
  }

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-line p-2">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <span className="text-xs text-zinc-400">Фильтры</span>
          <button
            type="button"
            disabled={busy}
            onClick={() => apply()}
            className="rounded-md border border-line px-2 py-0.5 text-[11px] text-zinc-200 hover:border-accent/50 hover:text-accent disabled:opacity-40"
          >
            Применить
          </button>
        </div>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-5">
          <FilterCheckList
            label="Этап"
            catalog={filterMeta.stages}
            picked={stages}
            onChange={setStages}
            onAll={() => setStages(filterMeta.stages)}
          />
          <FilterCheckList
            label="Подразделение"
            catalog={deptCatalog}
            picked={departments?.length ? departments : deptCatalog}
            onChange={(next) => {
              const allOn = deptCatalog.every((item) => next.includes(item));
              setDepartments(allOn ? undefined : next);
              setManagers(undefined);
            }}
            onAll={() => {
              setDepartments(undefined);
              setManagers(undefined);
            }}
          />
          <FilterCheckList
            label="Ответственный"
            catalog={managerOptions}
            picked={managers?.length ? managers : managerOptions}
            onChange={(next) => {
              const allOn = managerOptions.every((item) => next.includes(item));
              setManagers(allOn ? undefined : next);
            }}
            onAll={() => setManagers(undefined)}
          />
          {statusOptions.length > 0 ? (
            <FilterCheckList
              label="Статус"
              catalog={statusOptions}
              picked={statuses?.length ? statuses : statusOptions}
              onChange={(next) => {
                const allOn = statusOptions.every((item) => next.includes(item));
                setStatuses(allOn ? undefined : next);
              }}
              onAll={() => setStatuses(undefined)}
            />
          ) : null}
          <div>
            <div className="mb-1 text-xs text-zinc-400">Потенциал от, ₽</div>
            <input
              type="text"
              inputMode="numeric"
              value={minPotential}
              disabled={busy}
              onChange={(e) => setMinPotential(e.target.value.replace(/[^\d\s]/g, ""))}
              placeholder="без порога"
              className="w-full rounded-lg border border-line bg-bg px-2 py-1.5 text-sm outline-none focus:border-accent/60"
            />
          </div>
        </div>
      </div>

      {dealsList.truncated && (
        <p className="text-[11px] text-amber-300/90">
          Показаны первые {dealsList.rows.length} из {dealsList.total_matched} сделок. Увеличьте лимит в
          настройках тайла или сузьте фильтры.
        </p>
      )}

      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] border-collapse text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs text-zinc-400">
              {dealsList.columns.map((col) => {
                const active = col === sortColumn;
                const alignRight = col === "Потенциал";
                return (
                  <th key={col} className={`px-2 py-1.5 font-medium ${alignRight ? "text-right" : ""}`}>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => onColumnSort(col)}
                      className={`inline-flex items-center gap-1 hover:text-zinc-200 disabled:opacity-40 ${
                        alignRight ? "ml-auto" : ""
                      }`}
                    >
                      <span>{col}</span>
                      {active ? (
                        <span className="text-accent" aria-hidden>
                          {sortDir === "asc" ? "↑" : "↓"}
                        </span>
                      ) : (
                        <span className="text-zinc-600" aria-hidden>↕</span>
                      )}
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {dealsList.rows.length === 0 ? (
              <tr>
                <td colSpan={dealsList.columns.length} className="px-2 py-4 text-center text-zinc-500">
                  Нет сделок по выбранным фильтрам
                </td>
              </tr>
            ) : (
              dealsList.rows.map((row, ri) => (
                <tr key={ri} className="border-b border-line/60 text-zinc-200">
                  {row.cells.map((cell, ci) => (
                    <td
                      key={ci}
                      className={`px-2 py-1 ${
                        ci === potentialCol
                          ? "text-right font-mono tabular-nums"
                          : ci === 0 && dealsList.columns[0] === "УП"
                            ? "font-mono text-xs"
                            : ""
                      }`}
                    >
                      {ci === potentialCol && typeof cell === "number"
                        ? formatPotential(cell)
                        : String(cell ?? "—")}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function FilterCheckList({
  label,
  catalog,
  picked,
  onChange,
  onAll,
}: {
  label: string;
  catalog: string[];
  picked: string[];
  onChange: (next: string[]) => void;
  onAll: () => void;
}) {
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-xs text-zinc-400">
        <span>{label}</span>
        <button type="button" className="text-accent hover:underline" onClick={onAll}>
          Все
        </button>
      </div>
      <div className="max-h-36 space-y-1 overflow-y-auto rounded-lg border border-line p-2">
        {catalog.map((name) => {
          const on = picked.includes(name);
          return (
            <label key={name} className="flex items-center gap-2 text-xs text-zinc-200">
              <input
                type="checkbox"
                checked={on}
                onChange={() => onChange(toggleInList(picked, name, on, catalog))}
              />
              <span className="leading-tight">{name}</span>
            </label>
          );
        })}
      </div>
    </div>
  );
}
