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
        { label: "Personal", data: m.nominas, stack: "gastos", order: 1, ...segmento(COLOR.nominas) },
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
        { label: "Personal", data: d.nominas, ...segmento(COLOR.nominas, true) },
      ],
    },
    options: {
      indexAxis: "y",
      interaction: { mode: "index", intersect: false, axis: "y" },
      scales: { x: ejeValores(true), y: ejeCategorias(true) },
    },
  });

  // 3b. Gasto por categoría (proveedores y acreedores apilados)
  const cat = datos.categorias;
  crear("g-categorias", {
    type: "bar",
    data: {
      labels: cat.etiquetas,
      datasets: [
        { label: "Proveedores", data: cat.proveedores, ...segmento(COLOR.proveedores, true) },
        { label: "Acreedores", data: cat.acreedores, ...segmento(COLOR.acreedores, true) },
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

  // 6. Peso mensual de los canales principales (barras apiladas al 100 %)
  //    Orden de colores validado para daltonismo con el validador de paleta.
  const COLORES_CANAL = ["#e87ba4", "#008300", "#eda100", "#6250d6", "#e34948"];
  const p = datos.pesos;
  const colorCanal = Object.fromEntries(p.canales.map((c, i) => [c, COLORES_CANAL[i % COLORES_CANAL.length]]));
  document.querySelectorAll(".muestra[data-canal]").forEach((el) => {
    el.style.background = colorCanal[el.dataset.canal] || "#C3C2B7";
  });

  const lienzoPesos = document.getElementById("g-pesos");
  if (lienzoPesos) {
    const numero = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0 });
    let medida = "ingresos";
    const conjuntos = (med) => p.canales.map((c) => ({
      label: c,
      data: p[med][c].pesos,
      valores: p[med][c].valores,
      ...segmento(colorCanal[c]),
    }));
    const grafico = new Chart(lienzoPesos, {
      type: "bar",
      data: { labels: datos.meses, datasets: conjuntos(medida) },
      options: {
        interaction: { mode: "index", intersect: false },
        plugins: {
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const peso = ctx.parsed.y;
                if (peso === null || peso === undefined) return null;
                const valor = ctx.dataset.valores[ctx.dataIndex];
                const detalle = medida === "ingresos" ? eur.format(valor) : `${numero.format(valor)} noches`;
                return ` ${ctx.dataset.label}: ${peso.toLocaleString("es-ES", { maximumFractionDigits: 1 })} % (${detalle})`;
              },
            },
          },
        },
        scales: {
          x: ejeCategorias(true),
          y: { ...ejeValores(true), max: 100, ticks: { callback: (v) => `${v} %`, stepSize: 25 } },
        },
      },
    });

    document.querySelectorAll("#pesos-canales .conmutador button").forEach((boton) => {
      boton.addEventListener("click", () => {
        medida = boton.dataset.medida;
        document.querySelectorAll("#pesos-canales .conmutador button").forEach((b) => {
          b.setAttribute("aria-pressed", String(b === boton));
        });
        document.querySelectorAll("#pesos-canales [data-tabla-medida]").forEach((t) => {
          t.hidden = t.dataset.tablaMedida !== medida;
        });
        grafico.data.datasets = conjuntos(medida);
        grafico.update();
      });
    });
  }
})();
