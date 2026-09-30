(() => {
  const cfg = window.LIBRO;
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const lista = document.getElementById("lista-asientos");
  const panel = document.getElementById("panel");
  const aviso = document.getElementById("aviso");
  const departamentos = JSON.parse(document.getElementById("datos-departamentos").textContent);
  const sinMovimiento = matchMedia("(prefers-reduced-motion: reduce)").matches;

  const asientos = new Map();
  let seleccionado = null;
  let peticion = 0;

  // ---------- utilidades ----------
  const eur = new Intl.NumberFormat("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const f = (v) => eur.format(Number(v));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const url = (plantilla, id, extra = "") => plantilla.replace("/0/", `/${id}/`) + extra;

  async function api(metodo, direccion, datos) {
    const r = await fetch(direccion, {
      method: metodo,
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
      body: datos ? JSON.stringify(datos) : undefined,
    });
    const json = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(json.error || `Error ${r.status}`);
    return json;
  }

  function avisar(texto, tipo = "error") {
    aviso.textContent = texto;
    aviso.dataset.tipo = tipo;
    aviso.hidden = false;
    clearTimeout(avisar.t);
    avisar.t = setTimeout(() => (aviso.hidden = true), 4000);
  }

  // ---------- pintado ----------
  function filaHTML(ap, bloq) {
    const d = Number(ap.debe), h = Number(ap.haber);
    const nuevo = ap.id === "nuevo";
    const opciones = ['<option value="">Sin dpto.</option>']
      .concat(departamentos.map(([v, n]) =>
        `<option value="${v}" ${v === ap.departamento ? "selected" : ""}>${esc(n)}</option>`))
      .join("");
    return `
      <li class="apunte" data-id="${ap.id}">
        <span class="asa" title="Arrastra para reordenar" aria-hidden="true">⠿</span>
        <input class="campo" data-campo="cuenta" list="lista-cuentas" value="${esc(ap.cuenta)}"
               title="${esc(ap.cuenta_nombre)}" placeholder="Cuenta" aria-label="Cuenta" ${bloq}>
        <input class="campo" data-campo="concepto" value="${esc(ap.concepto)}" aria-label="Concepto" ${bloq}>
        <input class="campo num importe ${d ? "lleno-debe" : ""}" data-campo="debe" inputmode="decimal"
               value="${d ? eur.format(d) : ""}" aria-label="Debe" ${bloq}>
        <input class="campo num importe ${h ? "lleno-haber" : ""}" data-campo="haber" inputmode="decimal"
               value="${h ? eur.format(h) : ""}" aria-label="Haber" ${bloq}>
        <span class="contrapartida" title="Contrapartida">${nuevo ? "" : esc(ap.contrapartida) || "varias"}</span>
        <select class="campo" data-campo="departamento" aria-label="Departamento" ${bloq}>${opciones}</select>
        <button class="btn-x" data-accion="borrar-linea" title="Eliminar línea" aria-label="Eliminar línea" ${bloq}>×</button>
      </li>`;
  }

  function tarjeta(a) {
    const bloq = a.cerrado ? "disabled" : "";
    const dif = Number(a.debe) - Number(a.haber);
    const el = document.createElement("article");
    el.className = "asiento";
    el.dataset.id = a.id;
    el.dataset.estado = a.estado;
    el.innerHTML = `
      <header class="asiento-cab">
        <span class="asiento-num" title="Número de asiento">${a.numero}</span>
        <input class="campo campo-fecha" type="date" data-campo="fecha" value="${a.fecha}" aria-label="Fecha" ${bloq}>
        <input class="campo campo-concepto" data-campo="concepto" value="${esc(a.concepto)}" aria-label="Concepto del asiento" ${bloq}>
        <span class="estado">${esc(a.estado_label)}</span>
      </header>
      <div class="columnas" aria-hidden="true">
        <span></span><span>Cuenta</span><span>Concepto</span><span class="num">Debe</span>
        <span class="num">Haber</span><span>Contrapartida</span><span>Departamento</span><span></span>
      </div>
      <ol class="apuntes">${a.apuntes.map((ap) => filaHTML(ap, bloq)).join("")}</ol>
      <footer class="asiento-pie">
        ${a.cerrado ? '<span class="estado">Ejercicio cerrado</span>' : `
          <button class="btn-sec" data-accion="nueva-linea">Añadir línea</button>
          <button class="btn-sec" data-accion="estado">${a.estado === "borrador" ? "Contabilizar" : "Pasar a borrador"}</button>
          ${a.estado === "borrador" ? '<button class="btn-sec btn-peligro" data-accion="borrar">Eliminar asiento</button>' : ""}`}
        <span class="totales">
          <span class="d">Debe ${f(a.debe)}</span>
          <span class="h">Haber ${f(a.haber)}</span>
          ${dif ? `<span class="descuadre">Descuadre ${eur.format(Math.abs(dif))}</span>` : ""}
        </span>
      </footer>`;

    if (!a.cerrado) {
      const ol = el.querySelector(".apuntes");
      Sortable.create(ol, {
        handle: ".asa",
        animation: sinMovimiento ? 0 : 180,
        onEnd: async (ev) => {
          if (ev.oldIndex === ev.newIndex) return;
          const ids = [...ol.children].map((li) => li.dataset.id).filter((x) => x !== "nuevo").map(Number);
          try {
            actualizar(await api("POST", url(cfg.asientoUrl, a.id, "orden/"), { ids }));
          } catch (e) {
            avisar(e.message);
            actualizar(asientos.get(a.id));
          }
        },
      });
    }
    return el;
  }

  // Sustituye la tarjeta conservando el foco del campo que el usuario estaba usando
  function actualizar(a, { nueva = false } = {}) {
    asientos.set(a.id, a);
    const viejo = lista.querySelector(`.asiento[data-id="${a.id}"]`);
    const nuevo = tarjeta(a);
    if (a.id === seleccionado) nuevo.classList.add("activo");
    if (nueva) nuevo.classList.add("recien");

    let foco = null;
    const activo = document.activeElement;
    if (viejo && viejo.contains(activo) && activo.dataset.campo) {
      foco = { fila: activo.closest(".apunte")?.dataset.id ?? null, campo: activo.dataset.campo };
    }

    if (viejo) viejo.replaceWith(nuevo);
    else {
      lista.querySelector(".vacio")?.remove();
      lista.append(nuevo);
    }

    if (foco) {
      const contenedor = foco.fila === null ? nuevo.querySelector(".asiento-cab")
        : foco.fila === "nuevo" ? nuevo.querySelector(".apunte:last-child")
        : nuevo.querySelector(`.apunte[data-id="${foco.fila}"]`);
      contenedor?.querySelector(`[data-campo="${foco.campo}"]`)?.focus();
    }
    if (a.id === seleccionado) cargarResumen(a.id);
  }

  // ---------- panel derecho ----------
  const textoSaldo = (v) => {
    const n = Number(v);
    return n ? `${eur.format(Math.abs(n))} ${n > 0 ? "deudor" : "acreedor"}` : "0,00";
  };
  const movimiento = (c) => [
    Number(c.mov_debe) ? `Debe ${f(c.mov_debe)}` : "",
    Number(c.mov_haber) ? `Haber ${f(c.mov_haber)}` : "",
  ].filter(Boolean).join(", ");

  function saldoHTML(c, escala) {
    const s = Number(c.saldo_tras);
    const ancho = Math.round((Math.abs(s) / escala) * 100);
    return `
      <li>
        <div class="saldo-cab"><strong>${esc(c.codigo)}</strong><span>${esc(c.nombre)}</span></div>
        <div class="barra-saldo" aria-hidden="true">
          <div class="izq"><span style="width:${s > 0 ? ancho : 0}%"></span></div>
          <div class="der"><span style="width:${s < 0 ? ancho : 0}%"></span></div>
        </div>
        <dl class="saldo-cifras">
          <dt>En este asiento</dt><dd>${movimiento(c)}</dd>
          <dt>Saldo tras el asiento</dt><dd>${textoSaldo(c.saldo_tras)}</dd>
          <dt>Saldo actual del ejercicio</dt><dd>${textoSaldo(c.saldo_ejercicio)}</dd>
        </dl>
      </li>`;
  }

  function pintarPanel({ asiento: a, cuentas }) {
    const dif = Number(a.debe) - Number(a.haber);
    const escala = Math.max(1, ...cuentas.map((c) => Math.abs(Number(c.saldo_tras))));
    const fecha = new Date(`${a.fecha}T00:00`).toLocaleDateString("es-ES",
      { weekday: "long", day: "numeric", month: "long", year: "numeric" });
    const cuadre = dif
      ? `El ${dif > 0 ? "Debe" : "Haber"} supera en ${eur.format(Math.abs(dif))}`
      : a.apuntes.length ? "El asiento cuadra" : "Sin líneas todavía";

    panel.innerHTML = `
      <h2>Asiento ${a.numero}</h2>
      <p class="panel-sub">${fecha}</p>
      <div class="balanza">
        <div class="bd">Debe<strong>${f(a.debe)}</strong></div>
        <div class="bh">Haber<strong>${f(a.haber)}</strong></div>
        <div class="cuadre ${dif ? "mal" : ""}">${cuadre}</div>
      </div>
      <dl class="datos">
        <dt>Estado</dt><dd>${esc(a.estado_label)}</dd>
        <dt>Origen</dt><dd>${esc(a.origen_label)}</dd>
        ${a.referencia_origen ? `<dt>Referencia</dt><dd>${esc(a.referencia_origen)}</dd>` : ""}
        <dt>Ejercicio</dt><dd>${a.ejercicio}${a.cerrado ? " (cerrado)" : ""}</dd>
        <dt>Líneas</dt><dd>${a.apuntes.length}</dd>
      </dl>
      <h3>Saldo de las cuentas</h3>
      ${cuentas.length
        ? `<ul class="saldos">${cuentas.map((c) => saldoHTML(c, escala)).join("")}</ul>`
        : '<p class="panel-vacio">Añade líneas para ver los saldos.</p>'}`;
  }

  async function cargarResumen(id) {
    const mia = ++peticion;  // descarta respuestas de selecciones anteriores
    try {
      const r = await api("GET", url(cfg.asientoUrl, id, "resumen/"));
      if (mia === peticion) pintarPanel(r);
    } catch (e) {
      if (mia === peticion) avisar(e.message);
    }
  }

  function seleccionar(id) {
    if (seleccionado === id) return;
    seleccionado = id;
    lista.querySelectorAll(".asiento.activo").forEach((el) => el.classList.remove("activo"));
    lista.querySelector(`.asiento[data-id="${id}"]`)?.classList.add("activo");
    cargarResumen(id);
  }

  // ---------- eventos ----------
  const leerFila = (fila) => Object.fromEntries(
    [...fila.querySelectorAll("[data-campo]")].map((c) => [c.dataset.campo, c.value.trim()]));

  lista.addEventListener("focusin", (ev) => {
    const card = ev.target.closest(".asiento");
    if (card) seleccionar(Number(card.dataset.id));
  });

  lista.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && ev.target.matches("input.campo")) {
      ev.preventDefault();
      ev.target.blur();  // dispara "change" y guarda
    }
  });

  lista.addEventListener("change", async (ev) => {
    const campo = ev.target.dataset.campo;
    if (!campo) return;
    const card = ev.target.closest(".asiento");
    const id = Number(card.dataset.id);
    const fila = ev.target.closest(".apunte");

    if (fila?.dataset.id === "nuevo") {
      const valores = leerFila(fila);
      if (!valores.cuenta || !(valores.debe || valores.haber)) return;  // aún incompleta
      try {
        actualizar(await api("POST", url(cfg.asientoUrl, id, "apuntes/"), valores));
      } catch (e) {
        fila.classList.add("invalida");
        avisar(e.message);
      }
      return;
    }

    try {
      const destino = fila ? url(cfg.apunteUrl, fila.dataset.id) : url(cfg.asientoUrl, id);
      actualizar(await api("PATCH", destino, { [campo]: ev.target.value }));
    } catch (e) {
      avisar(e.message);
      actualizar(asientos.get(id));  // vuelve al último estado guardado
    }
  });

  lista.addEventListener("click", async (ev) => {
    const card = ev.target.closest(".asiento");
    if (!card) return;
    const id = Number(card.dataset.id);
    seleccionar(id);
    const boton = ev.target.closest("[data-accion]");
    if (!boton) return;
    const a = asientos.get(id);

    try {
      switch (boton.dataset.accion) {
        case "nueva-linea": {
          const ol = card.querySelector(".apuntes");
          let borrador = ol.querySelector('[data-id="nuevo"]');
          if (!borrador) {
            ol.insertAdjacentHTML("beforeend", filaHTML({
              id: "nuevo", cuenta: "", cuenta_nombre: "", concepto: "",
              debe: 0, haber: 0, contrapartida: "", departamento: "",
            }, ""));
            borrador = ol.lastElementChild;
            borrador.classList.add("recien");
          }
          borrador.querySelector('[data-campo="cuenta"]').focus();
          break;
        }
        case "borrar-linea": {
          const fila = boton.closest(".apunte");
          if (fila.dataset.id === "nuevo") fila.remove();
          else actualizar(await api("DELETE", url(cfg.apunteUrl, fila.dataset.id)));
          break;
        }
        case "estado": {
          const destino = a.estado === "borrador" ? "contabilizado" : "borrador";
          actualizar(await api("POST", url(cfg.asientoUrl, id, "estado/"), { estado: destino }));
          avisar(destino === "contabilizado" ? "Asiento contabilizado" : "Asiento pasado a borrador", "ok");
          break;
        }
        case "borrar": {
          if (!confirm(`¿Eliminar el asiento ${a.numero}?`)) break;
          await api("DELETE", url(cfg.asientoUrl, id));
          card.remove();
          asientos.delete(id);
          seleccionado = null;
          panel.innerHTML = '<p class="panel-vacio">Selecciona un asiento para ver sus totales y el saldo de sus cuentas.</p>';
          break;
        }
      }
    } catch (e) {
      avisar(e.message);
    }
  });

  document.getElementById("nuevo-asiento").addEventListener("click", async () => {
    try {
      const hoy = new Date();
      const iso = new Date(hoy - hoy.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
      const a = await api("POST", cfg.nuevoUrl, { fecha: iso, concepto: "Nuevo asiento" });
      actualizar(a, { nueva: true });
      const card = lista.querySelector(`.asiento[data-id="${a.id}"]`);
      card.scrollIntoView({ behavior: sinMovimiento ? "auto" : "smooth", block: "center" });
      const concepto = card.querySelector('[data-campo="concepto"]');
      concepto.focus();
      concepto.select();
    } catch (e) {
      avisar(e.message);
    }
  });

  // ---------- carga inicial ----------
  const iniciales = JSON.parse(document.getElementById("datos-asientos").textContent);
  if (!iniciales.length) {
    lista.innerHTML = '<p class="vacio">No hay asientos en este periodo. Crea uno con «Nuevo asiento».</p>';
  }
  iniciales.forEach((a) => actualizar(a));
})();