import { useCallback, useEffect, useRef, useState } from "react";
import { copyExcelMarkup } from "./clipboard";
import * as api from "./api";
import { ChatPanel } from "./ChatPanel";
import { chartTitleCaps, chartTitleCapsFiltered, type DepartmentChartFilter } from "./chartTitle";
import { COMPANY_CHART_COLOR, departmentChartColor } from "./deptColors";
import { formatStageLabel } from "./stageLabel";
import { BriefSummary } from "./BriefSummary";
import { PlotChart } from "./PlotChart";
import { dealsListToGrid, StagesDealListView, type DealsListFiltersPatch } from "./StagesDealList";
import { ChartEditor, type ChartEditorMode } from "./SpecEditor";
import { clearSession, readSession, writeSession, type SavedSession } from "./session";
import {
  flattenWorkspaceDashboard,
  specForSourceFile,
  tabSourceFileId,
} from "./workspaceDashboard";
import type {
  ChatMessage,
  Dashboard,
  DashSpec,
  FileContext,
  MoneyBoard,
  MoneyMetric,
  MoneyRow,
  PivotTable,
  Report,
  ReportChart,
  SectionBlock,
  SectionTable,
  Tile,
} from "./types";

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

const STAGES_PERIOD_KINDS = new Set(["deal_statuses", "in_work_stages"]);

const DYNAMICS_PERIOD_KINDS = [
  "deals_dynamics",
  "deals_dynamics_departments",
  "deals_dynamics_outcome_share",
] as const;

const DEALS_ZK_BUCKETS: { id: string; label: string }[] = [
  { id: "half", label: "Полугодие" },
  { id: "quarter", label: "Квартал" },
  { id: "month", label: "Месяц" },
];

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

/** Число без экспоненты: «909030644.37», не «9.09e+8». */
function excelLiteral(value: number, digits: number) {
  return value.toFixed(digits);
}

