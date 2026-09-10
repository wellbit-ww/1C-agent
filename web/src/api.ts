import type { ChatMessage, Dashboard, DashSpec, FileContext, Report, ReportChart } from "./types";

const TOKEN = import.meta.env.VITE_API_TOKEN ?? "";

function headers(json = true): HeadersInit {
  const h: Record<string, string> = {};
  if (json) h["Content-Type"] = "application/json";
  if (TOKEN) h["X-API-Token"] = TOKEN;
  return h;
}

async function readError(res: Response): Promise<string> {
  try {
    const payload = await res.json();
    const detail = payload.detail ?? payload.error;
    if (typeof detail === "string") {
      if (detail === "Internal Server Error") {
        return "Сервер не смог обработать запрос. Попробуйте ещё раз.";
      }
      return detail;
    }
    if (Array.isArray(detail)) return detail.map((d) => d.msg ?? d).join("; ");
    if (res.status >= 500) return "Сервер не смог обработать запрос. Попробуйте ещё раз.";
    return res.statusText;
  } catch {
    return res.statusText || `HTTP ${res.status}`;
  }
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<T>;
}

export async function checkBackend(): Promise<boolean> {
  try {
    const res = await fetch("/api/", { headers: headers(false) });
    return res.ok;
  } catch {
    return false;
  }
}

export async function uploadFile(file: File): Promise<{ file_id: string }> {
  const data = new FormData();
  data.append("file", file);
  const res = await fetch("/api/upload", {
    method: "POST",
    headers: TOKEN ? { "X-API-Token": TOKEN } : undefined,
    body: data,
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

export async function getDashboard(fileId: string): Promise<Dashboard> {
  return postJson("/dashboard", { file_id: fileId });
}

export async function enrichContext(fileId: string): Promise<FileContext> {
  return postJson("/file-context", { file_id: fileId });
}

export async function getHistory(fileId: string): Promise<{ messages: ChatMessage[] }> {
  return postJson("/history", { file_id: fileId });
}

export async function getTable(fileId: string): Promise<{ data: Record<string, unknown>[] }> {
  return postJson("/table", { file_id: fileId });
}

export async function sendChat(
  fileId: string,
  question: string,
): Promise<{ answer: string; charts: ChatMessage["charts"] }> {
  return postJson("/chat", { file_id: fileId, question });
}

export async function dashboardGenerate(fileId: string, request: string): Promise<Dashboard> {
  return postJson("/dashboard/generate", { file_id: fileId, request });
}

export async function dashboardEdit(fileId: string, request: string): Promise<Dashboard> {
  return postJson("/dashboard/edit", { file_id: fileId, request });
}

export async function dashboardPin(
  fileId: string,
  tile: Record<string, unknown>,
): Promise<{ ok: boolean; message?: string }> {
  return postJson("/dashboard/pin", { file_id: fileId, tile });
}

export async function dashboardComments(
  fileId: string,
): Promise<{ comments: Record<string, string> }> {
  return postJson("/dashboard/comments", { file_id: fileId });
}

export async function dashboardSaveSpec(fileId: string, spec: DashSpec): Promise<Dashboard> {
  return postJson("/dashboard/spec", { file_id: fileId, spec });
}

export async function getReport(fileId: string, filename?: string): Promise<Report> {
  return postJson("/report", { file_id: fileId, filename });
}

export async function downloadPdf(
  fileId: string,
  extras: {
    filename?: string;
    narrative?: string;
    insights?: string;
    comment?: string;
    report_charts?: ReportChart[];
  },
): Promise<Blob> {
  const res = await fetch("/api/report/pdf", {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify({
      file_id: fileId,
      filename: extras.filename,
      narrative: extras.narrative,
      insights: extras.insights,
      comment: extras.comment,
      report_charts: extras.report_charts?.map((c) => ({
        title: c.title,
        plotly_json: c.plotly_json,
      })),
    }),
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.blob();
}

export async function deleteFile(fileId: string): Promise<void> {
  const res = await fetch(`/api/file/${fileId}`, {
    method: "DELETE",
    headers: headers(false),
  });
  if (!res.ok) throw new Error(await readError(res));
}
