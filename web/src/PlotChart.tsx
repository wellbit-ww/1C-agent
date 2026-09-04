import { useEffect, useRef } from "react";
import Plotly from "plotly.js-dist-min";

const DARK_LAYOUT: Record<string, unknown> = {
  paper_bgcolor: "rgba(0,0,0,0)",
  plot_bgcolor: "rgba(0,0,0,0)",
  font: { color: "#c5cad3", size: 11, family: "Segoe UI, sans-serif" },
  margin: { t: 32, r: 16, b: 40, l: 48 },
  legend: { bgcolor: "rgba(0,0,0,0)", font: { color: "#c5cad3" } },
  xaxis: { gridcolor: "#232a33", zerolinecolor: "#232a33", linecolor: "#232a33" },
  yaxis: { gridcolor: "#232a33", zerolinecolor: "#232a33", linecolor: "#232a33" },
};

export function PlotChart({ json, className }: { json: string; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let fig: { data?: unknown; layout?: Record<string, unknown> };
    try {
      fig = JSON.parse(json);
    } catch {
      return;
    }
    const layout = { ...DARK_LAYOUT, ...(fig.layout || {}), paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)" };
    void Plotly.react(el, fig.data ?? [], layout, {
      responsive: true,
      displayModeBar: false,
    });
    const onResize = () => window.dispatchEvent(new Event("resize"));
    const ro = new ResizeObserver(onResize);
    ro.observe(el);
    return () => {
      ro.disconnect();
      Plotly.purge(el);
    };
  }, [json]);

  return <div ref={ref} className={className ?? "h-72 w-full"} />;
}
