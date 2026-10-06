/** Фиксированные цвета коммерческих служб — те же, что на бэкенде (dept_chart_colors.py). */

export const COMPANY_CHART_COLOR = "#C00000";

export const DEPARTMENT_CHART_COLORS: Record<string, string> = {
  СИО: "#4472C4",
  ОАПЛиС: "#7030A0",
  СТО: "#70AD47",
  СМЭ: "#FFC000",
  СТЕ: "#ED7D31",
  СООК: "#00B0F0",
  Сервис: "#17A2B8",
};

const LABEL_ALIASES: Record<string, string> = {
  сс: "Сервис",
  сервис: "Сервис",
  "сервисная служба": "Сервис",
};

export function departmentChartColor(label: string): string {
  const text = label.trim();
  if (text === "Совтест") return COMPANY_CHART_COLOR;
  const key = LABEL_ALIASES[text.toLowerCase()] ?? text;
  return DEPARTMENT_CHART_COLORS[key] ?? "#A5A5A5";
}
