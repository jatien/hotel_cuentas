// Diálogo "Nuevo asiento": elegir un asiento modelo, rellenar los datos, ver el asiento
// en forma de T (Debe | Haber) mientras se escribe y crearlo.
(() => {
  const cfg = window.LIBRO;
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const modelos = JSON.parse(document.getElementById("datos-modelos").textContent);
  const ejercicio = JSON.parse(document.getElementById("datos-ejercicio").textContent);

  const dialogo = document.getElementById("dialogo-modelo");
  const paso1 = document.getElementById("dm-paso1");
  const paso2 = document.getElementById("dm-paso2");
  const contModelos = document.getElementById("dm-modelos");
  const form = document.getElementById("dm-form");
  const contCampos = document.getElementById("dm-campos");
  const vista = document.getElementById("dm-vista");
  const btnCrear = document.getElementById("dm-crear");

  const eur = new Intl.NumberFormat("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const f = (v) => eur.format(Number(v));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const urlModelo = (plantilla, clave) => plantilla.replace("/x/", `/${clave}/`);

  let actual = null;

  // ---------- paso 1: elegir modelo ----------
  const grupos = [...new Set(modelos.map((m) => m.grupo))];
  contModelos.innerHTML = grupos.map((g) => `
    <h3 class="dm-grupo">${esc(g)}</h3>
    <div class="dm-rejilla">
      ${modelos.filter((m) => m.grupo === g).map((m) => `
        <button type="button" class="dm-modelo" data-clave="${m.clave}">
          <strong>${esc(m.nombre)}</strong>
          ${m.tipo !== "operacion" ? `<span class="etiqueta etiqueta-${m.tipo}">${esc(m.tipo_label)}</span>` : ""}
          <span>${esc(m.descripcion)}</span>
        </button>`).join("")}
    </div>`).join("");

  function hoy() {
    const d = new Date();
    return new Date(d - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  }

  function fechaPropuesta(m) {
    if (m.fecha === "inicio") return ejercicio.inicio;
    if (m.fecha === "fin") return ejercicio.fin;
    const h = hoy();
    return h >= ejercicio.inicio && h <= ejercicio.fin ? h : ejercicio.fin;
  }

  // ---------- paso 2: datos ----------
  function campoHTML(c) {
    const opcional = c.obligatorio ? "" : ' <span class="opcional">(opcional)</span>';
    const ayuda = c.ayuda ? `<small class="ayuda">${esc(c.ayuda)}</small>` : "";
    let control;
    if (c.tipo === "cuenta") {
      if (!c.cuentas.length) {
        return `<div class="dm-campo dm-falta">
          <span>${esc(c.etiqueta)}${opcional}</span>
          <p>No hay subcuentas de ${esc(c.prefijos.join(", "))}.
             <a href="${cfg.planUrl}" target="_blank" rel="noopener">Dalas de alta en el cuadro de cuentas</a>
             y vuelve a abrir este asiento.</p>
        </div>`;
      }
      const vacia = c.cuentas.length > 1 || !c.obligatorio
        ? `<option value="">${c.obligatorio ? "Elige…" : "Ninguna"}</option>` : "";
      control = `<select name="${c.nombre}">${vacia}${c.cuentas.map((x) =>
        `<option value="${x.codigo}">${x.codigo} ${esc(x.nombre)}</option>`).join("")}</select>`;
    } else if (c.tipo === "opcion") {
      control = `<select name="${c.nombre}">${c.opciones.map(([v, n]) =>
        `<option value="${esc(v)}" ${v === c.inicial ? "selected" : ""}>${esc(n)}</option>`).join("")}</select>`;
    } else if (c.tipo === "importe") {
      control = `<input name="${c.nombre}" inputmode="decimal" placeholder="0,00" class="num">`;
    } else {
      control = `<input name="${c.nombre}">`;
    }
    return `<label class="dm-campo">${esc(c.etiqueta)}${opcional}${control}${ayuda}</label>`;
  }

  function abrirModelo(clave) {
    actual = modelos.find((m) => m.clave === clave);
    document.getElementById("dm-nombre").textContent = actual.nombre;
    document.getElementById("dm-descripcion").textContent = actual.descripcion;
    document.getElementById("dm-documento").hidden = !actual.con_documento;
    form.reset();
    form.elements.fecha.value = fechaPropuesta(actual);
    contCampos.innerHTML = actual.campos.map(campoHTML).join("");
    paso1.hidden = true;
    paso2.hidden = false;
    btnCrear.hidden = false;
    previsualizar();
    (contCampos.querySelector("select, input") || form.elements.fecha).focus();
  }

  function volver() {
    actual = null;
    paso2.hidden = true;
    paso1.hidden = false;
    btnCrear.hidden = true;
    vista.innerHTML = "";
  }

  function cuerpo() {
    const datos = {};
    for (const c of actual.campos) {
      const el = form.elements[c.nombre];
      if (el) datos[c.nombre] = el.value;
    }
    return {
      fecha: form.elements.fecha.value,
      documento: form.elements.documento.value,
      concepto: form.elements.concepto.value,
      datos,
    };
  }

  async function llamar(plantilla) {
    const r = await fetch(urlModelo(plantilla, actual.clave), {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
      body: JSON.stringify(cuerpo()),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || `Error ${r.status}`);
    return d;
  }

  // ---------- vista previa en T ----------
  function lado(lineas, campo) {
    return lineas.filter((l) => Number(l[campo]) > 0).map((l) => `
      <li><span class="codigo">${esc(l.cuenta)}</span><span class="t-nombre">${esc(l.nombre)}</span>
          <strong>${f(l[campo])}</strong></li>`).join("");
  }

  function pintarVista(d) {
    if (actual.clave === "libre") {
      vista.innerHTML = `<p class="dm-estado">Se creará un asiento vacío de tipo
        <strong>${esc(form.elements.tipo?.selectedOptions[0]?.textContent || "")}</strong> para completar
        a mano en el libro diario.</p>`;
      return;
    }
    vista.innerHTML = `
      <div class="t">
        <div class="t-cab"><span>Debe</span><span>Haber</span></div>
        <div class="t-cuerpo">
          <ul class="t-lado t-debe">${lado(d.lineas, "debe")}</ul>
          <ul class="t-lado t-haber">${lado(d.lineas, "haber")}</ul>
        </div>
        <div class="t-pie"><strong>${f(d.debe)}</strong><strong>${f(d.haber)}</strong></div>
      </div>
      <p class="dm-estado ${d.cuadra ? "ok" : "mal"}">
        ${d.cuadra ? `Asiento ${esc(d.clase.toLowerCase())} que cuadra.` : "El asiento no cuadra."}
        <span>Concepto: ${esc(d.concepto)}</span>
      </p>`;
  }

  let espera;
  let turno = 0;
  function previsualizar() {
    clearTimeout(espera);
    espera = setTimeout(async () => {
      const mio = ++turno;
      btnCrear.disabled = true;
      try {
        const d = await llamar(cfg.previsualizarUrl);
        if (mio !== turno) return;
        pintarVista(d);
        btnCrear.disabled = !(d.cuadra || actual.clave === "libre");
      } catch (e) {
        if (mio !== turno) return;
        vista.innerHTML = `<p class="dm-estado pendiente">${esc(e.message)}</p>`;
      }
    }, 300);
  }

  async function crear() {
    btnCrear.disabled = true;
    try {
      const r = await llamar(cfg.crearModeloUrl);
      // se abre el libro en el mes del asiento nuevo, con el asiento seleccionado
      location.href = `?ejercicio=${r.ejercicio}&mes=${r.mes}#a${r.id}`;
    } catch (e) {
      vista.insertAdjacentHTML("afterbegin", `<p class="dm-estado mal">${esc(e.message)}</p>`);
      btnCrear.disabled = false;
    }
  }

  // ---------- eventos ----------
  document.getElementById("nuevo-asiento").addEventListener("click", () => {
    volver();
    dialogo.showModal();
  });
  contModelos.addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-clave]");
    if (b) abrirModelo(b.dataset.clave);
  });
  document.getElementById("dm-atras").addEventListener("click", volver);
  document.getElementById("dm-cancelar").addEventListener("click", () => dialogo.close());
  form.addEventListener("input", previsualizar);
  form.addEventListener("change", previsualizar);
  form.addEventListener("submit", (ev) => ev.preventDefault());
  btnCrear.addEventListener("click", crear);
  dialogo.addEventListener("click", (ev) => {
    if (ev.target === dialogo) dialogo.close();
  });
})();