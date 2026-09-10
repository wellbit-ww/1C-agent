import { useEffect, useRef } from "react";
import Plotly from "plotly.js-dist-min";

const DARK_TEMPLATE = {
  layout: {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: "#c5cad3", size: 11, family: "Segoe UI, sans-serif" },
    legend: { bgcolor: "rgba(0,0,0,0)", font: { color: "#c5cad3" } },
    colorway: ["#2ee6a6", "#5b8def", "#f0b429", "#e05d5d", "#c084fc", "#67e8f9"],
    xaxis: {
      gridcolor: "#232a33",
      zerolinecolor: "#232a33",
      linecolor: "#232a33",
      tickfont: { color: "#c5cad3" },
    },
    yaxis: {
      gridcolor: "#232a33",
      zerolinecolor: "#232a33",
      linecolor: "#232a33",
      tickfont: { color: "#c5cad3" },
    },
  },
};

type PlotlyFigure = {
  data?: unknown;
  layout?: Record<string, unknown>;
};

export function PlotChart({ json, className }: { json: string; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let fig: PlotlyFigure;
    try {
      fig = JSON.parse(json);
    } catch {
      return;
    }
    const src = fig.layout || {};
    const srcMargin = (src.margin || {}) as Record<string, number>;
    const layout: Record<string, unknown> = {
      ...src,
      autosize: true,
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      font: { color: "#c5cad3", size: 11, family: "Segoe UI, sans-serif", ...(src.font as object) },
      template: DARK_TEMPLATE,
      margin: {
        l: Math.max(64, srcMargin.l ?? 0),
        r: Math.max(16, srcMargin.r ?? 0),
        t: Math.max(28, srcMargin.t ?? 0),
        b: Math.max(110, srcMargin.b ?? 0),
      },
    };
    const traces = Array.isArray(fig.data) ? fig.data : [];
    const isPie = traces.some(
      (t) => t && typeof t === "object" && (t as { type?: string }).type === "pie",
    );
    if (!isPie) {
      const xaxis = (src.xaxis && typeof src.xaxis === "object" ? src.xaxis : {}) as Record<
        string,
        unknown
      >;
      const yaxis = (src.yaxis && typeof src.yaxis === "object" ? src.yaxis : {}) as Record<
        string,
        unknown
      >;
      layout.xaxis = { automargin: true, tickangle: -35, ...xaxis };
      layout.yaxis = { automargin: true, ...yaxis };
    }
    let cancelled = false;
    const resize = () => {
      const fn = Plotly.Plots?.resize;
      if (fn) fn(el);
    };
    void Plotly.react(el, fig.data ?? [], layout, {
      responsive: true,
      displayModeBar: false,
    }).then(() => {
      if (!cancelled) resize();
    });
    const ro = new ResizeObserver(() => {
      resize();
    });
    ro.observe(el);
    return () => {
      cancelled = true;
      ro.disconnect();
      Plotly.purge(el);
    };
  }, [json]);

  return <div ref={ref} className={className ?? "h-72 w-full min-w-0"} />;
}
