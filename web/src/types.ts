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

export type TileSpec = {
  title: string;
  chart_type: string;
  agg: string;
  top_n: number;
  unit: string;
  sort?: string;
  target_line?: number | null;
  source: {
    kind?: string;
    group_semantic?: string;
    group_column?: string;
    value_semantic?: string;
    value_column?: string;
    columns_pattern?: string;
    column_names?: string[];
    period?: string;
  };
};

export type DashSpec = {
  tabs: { title: string; tiles: TileSpec[] }[];
};

export type Dashboard = {
  report_type?: string;
  summary?: string;
  kpis?: Kpi[];
  insights?: string[];
  tabs?: DashTab[];
  charts?: ChartPayload[];
  spec?: DashSpec;
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

export type ReportChart = {
  id: string;
  title: string;
  plotly_json: string;
};
