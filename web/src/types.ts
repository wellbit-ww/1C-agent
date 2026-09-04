export type ChartPayload = {
  plotly_json?: string;
  pin_spec?: Record<string, unknown>;
  error?: string;
  title?: string;
};

export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  charts?: ChartPayload[];
};

export type Tile = {
  title: string;
  plotly_json?: string;
  error?: string;
};

export type DashTab = {
  title: string;
  tiles: Tile[];
};

export type Kpi = { label: string; value: string | number };

export type Dashboard = {
  report_type?: string;
  summary?: string;
  kpis?: Kpi[];
  insights?: string[];
  tabs?: DashTab[];
  charts?: ChartPayload[];
  spec?: { tabs: unknown[] };
  metadata?: {
    rows?: number;
    columns?: number;
    period?: string;
    column_names?: string[];
  };
  file_context?: FileContext;
  warning?: string;
};

export type FileContext = {
  title?: string;
  summary?: string;
  report_kind?: string;
  grain?: string;
  metrics?: string[];
  groupers?: string[];
  dashboard_ideas?: string[];
  caveats?: string[];
  llm_ready?: boolean;
  sheets?: { name: string; rows: number; n_columns: number; active?: boolean }[];
};

export type Report = {
  report_type?: string;
  narrative?: string;
  insights?: string[] | string;
  kpis?: Kpi[];
  charts?: ChartPayload[];
  comment?: string;
  metadata?: {
    filename?: string;
    rows?: number;
    columns?: number;
    period?: string;
  };
  data_quality?: {
    total_cells?: number;
    null_cells?: number;
    null_pct?: number;
    duplicates?: number;
    worst_columns?: { column: string; pct: number }[];
  };
};
