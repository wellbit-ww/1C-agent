/** Единый стиль заголовков Plotly-графиков на дашбордах. */
export const CHART_TITLE_FONT_SIZE = 14;

export type ChartTitleLayout = {
  text: string;
  x: number;
  xanchor: "center";
  font: { size: number };
};

export function chartTitleLayout(text: string): ChartTitleLayout {
  return {
    text,
    x: 0.5,
    xanchor: "center",
    font: { size: CHART_TITLE_FONT_SIZE },
  };
}

/** Столбчатые диаграммы воронки продаж — прописные буквы (как «ДИНАМИКА СДЕЛОК»). */
export function chartTitleCaps(text: string): ChartTitleLayout {
  return chartTitleLayout(text.toUpperCase());
}

export type DepartmentChartFilter = {
  /** Все службы в файле (короткие имена). */
  catalog?: string[];
  /** Отмеченные в настройках службы; если пусто/нет — считаются все. */
  included?: string[];
};

export function excludedDepartments(filter?: DepartmentChartFilter): string[] {
  const catalog = filter?.catalog ?? [];
  const included = filter?.included;
  if (!catalog.length || !included?.length) return [];
  const picked = new Set(included);
  if (catalog.every((item) => picked.has(item))) return [];
  return catalog.filter((item) => !picked.has(item));
}

export function withDepartmentExclusion(base: string, filter?: DepartmentChartFilter): string {
  const excluded = excludedDepartments(filter);
  if (!excluded.length) return base;
  return `${base} (без ${excluded.join(", ")})`;
}

export function chartTitleCapsFiltered(base: string, filter?: DepartmentChartFilter): ChartTitleLayout {
  return chartTitleCaps(withDepartmentExclusion(base, filter));
}
