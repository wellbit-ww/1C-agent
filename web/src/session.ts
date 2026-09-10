import type { ReportChart } from "./types";

const KEY = "excel-agent-session";

export type AppView = "dash" | "report";

export type SavedSession = {
  fileId: string;
  filename: string;
  view: AppView;
  tab: number;
  chatOpen: boolean;
  reportCharts: ReportChart[];
};

export function readSession(): SavedSession | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const data = JSON.parse(raw) as Partial<SavedSession>;
    if (!data.fileId || !data.filename) return null;
    return {
      fileId: data.fileId,
      filename: data.filename,
      view: data.view === "report" ? "report" : "dash",
      tab: Number.isFinite(data.tab) ? Math.max(0, Number(data.tab)) : 0,
      chatOpen: data.chatOpen !== false,
      reportCharts: Array.isArray(data.reportCharts) ? data.reportCharts : [],
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
      localStorage.setItem(KEY, JSON.stringify({ ...payload, reportCharts: [] }));
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
