import { useEffect, useMemo, useRef, useState } from "react";
import { PlotChart } from "./PlotChart";
import type { ChatMessage } from "./types";

const FALLBACK: Record<string, string[]> = {
  sales_pipeline: [
    "Общая выручка",
    "Топ-5 клиентов",
    "Топ ответственных по количеству и сумме сделок",
    "Круговая диаграмма продаж по менеджерам",
    "Динамика выручки по месяцам",
  ],
  deficit_report: [
    "Общий дефицит",
    "Топ-5 заказчиков",
    "Круговая диаграмма дефицита по подразделениям",
    "Сколько уникальных клиентов?",
  ],
};

const DEFAULT_CHIPS = ["Сколько строк в таблице?", "Какие колонки есть?", "Основные выводы"];

type Props = {
  disabled: boolean;
  reportType?: string;
  ideas?: string[];
  messages: ChatMessage[];
  busy: boolean;
  onSend: (q: string) => void;
  onPin?: (spec: Record<string, unknown>) => void;
  canPin: boolean;
};

export function ChatPanel({
  disabled,
  reportType,
  ideas,
  messages,
  busy,
  onSend,
  onPin,
  canPin,
}: Props) {
  const [text, setText] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const chips = useMemo(() => {
    const fromFile = (ideas ?? []).map((s) => s.trim()).filter(Boolean);
    const extra = FALLBACK[reportType ?? ""] ?? DEFAULT_CHIPS;
    const seen = new Set<string>();
    const out: string[] = [];
    for (const item of [...fromFile, ...extra]) {
      if (seen.has(item)) continue;
      seen.add(item);
      out.push(item);
      if (out.length >= 4) break;
    }
    return out;
  }, [ideas, reportType]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  function submit(q: string) {
    const question = q.trim();
    if (!question || disabled || busy) return;
    setText("");
    onSend(question);
  }

  return (
    <aside className="flex h-full w-[400px] shrink-0 flex-col border-l border-line bg-panel">
      <div className="border-b border-line px-4 py-3">
        <div className="text-sm font-medium">Чат</div>
        <p className="mt-1 text-xs text-zinc-500">
          Спросите про данные или попросите диаграмму
        </p>
      </div>
      <div className="flex flex-wrap gap-1.5 border-b border-line px-3 py-2">
        {chips.map((chip) => (
          <button
            key={chip}
            type="button"
            disabled={disabled || busy}
            onClick={() => submit(chip)}
            className="rounded-full border border-line bg-card px-2.5 py-1 text-left text-[11px] text-zinc-300 hover:border-accent/50 hover:text-accent disabled:opacity-40"
          >
            {chip}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-3">
        {messages.length === 0 && (
          <p className="px-1 text-xs text-zinc-500">
            {disabled
              ? "Загрузите выгрузку 1С — здесь появятся ответы и графики."
              : "Выберите подсказку или задайте вопрос по файлу."}
          </p>
        )}
        {messages.map((msg, i) => (
          <div key={`${msg.role}-${i}`} className={msg.role === "user" ? "ml-6" : "mr-2"}>
            <div
              className={
                msg.role === "user"
                  ? "rounded-2xl rounded-tr-sm bg-accent-dim px-3 py-2 text-sm text-accent"
                  : "rounded-2xl rounded-tl-sm bg-card px-3 py-2 text-sm whitespace-pre-wrap text-zinc-200"
              }
            >
              {msg.content}
            </div>
            {msg.charts?.map((chart, ci) =>
              chart.plotly_json ? (
                <div key={ci} className="mt-2 overflow-hidden rounded-xl border border-line bg-card p-2">
                  <PlotChart json={chart.plotly_json} className="h-52 w-full" />
                  {canPin && chart.pin_spec && onPin && (
                    <button
                      type="button"
                      className="mt-1 text-[11px] text-accent hover:underline"
                      onClick={() => onPin(chart.pin_spec!)}
                    >
                      На дашборд
                    </button>
                  )}
                </div>
              ) : null,
            )}
          </div>
        ))}
        {busy && <p className="text-xs text-zinc-500">Считаю…</p>}
        <div ref={endRef} />
      </div>
      <form
        className="border-t border-line p-3"
        onSubmit={(e) => {
          e.preventDefault();
          submit(text);
        }}
      >
        <input
          value={text}
          disabled={disabled || busy}
          onChange={(e) => setText(e.target.value)}
          placeholder="Задайте вопрос по файлу"
          className="w-full rounded-xl border border-line bg-bg px-3 py-2 text-sm outline-none placeholder:text-zinc-600 focus:border-accent/60"
        />
      </form>
    </aside>
  );
}
