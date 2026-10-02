declare module "plotly.js-dist-min" {
  const Plotly: {
    react: (
      el: HTMLElement,
      data: unknown,
      layout?: Record<string, unknown>,
      config?: Record<string, unknown>,
    ) => Promise<unknown>;
    purge: (el: HTMLElement) => void;
    newPlot: (
      el: HTMLElement,
      data: unknown,
      layout?: Record<string, unknown>,
      config?: Record<string, unknown>,
    ) => Promise<unknown>;
    toImage: (
      el: HTMLElement,
      opts: { format: "png"; width: number; height: number; scale?: number },
    ) => Promise<string>;
    Plots?: { resize?: (el: HTMLElement) => void };
  };
  export default Plotly;
}
