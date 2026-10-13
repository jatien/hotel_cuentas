/* Gráficos del análisis de beneficios (Chart.js 4).
 * Los datos llegan con json_script desde la vista: nunca se interpolan en el JS.
 * Cada origen tiene SIEMPRE el mismo color en todas las gráficas. */
(function () {
  "use strict";

  const datos = JSON.parse(document.getElementById("datos-graficos").textContent);

  const COLOR = {
    proveedores: "#2a78d6",   // azul
    acreedores: "#eb6834",    // naranja
    nominas: "#1baf7a",       // aguamarina
    ingresos: "#1C2733",      // tinta: el ingreso se distingue de los tres gastos
    beneficio: "#5B6878",     // neutro para beneficio positivo
    perdida: "#e34948",       // rojo para pérdida
    rejilla: "#E1E0D9",
    eje: "#C3C2B7",
    texto: "#5B6878",
  };

  const eur = new Intl.NumberFormat("es-ES", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
  const compacto = new Intl.NumberFormat("es-ES", { notation: "compact", maximumFractionDigits: 1 });
  const ejeEuros = (v) => `${compacto.format(v)} €`;

  Chart.defaults.font.family = '"IBM Plex Sans", system-ui, "Segoe UI", sans-serif';
  Chart.defaults.font.size = 12;
  Chart.defaults.color = COLOR.texto;
  Chart.defaults.borderColor = COLOR.rejilla;
  Chart.defaults.maintainAspectRatio = false;
  Chart.defaults.plugins.legend.position = "top";
  Chart.defaults.plugins.legend.align = "start";
  Chart.defaults.plugins.legend.labels.boxWidth = 12;
  Chart.defaults.plugins.legend.labels.boxHeight = 12;
  Chart.defaults.plugins.tooltip.backgroundColor = "#1C2733";
  Chart.defaults.plugins.tooltip.padding = 10;
  Chart.defaults.plugins.tooltip.boxWidth = 10;
  Chart.defaults.plugins.tooltip.boxHeight = 10;
  Chart.defaults.plugins.tooltip.callbacks.label = (ctx) => {
    const v = ctx.parsed[ctx.chart.options.indexAxis === "y" ? "x" : "y"];
    return ` ${ctx.dataset.label}: ${eur.format(v)}`;
  };

  // Separación de 2 px entre segmentos apilados, con el color de la tarjeta
  const segmento = (color, horizontal) => ({
    backgroundColor: color,
    borderColor: "#FFFFFF",
    borderWidth: horizontal ? { right: 2 } : { top: 2 },
    borderSkipped: false,
    maxBarThickness: horizontal ? 26 : 30,
  });

  const ejeValores = (apilado) => ({
    stacked: apilado,
    beginAtZero: true,
    grid: { color: COLOR.rejilla },
    border: { display: false },
    ticks: { callback: ejeEuros, maxTicksLimit: 6 },
  });
  const ejeCategorias = (apilado) => ({
    stacked: apilado,
    grid: { display: false },
    border: { color: COLOR.eje },
  });

  function crear(id, config) {
    const lienzo = document.getElementById(id);
    if (lienzo) new Chart(lienzo, config);
  }

  // 1. Ingresos frente a gastos (barras apiladas de gasto + línea de ingresos, un solo eje en €)
  const m = datos.mensual;
  crear("g-mensual", {
    type: "bar",
    data: {
      labels: datos.meses,
      datasets: [
        { type: "line", label: "Ingresos", data: m.ingresos, stack: "ingresos", order: 0,
          borderColor: COLOR.ingresos, backgroundColor: COLOR.ingresos, borderWidth: 2,
          pointRadius: 4, pointHoverRadius: 6, pointBackgroundColor: COLOR.ingresos,
          pointBorderColor: "#FFFFFF", pointBorderWidth: 2, tension: 0 },
        { label: "Proveedores", data: m.proveedores, stack: "gastos", order: 1, ...segmento(COLOR.proveedores) },
        { label: "Acreedores", data: m.acreedores, stack: "gastos", order: 1, ...segmento(COLOR.acreedores) },
        { label: "Nóminas", data: m.nominas, stack: "gastos", order: 1, ...segmento(COLOR.nominas) },
      ],
    },
    options: {
      interaction: { mode: "index", intersect: false },
      spanGaps: false,
      scales: { x: ejeCategorias(true), y: ejeValores(true) },
    },
  });

  // 2. Beneficio mensual (barras: neutro si gana, rojo si pierde) + acumulado (línea)
  crear("g-beneficio", {
    type: "bar",
    data: {
      labels: datos.meses,
      datasets: [
        { label: "Beneficio del mes", data: m.beneficio, order: 1,
          backgroundColor: m.beneficio.map((v) => (v < 0 ? COLOR.perdida : COLOR.beneficio)),
          borderRadius: 4, borderSkipped: "start", maxBarThickness: 30 },
        { type: "line", label: "Acumulado", data: m.acumulado, order: 0,
          borderColor: COLOR.ingresos, backgroundColor: COLOR.ingresos, borderWidth: 2,
          borderDash: [6, 4], pointRadius: 4, pointHoverRadius: 6,
          pointBackgroundColor: COLOR.ingresos, pointBorderColor: "#FFFFFF", pointBorderWidth: 2 },
      ],
    },
    options: {
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          labels: {
            // La leyenda de las barras muestra el color neutro, no el del primer mes
            generateLabels(chart) {
              const items = Chart.defaults.plugins.legend.labels.generateLabels(chart);
              const barras = items.find((it) => it.datasetIndex === 0);
              if (barras) { barras.fillStyle = COLOR.beneficio; barras.strokeStyle = COLOR.beneficio; }
              return items;
            },
          },
        },
      },
      scales: { x: ejeCategorias(false), y: { ...ejeValores(false), beginAtZero: true } },
    },
  });

  // 3. Gasto por departamento (barras horizontales apiladas por origen)
  const d = datos.departamentos;
  crear("g-departamentos", {
    type: "bar",
    data: {
      labels: d.etiquetas,
      datasets: [
        { label: "Proveedores", data: d.proveedores, ...segmento(COLOR.proveedores, true) },
        { label: "Acreedores", data: d.acreedores, ...segmento(COLOR.acreedores, true) },
        { label: "Nóminas", data: d.nominas, ...segmento(COLOR.nominas, true) },
      ],
    },
    options: {
      indexAxis: "y",
      interaction: { mode: "index", intersect: false, axis: "y" },
      scales: { x: ejeValores(true), y: ejeCategorias(true) },
    },
  });

  // 4 y 5. Una sola serie: sin leyenda (el título la nombra)
  function barrasSimples(id, serie, etiqueta, color) {
    crear(id, {
      type: "bar",
      data: {
        labels: serie.etiquetas,
        datasets: [{ label: etiqueta, data: serie.valores, backgroundColor: color,
                     borderRadius: 4, borderSkipped: "start", maxBarThickness: 22 }],
      },
      options: {
        indexAxis: "y",
        plugins: { legend: { display: false } },
        scales: { x: ejeValores(false), y: ejeCategorias(false) },
      },
    });
  }
  barrasSimples("g-cuentas", datos.cuentas, "Gasto", COLOR.acreedores);
  barrasSimples("g-canales", datos.canales, "Ingresos", COLOR.ingresos);
})();
