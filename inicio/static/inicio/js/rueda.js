/*
 * Página de inicio: rueda de las ramas del PMS y pantalla de cada rama.
 *
 * Rueda
 * - Las ramas se reparten sobre una elipse inclinada (círculo en móvil). Un «asa» invisible
 *   recibe el arrastre (GSAP Draggable) y la rueda se recoloca según `rotor.giro`.
 * - Engranaje pesado: el arrastre ofrece resistencia (nunca pasa de media posición) y al
 *   soltar avanza como mucho UNA rama, con un golpe al encajar. Clic o teclado: también
 *   de una en una, con su golpe en cada paso.
 * - El nombre de la rama de arriba aparece debajo de ella.
 *
 * Rama abierta (doble clic o Intro)
 * - El icono vuela a la esquina superior izquierda, la rueda se desvanece, cae el
 *   desplegable de secciones bajo el icono y a la derecha se abre la herramienta
 *   (la página de Django de la sección, en un iframe). La flecha del icono vuelve.
 * - La URL cambia a /m/<rama>/?seccion=n: recargar, compartir y el botón atrás funcionan.
 */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const escena = $("escena");
  const rueda = $("rueda");
  const asa = $("rueda-asa");
  const lineas = $("rueda-lineas");
  const nombre = $("rueda-nombre");
  const app = $("app");
  const appIcono = $("app-icono");
  const appCara = $("app-icono-cara");
  const appNombre = $("app-nombre");
  const appLista = $("app-secciones");
  const appDesplegar = $("app-desplegar");
  const appContenido = app.querySelector(".app-contenido");
  let marco = $("app-marco");   // se sustituye por uno nuevo al abrir o vaciar (ver cargarMarco)
  const vacio = $("app-vacio");
  const vacioTexto = $("app-vacio-texto");
  const volver = $("app-volver");
  const modulosEl = Array.from(rueda.querySelectorAll(".modulo"));
  const datos = JSON.parse($("modulos-datos").textContent);
  const URL_INICIO = volver.getAttribute("href");

  // Para probar temas: ?tema=azul o ?tema=abrat
  const tema = new URLSearchParams(location.search).get("tema");
  if (tema === "azul" || tema === "abrat") document.body.dataset.tema = tema;

  if (!window.gsap || !window.Draggable) return; // sin GSAP todo funciona con enlaces normales

  gsap.registerPlugin(Draggable);

  const n = modulosEl.length;
  const paso = 360 / n;
  const sinMovimiento = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const estrecha = window.matchMedia("(max-width: 700px)");
  const mod = (a, b) => ((a % b) + b) % b;
  const rad = (grados) => (grados * Math.PI) / 180;
  const dur = (s) => (sinMovimiento ? 0 : s);

  /** Color de fondo del tema con transparencia (para fundir sin pasar por gris). */
  function fondo(alfa) {
    const hex = getComputedStyle(document.body).getPropertyValue("--fondo").trim().replace("#", "");
    const [r, g2, b] = [0, 2, 4].map((k) => parseInt(hex.slice(k, k + 2), 16));
    return `rgba(${r}, ${g2}, ${b}, ${alfa})`;
  }

  // ------------------------------------------------------------- geometría
  const ovalo = {
    rx: +rueda.dataset.rx, ry: +rueda.dataset.ry, inclinacion: +rueda.dataset.inclinacion,
    ancho: +rueda.dataset.ancho, alto: +rueda.dataset.alto, img: +rueda.dataset.img,
  };
  const circulo = { rx: 72, ry: 72, inclinacion: 0, ancho: 200, alto: 200, img: 52 };
  let g = estrecha.matches ? circulo : ovalo;

  const SEPARACION = 6;   // distancia de cada línea a la elipse de las imágenes
  const MUESTRAS = 56;    // puntos por tramo de línea

  /** Punto de una elipse (rx, ry) inclinada, para un ángulo de fase (0° arriba, horario). */
  function punto(fase, rx, ry) {
    const t = rad(fase);
    const x = rx * Math.sin(t);
    const y = -ry * Math.cos(t);
    const a = rad(g.inclinacion);
    return [x * Math.cos(a) - y * Math.sin(a), x * Math.sin(a) + y * Math.cos(a)];
  }
  const aPorcentaje = ([x, y]) => [50 + (x / g.ancho) * 100, 50 + (y / g.alto) * 100];

  /** Un arco paralelo a la elipse entre dos ramas, sin pisar las imágenes. */
  function arco(desde, hasta, d, centroA, centroB) {
    const libre = g.img / 2 + 9;
    const pts = [];
    for (let k = 0; k <= MUESTRAS; k++) {
      const p = punto(desde + ((hasta - desde) * k) / MUESTRAS, g.rx + d, g.ry + d);
      if (Math.hypot(p[0] - centroA[0], p[1] - centroA[1]) > libre &&
          Math.hypot(p[0] - centroB[0], p[1] - centroB[1]) > libre) pts.push(p);
    }
    if (pts.length < 2) return "";
    return "M" + pts.map((p) => p[0].toFixed(2) + " " + p[1].toFixed(2)).join(" L");
  }

  // ----------------------------------------------------------------- rueda
  const rotor = { giro: parseFloat(rueda.dataset.giro) || 0 };
  const ajustar = (giro) => Math.round(giro / paso) * paso;      // posición encajada más cercana
  const indiceArriba = (giro) => mod(Math.round(-giro / paso), n);
  let arriba = -1;

  function colocar() {
    const fases = modulosEl.map((_, i) => i * paso + rotor.giro);
    const centros = fases.map((f) => punto(f, g.rx, g.ry));
    modulosEl.forEach((el, i) => {
      const [x, y] = aPorcentaje(centros[i]);
      el.style.left = x + "%";
      el.style.top = y + "%";
      el.style.zIndex = String(Math.round(centros[i][1] + 200)); // las de abajo, delante
    });
    let d = "";
    for (let i = 0; i < n; i++) {
      const j = (i + 1) % n;
      for (const s of [-SEPARACION, SEPARACION]) d += arco(fases[i], fases[i] + paso, s, centros[i], centros[j]) + " ";
    }
    lineas.innerHTML = '<path d="' + d.trim() + '"/>';

    const i = indiceArriba(rotor.giro);
    if (i !== arriba) {
      arriba = i;
      modulosEl.forEach((el, j) => el.classList.toggle("arriba", j === i));
      cambiarNombre(datos[i].nombre);
    }
  }

  function colocarNombre() {
    const [px, py] = punto(0, g.rx, g.ry);                     // centro de la rama de arriba
    const [x, y] = aPorcentaje([px, py + g.img / 2 + 9]);      // justo debajo de su imagen
    const c = Math.max(10, Math.min(90, x));
    nombre.style.left = c < 50 ? "0" : 2 * c - 100 + "%";     // centrado en c sin translate
    nombre.style.right = c < 50 ? 100 - 2 * c + "%" : "0";
    nombre.style.top = y + "%";
  }

  let primerNombre = true;
  function cambiarNombre(texto) {
    if (sinMovimiento || primerNombre) { nombre.textContent = texto; primerNombre = false; return; }
    gsap.timeline()
      .to(nombre, { opacity: 0, y: -4, duration: 0.12, ease: "power1.in" })
      .add(() => { nombre.textContent = texto; })
      .fromTo(nombre, { opacity: 0, y: 4 }, { opacity: 1, y: 0, duration: 0.22, ease: "power2.out" });
  }

  /** Golpe al encajar: la rueda da un pequeño tirón en sentido contrario y se asienta. */
  function golpe(dir) {
    if (sinMovimiento || !dir) return;
    gsap.fromTo(rueda, { x: -dir * 5, y: 2 }, { x: 0, y: 0, duration: 0.55, ease: "elastic.out(1.2, 0.3)" });
  }

  // Pasos pendientes (clic en una rama lejana o varias flechas): se dan de uno en uno.
  let pendientes = 0;
  let girando = false;
  let objetivo = rotor.giro;

  function siguientePaso() {
    if (pendientes === 0) { girando = false; return; }
    girando = true;
    const dir = Math.sign(pendientes);
    pendientes -= dir;
    objetivo = ajustar(rotor.giro) + dir * paso;
    // back.inOut: coge impulso hacia atrás, arranca pesado y se pasa un poco al encajar.
    gsap.to(rotor, {
      giro: objetivo, duration: dur(0.95), ease: "back.inOut(1.4)", onUpdate: colocar,
      onComplete() { colocar(); golpe(dir); gsap.delayedCall(dur(0.12), siguientePaso); },
    });
  }

  /** Gira `pasos` posiciones (positivo = horario), una a una. */
  function girar(pasos) {
    if (abierta !== null) return;
    pendientes = Math.max(-2, Math.min(2, pendientes + pasos));
    if (!girando) siguientePaso();
  }

  /** Lleva la rama i arriba por el camino más corto. */
  function traerArriba(i) {
    const base = girando ? objetivo : ajustar(rotor.giro);
    let pasos = Math.round((-i * paso - base) / paso);
    pasos = mod(pasos + n / 2, n) - n / 2;           // entre -n/2 y n/2
    pendientes = pasos;
    if (!girando) siguientePaso();
  }

  // Arrastre con resistencia: como mucho media posición mientras se arrastra.
  let baseGiro = 0;
  let inicioGiro = 0;
  let baseAsa = 0;
  let ultimoArrastre = 0;

  const [arrastre] = Draggable.create(asa, {
    type: "rotation",
    trigger: rueda,
    dragClickables: false,
    clickableTest: (el) => !!el.closest(".copilot"),   // el chat no arrastra la rueda
    minimumMovement: 6,
    onDragStart() {
      gsap.killTweensOf(rotor);
      pendientes = 0;
      girando = false;
      inicioGiro = rotor.giro;
      baseGiro = ajustar(rotor.giro);
      baseAsa = this.rotation;
      rueda.classList.add("arrastrando");
    },
    onDrag() {
      const delta = this.rotation - baseAsa;
      // tanh satura: cuanto más se tira, menos avanza. Engranaje pesado.
      rotor.giro = inicioGiro + paso * 0.5 * Math.tanh(delta / (paso * 1.3));
      colocar();
    },
    onDragEnd() {
      ultimoArrastre = Date.now();
      rueda.classList.remove("arrastrando");
      const recorrido = rotor.giro - baseGiro;
      const dir = Math.abs(recorrido) > paso * 0.15 ? Math.sign(recorrido) : 0;
      objetivo = baseGiro + dir * paso;
      gsap.to(rotor, {
        giro: objetivo, duration: dur(dir ? 0.75 : 0.45), ease: dir ? "back.out(1.6)" : "power2.out",
        onUpdate: colocar, onComplete() { colocar(); golpe(dir); },
      });
      gsap.set(asa, { rotation: 0 });
      this.update();
    },
  });

  modulosEl.forEach((el, i) => {
    // Sin JS cada icono es un enlace a /m/<rama>/. Con JS se quita el href: Draggable deja
    // pasar el clic nativo del enlace y la página se recargaría en vez de animar.
    el.dataset.href = el.getAttribute("href");
    el.removeAttribute("href");
    el.setAttribute("role", "button");
    el.setAttribute("tabindex", "0");
    el.addEventListener("click", (e) => {
      e.preventDefault();
      if (e.detail > 1) return;                          // el segundo clic es del doble clic
      if (Date.now() - ultimoArrastre < 250) return;     // era el final de un arrastre
      traerArriba(i);
    });
    el.addEventListener("dblclick", (e) => { e.preventDefault(); abrir(i); });
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); abrir(i); }
    });
  });

  rueda.addEventListener("keydown", (e) => {
    if (e.target.closest(".copilot")) return;            // no robar teclas al campo de texto
    if (e.key === "ArrowRight" || e.key === "ArrowDown") { e.preventDefault(); girar(-1); }
    if (e.key === "ArrowLeft" || e.key === "ArrowUp") { e.preventDefault(); girar(1); }
    if (e.key === "Enter" && e.target === rueda) { e.preventDefault(); abrir(indiceArriba(rotor.giro)); }
  });

  function aplicarGeometria() {
    g = estrecha.matches ? circulo : ovalo;
    lineas.setAttribute("viewBox", `${-g.ancho / 2} ${-g.alto / 2} ${g.ancho} ${g.alto}`);
    colocarNombre();
    colocar();
  }
  estrecha.addEventListener("change", aplicarGeometria);

  // ------------------------------------------------------ rama abierta
  let abierta = null;   // índice de la rama abierta

  function urlRama(i, seccion) {
    return datos[i].url_app + (seccion ? "?seccion=" + seccion : "");
  }

  function rellenarApp(i, seccion) {
    const m = datos[i];
    app.dataset.clave = m.clave;
    appCara.innerHTML = modulosEl[i].querySelector(".modulo-cara").innerHTML;
    appNombre.textContent = m.nombre;
    marco.title = m.nombre;
    vacioTexto.textContent = m.descripcion;
    document.title = m.nombre + " · Gestión hotelera";

    appLista.innerHTML = "";
    let grupo = "";
    m.secciones.forEach((s, k) => {
      if (s.grupo && s.grupo !== grupo) {
        grupo = s.grupo;
        const cabecera = document.createElement("li");
        cabecera.className = "app-grupo";
        cabecera.textContent = grupo;
        appLista.appendChild(cabecera);
      }
      const li = document.createElement("li");
      const el = document.createElement(s.url ? "a" : "span");
      el.className = "app-seccion" + (s.url ? "" : " futura");
      el.innerHTML = "<strong></strong>";
      el.querySelector("strong").textContent = s.nombre;
      const nota = s.url ? s.descripcion : "Próximamente";
      if (nota) {
        const small = document.createElement("small");
        small.textContent = nota;
        el.appendChild(small);
      }
      if (s.url) {
        el.href = s.url;
        el.target = "app-marco";
        el.dataset.indice = k;
        el.addEventListener("click", (e) => { e.preventDefault(); seleccionar(k); });
      }
      li.appendChild(el);
      appLista.appendChild(li);
    });
    const disponibles = m.secciones.map((s, k) => (s.url ? k : -1)).filter((k) => k >= 0);
    seleccionar(disponibles.includes(seccion) ? seccion : m.inicial, true);
  }

  /**
   * Carga una URL en el marco sin añadir pasos al historial del navegador:
   * con una página ya cargada se usa location.replace(); si no, se pone un iframe nuevo
   * con el src ya puesto (la primera carga de un iframe recién insertado no cuenta).
   * Con url = null el marco se vacía.
   */
  function cargarMarco(url) {
    try {
      const actual = marco.contentWindow && marco.contentWindow.location.href;
      if (url && actual && actual !== "about:blank") {
        marco.contentWindow.location.replace(url);
        return;
      }
    } catch (err) { /* sin acceso: se recrea */ }
    const nuevo = marco.cloneNode(false);
    if (url) nuevo.setAttribute("src", url); else nuevo.removeAttribute("src");
    nuevo.addEventListener("load", alCargarMarco);
    marco.replaceWith(nuevo);
    marco = nuevo;
  }

  function rutaMarco() {
    try {
      const loc = marco.contentWindow.location;
      if (loc.href !== "about:blank") return loc.pathname;
    } catch (err) { /* sin acceso */ }
    return marco.getAttribute("src");    // aún cargando: la que se pidió
  }

  function marcarActiva(k) {
    appLista.querySelectorAll(".app-seccion").forEach((el) =>
      el.classList.toggle("activa", el.dataset.indice !== undefined && +el.dataset.indice === k));
  }

  function seleccionar(k, inicial) {
    const secciones = datos[abierta].secciones;
    marcarActiva(k);
    if (k === null) {
      marco.hidden = true;
      cargarMarco(null);
      vacio.hidden = false;
      return;
    }
    vacio.hidden = true;
    marco.hidden = false;
    const url = secciones[k].url;
    if (rutaMarco() !== url) {
      if (!inicial && !sinMovimiento) gsap.fromTo(marco, { opacity: 0.3 }, { opacity: 1, duration: 0.4 });
      cargarMarco(url);   // sin pasos de más en el botón «atrás» del navegador
    }
    if (!inicial) history.replaceState({ modulo: datos[abierta].clave }, "", urlRama(abierta, k));
    if (estrecha.matches && !inicial) desplegar(false);
  }

  /** Abre la rama i: el icono sube a su sitio y aparece la pantalla de la herramienta. */
  function abrir(i, opciones = {}) {
    if (abierta !== null) return;
    const animar = opciones.animar !== false;
    const caraRueda = modulosEl[i].querySelector(".modulo-cara");
    const r0 = caraRueda.getBoundingClientRect();

    // Al volver, la rama abierta estará arriba.
    gsap.killTweensOf(rotor);
    pendientes = 0;
    girando = false;
    rotor.giro = -i * paso + Math.round((rotor.giro + i * paso) / 360) * 360;

    abierta = i;
    rellenarApp(i, opciones.seccion);
    desplegar(true, true);
    if (opciones.historial !== false) {
      history.pushState({ modulo: datos[i].clave }, "", urlRama(i, opciones.seccion));
    }
    app.hidden = false;

    if (!animar || sinMovimiento) {
      document.body.classList.add("con-app");
      colocar();
      return;
    }

    const r1 = appIcono.getBoundingClientRect();
    caraRueda.style.visibility = "hidden";
    app.classList.add("en-transicion");
    gsap.timeline({
      onComplete() {
        document.body.classList.add("con-app");
        app.classList.remove("en-transicion");
        gsap.set(escena, { clearProps: "opacity,scale" });
        caraRueda.style.visibility = "";
        colocar();
      },
    })
      .to(escena, { opacity: 0, scale: 0.94, duration: 0.45, ease: "power2.in" }, 0)
      .fromTo(appIcono,
        { x: r0.left - r1.left, y: r0.top - r1.top, scale: r0.width / r1.width, transformOrigin: "0 0" },
        { x: 0, y: 0, scale: 1, duration: 0.8, ease: "power3.inOut" }, 0)
      .fromTo(app, { backgroundColor: fondo(0) },
        { backgroundColor: fondo(1), duration: 0.5, clearProps: "backgroundColor" }, 0.15)
      .from(appNombre, { opacity: 0, x: -12, duration: 0.35 }, 0.55)
      .from(appDesplegar, { opacity: 0, y: -10, duration: 0.3 }, 0.65)
      .from(appLista.children, { opacity: 0, y: -16, duration: 0.35, stagger: 0.06, ease: "power2.out" }, 0.7)
      .from(appContenido, { opacity: 0, y: 28, duration: 0.5, ease: "power2.out" }, 0.55)
      .fromTo(volver, { scale: 0, rotation: -90 }, { scale: 1, rotation: 0, duration: 0.5, ease: "back.out(2.6)" }, 0.8);
  }

  /** Vuelve a la rueda: el icono baja a su sitio en lo alto de la rueda. */
  function cerrar(opciones = {}) {
    if (abierta === null) return;
    const i = abierta;
    abierta = null;
    if (opciones.historial !== false) history.pushState({ modulo: null }, "", URL_INICIO);
    document.title = "Gestión hotelera";

    const terminar = () => {
      app.hidden = true;
      app.classList.remove("en-transicion");
      gsap.set([appIcono, appNombre, appDesplegar, appContenido, appLista, volver, escena, app], { clearProps: "all" });
      gsap.set(appLista.children, { clearProps: "all" });
      modulosEl[i].querySelector(".modulo-cara").style.visibility = "";
      cargarMarco(null);
      rueda.focus({ preventScroll: true });
    };

    document.body.classList.remove("con-app");
    colocar();
    if (sinMovimiento) { terminar(); return; }

    const caraRueda = modulosEl[i].querySelector(".modulo-cara");
    gsap.set(escena, { opacity: 0, scale: 0.94 });
    const r0 = caraRueda.getBoundingClientRect();
    const r1 = appIcono.getBoundingClientRect();
    caraRueda.style.visibility = "hidden";
    app.classList.add("en-transicion");

    gsap.timeline({ onComplete: terminar })
      .to([appContenido, appLista, appDesplegar, appNombre], { opacity: 0, y: 12, duration: 0.25, ease: "power1.in" }, 0)
      .to(volver, { scale: 0, duration: 0.2 }, 0)
      .fromTo(app, { backgroundColor: fondo(1) }, { backgroundColor: fondo(0), duration: 0.4 }, 0.1)
      .to(appIcono, {
        x: r0.left - r1.left, y: r0.top - r1.top, scale: r0.width / r1.width, transformOrigin: "0 0",
        duration: 0.75, ease: "power3.inOut",
      }, 0.1)
      .to(escena, { opacity: 1, scale: 1, duration: 0.55, ease: "power2.out" }, 0.3);
  }

  volver.addEventListener("click", (e) => { e.preventDefault(); cerrar(); });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && abierta !== null) cerrar();
  });

  window.addEventListener("popstate", (e) => {
    const clave = e.state && e.state.modulo;
    const i = clave ? datos.findIndex((m) => m.clave === clave) : -1;
    if (i >= 0) {
      const seccion = parseInt(new URLSearchParams(location.search).get("seccion"), 10);
      if (abierta === null) abrir(i, { historial: false, seccion: Number.isNaN(seccion) ? undefined : seccion });
    } else {
      cerrar({ historial: false });
    }
  });

  // Desplegable de secciones bajo el icono
  function desplegar(abrirLista, instantaneo) {
    appDesplegar.setAttribute("aria-expanded", String(abrirLista));
    gsap.killTweensOf(appLista);
    if (instantaneo || sinMovimiento) {
      gsap.set(appLista, abrirLista ? { clearProps: "height,opacity,visibility" } : { height: 0, autoAlpha: 0 });
      return;
    }
    if (abrirLista) {
      gsap.fromTo(appLista, { height: 0, autoAlpha: 0 }, { height: "auto", autoAlpha: 1, duration: 0.35, ease: "power2.out",
        clearProps: "height" });
      gsap.from(appLista.children, { y: -10, opacity: 0, duration: 0.3, stagger: 0.05 });
    } else {
      gsap.to(appLista, { height: 0, autoAlpha: 0, duration: 0.3, ease: "power2.in" });
    }
  }
  appDesplegar.addEventListener("click", () =>
    desplegar(appDesplegar.getAttribute("aria-expanded") !== "true"));

  // Las páginas dentro del marco: sin su cabecera «← Cuentas»; si navegan al inicio, se cierra.
  function alCargarMarco() {
    try {
      const ruta = marco.contentWindow.location.pathname;
      if (ruta === URL_INICIO || ruta.startsWith(URL_INICIO + "m/")) {
        cerrar();
        return;
      }
      const doc = marco.contentDocument;
      if (doc && !doc.getElementById("inicio-embebido")) {
        const estilo = doc.createElement("style");
        estilo.id = "inicio-embebido";
        // Dentro del inicio sobran la cabecera «← Cuentas» y los menús laterales propios:
        // las secciones ya están en el desplegable de la izquierda.
        estilo.textContent = "header.top,.nav-lateral,a[href='/']{display:none!important}" +
          "body:has(> .nav-lateral){padding-left:2rem!important}";
        doc.head.appendChild(estilo);
      }
      // Si se navega dentro de la pantalla a otra sección, el desplegable la marca.
      if (abierta !== null) {
        const k = datos[abierta].secciones.findIndex((s) => s.url === ruta);
        if (k >= 0) {
          marcarActiva(k);
          history.replaceState({ modulo: datos[abierta].clave }, "", urlRama(abierta, k));
        }
      }
    } catch (err) { /* otra procedencia: se deja tal cual */ }
  }
  marco.addEventListener("load", alCargarMarco);

  // ---------------------------------------------------------------- inicio
  aplicarGeometria();
  if (app.dataset.clave) {
    // La página llegó con una rama ya abierta (/m/<rama>/): se enlaza sin animación.
    const i = datos.findIndex((m) => m.clave === app.dataset.clave);
    const seccion = parseInt(new URLSearchParams(location.search).get("seccion"), 10);
    if (i >= 0) {
      app.hidden = true;
      abrir(i, { animar: false, historial: false, seccion: Number.isNaN(seccion) ? undefined : seccion });
      history.replaceState({ modulo: datos[i].clave }, "", location.href);
    }
  } else {
    history.replaceState({ modulo: null }, "", location.href);
    if (!sinMovimiento) {
      gsap.from(modulosEl, { opacity: 0, y: 14, duration: 0.6, stagger: 0.09, ease: "power2.out" });
      gsap.from(lineas, { opacity: 0, duration: 0.8, delay: 0.25 });
      gsap.from(".copilot", { opacity: 0, scale: 0.94, duration: 0.5, delay: 0.35, ease: "back.out(2)" });
    }
  }
})();
