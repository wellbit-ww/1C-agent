import { useEffect, useRef, useState } from "react";
import Plotly from "plotly.js-dist-min";
import { blobToDataUrl, copyImageBlobSync } from "./clipboard";
import { CHART_TITLE_FONT_SIZE } from "./chartTitle";

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

function normalizeChartTitle(raw: unknown) {
  if (raw == null || raw === "") return undefined;
  const base: Record<string, unknown> =
    typeof raw === "string" ? { text: raw } : { ...(raw as Record<string, unknown>) };
  const prevFont =
    base.font && typeof base.font === "object" ? (base.font as Record<string, unknown>) : {};
  return {
    x: 0.5,
    xanchor: "center",
    ...base,
    font: { ...prevFont, size: CHART_TITLE_FONT_SIZE },
  };
}

function chartLayout(fig: PlotlyFigure, theme: "dark" | "light") {
  const src = fig.layout || {};
  const srcMargin = (src.margin || {}) as Record<string, number>;
  const light = theme === "light";
  const layout: Record<string, unknown> = {
    ...src,
    autosize: light ? false : src.autosize !== false,
    paper_bgcolor: light ? "#ffffff" : "rgba(0,0,0,0)",
    plot_bgcolor: light ? "#ffffff" : "rgba(0,0,0,0)",
    font: {
      color: light ? "#1f2937" : "#c5cad3",
      size: 12,
      family: "Segoe UI, sans-serif",
      ...(light ? {} : (src.font as object)),
    },
    template: light ? undefined : DARK_TEMPLATE,
    ...(light
      ? { colorway: ["#c8102e", "#9f1239", "#e11d48", "#fb7185", "#7f1d1d", "#fda4af"] }
      : {}),
  };
  const normalizedTitle = normalizeChartTitle(src.title);
  if (normalizedTitle) layout.title = normalizedTitle;
  const traces = Array.isArray(fig.data) ? fig.data : [];
  const isPie = traces.some(
    (t) => t && typeof t === "object" && (t as { type?: string }).type === "pie",
  );
  if (isPie) {
    layout.margin = {
      l: Math.max(24, srcMargin.l ?? 0),
      r: Math.max(24, srcMargin.r ?? 0),
      t: Math.max(56, srcMargin.t ?? 0),
      b: Math.max(96, srcMargin.b ?? 0),
    };
    const legend = (src.legend && typeof src.legend === "object" ? src.legend : {}) as Record<
      string,
      unknown
    >;
    layout.legend = {
      ...legend,
      orientation: "h",
      x: 0.5,
      xanchor: "center",
      font: {
        color: light ? "#1f2937" : "#c5cad3",
        size: 11,
      },
    };
  } else {
    layout.margin = {
      l: Math.max(64, srcMargin.l ?? 0),
      r: Math.max(16, srcMargin.r ?? 0),
      t: Math.max(48, srcMargin.t ?? 0),
      b: Math.max(110, srcMargin.b ?? 0),
    };
  }
  if (!isPie) {
    const xaxis = (src.xaxis && typeof src.xaxis === "object" ? src.xaxis : {}) as Record<
      string,
      unknown
    >;
    const yaxis = (src.yaxis && typeof src.yaxis === "object" ? src.yaxis : {}) as Record<
      string,
      unknown
    >;
    const axisInk = light
      ? { gridcolor: "#e5e7eb", zerolinecolor: "#e5e7eb", linecolor: "#d1d5db", tickfont: { color: "#1f2937" } }
      : {};
    layout.xaxis = { automargin: true, tickangle: -35, ...xaxis, ...axisInk };
    layout.yaxis = { automargin: true, ...yaxis, ...axisInk };
  }
  return { layout, isPie };
}

function loadImage(blob: Blob) {
  return new Promise<HTMLImageElement>((resolve, reject) => {
    const url = URL.createObjectURL(blob);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      resolve(img);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("Не удалось прочитать график"));
    };
    img.src = url;
  });
}

