import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "./api";
import { ChatPanel } from "./ChatPanel";
import { FileBrief } from "./FileBrief";
import { PlotChart } from "./PlotChart";
import { ChartEditor, type ChartEditorMode } from "./SpecEditor";
import { clearSession, readSession, writeSession, type SavedSession } from "./session";
import type { ChatMessage, Dashboard, DashSpec, FileContext, PivotTable, Report, ReportChart, SectionBlock } from "./types";

const TYPE_NAMES: Record<string, string> = {
  sales_pipeline: "Этапы продаж",
  deficit_report: "Дефицит / задолженность",
  pdo_report: "Отчёт ПДО",
  warranty: "Гарантия",
  sales_forecast: "Прогноз продаж",
  supplier_orders: "Заказы поставщикам",
  planned_receipts: "Планируемые поступления",
  incoming_requests: "Входящие запросы",
};

function typeName(t?: string) {
  return TYPE_NAMES[t ?? ""] ?? "Отчёт";
}

function formatPivotNumber(value: number | null | undefined, kind?: string) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  const n = Number(value);
  const text = Number.isInteger(n)
    ? n.toLocaleString("ru-RU")
    : n.toLocaleString("ru-RU", { maximumFractionDigits: 1 });
  return kind === "percent" ? `${text}%` : text;
}

function isYearTotalColumn(name: string) {
  return /^\d{4}$/.test(String(name).trim());
}

function quarterStarts(spans: { count: number }[]) {
  const starts = new Set<number>();
  let cursor = 0;
  for (const span of spans) {
    if (cursor > 0) starts.add(cursor);
    cursor += span.count;
  }
  return starts;
}

function formatSectionCell(value: number | null | undefined, kind?: string) {
  if (value == null || kind === "empty" || Number.isNaN(Number(value))) return "";
  const n = Number(value);
  if (kind === "percent") return `${Math.round(n)}%`;
  if (kind === "percent1") {
    return `${n.toLocaleString("ru-RU", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`;
  }
  const digits = kind === "count" || Number.isInteger(n) ? 0 : 2;
  return n.toLocaleString("ru-RU", { maximumFractionDigits: digits });
}

