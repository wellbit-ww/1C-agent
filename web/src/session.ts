import type { ReportDoc } from "./types";

const KEY = "excel-agent-session";

export type AppView = "dash" | "report";

export type SavedSession = {
  workspaceId?: string;
  fileId: string;
  filename: string;
  view: AppView;
  tab: number;
  chatOpen: boolean;
  reportDoc?: ReportDoc;
};

export function emptyReportDoc(title = "Новый отчёт"): ReportDoc {
  return { title, blocks: [] };
}

export function readSession(): SavedSession | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const data = JSON.parse(raw) as Partial<SavedSession> & {
      reportCharts?: unknown;
    };
    if (!data.fileId || !data.filename) return null;
    const reportDoc =
      data.reportDoc && Array.isArray(data.reportDoc.blocks)
        ? {
            id: data.reportDoc.id,
            title: data.reportDoc.title || "Новый отчёт",
            blocks: data.reportDoc.blocks,
          }
        : emptyReportDoc();
    return {
      workspaceId: data.workspaceId,
      fileId: data.fileId,
      filename: data.filename,
      view: data.view === "report" ? "report" : "dash",
      tab: Number.isFinite(data.tab) ? Math.max(0, Number(data.tab)) : 0,
      chatOpen: data.chatOpen !== false,
      reportDoc,
    };
  } catch {
    return null;
  }
}

export function writeSession(session: SavedSession): void {
  const payload: SavedSession = session;
  try {
    localStorage.setItem(KEY, JSON.stringify(payload));
  } catch {
    try {
      localStorage.setItem(
        KEY,
        JSON.stringify({
          ...payload,
          reportDoc: {
            ...payload.reportDoc,
            blocks: (payload.reportDoc?.blocks ?? []).map((b) => ({
              ...b,
              plotly_json: undefined,
            })),
          },
        }),
      );
    } catch {
      /* квота или приватный режим — сессия только в памяти */
    }
  }
}

export function clearSession(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}
