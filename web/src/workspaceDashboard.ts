import type { Dashboard, DashSpec, DashTab, Kpi } from "./types";

export type DashboardSourcePayload = Dashboard & {
  role: string;
  file_id: string;
  filename: string;
  report_type: string;
};

export type WorkspaceDashboardPayload = {
  workspace_id: string;
  sales_file_id?: string | null;
  deficit_file_id?: string | null;
  sources: DashboardSourcePayload[];
};

/** Подписи вкладок дашборда дефицита в UI (бэкенд: «Платежи», «Структура»). */
export function deficitDashboardTabTitle(title: string): string {
  if (title === "Платежи") return "Продажи";
  if (title === "Структура") return "Оплата";
  return title;
}

export function flattenWorkspaceDashboard(payload: WorkspaceDashboardPayload): Dashboard {
  const flatTabs: DashTab[] = [];
  const specTabs: DashSpec["tabs"] = [];
  const kpis: Kpi[] = [];
  let primary = payload.sources.find((s) => s.role === "sales_pipeline") ?? payload.sources[0];

  for (const src of payload.sources) {
    const deficitTabTitle = (title: string) => {
      if (src.role !== "deficit_report") return title;
      if (title.startsWith("Дефицит · ")) return title;
      return deficitDashboardTabTitle(title);
    };
    for (const kpi of src.kpis ?? []) {
      kpis.push({ ...kpi, label: kpi.label });
    }
    const srcSpec = src.spec;
    (src.tabs ?? []).forEach((tab, ti) => {
      flatTabs.push({
        ...tab,
        source_file_id: src.file_id,
        source_role: src.role,
        source_tab_index: ti,
        title:
          src.role === "deficit_report" ? deficitTabTitle(tab.title) : tab.title,
      });
      const specTab = srcSpec?.tabs?.[ti];
      if (specTab) specTabs.push(specTab);
    });
  }

  const spec: DashSpec | undefined =
    specTabs.length > 0 ? { tabs: specTabs } : primary?.spec;

  const sourceRefs = payload.sources.map((src) => ({
    file_id: src.file_id,
    filename: src.filename,
    role: src.role,
    report_type: src.report_type,
    spec: src.spec,
  }));

  return {
    workspace_id: payload.workspace_id,
    sales_file_id: payload.sales_file_id ?? undefined,
    deficit_file_id: payload.deficit_file_id ?? undefined,
    sources: sourceRefs,
    report_type: primary?.report_type,
    summary: primary?.summary,
    kpis,
    insights: primary?.insights,
    tabs: flatTabs,
    spec,
    metadata: primary?.metadata,
    file_context: primary?.file_context,
    warning: payload.sources.map((s) => s.warning).filter(Boolean).join(" ") || undefined,
  };
}

export function tabSourceFileId(dashboard: Dashboard | null, tabIndex: number): string | null {
  const tab = dashboard?.tabs?.[tabIndex];
  return tab?.source_file_id ?? dashboard?.sales_file_id ?? null;
}

/** Спека тайла с учётом workspace (несколько файлов в одной ленте вкладок). */
export function specTileAt(
  dashboard: Dashboard | null,
  tabIndex: number,
  tileIndex: number,
) {
  const flatTab = dashboard?.tabs?.[tabIndex];
  if (!flatTab) return undefined;
  const fileId = flatTab.source_file_id;
  const sourceTabIndex = flatTab.source_tab_index;
  if (fileId != null && sourceTabIndex != null && dashboard?.sources?.length) {
    const src = dashboard.sources.find((item) => item.file_id === fileId);
    return src?.spec?.tabs?.[sourceTabIndex]?.tiles?.[tileIndex];
  }
  return dashboard?.spec?.tabs?.[tabIndex]?.tiles?.[tileIndex];
}

export function specForSourceFile(
  dashboard: Dashboard,
  targetFileId: string,
  merged: DashSpec,
): DashSpec {
  const src = dashboard.sources?.find((item) => item.file_id === targetFileId);
  if (!src?.spec?.tabs?.length) return merged;
  return {
    tabs: src.spec.tabs.map((tab, ti) => {
      const flatIndex = dashboard.tabs?.findIndex(
        (ft) => ft.source_file_id === targetFileId && ft.source_tab_index === ti,
      );
      if (flatIndex == null || flatIndex < 0) return tab;
      return merged.tabs[flatIndex] ?? tab;
    }),
  };
}