function tsvEscape(cell: string) {
  if (/[\t\n\r"]/.test(cell)) return `"${cell.replaceAll('"', '""')}"`;
  return cell;
}

function htmlEscape(text: string) {
  return text.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

type HtmlCell = { text: string; colspan?: number; rowspan?: number; num?: string; format?: string };

function gridToTsv(rows: string[][]) {
  return rows.map((row) => row.map(tsvEscape).join("\t")).join("\r\n");
}

function gridWidth(rows: HtmlCell[][]) {
  return rows.reduce((max, row) => {
    const span = row.reduce((sum, cell) => sum + (cell.colspan ?? 1), 0);
    return Math.max(max, span);
  }, 1);
}

function displayedChars(cell: HtmlCell) {
  if (cell.format === "#,##0.00" && cell.num != null) {
    const formatted = Number(cell.num).toLocaleString("ru-RU", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    return formatted.replace(/\s/g, " ").length;
  }
  return cell.text.length;
}

/** Ширина колонки в пикселях, чтобы Excel показал число целиком, а не ######. */
function columnPixelWidths(rows: HtmlCell[][]) {
  const width = gridWidth(rows);
  const chars = Array.from({ length: width }, () => 0);
  for (const row of rows) {
    let col = 0;
    for (const cell of row) {
      const span = cell.colspan ?? 1;
      if (span === 1 && col < width) chars[col] = Math.max(chars[col], displayedChars(cell));
      col += span;
    }
  }
  return chars.map((count) => Math.max(72, Math.round(count * 9 + 28)));
}

function gridToHtml(rows: HtmlCell[][]) {
  const widths = columnPixelWidths(rows);
  const totalPx = widths.reduce((sum, px) => sum + px, 0);
  const totalPt = ((totalPx * 72) / 96).toFixed(2);
  const cols = widths
    .map((px) => {
      const pt = ((px * 72) / 96).toFixed(2);
      return `<col width="${px}" style="mso-width-source:userset;width:${pt}pt">`;
    })
    .join("");
  const body = rows
    .map((row) => {
      let col = 0;
      const cells = row
        .map((cell) => {
          const span = cell.colspan ?? 1;
          const px = widths.slice(col, col + span).reduce((sum, item) => sum + item, 0);
          const pt = ((px * 72) / 96).toFixed(2);
          col += span;
          const colspan = span > 1 ? ` colspan="${span}"` : "";
          const rowspan = cell.rowspan && cell.rowspan > 1 ? ` rowspan="${cell.rowspan}"` : "";
          const numeric = cell.num != null ? ` x:num="${cell.num}"` : "";
          const format = cell.format ? `mso-number-format:'${cell.format}';` : "";
          return `<td${colspan}${rowspan}${numeric} width="${px}" style="${format}white-space:nowrap;mso-text-control:shrinktofit;width:${pt}pt">${htmlEscape(cell.text)}</td>`;
        })
        .join("");
      return `<tr>${cells}</tr>`;
    })
    .join("");
  return `<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:x="urn:schemas-microsoft-com:office:excel" xmlns="http://www.w3.org/TR/REC-html40"><head><meta http-equiv="Content-Type" content="text/html; charset=utf-8"><meta name="ProgId" content="Excel.Sheet"><style><!--
td{white-space:nowrap;mso-text-control:shrinktofit;}
--></style></head><body><table border="0" cellpadding="0" cellspacing="0" width="${totalPx}" style="border-collapse:collapse;table-layout:fixed;width:${totalPt}pt"><!--StartFragment-->${cols}${body}<!--EndFragment--></table></body></html>`;
}

function padRow(cells: string[], width: number) {
  const row = cells.slice();
  while (row.length < width) row.push("");
  return row;
}

function pushExcelLine(
  plain: string[][],
  html: HtmlCell[][],
  label: string,
  values: (number | null | undefined)[],
  kinds?: string[],
) {
  const plainCells: string[] = [];
  const htmlCells: HtmlCell[] = [{ text: label }];
  values.forEach((value, i) => {
    const kind = kinds?.[i];
    if (value == null || kind === "empty" || Number.isNaN(Number(value))) {
      plainCells.push("");
      htmlCells.push({ text: "" });
      return;
    }
    const n = Number(value);
    if (kind === "percent") {
      const text = `${Math.round(n)}%`;
      plainCells.push(text);
      htmlCells.push({ text });
      return;
    }
    if (kind === "percent1") {
      const text = `${n.toFixed(1).replace(".", ",")}%`;
      plainCells.push(text);
      htmlCells.push({ text });
      return;
    }
    const money = kind === "money" || (kind !== "count" && !Number.isInteger(n));
    const literal = money ? excelLiteral(n, 2) : excelLiteral(n, 0);
    const text = literal.replace(".", ",");
    plainCells.push(text);
    htmlCells.push({
      text,
      num: literal,
      format: money ? "#,##0.00" : "0",
    });
  });
  plain.push([label, ...plainCells]);
  html.push(htmlCells);
}

function pivotColumnLabel(col: string, hasYears: boolean) {
  if (!hasYears) return col;
  if (isYearTotalColumn(col)) return col;
  return col.replace(/\s+\d{4}$/, "");
}

function pivotToGrid(table: PivotTable): { plain: string[][]; html: HtmlCell[][] } {
  const spans = table.year_spans ?? [];
  const hasYears = spans.some((span) => span.count > 1) || spans.length > 1;
  const index = table.index_label || "Подразделение";
  const width = 1 + table.columns.length;
  const plain: string[][] = [];
  const html: HtmlCell[][] = [];

  if (hasYears) {
    const spanPlain = [index];
    const spanHtml: HtmlCell[] = [{ text: index, rowspan: 2 }];
    for (const span of spans) {
      spanPlain.push(span.label);
      for (let i = 1; i < span.count; i += 1) spanPlain.push("");
      spanHtml.push({ text: span.label, colspan: span.count });
    }
    plain.push(padRow(spanPlain, width));
    html.push(spanHtml);
  }

  const labels = table.columns.map((col) => pivotColumnLabel(col, hasYears));
  const header = [hasYears ? "" : index, ...labels];
  plain.push(header);
  html.push((hasYears ? labels : header).map((text) => ({ text })));

  for (const row of table.rows) {
    pushExcelLine(plain, html, row.label, row.values, table.column_kinds);
  }
  if (table.totals && table.totals.length > 0) {
    pushExcelLine(plain, html, table.totals_label || "Итого", table.totals, table.column_kinds);
  }
  return { plain, html };
}

function sectionsToGrid(sections: SectionBlock[]): { plain: string[][]; html: HtmlCell[][] } {
  const plain: string[][] = [];
  const html: HtmlCell[][] = [];
  sections.forEach((block, blockI) => {
    const width = 1 + Math.max(0, ...block.tables.map((item) => item.columns.length));
    if (blockI > 0) {
      plain.push(padRow([], width));
      html.push([{ text: "" }]);
    }
    plain.push(padRow([block.title], width));
    html.push([{ text: block.title, colspan: width }]);
    for (const item of block.tables) {
      plain.push(padRow([item.title], width));
      html.push([{ text: item.title, colspan: width }]);
      const header = ["", ...item.columns];
      plain.push(padRow(header, width));
      html.push(header.map((text) => ({ text })));
      for (const row of item.rows) {
        const before = plain.length;
        pushExcelLine(plain, html, row.label, row.values, row.kinds);
        plain[before] = padRow(plain[before], width);
      }
    }
  });
  return { plain, html };
}

async function writeExcelTable(plain: string[][], html: HtmlCell[][]) {
  await copyExcelMarkup(gridToHtml(html), gridToTsv(plain));
}

function HalfYearTables({ sections }: { sections: SectionBlock[] }) {
  return (
    <div className="space-y-6">
      {sections.map((block) => (
        <section key={block.title || "table-block"}>
          {block.title ? (
            <h3 className="mb-3 text-sm font-semibold text-zinc-100">{block.title}</h3>
          ) : null}
          <div className={`grid gap-3 ${block.tables.length > 1 ? "md:grid-cols-2" : ""}`}>
            {block.tables.map((item, tableI) => (
              <div key={`${block.title}-${tableI}-${item.title}`} className="overflow-hidden rounded-lg border border-line">
                {item.title ? (
                  <div className="border-b border-line bg-panel px-3 py-1.5 text-sm font-medium text-zinc-100">
                    {item.title}
                  </div>
                ) : null}
                <table className="w-full border-collapse text-right text-xs">
                  <thead>
                    <tr className="text-zinc-500">
                      <th className="border-b border-line px-2 py-1.5 text-left font-medium">
                        {item.index_label || ""}
                      </th>
                      {item.columns.map((col, i) => (
                        <th key={`${i}-${col}`} className="border-b border-line px-2 py-1.5 font-medium whitespace-nowrap">
                          {col}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {item.rows.map((row) => (
                      <tr key={`${row.label}-${row.indent ? "sub" : "main"}`} className="border-t border-line">
                        <th
                          className={`px-2 py-1.5 text-left font-medium text-zinc-200 ${
                            row.label === "Всего" ? "font-semibold text-accent" : ""
                          } ${row.indent ? "pl-5 text-[11px] font-normal italic text-zinc-400" : ""} ${
                            row.row_role === "group" ? "font-semibold" : ""
                          }`}
                        >
                          {formatStageLabel(row.label)}
                        </th>
                        {item.columns.map((_col, i) => (
                          <td
                            key={`${row.label}-${i}`}
                            className={`px-2 py-1.5 tabular-nums text-zinc-100 ${
                              row.label === "Всего" ? "font-semibold text-accent" : ""
                            }`}
                          >
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

const STATUS_PIE_COLORS: Record<string, string> = {
  "В работе": "#5B9BD5",
  Выиграна: "#ED7D31",
  Проиграна: "#A5A5A5",
  Отменена: "#FFC000",
};

function statusDealsOrdersFigure(block: SectionBlock) {
  const table = block.tables[0];
  const labels = table.rows.map((row) => row.label.toUpperCase());
  const deals = table.rows.map((row) => Number(row.values[0] ?? 0));
  const orders = table.rows.map((row) => Number(row.values[2] ?? 0));
  const dealSums = table.rows.map((row) => Number(row.values[1] ?? 0));
  const orderSums = table.rows.map((row) => Number(row.values[3] ?? 0));
  const peak = Math.max(1, ...deals, ...orders);
  return JSON.stringify({
    data: [
      {
        type: "bar",
        name: "Сделки",
        x: labels,
        y: deals,
        customdata: dealSums,
        marker: { color: "#5B9BD5" },
        text: deals.map((value) => String(value)),
        textposition: "outside",
        textangle: 0,
        cliponaxis: false,
        hovertemplate: "Сделки<br>%{y:,.0f}<br>%{customdata:,.0f} р.<extra></extra>",
      },
      {
        type: "bar",
        name: "Заказы",
        x: labels,
        y: orders,
        customdata: orderSums,
        marker: { color: "#ED7D31" },
        text: orders.map((value) => String(value)),
        textposition: "outside",
        textangle: 0,
        cliponaxis: false,
        hovertemplate: "Заказы<br>%{y:,.0f}<br>%{customdata:,.0f} р.<extra></extra>",
      },
    ],
    layout: {
      title: chartTitleCaps(`Сделки и заказы ${block.title}`),
      barmode: "group",
      bargap: 0.28,
      bargroupgap: 0.08,
      showlegend: true,
      legend: { orientation: "h", y: 1.08, x: 0.5, xanchor: "center" },
      margin: { l: 16, r: 16, t: 72, b: 48 },
      height: 420,
      autosize: true,
      xaxis: { type: "category", categoryorder: "array", categoryarray: labels, tickangle: 0 },
      yaxis: { visible: false, range: [0, peak * 1.42] },
    },
  });
}

function statusDealsPieFigure(block: SectionBlock) {
  const rows = block.tables[0].rows.filter((row) => row.label !== "Всего");
  const values = rows.map((row) => Number(row.values[0] ?? 0));
  const total = values.reduce((sum, value) => sum + value, 0) || 1;
  const texttemplate = "%{label}<br>%{percent:.1%}";
  const hasValue = (value: number) => value > 0;
  return JSON.stringify({
    data: [
      {
        type: "pie",
        labels: rows.map((row) => row.label),
        values,
        marker: { colors: rows.map((row) => STATUS_PIE_COLORS[row.label] ?? "#5B9BD5") },
        textinfo: values.map((value) => (hasValue(value) ? "text" : "none")),
        texttemplate: values.map((value) => (hasValue(value) ? texttemplate : "")),
        textposition: values.map((value) => (hasValue(value) ? "outside" : "none")),
        outsidetextfont: { size: 11 },
        pull: values.map((value) => (hasValue(value) && value / total < 0.03 ? 0.06 : 0)),
        sort: false,
        domain: { x: [0.12, 0.88], y: [0.14, 0.82] },
        hovertemplate: "%{label}<br>%{value:,.0f}<br>%{percent:.1%}<extra></extra>",
      },
    ],
    layout: {
      title: chartTitleCaps("Сделки"),
      showlegend: true,
      legend: { orientation: "h", y: -0.08, x: 0.5, xanchor: "center" },
      uniformtext: { mode: "show", minsize: 9 },
      margin: { l: 56, r: 56, t: 56, b: 72 },
      height: 440,
      autosize: true,
    },
  });
}

function DealStatusView({ sections, variant }: { sections: SectionBlock[]; variant?: string }) {
  const counts = variant === "counts";
  return (
    <div className="space-y-4">
      {sections.map((block) => (
        <div key={block.title} className="space-y-3">
          <HalfYearTables sections={[block]} />
          <PlotChart
            json={counts ? statusDealsPieFigure(block) : statusDealsOrdersFigure(block)}
            className="h-[26rem] w-full"
          />
        </div>
      ))}
    </div>
  );
}

const IN_WORK_STAGE_BAR_BLUE = "#5B9BD5";
const IN_WORK_STAGE_BAR_YELLOW = "#FFC000";
const IN_WORK_STAGE_BAR_PERIWINKLE = "#9DC3E6";
const IN_WORK_STAGE_BAR_GREY = "#A5A5A5";

const IN_WORK_STAGE_TOP_COLORS = [
  IN_WORK_STAGE_BAR_YELLOW,
  IN_WORK_STAGE_BAR_PERIWINKLE,
  IN_WORK_STAGE_BAR_GREY,
];

function inWorkStageTopRanks(labels: string[], values: number[]): Map<string, number> {
  const ranked = labels
    .map((label, index) => ({ label, value: values[index] ?? 0 }))
    .filter((item) => item.value > 0)
    .sort(
      (left, right) =>
        right.value - left.value || left.label.localeCompare(right.label, "ru"),
    );
  const ranks = new Map<string, number>();
  ranked.slice(0, 3).forEach((item, index) => {
    ranks.set(item.label, index);
  });
  return ranks;
}

function inWorkStageBarColors(labels: string[], values: number[]): string[] {
  const ranks = inWorkStageTopRanks(labels, values);
  return labels.map((label) => {
    const rank = ranks.get(label);
    if (rank == null) return IN_WORK_STAGE_BAR_BLUE;
    return IN_WORK_STAGE_TOP_COLORS[rank] ?? IN_WORK_STAGE_BAR_BLUE;
  });
}

function inWorkPeriodLabel(sectionTitle: string): string {
  const match = sectionTitle.match(/\(([^)]+)\)\s*$/);
  return match ? match[1] : sectionTitle;
}

function inWorkStageRows(block: SectionBlock) {
  return block.tables[0].rows.filter((row) => row.label !== "Всего");
}

function inWorkStageHbarFigure(block: SectionBlock, metric: "count" | "money") {
  const rows = inWorkStageRows(block);
  const labels = rows.map((row) => formatStageLabel(row.label));
  const values = rows.map((row) => Number(row.values[metric === "count" ? 0 : 1] ?? 0));
  const ranks = inWorkStageTopRanks(labels, values);
  const colors = inWorkStageBarColors(labels, values);
  const highlights = labels.map((label) => ranks.has(label));
  const period = inWorkPeriodLabel(block.title);
  const suffix = metric === "count" ? "Количество" : "Сумма, р.";
  const text = values.map((value) =>
    metric === "count"
      ? formatSectionCell(value, "count")
      : formatSectionCell(value, "money"),
  );
  const peak = Math.max(1, ...values);
  const barHeight = Math.max(320, 26 * labels.length + 72);
  return JSON.stringify({
    data: [
      {
        type: "bar",
        orientation: "h",
        y: labels,
        x: values,
        marker: { color: colors },
        text,
        textposition: "outside",
        cliponaxis: false,
        textfont: {
          size: highlights.map((bold) => (bold && metric === "money" ? 12 : 11)),
          weight: highlights.map((bold) => (bold && metric === "money" ? 700 : 400)),
        },
        hovertemplate:
          metric === "count"
            ? "%{y}<br>%{x:,.0f}<extra></extra>"
            : "%{y}<br>%{x:,.0f} р.<extra></extra>",
      },
    ],
    layout: {
      title: chartTitleCaps(`Сделки в работе (${period}) — ${suffix}`),
      showlegend: false,
      margin: { l: 8, r: 88, t: 48, b: 8 },
      height: barHeight,
      autosize: true,
      xaxis: { visible: false, range: [0, peak * 1.22] },
      yaxis: {
        automargin: true,
        autorange: "reversed",
        showgrid: false,
        tickfont: { size: 11 },
      },
    },
  });
}

function StageDeptBreakdownTableCard({ stage, table }: { stage: string; table: SectionTable }) {
  const [copied, setCopied] = useState(false);

  async function onCopy() {
    const grid = sectionsToGrid([{ title: stage, tables: [{ ...table, title: "" }] }]);
    await writeExcelTable(grid.plain, grid.html);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  }

  return (
    <div className="overflow-hidden rounded-lg border border-line">
      <div className="flex items-center justify-between gap-2 border-b border-line bg-panel px-3 py-1.5">
        <div className="min-w-0 flex-1 text-center text-sm font-medium text-zinc-100">
          {formatStageLabel(stage)}
        </div>
        <button
          type="button"
          className="shrink-0 rounded-md border border-line px-2 py-0.5 text-[11px] text-zinc-300 hover:border-accent/50 hover:text-accent"
          onClick={() => void onCopy().catch(() => setCopied(false))}
        >
          {copied ? "Скопировано" : "Скопировать"}
        </button>
      </div>
      <HalfYearTables
        sections={[
          {
            title: "",
            tables: [{ ...table, title: "" }],
          },
        ]}
      />
    </div>
  );
}

function StageDeptBreakdownGrid({
  title,
  items,
}: {
  title: string;
  items: { stage: string; table: SectionTable }[];
}) {
  if (!items.length) return null;
  return (
    <div className="space-y-2">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-zinc-400">{title}</h4>
      <div className="grid gap-3 xl:grid-cols-3">
        {items.map((item) => (
          <StageDeptBreakdownTableCard key={item.stage} stage={item.stage} table={item.table} />
        ))}
      </div>
    </div>
  );
}

function InWorkStagesView({ sections }: { sections: SectionBlock[] }) {
  return (
    <div className="space-y-6">
      {sections.map((block) => (
        <div key={block.title} className="space-y-4">
          <HalfYearTables sections={[{ title: block.title, tables: block.tables }]} />
          <PlotChart json={inWorkStageHbarFigure(block, "count")} className="w-full" />
          {block.stage_breakdowns?.by_count?.length ? (
            <StageDeptBreakdownGrid
              title="Разрез по службам — топ‑3 этапа по количеству"
              items={block.stage_breakdowns.by_count}
            />
          ) : null}
          <PlotChart json={inWorkStageHbarFigure(block, "money")} className="w-full" />
          {block.stage_breakdowns?.by_sum?.length ? (
            <StageDeptBreakdownGrid
              title="Разрез по службам — топ‑3 этапа по сумме"
              items={block.stage_breakdowns.by_sum}
            />
          ) : null}
        </div>
      ))}
    </div>
  );
}

function PivotTableView({
  table,
  activeColumn,
  onColumnClick,
}: {
  table: PivotTable;
  activeColumn?: number;
  onColumnClick?: (column: number) => void;
}) {
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
            {table.columns.map((col, i) => {
              const label =
                hasYears && isYearTotalColumn(col) ? col : hasYears ? col.replace(/\s+\d{4}$/, "") : col;
              const selected = activeColumn === i;
              return (
              <th
                key={`${i}-${col}`}
                className={`border-b border-line px-2 py-1.5 font-medium whitespace-nowrap ${
                  selected ? "bg-accent/15 text-accent" : isYearTotalColumn(col) ? "text-zinc-200" : ""
                } ${breaks.has(i) ? quarterEdge : ""}`}
              >
                {onColumnClick ? (
                  <button
                    type="button"
                    className={`underline-offset-2 hover:text-accent hover:underline ${
                      selected ? "text-accent" : ""
                    }`}
                    onClick={() => onColumnClick(i)}
                  >
                    {label}
                  </button>
                ) : (
                  label
                )}
              </th>
              );
            })}
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
                    activeColumn === i
                      ? "bg-accent/10 font-medium text-zinc-100"
                      : isYearTotalColumn(col) || table.column_kinds?.[i] === "percent"
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

const CONVERSION_COMPANY = "Совтест";
const CONVERSION_ABOVE_COLOR = "#548235";
const CONVERSION_OTHER_COLOR = "#5B9BD5";

/** Цвета периодов на групповых графиках (как на вкладке «Динамика»). */
const PERIOD_BAR_COLORS = [
  "#1F4E79",
  "#ED7D31",
  "#A5A5A5",
  "#FFC000",
  "#5B9BD5",
  "#70AD47",
  "#264478",
  "#9E480E",
  "#636363",
  "#997300",
];

function conversionBarColor(label: string, value: number, companyValue: number | null): string {
  if (label === CONVERSION_COMPANY) return COMPANY_CHART_COLOR;
  if (companyValue != null && value > companyValue) return CONVERSION_ABOVE_COLOR;
  return CONVERSION_OTHER_COLOR;
}

function conversionSingleFigure(
  table: PivotTable,
  column: number,
  title: string,
  deptFilter?: DepartmentChartFilter,
) {
  const points = table.rows.map((row) => ({
    label: row.label,
    value: Number(row.values[column] ?? 0),
    company: row.label === CONVERSION_COMPANY,
  }));
  points.sort((a, b) => b.value - a.value);
  const companyValue = points.find((item) => item.company)?.value ?? null;
  const colors = points.map((item) => conversionBarColor(item.label, item.value, companyValue));
  const labels = points.map((item) => item.label);
  const values = points.map((item) => item.value);
  return JSON.stringify({
    data: [
      {
        type: "bar",
        x: labels,
        y: values,
        marker: { color: colors },
        text: values.map((value) => `${Math.round(value)}%`),
        textposition: "outside",
        cliponaxis: false,
        width: 0.62,
      },
    ],
    layout: {
      title: chartTitleCapsFiltered(title, deptFilter),
      barmode: "relative",
      autosize: false,
      width: Math.max(720, Math.min(88 * Math.max(labels.length, 1), 1500)),
      height: 520,
      showlegend: false,
      margin: { l: 16, r: 16, t: 64, b: 64 },
      bargap: 0.22,
      xaxis: { type: "category", tickangle: labels.length > 6 ? -25 : 0 },
      yaxis: { visible: false, range: [0, 140] },
    },
  });
}

function conversionGroupedFigure(
  table: PivotTable,
  columns: number[],
  title: string,
  deptFilter?: DepartmentChartFilter,
) {
  if (columns.length === 1) return conversionSingleFigure(table, columns[0], title, deptFilter);
  const ranked = [...table.rows].sort((a, b) => {
    const score = (row: (typeof table.rows)[number]) =>
      columns.reduce((total, index) => total + Number(row.values[index] ?? 0), 0);
    return score(b) - score(a);
  });
  const labels = ranked.map((row) => row.label);
  const periodNames = columns.map((index) => table.columns[index] ?? "");
  const showText = columns.length * Math.max(labels.length, 1) <= 36;
  const data = columns.map((index, seriesI) => {
    return {
    type: "bar",
    name: periodNames[seriesI],
    x: labels,
    y: ranked.map((row) => Number(row.values[index] ?? 0)),
    marker: { color: PERIOD_BAR_COLORS[seriesI % PERIOD_BAR_COLORS.length] },
    text: showText
      ? ranked.map((row) => `${Math.round(Number(row.values[index] ?? 0))}%`)
      : undefined,
    textposition: showText ? "outside" : undefined,
    cliponaxis: false,
  };
  });
  return JSON.stringify({
    data,
    layout: {
      title: chartTitleCapsFiltered(title, deptFilter),
      barmode: columns.length > 1 ? "group" : "relative",
      autosize: false,
      width: Math.max(720, Math.min(88 * Math.max(labels.length, 1) + 24 * columns.length, 1500)),
      height: 520,
      showlegend: columns.length > 1,
      legend: { orientation: "h", y: -0.22, x: 0.5, xanchor: "center" },
      margin: { l: 16, r: 16, t: 64, b: columns.length > 1 ? 96 : 64 },
      bargap: 0.22,
      bargroupgap: 0.06,
      xaxis: {
        type: "category",
        categoryorder: "array",
        categoryarray: labels,
        tickangle: labels.length > 6 ? -25 : 0,
      },
      yaxis: { visible: false, range: [0, 140] },
    },
  });
}

function conversionCompareFigure(
  table: PivotTable,
  columns: number[],
  deptFilter?: DepartmentChartFilter,
) {
  const periodNames = columns.map((index) => table.columns[index] ?? "");
  return conversionGroupedFigure(
    table,
    columns,
    `Конверсия сделок в заказы (${periodNames.join("/")})`,
    deptFilter,
  );
}

function conversionOverviewFigure(table: PivotTable, deptFilter?: DepartmentChartFilter) {
  return conversionGroupedFigure(
    table,
    table.columns.map((_, index) => index),
    "Конверсия сделок в заказы",
    deptFilter,
  );
}

function ConversionCompareBlock({
  table,
  columns,
  deptFilter,
}: {
  table: PivotTable;
  columns: number[];
  deptFilter?: DepartmentChartFilter;
}) {
  return (
    <div className="space-y-4 border-t border-line pt-4">
      <div className="overflow-auto rounded-lg border border-line">
        <table className="min-w-full border-collapse text-right text-xs">
          <thead className="bg-panel text-zinc-400">
            <tr>
              <th className="sticky left-0 bg-panel px-3 py-1.5 text-left font-medium text-zinc-300">
                {table.index_label || "Подразделение"}
              </th>
              {columns.map((index) => (
                <th key={index} className="px-3 py-1.5 font-medium whitespace-nowrap text-zinc-200">
                  Конверсия сделок в заказы ({table.columns[index]})
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row) => {
              const company = row.label === "Совтест";
              return (
                <tr key={row.label} className="border-t border-line">
                  <th
                    className={`sticky left-0 bg-card px-3 py-1.5 text-left font-medium ${
                      company ? "text-red-400" : "text-zinc-200"
                    }`}
                  >
                    {row.label}
                  </th>
                  {columns.map((index) => (
                    <td
                      key={index}
                      className={`px-3 py-1.5 tabular-nums font-medium ${
                        company ? "text-red-400" : "text-zinc-100"
                      }`}
                    >
                      {formatPivotNumber(row.values[index], table.column_kinds?.[index] ?? "percent")}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="flex justify-center overflow-x-auto">
        <PlotChart
          json={conversionCompareFigure(table, columns, deptFilter)}
          className="h-[30rem] w-full min-w-[640px]"
        />
      </div>
    </div>
  );
}

function formatMoney(value: number) {
  return Math.round(value).toLocaleString("ru-RU");
}

function formatMoneyDelta(current: number, previous: number | null) {
  if (previous == null || previous === 0) return "—";
  const pct = Math.round((100 * (current - previous)) / previous);
  return pct > 0 ? `+${pct}%` : `${pct}%`;
}

/** Доля подразделения в сумме по отделам (как на круговой диаграмме, без строки компании). */
function moneyDepartmentPieTotal(rows: MoneyMetric["rows"], periodIndex: number) {
  return rows
    .filter((row) => !row.company)
    .reduce((sum, row) => sum + Math.max(0, Number(row.values[periodIndex] ?? 0)), 0);
}

function formatMoneySharePercent(value: number, total: number) {
  if (total <= 0 || value <= 0) return "—";
  const pct = (100 * value) / total;
  const maxDigits = pct >= 10 ? 1 : 2;
  return `${pct.toLocaleString("ru-RU", { maximumFractionDigits: maxDigits })}%`;
}

function moneyRowSharePercent(row: MoneyRow, value: number, deptTotal: number) {
  if (row.company) {
    return value > 0 && deptTotal > 0 ? "100%" : "—";
  }
  return formatMoneySharePercent(value, deptTotal);
}

function moneyRowShareValue(row: MoneyRow, value: number, deptTotal: number): number | null {
  if (row.company) {
    return value > 0 && deptTotal > 0 ? 100 : null;
  }
  if (value > 0 && deptTotal > 0) {
    return (100 * value) / deptTotal;
  }
  return null;
}

function moneyPieFigure(
  title: string,
  slices: { label: string; value: number }[],
  deptFilter?: DepartmentChartFilter,
) {
  return JSON.stringify({
    data: [
      {
        type: "pie",
        labels: slices.map((item) => item.label),
        values: slices.map((item) => item.value),
        marker: {
          colors: slices.map((item) => departmentChartColor(item.label)),
        },
        textinfo: "percent",
        textposition: "inside",
        sort: false,
        domain: { x: [0.08, 0.92], y: [0.12, 0.88] },
        hovertemplate: "%{label}<br>%{value:,.0f}<br>%{percent}<extra></extra>",
      },
    ],
    layout: {
      title: chartTitleCapsFiltered(title, deptFilter),
      showlegend: true,
      legend: { orientation: "h", y: -0.02, x: 0.5, xanchor: "center", font: { size: 11 } },
      height: 420,
      autosize: true,
      margin: { l: 24, r: 24, t: 56, b: 80 },
    },
  });
}

function MoneyMetricBlock({
  metric,
  currentIndex,
  previousIndex,
  currentLabel,
  previousLabel,
  deptFilter,
}: {
  metric: MoneyMetric;
  currentIndex: number;
  previousIndex: number | null;
  currentLabel: string;
  previousLabel: string | null;
  deptFilter?: DepartmentChartFilter;
}) {
  const rows = [...metric.rows].sort((a, b) => {
    if (a.company) return -1;
    if (b.company) return 1;
    return Number(b.values[currentIndex] ?? 0) - Number(a.values[currentIndex] ?? 0);
  });
  const slices = (index: number) =>
    rows
      .filter((row) => !row.company && Number(row.values[index] ?? 0) > 0)
      .map((row) => ({ label: row.label, value: Number(row.values[index] ?? 0) }));
  const currentSlices = slices(currentIndex);
  const previousSlices = previousIndex == null ? [] : slices(previousIndex);
  const currentDeptTotal = moneyDepartmentPieTotal(rows, currentIndex);
  const previousDeptTotal =
    previousIndex == null ? 0 : moneyDepartmentPieTotal(rows, previousIndex);
  const [copied, setCopied] = useState(false);

  async function onCopy() {
    const header = ["Подразделение", currentLabel, "%"];
    if (previousLabel) header.push(previousLabel, "%", "Отклонение");
    const plain: string[][] = [header];
    const html: HtmlCell[][] = [header.map((text) => ({ text }))];
    for (const row of rows) {
      const current = Number(row.values[currentIndex] ?? 0);
      const values: (number | null)[] = [current];
      const kinds = ["count"];
      const currentShare = moneyRowShareValue(row, current, currentDeptTotal);
      if (currentShare != null) {
        values.push(currentShare);
        kinds.push(row.company ? "percent" : "percent1");
      } else {
        values.push(null);
        kinds.push("count");
      }
      if (previousIndex != null) {
        const previous = Number(row.values[previousIndex] ?? 0);
        values.push(previous);
        kinds.push("count");
        const previousShare = moneyRowShareValue(row, previous, previousDeptTotal);
        if (previousShare != null) {
          values.push(previousShare);
          kinds.push(row.company ? "percent" : "percent1");
        } else {
          values.push(null);
          kinds.push("count");
        }
        values.push(previous === 0 ? null : Math.round((100 * (current - previous)) / previous));
        kinds.push("percent");
      }
      pushExcelLine(plain, html, row.label, values, kinds);
    }
    await writeExcelTable(plain, html);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  }

  return (
    <section className="space-y-3 rounded-xl border border-line bg-bg/40 p-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-medium text-zinc-100">{metric.title}</h3>
        <button
          type="button"
          className="rounded-md border border-line px-2 py-0.5 text-[11px] text-zinc-300 hover:border-accent/50 hover:text-accent"
          onClick={() => void onCopy().catch(() => setCopied(false))}
        >
          {copied ? "Скопировано" : "Скопировать"}
        </button>
      </div>
      <div className="overflow-auto rounded-lg border border-line">
        <table className="min-w-full border-collapse text-right text-xs">
          <thead className="bg-panel text-zinc-400">
            <tr>
              <th className="sticky left-0 bg-panel px-3 py-1.5 text-left font-medium text-zinc-300">
                Подразделение
              </th>
              <th className="px-3 py-1.5 font-medium whitespace-nowrap text-zinc-200">{currentLabel}</th>
              <th className="px-3 py-1.5 font-medium whitespace-nowrap text-zinc-200">%</th>
              {previousLabel ? (
                <th className="px-3 py-1.5 font-medium whitespace-nowrap text-zinc-200">{previousLabel}</th>
              ) : null}
              {previousLabel ? (
                <th className="px-3 py-1.5 font-medium whitespace-nowrap text-zinc-200">%</th>
              ) : null}
              {previousLabel ? (
                <th className="px-3 py-1.5 font-medium whitespace-nowrap text-zinc-200">Отклонение</th>
              ) : null}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const current = Number(row.values[currentIndex] ?? 0);
              const previous = previousIndex == null ? null : Number(row.values[previousIndex] ?? 0);
              const delta = formatMoneyDelta(current, previous);
              return (
                <tr key={row.label} className="border-t border-line">
                  <th
                    className={`sticky left-0 bg-card px-3 py-1.5 text-left font-medium ${
                      row.company ? "text-red-400" : "text-zinc-200"
                    }`}
                  >
                    {row.label}
                  </th>
                  <td className={`px-3 py-1.5 tabular-nums ${row.company ? "text-red-400" : "text-zinc-100"}`}>
                    {formatMoney(current)}
                  </td>
                  <td
                    className={`px-3 py-1.5 tabular-nums ${row.company ? "text-red-400" : "text-zinc-400"}`}
                  >
                    {moneyRowSharePercent(row, current, currentDeptTotal)}
                  </td>
                  {previousLabel ? (
                    <td className={`px-3 py-1.5 tabular-nums ${row.company ? "text-red-400" : "text-zinc-300"}`}>
                      {formatMoney(previous ?? 0)}
                    </td>
                  ) : null}
                  {previousLabel ? (
                    <td
                      className={`px-3 py-1.5 tabular-nums ${row.company ? "text-red-400" : "text-zinc-400"}`}
                    >
                      {moneyRowSharePercent(row, previous ?? 0, previousDeptTotal)}
                    </td>
                  ) : null}
                  {previousLabel ? (
                    <td
                      className={`px-3 py-1.5 tabular-nums ${
                        delta.startsWith("+") ? "text-emerald-300" : delta.startsWith("-") ? "text-red-300" : "text-zinc-400"
                      }`}
                    >
                      {delta}
                    </td>
                  ) : null}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div
        className={`grid gap-3 ${
          previousLabel ? "lg:grid-cols-2" : "grid-cols-1 place-items-center"
        }`}
      >
        {currentSlices.length > 0 ? (
          <div className="flex w-full justify-center">
            <PlotChart
              json={moneyPieFigure(`${metric.title} (${currentLabel})`, currentSlices, deptFilter)}
              className="h-[26rem] w-full max-w-md"
            />
          </div>
        ) : (
          <p className="py-8 text-center text-sm text-zinc-500">Нет сумм за {currentLabel}</p>
        )}
        {previousLabel && previousIndex != null ? (
          previousSlices.length > 0 ? (
            <div className="flex w-full justify-center">
              <PlotChart
                json={moneyPieFigure(`${metric.title} (${previousLabel})`, previousSlices, deptFilter)}
                className="h-[26rem] w-full max-w-md"
              />
            </div>
          ) : (
            <p className="py-8 text-center text-sm text-zinc-500">Нет сумм за {previousLabel}</p>
          )
        ) : null}
      </div>
    </section>
  );
}

function MoneyBoardView({
  board,
  deptFilter,
}: {
  board: MoneyBoard;
  deptFilter?: DepartmentChartFilter;
}) {
  const [index, setIndex] = useState(Math.max(0, board.periods.length - 1));
  const [compareIndex, setCompareIndex] = useState<number | null>(null);
  const currentIndex = Math.min(index, Math.max(0, board.periods.length - 1));
  const current = board.periods[currentIndex];
  const previousIndex =
    compareIndex != null && compareIndex !== currentIndex && compareIndex < board.periods.length
      ? compareIndex
      : null;
  const previous = previousIndex == null ? undefined : board.periods[previousIndex];
  if (!current) return <p className="text-sm text-zinc-500">Нет периодов с датами сделок</p>;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3 text-xs text-zinc-400">
        <label className="flex items-center gap-2">
          Период
          <select
            value={currentIndex}
            onChange={(event) => {
              const next = Number(event.target.value);
              setIndex(next);
              if (compareIndex === next) setCompareIndex(null);
            }}
            className="rounded-md border border-line bg-bg px-2 py-1 text-xs text-zinc-200 outline-none focus:border-accent/60"
          >
            {board.periods.map((period, periodI) => (
              <option key={period.label} value={periodI}>
                {period.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2">
          Сравнить с
          <select
            value={previousIndex == null ? "" : String(previousIndex)}
            onChange={(event) => {
              const value = event.target.value;
              setCompareIndex(value === "" ? null : Number(value));
            }}
            className="rounded-md border border-line bg-bg px-2 py-1 text-xs text-zinc-200 outline-none focus:border-accent/60"
          >
            <option value="">Без сравнения</option>
            {board.periods.map((period, periodI) =>
              periodI === currentIndex ? null : (
                <option key={`compare-${period.label}`} value={periodI}>
                  {period.label}
                </option>
              ),
            )}
          </select>
        </label>
      </div>
      {board.metrics.map((metric) => (
        <MoneyMetricBlock
          key={metric.title}
          metric={metric}
          currentIndex={currentIndex}
          previousIndex={previousIndex}
          currentLabel={current.label}
          previousLabel={previous?.label ?? null}
          deptFilter={deptFilter}
        />
      ))}
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
  const [theme, setTheme] = useState<"dark" | "light">(() =>
    document.documentElement.dataset.theme === "light" ? "light" : "dark",
  );
  const [view, setView] = useState<View>("dash");
  const [workspaceId, setWorkspaceId] = useState<string | null>(null);
  const [fileId, setFileId] = useState<string | null>(null);
  const [filename, setFilename] = useState<string | null>(null);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [ctx, setCtx] = useState<FileContext | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [reportCharts, setReportCharts] = useState<ReportChart[]>([]);
  const [narrative, setNarrative] = useState("");
  const [insightsText, setInsightsText] = useState("");
  const [comment, setComment] = useState("");
  const [comments, setComments] = useState<Record<string, string>>({});
  const [tab, setTab] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const [conversionPeriod, setConversionPeriod] = useState<Record<string, number>>({});
  const [conversionCompare, setConversionCompare] = useState<
    Record<string, { columnsKey: string; base: number; second: number | null }>
  >({});
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [chatOpen, setChatOpen] = useState(() => !isNarrowViewport());
  const [chartEditor, setChartEditor] = useState<ChartEditorMode | null>(null);
  const busyLabel = useBusyLabel(busy);
  const fileRef = useRef<HTMLInputElement>(null);
  const fileIdRef = useRef<string | null>(null);
  const restoredRef = useRef(false);

  const refreshWorkspace = useCallback(async (wsId: string, restore?: SavedSession) => {
    fileIdRef.current = wsId;
    setBusy("Читаю выгрузку…");
    setError(null);
    try {
      const [payload, history] = await Promise.all([
        api.getWorkspaceDashboard(wsId),
        api.getWorkspaceHistory(wsId).catch(() => ({ messages: [] as ChatMessage[] })),
      ]);
      const dash = flattenWorkspaceDashboard(payload);
      const primaryId = payload.sales_file_id ?? payload.deficit_file_id ?? null;
      const primary =
        payload.sources.find((s) => s.file_id === primaryId) ?? payload.sources[0];
      const primaryName = primary?.filename ?? "Workspace";
      if (fileIdRef.current !== wsId) return;
      setWorkspaceId(wsId);
      setDashboard(dash);
      setCtx(dash.file_context ?? null);
      setMessages(history.messages ?? []);
      setFileId(primaryId);
      setFilename(primaryName);
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
      if (primaryId) {
        void api
          .enrichContext(primaryId)
          .then((brief) => {
            if (fileIdRef.current === wsId) setCtx(brief);
          })
          .catch(() => undefined);
      }
    } catch (exc) {
      if (fileIdRef.current !== wsId) return;
      const message = exc instanceof Error ? exc.message : String(exc);
      if (restore) {
        clearSession();
        setWorkspaceId(null);
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
    if (saved.workspaceId) {
      void refreshWorkspace(saved.workspaceId, saved);
    } else {
      void loadFile(saved.fileId, saved.filename, saved);
    }
  }, [loadFile, refreshWorkspace]);

  useEffect(() => {
    if (!fileId || !filename) return;
    writeSession({
      workspaceId: workspaceId ?? undefined,
      fileId,
      filename,
      view,
      tab,
      chatOpen,
      reportCharts,
    });
  }, [workspaceId, fileId, filename, view, tab, chatOpen, reportCharts]);

  async function onUpload(file: File) {
    setBusy("Загружаю файл…");
    setError(null);
    setChartEditor(null);
    try {
      const { file_id } = await api.uploadFile(file);
      let wsId = workspaceId;
      if (!wsId) {
        const created = await api.createWorkspace();
        wsId = created.workspace_id;
        setWorkspaceId(wsId);
        setDashboard(null);
        setCtx(null);
        setFileId(null);
        setFilename(null);
        fileIdRef.current = wsId;
      }
      try {
        await api.attachWorkspaceFile(wsId, file_id);
      } catch (attachExc) {
        const message = attachExc instanceof Error ? attachExc.message : String(attachExc);
        if (message.toLowerCase().includes("занят") || message.includes("replace")) {
          await api.attachWorkspaceFile(wsId, file_id, true);
        } else {
          throw attachExc;
        }
      }
      await refreshWorkspace(wsId);
      setNotice(
        workspaceId && workspaceId === wsId
          ? "Файл добавлен в рабочее пространство"
          : "Файл загружен",
      );
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
      setBusy(null);
    }
  }

  function fileIdForTab(tabIndex: number): string | null {
    return tabSourceFileId(dashboard, tabIndex) ?? fileId;
  }

  async function onSend(question: string) {
    if (!fileId && !workspaceId) return;
    setMessages((m) => [...m, { role: "user", content: question }]);
    setBusy("Отвечаю…");
    try {
      const result = workspaceId
        ? await api.sendWorkspaceChat(workspaceId, question)
        : await api.sendChat(fileId!, question);
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

  async function applyDashboardPatch(
    patch: Dashboard,
    specFallback: DashSpec,
    noticeText?: string,
  ) {
    let tabs = patch.tabs;
    let spec = patch.spec ?? specFallback;
    if ((!tabs || !tabs.length) && workspaceId) {
      const fresh = flattenWorkspaceDashboard(await api.getWorkspaceDashboard(workspaceId));
      tabs = fresh.tabs ?? tabs;
      spec = fresh.spec ?? spec;
    } else if ((!tabs || !tabs.length) && fileId) {
      const fresh = await api.getDashboard(fileId);
      tabs = fresh.tabs ?? tabs;
      spec = fresh.spec ?? spec;
    }
    setDashboard((cur) => ({
      ...(cur ?? {}),
      ...patch,
      tabs: tabs ?? cur?.tabs,
      spec,
    }));
    if (noticeText) setNotice(noticeText);
  }

  async function onTileFiltersPatch(tabI: number, tileI: number, filters: DealsListFiltersPatch) {
    const spec = dashboard?.spec;
    const targetFileId = fileIdForTab(tabI);
    if (!spec || !targetFileId) return;
    const next: DashSpec = {
      tabs: spec.tabs.map((t, ti) =>
        ti !== tabI
          ? t
          : {
              ...t,
              tiles: t.tiles.map((item, ij) => {
                if (ij !== tileI) return item;
                const { sort, list_sort_column, ...sourcePatch } = filters;
                const nextSource = { ...item.source, ...sourcePatch };
                if (list_sort_column !== undefined) {
                  if (list_sort_column) nextSource.list_sort_column = list_sort_column;
                  else delete nextSource.list_sort_column;
                }
                return {
                  ...item,
                  ...(sort !== undefined ? { sort } : {}),
                  source: nextSource,
                };
              }),
            },
      ),
    };
    setBusy("Обновляю список…");
    setError(null);
    try {
      const toSave =
        workspaceId && dashboard
          ? specForSourceFile(dashboard, targetFileId, next)
          : next;
      if (workspaceId) {
        await api.dashboardSaveSpec(targetFileId, toSave);
        await refreshWorkspace(workspaceId);
      } else {
        const patch = await api.dashboardSaveSpec(targetFileId, toSave);
        await applyDashboardPatch(patch, next);
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(null);
    }
  }

  async function onTilePeriodBucket(
    tabI: number,
    tileI: number,
    period: string,
    kind:
      | "halfyear"
      | "pivot"
      | "outcome"
      | "deals_dynamics"
      | "deals_dynamics_departments"
      | "deals_dynamics_outcome_share"
      | "deals_conversion"
      | "deals_money"
      | "deal_statuses"
      | "in_work_stages",
  ) {
    const spec = dashboard?.spec;
    const targetFileId = fileIdForTab(tabI);
    if (!spec || !targetFileId) return;
    const syncDynamics = DYNAMICS_PERIOD_KINDS.includes(
      kind as (typeof DYNAMICS_PERIOD_KINDS)[number],
    );
    const syncStages = STAGES_PERIOD_KINDS.has(kind);
    const next: DashSpec = {
      tabs: spec.tabs.map((t, ti) =>
        ti !== tabI
          ? t
          : {
              ...t,
              tiles: t.tiles.map((item, ij) => {
                const itemKind = item.source?.kind;
                if (
                  syncDynamics &&
                  itemKind &&
                  DYNAMICS_PERIOD_KINDS.includes(
                    itemKind as (typeof DYNAMICS_PERIOD_KINDS)[number],
                  )
                ) {
                  return { ...item, source: { ...item.source, period } };
                }
                if (syncStages && itemKind && STAGES_PERIOD_KINDS.has(itemKind)) {
                  return { ...item, source: { ...item.source, period } };
                }
                if (ij !== tileI) return item;
                return { ...item, source: { ...item.source, kind, period } };
              }),
            },
      ),
    };
    setBusy("Обновляю таблицу…");
    setError(null);
    try {
      const toSave =
        workspaceId && dashboard
          ? specForSourceFile(dashboard, targetFileId, next)
          : next;
      if (workspaceId) {
        await api.dashboardSaveSpec(targetFileId, toSave);
        await refreshWorkspace(workspaceId);
      } else {
        const patch = await api.dashboardSaveSpec(targetFileId, toSave);
        await applyDashboardPatch(patch, next);
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(null);
    }
  }

  async function onCopyTable(tile: Tile) {
    const grid = tile.sections
      ? sectionsToGrid(tile.sections)
      : tile.deals_list
        ? dealsListToGrid(tile.deals_list)
        : tile.table
          ? pivotToGrid(tile.table)
          : null;
    if (!grid) return;
    setError(null);
    try {
      await writeExcelTable(grid.plain, grid.html);
      setNotice("Таблица скопирована. Вставьте её в Excel — каждая ячейка ляжет в свою.");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Не удалось скопировать таблицу");
    }
  }

  async function onSaveSpec(spec: DashSpec) {
    if (!fileId && !workspaceId) return;
    setBusy("Сохраняю дашборд…");
    setError(null);
    try {
      if (workspaceId && dashboard?.sources?.length) {
        for (const src of dashboard.sources) {
          const base = src.spec;
          if (!base?.tabs?.length) continue;
          const nextSpec: DashSpec = {
            tabs: base.tabs.map((tab, ti) => {
              const flatIndex = dashboard.tabs?.findIndex(
                (ft) => ft.source_file_id === src.file_id && ft.source_tab_index === ti,
              );
              if (flatIndex == null || flatIndex < 0) return tab;
              return spec.tabs[flatIndex] ?? tab;
            }),
          };
          await api.dashboardSaveSpec(src.file_id, nextSpec);
        }
        await refreshWorkspace(workspaceId);
        setNotice("Дашборд сохранён");
      } else if (fileId) {
        const patch = await api.dashboardSaveSpec(fileId, spec);
        await applyDashboardPatch(patch, spec, "Дашборд сохранён");
      }
      setChartEditor(null);
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
    const reportFileId = dashboard?.sales_file_id ?? fileId;
    if (!reportFileId) return;
    const reportName =
      dashboard?.sources?.find((s) => s.file_id === reportFileId)?.filename ?? filename;
    setBusy("Формирую отчёт…");
    setError(null);
    try {
      const rep = await api.getReport(reportFileId, reportName ?? undefined);
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
    setWorkspaceId(null);
    setFileId(null);
    setFilename(null);
    setDashboard(null);
    setCtx(null);
    setMessages([]);
    setReport(null);
    setReportCharts([]);
    setChartEditor(null);
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
          <button
            type="button"
            className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-line text-accent hover:bg-accent-dim"
            title={theme === "dark" ? "Светлая тема" : "Тёмная тема"}
            aria-label={theme === "dark" ? "Включить светлую тему" : "Включить тёмную тему"}
            onClick={() => {
              const next = theme === "dark" ? "light" : "dark";
              if (next === "light") document.documentElement.dataset.theme = "light";
              else delete document.documentElement.dataset.theme;
              localStorage.setItem("excel-agent-theme", next);
              setTheme(next);
              window.dispatchEvent(new Event("themechange"));
            }}
          >
            {theme === "dark" ? <SunIcon /> : <MoonIcon />}
          </button>
        </div>
      </nav>

      <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <header className="flex items-center gap-3 border-b border-line px-5 py-3">
          <button
            type="button"
            className="rounded-lg border border-line bg-card px-3 py-1.5 text-sm hover:border-accent/50"
            onClick={() => fileRef.current?.click()}
          >
            {workspaceId ? "Добавить файл" : "Загрузить"}
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
            <div className="flex flex-wrap items-center gap-2 text-sm">
              {dashboard?.sources?.length
                ? dashboard.sources.map((src) => (
                    <span
                      key={src.file_id}
                      className="max-w-[14rem] truncate rounded-md border border-line bg-card px-2 py-0.5 text-xs text-zinc-200"
                      title={src.filename}
                    >
                      {src.role === "deficit_report" ? "Дефицит" : "Сделки"}: {src.filename}
                    </span>
                  ))
                : <span className="truncate">{filename ?? "Файл не выбран"}</span>}
            </div>
            <div className="text-xs text-zinc-500">
              {busyLabel ??
                (workspaceId
                  ? "Сделки и дефицит"
                  : fileId
                    ? typeName(dashboard?.report_type)
                    : "Excel Agent")}
            </div>
          </div>
          {(fileId || workspaceId) && (
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
                {dashboard?.summary && <BriefSummary text={dashboard.summary} />}
                {meta && (
                  <p className="mb-4 text-xs text-zinc-500">
                    Период {meta.period ?? "—"} · {meta.rows ?? 0} строк · {meta.columns ?? 0} колонок
                  </p>
                )}
                {kpis.length > 0 && (
                  <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-3 xl:grid-cols-6">
                    {kpis.map((kpi) => (
                      <div key={kpi.label} className="rounded-xl border border-line bg-card px-4 py-3">
                        <div className="text-xs text-zinc-500">{kpi.label}</div>
                        <div className="mt-1 text-lg font-medium text-accent">{String(kpi.value)}</div>
                      </div>
                    ))}
                  </div>
                )}
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
                        const specTile = dashboard?.spec?.tabs[tab]?.tiles[tileI];
                        const conversionTile =
                          specTile?.source?.kind === "deals_conversion" ||
                          tile.chart_type === "deals_conversion";
                        const moneyTile =
                          specTile?.source?.kind === "deals_money" || tile.chart_type === "deals_money";
                        const stagesDealListTile =
                          specTile?.source?.kind === "stages_deal_list" ||
                          tile.chart_type === "stages_deal_list";
                        const periodBucketTile =
                          specTile?.source?.kind === "halfyear" ||
                          specTile?.source?.kind === "pivot" ||
                          specTile?.source?.kind === "outcome" ||
                          specTile?.source?.kind === "deal_statuses" ||
                          specTile?.source?.kind === "in_work_stages" ||
                          conversionTile ||
                          moneyTile ||
                          DYNAMICS_PERIOD_KINDS.includes(
                            specTile?.source?.kind as (typeof DYNAMICS_PERIOD_KINDS)[number],
                          );
                        const periodBucketDefault = "quarter";
                        const dynamicsTile = DYNAMICS_PERIOD_KINDS.includes(
                          tile.chart_type as (typeof DYNAMICS_PERIOD_KINDS)[number],
                        );
                        const sideBySideTile = specTile?.source?.kind === "deal_statuses";
                        const periodCharts = tile.period_charts ?? [];
                        const selectedPeriod =
                          conversionPeriod[id] != null && conversionPeriod[id] < periodCharts.length
                            ? conversionPeriod[id]
                            : Math.max(0, periodCharts.length - 1);
                        const deptChartFilter: DepartmentChartFilter = {
                          catalog: tile.departments,
                          included: specTile?.source?.departments,
                        };
                        const conversionChart = periodCharts[selectedPeriod] ?? tile.plotly_json;
                        const conversionOverview =
                          conversionTile && tile.table
                            ? conversionOverviewFigure(tile.table, deptChartFilter)
                            : conversionChart;
                        const conversionColumnsKey = (tile.table?.columns ?? []).join("|");
                        const storedCompare = conversionCompare[id];
                        const activeCompare =
                          storedCompare &&
                          storedCompare.columnsKey === conversionColumnsKey &&
                          storedCompare.base < (tile.table?.columns.length ?? 0)
                            ? storedCompare
                            : null;
                        const compareColumns = activeCompare
                          ? [
                              activeCompare.base,
                              ...(activeCompare.second != null &&
                              activeCompare.second !== activeCompare.base &&
                              activeCompare.second < (tile.table?.columns.length ?? 0)
                                ? [activeCompare.second]
                                : []),
                            ]
                          : [];
                        return (
                        <div
                          key={`${tile.title}-${tileI}`}
                          className={`min-w-0 overflow-hidden rounded-xl border bg-card p-3 ${
                            editing ? "border-accent/60" : "border-line"
                          } ${(tile.table || tile.sections || tile.money || tile.deals_list || dynamicsTile || conversionTile || moneyTile || stagesDealListTile) && !sideBySideTile || (tabs[tab]?.tiles ?? []).length === 1 ? "xl:col-span-2" : ""}`}
                        >
                          <div className="mb-2 flex flex-wrap items-start justify-between gap-2">
                            <div className="flex min-w-0 flex-wrap items-center gap-2">
                              <div className="text-sm font-medium">{tile.title}</div>
                              {periodBucketTile && dashboard?.spec && specTile?.source?.kind && (
                                <select
                                  value={specTile?.source?.period || periodBucketDefault}
                                  disabled={!!busy}
                                  onChange={(e) =>
                                    void onTilePeriodBucket(
                                      tab,
                                      tileI,
                                      e.target.value,
                                      specTile.source.kind as
                                        | "halfyear"
                                        | "pivot"
                                        | "outcome"
                                        | "deals_dynamics"
                                        | "deals_dynamics_departments"
                                        | "deals_dynamics_outcome_share"
                                        | "deals_conversion"
                                        | "deals_money"
                                        | "deal_statuses"
                                        | "in_work_stages",
                                    )
                                  }
                                  className="rounded-md border border-line bg-bg px-2 py-0.5 text-[11px] text-zinc-300 outline-none focus:border-accent/60"
                                  aria-label="Группировка по периоду"
                                >
                                  {DEALS_ZK_BUCKETS.map((item) => (
                                    <option key={item.id} value={item.id}>
                                      {item.label}
                                    </option>
                                  ))}
                                </select>
                              )}
                            </div>
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
                              {(tile.table || tile.sections || tile.deals_list) && (
                                <button
                                  type="button"
                                  className="rounded-md border border-line px-2 py-0.5 text-[11px] text-zinc-300 hover:border-accent/50 hover:text-accent"
                                  onClick={() => void onCopyTable(tile)}
                                >
                                  Скопировать
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
                                      plotly_json: conversionTile ? conversionOverview : tile.plotly_json,
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
                          ) : stagesDealListTile && tile.deals_list && tile.filter_meta && specTile ? (
                            <StagesDealListView
                              key={`${tile.title}-${(specTile.source.stages ?? []).join("|")}-${(specTile.source.departments ?? []).join("|")}-${(specTile.source.managers ?? []).join("|")}-${specTile.source.min_potential ?? ""}`}
                              dealsList={tile.deals_list}
                              filterMeta={tile.filter_meta}
                              deptCatalog={tile.departments ?? tile.filter_meta.departments}
                              specSource={specTile.source}
                              specSort={specTile.sort}
                              busy={!!busy}
                              onApply={(patch) => void onTileFiltersPatch(tab, tileI, patch)}
                            />
                          ) : moneyTile && tile.money ? (
                            <MoneyBoardView
                              key={`${tile.bucket_period ?? "half"}-${tile.money.periods.length}`}
                              board={tile.money}
                              deptFilter={deptChartFilter}
                            />
                          ) : sideBySideTile && tile.sections ? (
                            <DealStatusView
                              key={`${tile.title}-${tile.bucket_period ?? specTile?.source?.period ?? "quarter"}-${specTile?.source?.variant ?? "full"}`}
                              sections={tile.sections}
                              variant={specTile?.source?.variant}
                            />
                          ) : specTile?.source?.kind === "in_work_stages" && tile.sections ? (
                            <InWorkStagesView
                              key={`${tile.title}-${tile.bucket_period ?? specTile?.source?.period ?? "quarter"}`}
                              sections={tile.sections}
                            />
                          ) : tile.sections ? (
                            <HalfYearTables
                              key={`${tile.title}-${tile.bucket_period ?? specTile?.source?.period ?? "half"}`}
                              sections={tile.sections}
                            />
                          ) : (dynamicsTile || conversionTile) && tile.table ? (
                            <div
                              key={`${tile.title}-${tile.bucket_period ?? specTile?.source?.period ?? "quarter"}`}
                              className="space-y-4"
                            >
                              <PivotTableView
                                table={tile.table}
                                activeColumn={conversionTile ? selectedPeriod : undefined}
                                onColumnClick={
                                  conversionTile
                                    ? (column) => {
                                        setConversionPeriod((prev) => ({ ...prev, [id]: column }));
                                        setConversionCompare((prev) => {
                                          const current = prev[id];
                                          if (
                                            !current ||
                                            current.columnsKey !== conversionColumnsKey ||
                                            column === current.base
                                          ) {
                                            return prev;
                                          }
                                          return { ...prev, [id]: { ...current, second: column } };
                                        });
                                      }
                                    : undefined
                                }
                              />
                              {(conversionTile ? conversionOverview : tile.plotly_json) ? (
                                <div className="relative flex justify-center overflow-x-auto">
                                  {conversionTile && conversionOverview ? (
                                    <button
                                      type="button"
                                      className="absolute right-2 top-2 z-10 rounded-md border border-line bg-card/90 px-2 py-1 text-[11px] text-zinc-200 hover:border-accent/50 hover:text-accent"
                                      onClick={() =>
                                        setConversionCompare((prev) => ({
                                          ...prev,
                                          [id]: {
                                            columnsKey: conversionColumnsKey,
                                            base: selectedPeriod,
                                            second: null,
                                          },
                                        }))
                                      }
                                    >
                                      К сравнению
                                    </button>
                                  ) : null}
                                  <PlotChart
                                    json={(conversionTile ? conversionOverview : tile.plotly_json)!}
                                    className={
                                      conversionTile
                                        ? "h-[28rem] w-full min-w-[640px]"
                                        : "h-[32rem] w-auto min-w-[min(100%,400px)]"
                                    }
                                  />
                                </div>
                              ) : null}
                              {conversionTile && tile.table && compareColumns.length > 0 ? (
                                <ConversionCompareBlock
                                  table={tile.table}
                                  columns={compareColumns}
                                  deptFilter={deptChartFilter}
                                />
                              ) : null}
                            </div>
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

              </>
            )}
          </div>
          {chartEditor && dashboard?.spec && (
            <ChartEditor
              key={`${chartEditor.mode}-${chartEditor.tabI}-${chartEditor.mode === "edit" ? chartEditor.tileI : "new"}`}
              spec={dashboard.spec}
              columns={meta?.column_names ?? []}
              departments={
                chartEditor.mode === "edit"
                  ? tabs[chartEditor.tabI]?.tiles[chartEditor.tileI]?.departments ?? []
                  : []
              }
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

function SunIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="4" stroke="currentColor" strokeWidth="1.8" />
      <path
        d="M12 2.5v2.2M12 19.3v2.2M4.8 4.8l1.6 1.6M17.6 17.6l1.6 1.6M2.5 12h2.2M19.3 12h2.2M4.8 19.2l1.6-1.6M17.6 6.4l1.6-1.6"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M16.5 3.5a8.5 8.5 0 1 0 4 13.5A7.2 7.2 0 0 1 16.5 3.5Z"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinejoin="round"
      />
    </svg>
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
