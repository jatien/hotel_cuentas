/* Gráficos del PMS Copilot (Chart.js 4).
 * Cada gráfico llega como especificación JSON (json_script) con el id indicado en
 * data-grafico del <canvas>: nunca se interpola nada en el JS.
 * Mismos colores que el análisis de beneficios para las series que coinciden. */
(function () {
  "use strict";
  if (typeof Chart === "undefined") return;

  const FIJOS = {
    "Proveedores": "#2a78d6", "Acreedores": "#eb6834", "Personal": "#1baf7a",
    "Ingresos": "#1C2733", "Gastos": "#9B3D5B", "Beneficio": "#5B6878", "Beneficio acumulado": "#B7791F",
  };
  const PALETA = ["#2D4FC4", "#0E7A6D", "#B7791F", "#9B3D5B", "#6B46C1", "#2a78d6", "#eb6834", "#5B6878"];

  const eur = new Intl.NumberFormat("es-ES", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
  const compacto = new Intl.NumberFormat("es-ES", { notation: "compact", maximumFractionDigits: 1 });

  Chart.defaults.font.family = '"IBM Plex Sans", system-ui, "Segoe UI", sans-serif';
  Chart.defaults.font.size = 12;
  Chart.defaults.color = "#5B6878";
  Chart.defaults.maintainAspectRatio = false;
  Chart.defaults.plugins.legend.align = "start";
  Chart.defaults.plugins.legend.labels.boxWidth = 12;

  function color(nombre, i) {
    const base = nombre.replace(/ \d{4}$/, "");      // «Bar 2025» usa el color de «Bar»
    return FIJOS[base] || PALETA[i % PALETA.length];
  }

  function dibujar(canvas) {
    const g = JSON.parse(document.getElementById(canvas.dataset.grafico).textContent);
    const tipo = g.tipo;
    const tarta = tipo === "tarta";
    const horizontal = tipo === "barras_horizontales";
    const apilado = tipo === "barras_apiladas" || horizontal || tipo === "area";
    const lineal = tipo === "lineas" || tipo === "area";

    // Años comparados: mismo color por serie, el año anterior más claro
    const anios = [...new Set(g.series.map((s) => (s.nombre.match(/ (\d{4})$/) || [])[1]).filter(Boolean))];
    const datasets = g.series.map((s, i) => {
      const valores = s.valores.map(Number);
      if (tarta) {
        return { label: s.nombre, data: valores, backgroundColor: g.etiquetas.map((_, j) => PALETA[j % PALETA.length]),
                 borderColor: "#FFFFFF", borderWidth: 2 };
      }
      const anio = (s.nombre.match(/ (\d{4})$/) || [])[1];
      const c = color(s.nombre, anios.length ? g.series.findIndex((x) => x.nombre.replace(/ \d{4}$/, "") === s.nombre.replace(/ \d{4}$/, "")) : i);
      const atenuado = anio && anio !== anios[0];
      return {
        label: s.nombre, data: valores,
        backgroundColor: atenuado ? c + "66" : (tipo === "area" ? c + "55" : c),
        borderColor: c, borderWidth: lineal ? 2 : 0, borderDash: atenuado && lineal ? [6, 4] : [],
        fill: tipo === "area", tension: 0.25, pointRadius: lineal ? 3 : 0,
        maxBarThickness: 34, stack: apilado && anio ? anio : undefined,
      };
    });

    const ejeValor = { stacked: apilado, beginAtZero: true, grid: { color: "#E1E0D9" }, border: { display: false },
                       ticks: { callback: (v) => compacto.format(v) + " €", maxTicksLimit: 6 } };
    const ejeCategoria = { stacked: apilado, grid: { display: false } };

    new Chart(canvas, {
      type: tarta ? "doughnut" : (lineal ? "line" : "bar"),
      data: { labels: g.etiquetas, datasets },
      options: {
        indexAxis: horizontal ? "y" : "x",
        plugins: {
          title: { display: true, text: g.titulo, align: "start", color: "#1C2733", font: { size: 14, weight: "600" } },
          legend: { position: tarta ? "right" : "top" },
          tooltip: { callbacks: { label: (ctx) => ` ${tarta ? ctx.label : ctx.dataset.label}: ${eur.format(tarta ? ctx.parsed : ctx.parsed[horizontal ? "x" : "y"])}` } },
        },
        scales: tarta ? {} : (horizontal ? { x: ejeValor, y: ejeCategoria } : { x: ejeCategoria, y: ejeValor }),
      },
    });
  }

  document.querySelectorAll("canvas[data-grafico]").forEach(dibujar);
})();