/** Убирает пустые белые поля вокруг рисунка, которые Excel вставляет как есть. */
async function trimPaperMargins(blob: Blob, pad = 18): Promise<Blob> {
  const img = await loadImage(blob);
  const canvas = document.createElement("canvas");
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx || canvas.width < 2 || canvas.height < 2) return blob;
  ctx.drawImage(img, 0, 0);
  const { data, width, height } = ctx.getImageData(0, 0, canvas.width, canvas.height);
  const paper = (i: number) => {
    if (data[i + 3] < 12) return true;
    return data[i] >= 248 && data[i + 1] >= 248 && data[i + 2] >= 248;
  };
  let minX = width;
  let minY = height;
  let maxX = -1;
  let maxY = -1;
  for (let y = 0; y < height; y += 1) {
    const row = y * width * 4;
    for (let x = 0; x < width; x += 1) {
      if (paper(row + x * 4)) continue;
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    }
  }
  if (maxX < minX || maxY < minY) return blob;
  const x0 = Math.max(0, minX - pad);
  const y0 = Math.max(0, minY - pad);
  const x1 = Math.min(width, maxX + 1 + pad);
  const y1 = Math.min(height, maxY + 1 + pad);
  if (x0 === 0 && y0 === 0 && x1 === width && y1 === height) return blob;
  const out = document.createElement("canvas");
  out.width = x1 - x0;
  out.height = y1 - y0;
  const octx = out.getContext("2d");
  if (!octx) return blob;
  octx.fillStyle = "#ffffff";
  octx.fillRect(0, 0, out.width, out.height);
  octx.drawImage(canvas, x0, y0, out.width, out.height, 0, 0, out.width, out.height);
  const next = await new Promise<Blob | null>((resolve) => out.toBlob((result) => resolve(result), "image/png"));
  return next ?? blob;
}

async function renderChartPng(json: string) {
  const fig = JSON.parse(json) as PlotlyFigure;
  const { layout } = chartLayout(fig, "light");
  const width = Number(layout.width) || 960;
  const height = Number(layout.height) || 540;
  const host = document.createElement("div");
  host.style.position = "fixed";
  host.style.left = "-12000px";
  host.style.top = "0";
  host.style.width = `${width}px`;
  host.style.height = `${height}px`;
  document.body.appendChild(host);
  try {
    await Plotly.newPlot(host, fig.data ?? [], { ...layout, width, height }, {
      staticPlot: true,
      displayModeBar: false,
    });
    const url = await Plotly.toImage(host, { format: "png", width, height, scale: 2 });
    const blob = await trimPaperMargins(await (await fetch(url)).blob());
    const dataUrl = await blobToDataUrl(blob);
    return { blob, dataUrl };
  } finally {
    Plotly.purge(host);
    host.remove();
  }
}

function currentTheme(): "dark" | "light" {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

export function PlotChart({ json, className }: { json: string; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [copyState, setCopyState] = useState<"idle" | "busy" | "done" | "again" | "error">("idle");
  const [theme, setTheme] = useState<"dark" | "light">(currentTheme);
  const readyPng = useRef<{ blob: Blob; dataUrl: string } | null>(null);
  const preparing = useRef<Promise<{ blob: Blob; dataUrl: string }> | null>(null);

  function preparePng() {
    if (readyPng.current) return Promise.resolve(readyPng.current);
    if (!preparing.current) {
      preparing.current = renderChartPng(json)
        .then((ready) => {
          readyPng.current = ready;
          return ready;
        })
        .finally(() => {
          preparing.current = null;
        });
    }
    return preparing.current;
  }

  useEffect(() => {
    readyPng.current = null;
    preparing.current = null;
  }, [json, theme]);

  useEffect(() => {
    const onTheme = () => setTheme(currentTheme());
    window.addEventListener("themechange", onTheme);
    return () => window.removeEventListener("themechange", onTheme);
  }, []);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let fig: PlotlyFigure;
    try {
      fig = JSON.parse(json);
    } catch {
      return;
    }
    const { layout } = chartLayout(fig, theme);
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
  }, [json, theme]);

  function copyReady() {
    const ready = readyPng.current;
    if (!ready) return false;
    copyImageBlobSync(ready.blob, ready.dataUrl);
    return true;
  }

  function onCopy() {
    if (readyPng.current) {
      try {
        copyReady();
        setCopyState("done");
      } catch {
        setCopyState("error");
      }
      window.setTimeout(() => setCopyState("idle"), 1600);
      return;
    }
    setCopyState("busy");
    void preparePng()
      .then(() => {
        try {
          copyReady();
          setCopyState("done");
          window.setTimeout(() => setCopyState("idle"), 1600);
        } catch {
          setCopyState("again");
        }
      })
      .catch(() => {
        setCopyState("error");
        window.setTimeout(() => setCopyState("idle"), 1600);
      });
  }

  const copyLabel =
    copyState === "busy"
      ? "Копирую…"
      : copyState === "done"
        ? "Скопировано"
        : copyState === "again"
          ? "Нажмите ещё раз"
          : copyState === "error"
            ? "Не удалось"
            : "Скопировать график";

  return (
    <div className={`relative min-w-0 ${className ?? "h-72 w-full min-w-0"}`}>
      <button
        type="button"
        className="absolute left-2 top-2 z-10 rounded-md border border-line bg-card/90 px-2 py-1 text-[11px] text-zinc-200 hover:border-accent/50 hover:text-accent disabled:opacity-60"
        onPointerDown={() => void preparePng()}
        onClick={onCopy}
        disabled={copyState === "busy"}
      >
        {copyLabel}
      </button>
      <div ref={ref} className="h-full w-full min-w-0" />
    </div>
  );
}
