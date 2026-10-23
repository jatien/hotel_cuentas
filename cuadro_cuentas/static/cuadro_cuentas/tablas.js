/* Comportamiento común de las tablas de datos (diario, mayor, balances, buscador).

   <table class="tabla-datos" data-tabla="clave" [data-agrupada]>  con <th data-col="x" data-tipo="num|fecha|texto">
   - ordenar al pulsar la cabecera (no en tablas agrupadas por asiento)
   - filtro por columna y búsqueda general (sobre las filas cargadas)
   - selección de filas (clic, Ctrl/Cmd + clic, Mayús + clic) y navegación con el teclado:
     ↑ ↓ moverse · Espacio seleccionar · Intro abrir · Ctrl+A todo · Ctrl+C copiar filas
   - mostrar u ocultar columnas y cambiar su ancho; se guarda por usuario
   - punteo de apuntes (botones .punteo y acción sobre la selección)
   Nada de esto cambia los datos ni los totales, que calcula el servidor con todo el filtro. */
(() => {
  const cfg = window.CONTA || {};
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content;
  const numero = (t) => {
    const s = String(t).replace(/[^\d,().-]/g, "");
    if (!s) return null;
    const neg = /^\(.*\)$/.test(s) || s.startsWith("-");
    const n = Number(s.replace(/[()-]/g, "").replace(/\./g, "").replace(",", "."));
    return Number.isNaN(n) ? null : neg ? -n : n;
  };
  const fecha = (t) => {
    const m = String(t).match(/(\d{2})\/(\d{2})\/(\d{4})/);
    return m ? `${m[3]}${m[2]}${m[1]}` : "";
  };
  const textoCelda = (td) => (td?.dataset.valor ?? td?.innerText ?? "").trim();
  const filasDatos = (tabla) => [...tabla.tBodies[0]?.rows ?? []].filter((r) => !r.classList.contains("cab-asiento") && !r.classList.contains("sin-datos"));

  // ---------- preferencias (servidor si hay sesión; si no, este navegador) ----------
  async function leerPref(clave) {
    try {
      const r = await fetch(cfg.preferenciasUrl.replace("TABLA", clave));
      const j = await r.json();
      if (j.servidor) return j.columnas || {};
    } catch (e) { /* sin red: navegador */ }
    try { return JSON.parse(localStorage.getItem(`tabla:${clave}`) || "{}"); } catch (e) { return {}; }
  }
  function guardarPref(clave, pref) {
    try { localStorage.setItem(`tabla:${clave}`, JSON.stringify(pref)); } catch (e) { /* modo privado */ }
    fetch(cfg.preferenciasUrl.replace("TABLA", clave), {
      method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: JSON.stringify(pref),
    }).catch(() => {});
  }

  function aplicarColumnas(tabla, ocultas) {
    const ths = [...tabla.tHead.rows[0].cells];
    ths.forEach((th, i) => {
      const oculta = ocultas.includes(th.dataset.col);
      [...tabla.rows].forEach((r) => {
        if (r.cells.length === ths.length) r.cells[i]?.classList.toggle("oculta", oculta);
      });
    });
  }

  async function preparar(tabla) {
    const clave = tabla.dataset.tabla;
    const cab = tabla.tHead.rows[0];
    const ths = [...cab.cells];
    const pref = await leerPref(clave);
    pref.ocultas = pref.ocultas || [];
    pref.anchos = pref.anchos || {};
    const agrupada = tabla.hasAttribute("data-agrupada");

    // barra: búsqueda, columnas, selección
    const barra = document.createElement("div");
    barra.className = "barra-tabla";
    barra.innerHTML = `
      <input type="search" class="buscar-tabla" placeholder="Buscar en la tabla" aria-label="Buscar en la tabla">
      <span class="seleccion" aria-live="polite"></span>
      ${tabla.dataset.punteo !== undefined ? `<select class="accion-punteo" aria-label="Puntear la selección">
        <option value="">Puntear selección…</option><option value="punteado">Punteado</option>
        <option value="revisar">Pendiente de revisión</option><option value="discrepancia">Discrepancia</option>
        <option value="__sin">Quitar punteo</option></select>` : ""}
      <button type="button" class="btn-sec copiar" title="Ctrl+C">Copiar filas</button>
      <details class="menu-columnas"><summary class="btn-sec">Columnas</summary>
        <div class="panel-columnas">${ths.filter((th) => th.dataset.col).map((th) => `
          <label><input type="checkbox" value="${th.dataset.col}" ${pref.ocultas.includes(th.dataset.col) ? "" : "checked"}> ${th.innerText.trim() || th.dataset.col}</label>`).join("")}
          <button type="button" class="btn-sec restablecer">Restablecer</button>
        </div></details>`;
    tabla.closest(".desplaza")?.before(barra) ?? tabla.before(barra);

    // filtros por columna
    const filtros = document.createElement("tr");
    filtros.className = "filtro-col";
    filtros.innerHTML = ths.map((th) => `<th>${th.dataset.col && th.dataset.filtro !== "no"
      ? `<input aria-label="Filtrar ${th.innerText.trim()}" data-col="${th.dataset.col}">` : ""}</th>`).join("");
    tabla.tHead.append(filtros);

    // anchos y redimensionado
    ths.forEach((th) => {
      if (!th.dataset.col) return;
      if (pref.anchos[th.dataset.col]) th.style.width = pref.anchos[th.dataset.col];
      const asa = document.createElement("span");
      asa.className = "asa-ancho";
      th.append(asa);
      asa.addEventListener("pointerdown", (ev) => {
        ev.preventDefault();
        const x0 = ev.clientX, w0 = th.offsetWidth;
        const mover = (e) => { th.style.width = `${Math.max(40, w0 + e.clientX - x0)}px`; };
        const soltar = () => {
          removeEventListener("pointermove", mover);
          removeEventListener("pointerup", soltar);
          pref.anchos[th.dataset.col] = th.style.width;
          guardarPref(clave, pref);
        };
        addEventListener("pointermove", mover);
        addEventListener("pointerup", soltar);
      });
    });
    aplicarColumnas(tabla, pref.ocultas);

    barra.querySelector(".panel-columnas").addEventListener("change", () => {
      pref.ocultas = [...barra.querySelectorAll(".panel-columnas input:not(:checked)")].map((i) => i.value);
      aplicarColumnas(tabla, pref.ocultas);
      guardarPref(clave, pref);
    });
    barra.querySelector(".restablecer").addEventListener("click", () => {
      pref.ocultas = []; pref.anchos = {};
      ths.forEach((th) => (th.style.width = ""));
      barra.querySelectorAll(".panel-columnas input").forEach((i) => (i.checked = true));
      aplicarColumnas(tabla, []);
      guardarPref(clave, pref);
    });

    // ordenar
    if (!agrupada) {
      ths.forEach((th, i) => {
        if (!th.dataset.col) return;
        th.classList.add("ordenable");
        th.addEventListener("click", (ev) => {
          if (ev.target.classList.contains("asa-ancho")) return;
          const asc = th.getAttribute("aria-sort") !== "ascending";
          ths.forEach((x) => x.removeAttribute("aria-sort"));
          th.setAttribute("aria-sort", asc ? "ascending" : "descending");
          const tipo = th.dataset.tipo || "texto";
          const valor = (r) => {
            const t = textoCelda(r.cells[i]);
            return tipo === "num" ? numero(t) ?? -Infinity : tipo === "fecha" ? fecha(t) : t.toLocaleLowerCase("es");
          };
          const filas = filasDatos(tabla);
          filas.sort((a, b) => (valor(a) > valor(b) ? 1 : valor(a) < valor(b) ? -1 : 0) * (asc ? 1 : -1));
          tabla.tBodies[0].append(...filas);   // las filas fijas (saldo inicial) se quedan arriba
        });
      });
    }

    // filtrar (búsqueda general + columnas)
    const buscar = barra.querySelector(".buscar-tabla");
    const filtrar = () => {
      const general = buscar.value.trim().toLocaleLowerCase("es");
      const porCol = [...filtros.querySelectorAll("input")].filter((x) => x.value.trim())
        .map((x) => [ths.findIndex((th) => th.dataset.col === x.dataset.col), x.value.trim().toLocaleLowerCase("es")]);
      filasDatos(tabla).forEach((r) => {
        const ok = (!general || r.innerText.toLocaleLowerCase("es").includes(general))
          && porCol.every(([i, v]) => textoCelda(r.cells[i]).toLocaleLowerCase("es").includes(v));
        r.hidden = !ok;
      });
      if (agrupada) {
        [...tabla.tBodies[0].rows].filter((r) => r.classList.contains("cab-asiento")).forEach((cabRow) => {
          let r = cabRow.nextElementSibling, alguna = false;
          while (r && !r.classList.contains("cab-asiento")) { alguna = alguna || !r.hidden; r = r.nextElementSibling; }
          cabRow.hidden = !alguna;
        });
      }
    };
    buscar.addEventListener("input", filtrar);
    filtros.addEventListener("input", filtrar);

    // selección y teclado
    let ancla = null;
    const seleccionadas = () => filasDatos(tabla).filter((r) => r.classList.contains("seleccionada") && !r.hidden);
    const pintarSeleccion = () => {
      const n = seleccionadas().length;
      barra.querySelector(".seleccion").textContent = n ? `${n} fila${n > 1 ? "s" : ""} seleccionada${n > 1 ? "s" : ""}` : "";
    };
    filasDatos(tabla).forEach((r) => (r.tabIndex = 0));
    tabla.tBodies[0]?.addEventListener("click", (ev) => {
      if (ev.target.closest("a, button, input, select")) return;
      const fila = ev.target.closest("tr");
      if (!fila || !filasDatos(tabla).includes(fila)) return;
      const visibles = filasDatos(tabla).filter((r) => !r.hidden);
      if (ev.shiftKey && ancla) {
        const [a, b] = [visibles.indexOf(ancla), visibles.indexOf(fila)].sort((x, y) => x - y);
        visibles.forEach((r, i) => r.classList.toggle("seleccionada", i >= a && i <= b));
      } else if (ev.ctrlKey || ev.metaKey) {
        fila.classList.toggle("seleccionada");
        ancla = fila;
      } else {
        visibles.forEach((r) => r.classList.toggle("seleccionada", r === fila && !fila.classList.contains("seleccionada")));
        ancla = fila;
      }
      pintarSeleccion();
    });
    tabla.addEventListener("keydown", (ev) => {
      const fila = ev.target.closest?.("tbody tr");
      if (!fila || ev.target !== fila) return;
      const visibles = filasDatos(tabla).filter((r) => !r.hidden);
      const i = visibles.indexOf(fila);
      if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
        ev.preventDefault();
        const otra = visibles[i + (ev.key === "ArrowDown" ? 1 : -1)];
        if (otra) {
          otra.focus();
          if (ev.shiftKey) { otra.classList.add("seleccionada"); fila.classList.add("seleccionada"); pintarSeleccion(); }
        }
      } else if (ev.key === " ") {
        ev.preventDefault();
        fila.classList.toggle("seleccionada");
        ancla = fila;
        pintarSeleccion();
      } else if (ev.key === "Enter") {
        fila.querySelector("a[href]")?.click();
      } else if ((ev.ctrlKey || ev.metaKey) && ev.key === "a") {
        ev.preventDefault();
        visibles.forEach((r) => r.classList.add("seleccionada"));
        pintarSeleccion();
      } else if ((ev.ctrlKey || ev.metaKey) && ev.key === "c") {
        ev.preventDefault();
        copiar();
      }
    });

    // copiar filas como texto tabulado (se pega en Excel)
    const copiar = async () => {
      const filas = seleccionadas().length ? seleccionadas() : filasDatos(tabla).filter((r) => !r.hidden);
      const visiblesCols = ths.map((th, i) => [th, i]).filter(([th]) => th.dataset.col && !pref.ocultas.includes(th.dataset.col));
      const lineas = [visiblesCols.map(([th]) => th.innerText.trim()).join("\t")]
        .concat(filas.map((r) => visiblesCols.map(([, i]) => textoCelda(r.cells[i]).replace(/\s+/g, " ")).join("\t")));
      try {
        await navigator.clipboard.writeText(lineas.join("\n"));
        barra.querySelector(".seleccion").textContent = `${filas.length} filas copiadas`;
      } catch (e) {
        barra.querySelector(".seleccion").textContent = "No se pudo copiar";
      }
    };
    barra.querySelector(".copiar").addEventListener("click", copiar);

    // punteo de la selección
    barra.querySelector(".accion-punteo")?.addEventListener("change", async (ev) => {
      const estado = ev.target.value === "__sin" ? "" : ev.target.value;
      ev.target.value = "";
      const botones = seleccionadas().map((r) => r.querySelector(".punteo[data-id]")).filter((b) => b && !b.disabled);
      if (!botones.length) { barra.querySelector(".seleccion").textContent = "Selecciona antes los apuntes"; return; }
      await enviarPunteo(botones, estado);
    });
  }

  // ---------- punteo ----------
  const CICLO = ["", "punteado", "revisar", "discrepancia"];
  const ETIQUETA = { "": "·", punteado: "✓", revisar: "?", discrepancia: "!", conciliado: "≡" };
  const TITULO = { "": "Sin puntear", punteado: "Punteado", revisar: "Pendiente de revisión",
    discrepancia: "Discrepancia", conciliado: "Conciliado con el banco" };

  function pintarPunteo(b, estado) {
    b.dataset.punteo = estado;
    b.textContent = ETIQUETA[estado] ?? "·";
    b.title = `${TITULO[estado]} — pulsa para cambiar`;
    const td = b.closest("td");
    if (td) td.dataset.valor = TITULO[estado];
  }

  async function enviarPunteo(botones, estado) {
    try {
      const r = await fetch(cfg.punteoUrl, {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
        body: JSON.stringify({ ids: botones.map((b) => Number(b.dataset.id)), estado }),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || `Error ${r.status}`);
      botones.forEach((b) => pintarPunteo(b, estado));
    } catch (e) {
      alertaBreve(e.message);
    }
  }

  function alertaBreve(texto) {
    let el = document.getElementById("aviso");
    if (!el) { el = document.createElement("div"); el.id = "aviso"; el.className = "aviso"; document.body.append(el); }
    el.textContent = texto; el.dataset.tipo = "error"; el.hidden = false;
    clearTimeout(alertaBreve.t);
    alertaBreve.t = setTimeout(() => (el.hidden = true), 4000);
  }

  document.querySelectorAll(".punteo[data-id]").forEach((b) => pintarPunteo(b, b.dataset.punteo || ""));
  document.addEventListener("click", (ev) => {
    const b = ev.target.closest(".punteo[data-id]");
    if (!b || b.disabled) return;
    const siguiente = CICLO[(CICLO.indexOf(b.dataset.punteo || "") + 1) % CICLO.length];
    enviarPunteo([b], siguiente);
  });

  document.querySelectorAll("table.tabla-datos[data-tabla]").forEach(preparar);
})();
