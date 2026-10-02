(() => {
  const arbol = document.getElementById("arbol");
  const buscador = document.getElementById("buscador");
  const form = document.getElementById("form-alta");
  const selBase = document.getElementById("id_base");
  const nombre = document.getElementById("id_nombre");
  const nif = document.getElementById("id_nif");
  const codigo = document.getElementById("id_codigo");
  const ayudaTipo = document.getElementById("tipo-ayuda");
  const baseResumen = document.getElementById("base-resumen");
  const bloqueNif = document.getElementById("bloque-nif");
  const nifMarca = document.getElementById("nif-marca");
  const vista = document.getElementById("vista-previa");
  const ficha = document.getElementById("ficha");
  const fichaContenido = document.getElementById("ficha-contenido");

  const tipos = JSON.parse(document.getElementById("datos-tipos").textContent);
  const porClave = Object.fromEntries(tipos.map((t) => [t.clave, t]));

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const eur = new Intl.NumberFormat("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const normalizar = (s) => s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();

  // ---------- árbol y búsqueda ----------
  const nodos = [...arbol.querySelectorAll("[data-texto]")];
  nodos.forEach((n) => (n.dataset.texto = normalizar(n.dataset.texto)));

  function abrirAncestros(el) {
    let p = el.parentElement?.closest("details");
    while (p) {
      p.open = true;
      p.hidden = false;
      p = p.parentElement?.closest("details");
    }
  }

  buscador.addEventListener("input", () => {
    const q = normalizar(buscador.value.trim());
    if (!q) {
      nodos.forEach((n) => (n.hidden = false));
      arbol.querySelectorAll("details").forEach((d) => (d.open = d.classList.contains("nivel-1")));
      return;
    }
    nodos.forEach((n) => (n.hidden = true));
    nodos.forEach((n) => {
      if (!n.dataset.texto.includes(q)) return;
      n.hidden = false;
      n.querySelectorAll("[data-texto]").forEach((d) => (d.hidden = false));
      abrirAncestros(n);
    });
  });

  // ---------- asistente de alta ----------
  const tipoActual = () => form.querySelector('input[name="tipo"]:checked')?.value || "proveedor";

  function pintarTipo(clave, base) {
    const t = porClave[clave];
    ayudaTipo.textContent = t.ayuda;
    selBase.innerHTML = t.bases
      .map((b) => `<option value="${b.codigo}">${b.codigo} ${esc(b.nombre)}</option>`)
      .join("");
    if (base && t.bases.some((b) => b.codigo === base)) selBase.value = base;
    bloqueNif.hidden = t.nif === "no";
    nif.required = t.nif === "obligatorio";
    if (t.nif === "no") nif.value = "";
    nifMarca.textContent = t.nif === "obligatorio" ? "(obligatorio)" : "(opcional)";
    actualizarVista();
  }

  // Elige el tipo que contiene esa cuenta (o "otra") y la deja seleccionada
  function prepararAlta(base) {
    const t = tipos.find((x) => x.clave !== "otra" && x.bases.some((b) => b.codigo === base)) || porClave.otra;
    form.querySelector(`input[name="tipo"][value="${t.clave}"]`).checked = true;
    pintarTipo(t.clave, base);
    nombre.focus();
  }

  let peticion = 0;
  async function actualizarVista() {
    const mia = ++peticion;
    const base = selBase.value;
    if (!base) {
      vista.hidden = true;
      baseResumen.textContent = "";
      return;
    }
    try {
      const r = await fetch(`${form.dataset.siguienteUrl}?base=${encodeURIComponent(base)}`);
      const d = await r.json();
      if (mia !== peticion) return;
      if (!r.ok) throw new Error(d.error);
      baseResumen.textContent = d.resumen;
      codigo.placeholder = d.codigo;
      vista.innerHTML = `Se creará <strong>${esc(codigo.value.trim() || d.codigo)}</strong>` +
        (d.ubicacion ? `<span>${esc(d.ubicacion)}</span>` : "");
      vista.hidden = false;
    } catch (e) {
      baseResumen.textContent = e.message;
      vista.hidden = true;
    }
  }

  form.addEventListener("change", (ev) => {
    if (ev.target.name === "tipo") pintarTipo(ev.target.value);
  });
  selBase.addEventListener("change", actualizarVista);
  codigo.addEventListener("input", actualizarVista);

  // ---------- ficha de cuenta ----------
  const urlFicha = (c) => form.dataset.fichaUrl.replace("/0/", `/${encodeURIComponent(c)}/`);

  async function abrirFicha(cod) {
    try {
      const r = await fetch(urlFicha(cod));
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || `Error ${r.status}`);
      const saldo = Number(d.saldo);
      fichaContenido.innerHTML = `
        ${d.ruta.length ? `<nav class="miga" aria-label="Cuentas superiores">${d.ruta
          .map((a) => `<button type="button" class="enlace-ficha" data-ficha="${a.codigo}" title="${esc(a.nombre)}">${a.codigo}</button>`)
          .join(" › ")}</nav>` : ""}
        <h2 id="ficha-titulo"><span class="codigo">${d.codigo}</span> ${esc(d.nombre)}</h2>
        <p class="ficha-meta">${esc(d.nivel)}${d.imputable ? ", admite apuntes" : ", agrupa otras cuentas"}${d.nif ? `, NIF ${esc(d.nif)}` : ""}</p>
        ${d.ubicacion ? `<p class="ficha-ubicacion">${esc(d.ubicacion)}</p>` : ""}
        <dl class="ficha-cifras">
          <div><dt>Debe</dt><dd>${eur.format(Number(d.debe))}</dd></div>
          <div><dt>Haber</dt><dd>${eur.format(Number(d.haber))}</dd></div>
          <div><dt>Saldo</dt><dd>${saldo ? `${eur.format(Math.abs(saldo))} ${saldo > 0 ? "deudor" : "acreedor"}` : "0,00"}</dd></div>
        </dl>
        ${d.definicion ? `
          <h3>Definición y movimientos${d.definicion_de ? ` <small>(de la cuenta ${d.definicion_de})</small>` : ""}</h3>
          <div class="ficha-texto">${esc(d.definicion)}</div>` : ""}
        ${d.hijas.length ? `
          <h3>Contiene ${d.hijas.length} cuenta${d.hijas.length > 1 ? "s" : ""}</h3>
          <ul class="ficha-hijas">${d.hijas.map((h) => `
            <li><button type="button" class="enlace-ficha" data-ficha="${h.codigo}">
              <span class="codigo">${h.codigo}</span> ${esc(h.nombre)}${h.nif ? ` <small class="nif">${esc(h.nif)}</small>` : ""}
            </button></li>`).join("")}</ul>` : ""}
        ${d.admite_subcuentas ? `<button type="button" class="btn-primario" data-alta="${d.codigo}">Dar de alta una subcuenta aquí</button>` : ""}`;
      if (!ficha.open) ficha.showModal();
      fichaContenido.scrollTop = 0;
    } catch (e) {
      alert(e.message);
    }
  }

  fichaContenido.addEventListener("click", (ev) => {
    const enlace = ev.target.closest("[data-ficha]");
    const alta = ev.target.closest("[data-alta]");
    if (enlace) abrirFicha(enlace.dataset.ficha);
    if (alta) {
      ficha.close();
      prepararAlta(alta.dataset.alta);
    }
  });
  // cerrar al pulsar fuera de la ventana
  ficha.addEventListener("click", (ev) => {
    if (ev.target === ficha) ficha.close();
  });

  arbol.addEventListener("click", (ev) => {
    const alta = ev.target.closest("[data-base]");
    const enlace = ev.target.closest("[data-ficha]");
    if (alta) {
      ev.preventDefault();  // no plegar/desplegar la fila
      prepararAlta(alta.dataset.base);
    } else if (enlace) {
      ev.preventDefault();
      abrirFicha(enlace.dataset.ficha);
    }
  });

  // ---------- inicio ----------
  pintarTipo(tipoActual(), selBase.dataset.inicial);

  if (location.hash) {
    const el = document.getElementById(location.hash.slice(1));
    if (el) {
      abrirAncestros(el);
      el.scrollIntoView({ block: "center" });
      el.classList.add("resaltada");
    }
  }
})();