function HalfYearTables({ sections }: { sections: SectionBlock[] }) {
  return (
    <div className="space-y-6">
      {sections.map((block) => (
        <section key={block.title}>
          <h3 className="mb-3 text-sm font-semibold text-zinc-100">{block.title}</h3>
          <div className="grid gap-3 md:grid-cols-2">
            {block.tables.map((item) => (
              <div key={`${block.title}-${item.title}`} className="overflow-hidden rounded-lg border border-line">
                <div className="border-b border-line bg-panel px-3 py-1.5 text-sm font-medium text-zinc-100">
                  {item.title}
                </div>
                <table className="w-full border-collapse text-right text-xs">
                  <thead>
                    <tr className="text-zinc-500">
                      <th className="border-b border-line px-2 py-1.5 text-left font-medium" />
                      {item.columns.map((col) => (
                        <th key={col} className="border-b border-line px-2 py-1.5 font-medium whitespace-nowrap">
                          {col}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {item.rows.map((row) => (
                      <tr key={row.label} className="border-t border-line">
                        <th className="px-2 py-1.5 text-left font-medium text-zinc-200">{row.label}</th>
                        {item.columns.map((col, i) => (
                          <td key={col} className="px-2 py-1.5 tabular-nums text-zinc-100">
                            {formatSectionCell(row.values[i], row.kinds?.[i])}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function PivotTableView({ table }: { table: PivotTable }) {
  const spans = table.year_spans ?? [];
  const hasYears = spans.some((span) => span.count > 1) || spans.length > 1;
  const breaks = quarterStarts(spans);
  const quarterEdge = "border-l border-zinc-400";
  return (
    <div className="max-h-[28rem] overflow-auto rounded-lg border border-line">
      <table className="min-w-full border-collapse text-right text-xs">
        <thead className="sticky top-0 z-10 bg-panel">
          {hasYears && (
            <tr className="text-zinc-400">
              <th
                rowSpan={2}
                className="sticky left-0 z-20 border-b border-r border-line bg-panel px-3 py-1.5 text-left font-medium text-zinc-300"
              >
                {table.index_label || "Подразделение"}
              </th>
              {spans.map((span, spanI) => (
                <th
                  key={span.label}
                  colSpan={span.count}
                  className={`border-b border-line px-2 py-1.5 text-center font-semibold text-zinc-200 ${
                    spanI > 0 ? quarterEdge : ""
                  }`}
                >
                  {span.label}
                </th>
              ))}
            </tr>
          )}
          <tr className="text-zinc-500">
            {!hasYears && (
              <th className="sticky left-0 z-20 border-b border-r border-line bg-panel px-3 py-1.5 text-left font-medium text-zinc-300">
                {table.index_label || "Подразделение"}
              </th>
            )}
            {table.columns.map((col, i) => (
              <th
                key={`${i}-${col}`}
                className={`border-b border-line px-2 py-1.5 font-medium whitespace-nowrap ${
                  isYearTotalColumn(col) ? "text-zinc-200" : ""
                } ${breaks.has(i) ? quarterEdge : ""}`}
              >
                {hasYears && isYearTotalColumn(col) ? col : hasYears ? col.replace(/\s+\d{4}$/, "") : col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row) => (
            <tr key={row.label} className="border-t border-line">
              <th className="sticky left-0 bg-card px-3 py-1.5 text-left font-medium text-zinc-200">
                {row.label}
              </th>
              {table.columns.map((col, i) => (
                <td
                  key={`${row.label}-${i}`}
                  className={`px-2 py-1.5 tabular-nums ${
                    isYearTotalColumn(col) || table.column_kinds?.[i] === "percent"
                      ? "font-medium text-zinc-100"
                      : "text-zinc-300"
                  } ${breaks.has(i) ? quarterEdge : ""}`}
                >
                  {formatPivotNumber(row.values[i], table.column_kinds?.[i])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
        {table.totals && table.totals.length > 0 && (
          <tfoot>
            <tr className="border-t border-line bg-panel font-medium">
              <th className="sticky left-0 bg-panel px-3 py-1.5 text-left text-zinc-100">
                {table.totals_label || "Итого"}
              </th>
              {table.totals.map((value, i) => (
                <td
                  key={`total-${i}`}
                  className={`px-2 py-1.5 tabular-nums text-accent ${breaks.has(i) ? quarterEdge : ""}`}
                >
                  {formatPivotNumber(value, table.column_kinds?.[i])}
                </td>
              ))}
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}

function reportChartVerb(chart: ReportChart, removed: boolean) {
  if (chart.table) return removed ? "убрана" : "добавлена";
  return removed ? "убран" : "добавлен";
}

function reportChartNotice(chart: ReportChart, removed: boolean) {
  const verb = reportChartVerb(chart, removed);
  return removed ? `«${chart.title}» ${verb} из отчёта` : `«${chart.title}» ${verb} в отчёт`;
}

function GrowingTextarea({
  value,
  onChange,
  className,
  minHeight = 192,
}: {
  value: string;
  onChange: (value: string) => void;
  className?: string;
  minHeight?: number;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.max(minHeight, el.scrollHeight)}px`;
  }, [value, minHeight]);
  return (
    <textarea
      ref={ref}
      value={value}
      rows={8}
      onChange={(e) => onChange(e.target.value)}
      className={`overflow-hidden ${className ?? ""}`}
    />
  );
}

function isNarrowViewport() {
  return typeof window !== "undefined" && window.innerWidth <= 900;
}

const LLM_ESTIMATE_SEC: Record<string, number> = {
  "Собираю дашборд…": 80,
  "Правлю дашборд…": 60,
  "Формирую отчёт…": 55,
  "Комментарии…": 40,
};

function formatRemain(seconds: number) {
  const min = Math.floor(seconds / 60);
  const sec = seconds % 60;
  if (min > 0) return `${min} мин ${sec} с`;
  return `${sec} с`;
}

function useBusyLabel(busy: string | null) {
  const [left, setLeft] = useState<number | null>(null);
  useEffect(() => {
    const total = busy ? LLM_ESTIMATE_SEC[busy] : undefined;
    if (!busy || total == null) {
      setLeft(null);
      return;
    }
    const started = Date.now();
    setLeft(total);
    const id = window.setInterval(() => {
      const elapsed = Math.floor((Date.now() - started) / 1000);
      setLeft(Math.max(0, total - elapsed));
    }, 1000);
    return () => window.clearInterval(id);
  }, [busy]);
  if (!busy) return null;
  if (left == null) return busy;
  const stem = busy.replace(/…$/, "");
  if (left <= 0) return `${stem}… ещё немного`;
  return `${stem}… ещё около ${formatRemain(left)}`;
}

type View = "dash" | "report";

export function App() {
  const [backendOk, setBackendOk] = useState(false);
  const [view, setView] = useState<View>("dash");
  const [fileId, setFileId] = useState<string | null>(null);
  const [filename, setFilename] = useState<string | null>(null);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [ctx, setCtx] = useState<FileContext | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [table, setTable] = useState<Record<string, unknown>[] | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [reportCharts, setReportCharts] = useState<ReportChart[]>([]);
  const [narrative, setNarrative] = useState("");
  const [insightsText, setInsightsText] = useState("");
  const [comment, setComment] = useState("");
  const [comments, setComments] = useState<Record<string, string>>({});
  const [tab, setTab] = useState(0);
  const [dashPrompt, setDashPrompt] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [chatOpen, setChatOpen] = useState(() => !isNarrowViewport());
  const [chartEditor, setChartEditor] = useState<ChartEditorMode | null>(null);
  const [specBackup, setSpecBackup] = useState<DashSpec | null>(null);
  const busyLabel = useBusyLabel(busy);
  const fileRef = useRef<HTMLInputElement>(null);
  const fileIdRef = useRef<string | null>(null);
  const restoredRef = useRef(false);

  useEffect(() => {
    void api.checkBackend().then(setBackendOk);
    const id = window.setInterval(() => {
      void api.checkBackend().then(setBackendOk);
    }, 15000);
    return () => window.clearInterval(id);
  }, []);

  const loadFile = useCallback(async (id: string, name: string, restore?: SavedSession) => {
    fileIdRef.current = id;
    setBusy("Читаю выгрузку…");
    setError(null);
    try {
      const [dash, history] = await Promise.all([
        api.getDashboard(id),
        api.getHistory(id).catch(() => ({ messages: [] as ChatMessage[] })),
      ]);
      if (fileIdRef.current !== id) return;
      setDashboard(dash);
      setCtx(dash.file_context ?? null);
      setMessages(history.messages ?? []);
      setFilename(name);
      setFileId(id);
      const tabCount = dash.tabs?.length ?? 0;
      if (restore) {
        setTab(tabCount ? Math.min(restore.tab, tabCount - 1) : 0);
      } else {
        setView("dash");
        setTab(0);
        setComments({});
        setReport(null);
        setReportCharts([]);
      }
      setBusy(null);
      void api
        .enrichContext(id)
        .then((brief) => {
          if (fileIdRef.current === id) setCtx(brief);
        })
        .catch(() => {
          /* карточка по колонкам уже в dashboard */
        });
      void api
        .getTable(id)
        .then((rows) => {
          if (fileIdRef.current === id) setTable(rows.data ?? []);
        })
        .catch(() => {
          if (fileIdRef.current === id) setTable(null);
        });
      if (restore?.view === "report") {
        try {
          const rep = await api.getReport(id, name);
          if (fileIdRef.current !== id) return;
          setReport(rep);
          setNarrative(rep.narrative ?? "");
          const ins = rep.insights;
          setInsightsText(Array.isArray(ins) ? ins.join("\n") : (ins ?? ""));
          setComment(rep.comment ?? "");
          setView("report");
        } catch {
          setView("dash");
        }
      }
    } catch (exc) {
      if (fileIdRef.current !== id) return;
      const message = exc instanceof Error ? exc.message : String(exc);
      if (restore) {
        clearSession();
        fileIdRef.current = null;
        setFileId(null);
        setFilename(null);
        setDashboard(null);
        setMessages([]);
        setReportCharts([]);
        setError("Сессия не найдена на сервере. Загрузите файл снова.");
      } else {
        setError(message);
      }
      setBusy(null);
    }
  }, []);

  useEffect(() => {
    if (restoredRef.current) return;
    restoredRef.current = true;
    const saved = readSession();
    if (!saved) return;
    setChatOpen(saved.chatOpen && !isNarrowViewport());
    setReportCharts(saved.reportCharts);
    void loadFile(saved.fileId, saved.filename, saved);
  }, [loadFile]);

  useEffect(() => {
    if (!fileId || !filename) return;
    writeSession({
      fileId,
      filename,
      view,
      tab,
      chatOpen,
      reportCharts,
    });
  }, [fileId, filename, view, tab, chatOpen, reportCharts]);

  async function onUpload(file: File) {
    setBusy("Загружаю файл…");
    setError(null);
    try {
      const { file_id } = await api.uploadFile(file);
      await loadFile(file_id, file.name);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
      setBusy(null);
    }
  }

  async function onSend(question: string) {
    if (!fileId) return;
    setMessages((m) => [...m, { role: "user", content: question }]);
    setBusy("Отвечаю…");
    try {
      const result = await api.sendChat(fileId, question);
      setMessages((m) => [
        ...m,
        { role: "assistant", content: result.answer, charts: result.charts },
      ]);
    } catch (exc) {
      setMessages((m) => [
        ...m,
        { role: "assistant", content: exc instanceof Error ? exc.message : String(exc) },
      ]);
    } finally {
      setBusy(null);
    }
  }

  async function onGenerate(edit: boolean) {
    if (!fileId || !dashPrompt.trim()) return;
    if (!edit && (dashboard?.tabs?.length ?? 0) > 0) {
      const ok = window.confirm(
        "Сгенерировать дашборд заново? Текущие вкладки и закреплённые графики будут заменены. После этого можно вернуть прежний дашборд.",
      );
      if (!ok) return;
    }
    const previousSpec = dashboard?.spec ?? null;
    setBusy(edit ? "Правлю дашборд…" : "Собираю дашборд…");
    setError(null);
    try {
      const fn = edit ? api.dashboardEdit : api.dashboardGenerate;
      const patch = await fn(fileId, dashPrompt.trim());
      setDashboard((cur) => ({ ...(cur ?? {}), ...patch, tabs: patch.tabs ?? cur?.tabs, spec: patch.spec ?? cur?.spec }));
      if (patch.warning) setError(patch.warning);
      setTab(0);
      setChartEditor(null);
      if (!edit && previousSpec) {
        setSpecBackup(previousSpec);
        setNotice("Дашборд собран заново. Можно вернуть прежний.");
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(null);
    }
  }

  async function onComments() {
    if (!fileId) return;
    setBusy("Комментарии…");
    try {
      const res = await api.dashboardComments(fileId);
      setComments(res.comments ?? {});
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(null);
    }
  }

  async function onPin(spec: Record<string, unknown>) {
    if (!fileId) return;
    setBusy("Закрепляю график…");
    try {
      await api.dashboardPin(fileId, spec);
      const dash = await api.getDashboard(fileId);
      setDashboard(dash);
      setView("dash");
      const title = String(spec.title ?? "");
      const idx = (dash.tabs ?? []).findIndex((item) => item.tiles.some((tile) => tile.title === title));
      if (idx >= 0) setTab(idx);
      const tabTitle = idx >= 0 ? dash.tabs?.[idx]?.title : undefined;
      setNotice(tabTitle ? `График закреплён на вкладке «${tabTitle}»` : "График закреплён на дашборде");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(null);
    }
  }

  async function onRestoreSpec() {
    if (!fileId || !specBackup) return;
    setBusy("Возвращаю дашборд…");
    setError(null);
    try {
      const patch = await api.dashboardSaveSpec(fileId, specBackup);
      setDashboard((cur) => ({
        ...(cur ?? {}),
        ...patch,
        tabs: patch.tabs ?? cur?.tabs,
        spec: patch.spec ?? specBackup,
      }));
      setSpecBackup(null);
      setChartEditor(null);
      setTab(0);
      setNotice("Прежний дашборд возвращён");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(null);
    }
  }

  async function onSaveSpec(spec: DashSpec) {
    if (!fileId) return;
    setBusy("Сохраняю дашборд…");
    setError(null);
    try {
      const patch = await api.dashboardSaveSpec(fileId, spec);
      setDashboard((cur) => ({
        ...(cur ?? {}),
        ...patch,
        tabs: patch.tabs ?? cur?.tabs,
        spec: patch.spec ?? spec,
      }));
      setChartEditor(null);
      setNotice("Дашборд сохранён");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(null);
    }
  }

  function toggleReportChart(chart: ReportChart) {
    const exists = reportCharts.some((c) => c.id === chart.id);
    if (!exists && reportCharts.length >= 8) {
      setNotice("В отчёт можно добавить не больше 8 графиков");
      return;
    }
    setReportCharts((cur) =>
      exists ? cur.filter((c) => c.id !== chart.id) : [...cur, chart],
    );
    setNotice(reportChartNotice(chart, exists));
  }

  async function openReport() {
    if (!fileId) return;
    setBusy("Формирую отчёт…");
    setError(null);
    try {
      const rep = await api.getReport(fileId, filename ?? undefined);
      setReport(rep);
      setNarrative(rep.narrative ?? "");
      const ins = rep.insights;
      setInsightsText(Array.isArray(ins) ? ins.join("\n") : (ins ?? ""));
      setComment(rep.comment ?? "");
      setView("report");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(null);
    }
  }

  async function onPdf() {
    if (!fileId) return;
    setBusy("PDF…");
    try {
      const blob = await api.downloadPdf(fileId, {
        filename: filename ?? undefined,
        narrative,
        insights: insightsText,
        comment,
        report_charts: reportCharts.length ? reportCharts : undefined,
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = (filename ?? "report").replace(/\.[^.]+$/, "") + ".pdf";
      a.click();
      URL.revokeObjectURL(url);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(null);
    }
  }

  async function resetFile() {
    if (fileId) {
      try {
        await api.deleteFile(fileId);
      } catch {
        /* локальный сброс всё равно */
      }
    }
    fileIdRef.current = null;
    clearSession();
    setFileId(null);
    setFilename(null);
    setDashboard(null);
    setCtx(null);
    setMessages([]);
    setTable(null);
    setReport(null);
    setReportCharts([]);
    setChartEditor(null);
    setSpecBackup(null);
    setView("dash");
  }

  const tabs = dashboard?.tabs ?? [];
  const kpis = dashboard?.kpis ?? [];
  const meta = dashboard?.metadata;

  return (
    <div className="relative flex h-full w-full overflow-hidden">
      <nav className="flex w-16 shrink-0 flex-col items-center gap-2 border-r border-line bg-panel py-4">
        <div className="mb-4 h-8 w-8 rounded-lg bg-accent-dim text-center text-sm leading-8 text-accent">
          1C
        </div>
        <NavBtn active={view === "dash"} label="Дашборд" onClick={() => setView("dash")} />
        <NavBtn
          active={view === "report"}
          label="Отчёт"
          disabled={!fileId}
          onClick={() => void openReport()}
        />
        <NavBtn
          active={chatOpen}
          label="Чат"
          onClick={() => setChatOpen((open) => !open)}
        />
        <div className="mt-auto flex flex-col items-center gap-2">
          <span
            className={`h-2 w-2 rounded-full ${backendOk ? "bg-accent" : "bg-red-500"}`}
            title={backendOk ? "Backend доступен" : "Backend недоступен"}
          />
        </div>
      </nav>

      <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <header className="flex items-center gap-3 border-b border-line px-5 py-3">
          <button
            type="button"
            className="rounded-lg border border-line bg-card px-3 py-1.5 text-sm hover:border-accent/50"
            onClick={() => fileRef.current?.click()}
          >
            Загрузить
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".xlsx,.xls,.csv"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void onUpload(f);
              e.target.value = "";
            }}
          />
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm">
              {filename ?? "Файл не выбран"}
            </div>
            <div className="text-xs text-zinc-500">
              {busyLabel ?? (fileId ? typeName(dashboard?.report_type) : "Excel Agent")}
            </div>
          </div>
          {fileId && (
            <button
              type="button"
              className="rounded-lg border border-line px-3 py-1.5 text-sm text-zinc-300 hover:border-red-400/50 hover:text-red-200"
              onClick={() => void resetFile()}
            >
              Закрыть файл
            </button>
          )}
        </header>

        {notice && (
          <div className="border-b border-accent/30 bg-accent-dim px-5 py-2 text-sm text-accent">
            {notice}
            <button className="ml-3 text-xs underline" onClick={() => setNotice(null)}>
              скрыть
            </button>
          </div>
        )}
        {error && (
          <div className="border-b border-red-900/60 bg-red-950/40 px-5 py-2 text-sm text-red-200">
            {error}
            <button className="ml-3 text-xs underline" onClick={() => setError(null)}>
              скрыть
            </button>
          </div>
        )}

        {view === "report" && report ? (
          <ReportPane
            report={report}
            narrative={narrative}
            insightsText={insightsText}
            comment={comment}
            charts={reportCharts}
            onNarrative={setNarrative}
            onInsights={setInsightsText}
            onComment={setComment}
            onRemoveChart={(id) => {
              const item = reportCharts.find((c) => c.id === id);
              setReportCharts((cur) => cur.filter((c) => c.id !== id));
              if (item) setNotice(reportChartNotice(item, true));
            }}
            onBack={() => setView("dash")}
            onPdf={() => void onPdf()}
          />
        ) : (
          <div className="flex min-h-0 flex-1 overflow-hidden">
          <div className="min-h-0 flex-1 overflow-y-auto p-5">
            {!fileId ? (
              <EmptyState onPick={() => fileRef.current?.click()} />
            ) : (
              <>
                {ctx?.summary && (
                  <p className="mb-3 max-w-3xl text-sm text-zinc-400">{ctx.summary}</p>
                )}
                {dashboard?.summary && dashboard.summary !== ctx?.summary && (
                  <p className="mb-3 max-w-3xl rounded-xl border border-line bg-card px-3 py-2 text-sm text-zinc-300">
                    {dashboard.summary}
                  </p>
                )}
                {ctx && <FileBrief ctx={ctx} />}
                {meta && (
                  <p className="mb-4 text-xs text-zinc-500">
                    Период {meta.period ?? "—"} · {meta.rows ?? 0} строк · {meta.columns ?? 0} колонок
                  </p>
                )}
                {kpis.length > 0 && (
                  <div className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
                    {kpis.map((kpi) => (
                      <div key={kpi.label} className="rounded-xl border border-line bg-card px-4 py-3">
                        <div className="text-xs text-zinc-500">{kpi.label}</div>
                        <div className="mt-1 text-lg font-medium text-accent">{String(kpi.value)}</div>
                      </div>
                    ))}
                  </div>
                )}
                {dashboard?.insights && dashboard.insights.length > 0 && (
                  <ul className="mb-5 space-y-1 text-sm text-zinc-300">
                    {dashboard.insights.map((item) => (
                      <li key={item}>· {item}</li>
                    ))}
                  </ul>
                )}

                <div className="mb-3 flex flex-wrap gap-2">
                  <input
                    value={dashPrompt}
                    onChange={(e) => setDashPrompt(e.target.value)}
                    placeholder="Собери дашборд по менеджерам…"
                    className="min-w-56 flex-1 rounded-lg border border-line bg-card px-3 py-2 text-sm outline-none focus:border-accent/60"
                  />
                  <button
                    type="button"
                    className="rounded-lg bg-accent px-3 py-2 text-sm font-medium text-bg disabled:opacity-40"
                    disabled={!dashPrompt.trim() || !!busy}
                    onClick={() => void onGenerate(false)}
                  >
                    Сгенерировать
                  </button>
                  <button
                    type="button"
                    className="rounded-lg border border-line px-3 py-2 text-sm disabled:opacity-40"
                    disabled={!dashPrompt.trim() || !!busy}
                    onClick={() => void onGenerate(true)}
                  >
                    Правка
                  </button>
                  {specBackup && (
                    <button
                      type="button"
                      className="rounded-lg border border-accent/40 px-3 py-2 text-sm text-accent disabled:opacity-40"
                      disabled={!!busy}
                      onClick={() => void onRestoreSpec()}
                    >
                      Вернуть прежний дашборд
                    </button>
                  )}
                  <button
                    type="button"
                    className="rounded-lg border border-line px-3 py-2 text-sm"
                    disabled={!!busy}
                    onClick={() => void onComments()}
                  >
                    Комментарии
                  </button>
                </div>

                {tabs.length > 0 && (
                  <>
                    <div className="mb-3 flex flex-wrap items-center gap-1">
                      {tabs.map((t, i) => (
                        <button
                          key={t.title}
                          type="button"
                          onClick={() => {
                            setTab(i);
                            setChartEditor(null);
                          }}
                          className={
                            i === tab
                              ? "rounded-lg bg-accent-dim px-3 py-1.5 text-sm text-accent"
                              : "rounded-lg px-3 py-1.5 text-sm text-zinc-400 hover:text-zinc-200"
                          }
                        >
                          {t.title}
                        </button>
                      ))}
                      {dashboard?.spec && (
                        <button
                          type="button"
                          className="ml-auto rounded-lg border border-line px-3 py-1.5 text-sm text-zinc-300 hover:border-accent/50 hover:text-accent"
                          onClick={() => setChartEditor({ mode: "add", tabI: tab })}
                        >
                          Добавить график
                        </button>
                      )}
                    </div>
                    {comments[tabs[tab]?.title] && (
                      <p className="mb-3 rounded-lg border border-line bg-card px-3 py-2 text-sm text-zinc-300">
                        {comments[tabs[tab].title]}
                      </p>
                    )}
                    <div className="grid min-w-0 grid-cols-1 gap-3 xl:grid-cols-2">
                      {(tabs[tab]?.tiles ?? []).map((tile, tileI) => {
                        const id = `${tabs[tab]?.title ?? "tab"}::${tile.title}`;
                        const inReport = reportCharts.some((c) => c.id === id);
                        const editing =
                          chartEditor?.mode === "edit" &&
                          chartEditor.tabI === tab &&
                          chartEditor.tileI === tileI;
                        return (
                        <div
                          key={`${tile.title}-${tileI}`}
                          className={`min-w-0 overflow-hidden rounded-xl border bg-card p-3 ${
                            editing ? "border-accent/60" : "border-line"
                          } ${tile.table || tile.sections || (tabs[tab]?.tiles ?? []).length === 1 ? "xl:col-span-2" : ""}`}
                        >
                          <div className="mb-2 flex items-start justify-between gap-2">
                            <div className="text-sm font-medium">{tile.title}</div>
                            <div className="flex shrink-0 gap-1">
                              {dashboard?.spec && (
                                <button
                                  type="button"
                                  className="rounded-md border border-line px-2 py-0.5 text-[11px] text-zinc-300 hover:border-accent/50 hover:text-accent"
                                  onClick={() => setChartEditor({ mode: "edit", tabI: tab, tileI })}
                                >
                                  Настроить
                                </button>
                              )}
                              {(tile.plotly_json || tile.table) && (
                                <button
                                  type="button"
                                  className="rounded-md border border-line px-2 py-0.5 text-[11px] text-zinc-300 hover:border-accent/50 hover:text-accent"
                                  onClick={() =>
                                    toggleReportChart({
                                      id,
                                      title: tile.title,
                                      plotly_json: tile.plotly_json,
                                      table: tile.table,
                                    })
                                  }
                                >
                                  {inReport ? "В отчёте" : "В отчёт"}
                                </button>
                              )}
                            </div>
                          </div>
                          {tile.error ? (
                            <p className="text-sm text-amber-300">{tile.error}</p>
                          ) : tile.sections ? (
                            <HalfYearTables sections={tile.sections} />
                          ) : tile.table ? (
                            <PivotTableView table={tile.table} />
                          ) : tile.plotly_json ? (
                            <PlotChart json={tile.plotly_json} />
                          ) : null}
                        </div>
                        );
                      })}
                    </div>
                  </>
                )}

                {!tabs.length && (dashboard?.charts ?? []).length > 0 && (
                  <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
                    {dashboard!.charts!.map((c, i) =>
                      c.plotly_json ? (
                        <div key={i} className="rounded-xl border border-line bg-card p-3">
                          <div className="mb-2 flex items-start justify-between gap-2">
                            <div className="text-sm font-medium">{c.title ?? "График"}</div>
                            <button
                              type="button"
                              className="shrink-0 rounded-md border border-line px-2 py-0.5 text-[11px] text-zinc-300 hover:border-accent/50 hover:text-accent"
                              onClick={() =>
                                toggleReportChart({
                                  id: `chart::${c.title ?? i}`,
                                  title: c.title ?? "График",
                                  plotly_json: c.plotly_json!,
                                })
                              }
                            >
                              {reportCharts.some((x) => x.id === `chart::${c.title ?? i}`)
                                ? "В отчёте"
                                : "В отчёт"}
                            </button>
                          </div>
                          <PlotChart json={c.plotly_json} />
                        </div>
                      ) : null,
                    )}
                  </div>
                )}

                {table && table.length > 0 && (
                  <details className="mt-6">
                    <summary className="cursor-pointer text-sm text-zinc-400">
                      Детализация ({table.length} строк)
                    </summary>
                    <div className="mt-2 max-h-80 overflow-auto rounded-xl border border-line">
                      <table className="min-w-full text-left text-xs">
                        <thead className="sticky top-0 bg-panel text-zinc-500">
                          <tr>
                            {Object.keys(table[0]).slice(0, 12).map((col) => (
                              <th key={col} className="px-2 py-1 font-medium">
                                {col}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {table.slice(0, 50).map((row, i) => (
                            <tr key={i} className="border-t border-line">
                              {Object.keys(table[0]).slice(0, 12).map((col) => (
                                <td key={col} className="max-w-40 truncate px-2 py-1 text-zinc-300">
                                  {String(row[col] ?? "")}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </details>
                )}
              </>
            )}
          </div>
          {chartEditor && dashboard?.spec && (
            <ChartEditor
              key={`${chartEditor.mode}-${chartEditor.tabI}-${chartEditor.mode === "edit" ? chartEditor.tileI : "new"}`}
              spec={dashboard.spec}
              columns={meta?.column_names ?? []}
              busy={!!busy}
              mode={chartEditor}
              onClose={() => setChartEditor(null)}
              onSave={(spec) => void onSaveSpec(spec)}
            />
          )}
          </div>
        )}
      </main>

      <ChatPanel
        className={
          chatOpen
            ? "flex h-full w-[400px] shrink-0 flex-col border-l border-line bg-panel max-[900px]:absolute max-[900px]:top-0 max-[900px]:right-0 max-[900px]:z-30 max-[900px]:h-full max-[900px]:w-[min(22rem,calc(100%-4rem))] max-[900px]:shadow-2xl"
            : "hidden"
        }
        disabled={!fileId}
        reportType={dashboard?.report_type}
        ideas={ctx?.dashboard_ideas}
        messages={messages}
        busy={busy === "Отвечаю…"}
        canPin={!!tabs.length}
        reportChartIds={reportCharts.map((c) => c.id)}
        onSend={(q) => void onSend(q)}
        onPin={(spec) => void onPin(spec)}
        onAddToReport={(chart) => toggleReportChart(chart)}
        onClose={() => setChatOpen(false)}
      />
    </div>
  );
}

function NavBtn({
  active,
  label,
  onClick,
  disabled,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={`w-12 rounded-lg py-2 text-[10px] leading-tight ${
        active ? "bg-accent-dim text-accent" : "text-zinc-500 hover:text-zinc-200"
      } disabled:opacity-30`}
    >
      {label}
    </button>
  );
}

function EmptyState({ onPick }: { onPick: () => void }) {
  return (
    <div className="flex h-full flex-col items-center justify-center text-center">
      <p className="text-lg font-medium">Загрузите выгрузку 1С</p>
      <p className="mt-2 max-w-md text-sm text-zinc-500">
        Дашборд, отчёт и чат появятся после файла .xlsx / .xls / .csv
      </p>
      <button
        type="button"
        onClick={onPick}
        className="mt-6 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-bg"
      >
        Выбрать файл
      </button>
    </div>
  );
}

function ReportPane({
  report,
  narrative,
  insightsText,
  comment,
  charts,
  onNarrative,
  onInsights,
  onComment,
  onRemoveChart,
  onBack,
  onPdf,
}: {
  report: Report;
  narrative: string;
  insightsText: string;
  comment: string;
  charts: ReportChart[];
  onNarrative: (v: string) => void;
  onInsights: (v: string) => void;
  onComment: (v: string) => void;
  onRemoveChart: (id: string) => void;
  onBack: () => void;
  onPdf: () => void;
}) {
  const meta = report.metadata;
  const q = report.data_quality;
  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-5">
      <div className="mb-4 flex items-center gap-3">
        <button type="button" className="text-sm text-zinc-400 hover:text-zinc-100" onClick={onBack}>
          К дашборду
        </button>
        <h1 className="flex-1 text-lg font-medium">{typeName(report.report_type)}</h1>
        <button
          type="button"
          className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-bg"
          onClick={onPdf}
        >
          Скачать PDF
        </button>
      </div>
      <p className="mb-4 text-xs text-zinc-500">
        {meta?.filename} · {meta?.period ?? "—"} · {meta?.rows ?? 0} строк
      </p>
      <label className="mb-1 block text-xs text-zinc-500">Резюме</label>
      <GrowingTextarea
        value={narrative}
        onChange={onNarrative}
        className="mb-4 w-full rounded-xl border border-line bg-card p-3 text-sm outline-none focus:border-accent/60"
      />
      <label className="mb-1 block text-xs text-zinc-500">Выводы (по строке)</label>
      <textarea
        value={insightsText}
        onChange={(e) => onInsights(e.target.value)}
        className="mb-4 h-28 w-full rounded-xl border border-line bg-card p-3 text-sm outline-none focus:border-accent/60"
      />
      <label className="mb-1 block text-xs text-zinc-500">Комментарий</label>
      <textarea
        value={comment}
        onChange={(e) => onComment(e.target.value)}
        className="mb-4 h-20 w-full rounded-xl border border-line bg-card p-3 text-sm outline-none focus:border-accent/60"
      />
      {charts.length > 0 && (
        <>
          <div className="mb-2 text-xs text-zinc-500">Графики в отчёте</div>
          <div className="mb-4 grid min-w-0 grid-cols-1 gap-4">
            {charts.map((chart) => (
              <div key={chart.id} className="min-w-0 overflow-hidden rounded-xl border border-line bg-card p-3">
                <div className="mb-2 flex items-start justify-between gap-2">
                  <div className="text-sm font-medium">{chart.title}</div>
                  <button
                    type="button"
                    className="shrink-0 text-[11px] text-zinc-500 hover:text-red-300"
                    onClick={() => onRemoveChart(chart.id)}
                  >
                    Убрать
                  </button>
                </div>
                {chart.table ? (
                  <PivotTableView table={chart.table} />
                ) : chart.plotly_json ? (
                  <PlotChart json={chart.plotly_json} />
                ) : null}
              </div>
            ))}
          </div>
        </>
      )}
      {q && (
        <p className="text-xs text-zinc-500">
          Ячеек {q.total_cells ?? 0} · пропуски {q.null_cells ?? 0} ({q.null_pct ?? 0}%) ·
          дубликаты {q.duplicates ?? 0}
        </p>
      )}
    </div>
  );
}
