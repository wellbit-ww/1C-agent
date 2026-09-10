import type { FileContext } from "./types";

export function FileBrief({ ctx }: { ctx: FileContext }) {
  const sheets = ctx.sheets ?? [];
  const metrics = ctx.metrics ?? [];
  const groupers = ctx.groupers ?? [];
  const ideas = ctx.dashboard_ideas ?? [];
  const caveats = ctx.caveats ?? [];
  const hasBody =
    Boolean(ctx.title || ctx.report_kind || ctx.grain) ||
    sheets.length > 1 ||
    metrics.length > 0 ||
    groupers.length > 0 ||
    ideas.length > 0 ||
    caveats.length > 0;
  if (!hasBody) return null;

  return (
    <details className="mb-4 rounded-xl border border-line bg-card">
      <summary className="cursor-pointer px-3 py-2 text-sm text-zinc-300">
        Как ИИ видит этот файл
      </summary>
      <div className="space-y-2 border-t border-line px-3 py-3 text-sm text-zinc-400">
        {ctx.title && <p className="font-medium text-zinc-200">{ctx.title}</p>}
        {ctx.report_kind && <p className="text-xs text-zinc-500">{ctx.report_kind}</p>}
        {ctx.grain && <p className="text-xs">{ctx.grain}</p>}
        {sheets.length > 1 && (
          <div>
            <div className="mb-1 text-xs text-zinc-500">Листы книги</div>
            {sheets.map((sheet) => (
              <p key={sheet.name}>
                · {sheet.name}: {sheet.rows} строк, {sheet.n_columns} колонок
                {sheet.active ? " — рабочий" : ""}
              </p>
            ))}
          </div>
        )}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {metrics.length > 0 && (
            <div>
              <div className="mb-1 text-xs text-zinc-500">Метрики</div>
              {metrics.slice(0, 8).map((name) => (
                <p key={name}>· {name}</p>
              ))}
            </div>
          )}
          {groupers.length > 0 && (
            <div>
              <div className="mb-1 text-xs text-zinc-500">Разрезы</div>
              {groupers.slice(0, 8).map((name) => (
                <p key={name}>· {name}</p>
              ))}
            </div>
          )}
        </div>
        {ideas.length > 0 && (
          <div>
            <div className="mb-1 text-xs text-zinc-500">Что можно собрать</div>
            {ideas.slice(0, 6).map((idea) => (
              <p key={idea}>· {idea}</p>
            ))}
          </div>
        )}
        {caveats.length > 0 && (
          <p className="text-xs text-zinc-500">Ограничения: {caveats.slice(0, 4).join("; ")}</p>
        )}
        <p className="text-xs text-zinc-600">
          {ctx.llm_ready ? "Карточка собрана моделью" : "Краткая карточка по колонкам"}
        </p>
      </div>
    </details>
  );
}
