/*
 * Rueda de la página de inicio.
 *
 * Las ramas se reparten sobre una elipse inclinada (como en el boceto). Al arrastrar,
 * un «asa» invisible gira (GSAP Draggable con inercia) y cada rama se recoloca sobre
 * la elipse según ese giro, así las imágenes siguen el óvalo y siempre van derechas.
 * Las líneas dobles entre ramas se redibujan siguiendo la elipse en cada fotograma.
 *
 * - Arrastrar: gira como un engranaje y al soltar encaja con una rama arriba.
 * - Clic en una imagen: la rueda gira hasta dejarla arriba.
 * - Flechas del teclado: giran una posición.
 * - El nombre de la rama de arriba aparece debajo de ella.
 */
(function () {
  "use strict";

  const rueda = document.getElementById("rueda");
  const asa = document.getElementById("rueda-asa");
  const lineas = document.getElementById("rueda-lineas");
  const nombre = document.getElementById("rueda-nombre");
  const modulosEl = Array.from(rueda.querySelectorAll(".modulo"));
  const datos = JSON.parse(document.getElementById("modulos-datos").textContent);

  if (!window.gsap || !window.Draggable) return; // sin GSAP la página sigue funcionando con enlaces

  gsap.registerPlugin(Draggable, InertiaPlugin);

  const n = modulosEl.length;
  const paso = 360 / n;
  const sinMovimiento = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const estrecha = window.matchMedia("(max-width: 600px)");

  // Geometría (unidades del viewBox). Óvalo inclinado en escritorio; círculo en móvil.
  const ovalo = {
    rx: +rueda.dataset.rx, ry: +rueda.dataset.ry, inclinacion: +rueda.dataset.inclinacion,
    ancho: +rueda.dataset.ancho, alto: +rueda.dataset.alto, img: 52,
  };
  const circulo = { rx: 72, ry: 72, inclinacion: 0, ancho: 200, alto: 200, img: 52 };
  let g = estrecha.matches ? circulo : ovalo;

  const SEPARACION = 6;   // distancia de cada línea a la elipse de las imágenes
  const MUESTRAS = 48;    // puntos por tramo de línea
  const mod = (a, b) => ((a % b) + b) % b;
  const rad = (grados) => (grados * Math.PI) / 180;

  let arriba = -1;
  let ultimoArrastre = 0;

  /** Punto de una elipse (rx, ry) inclinada, para un ángulo de fase (0° arriba, horario). */
  function punto(fase, rx, ry) {
    const t = rad(fase);
    const x = rx * Math.sin(t);
    const y = -ry * Math.cos(t);
    const a = rad(g.inclinacion);
    return [x * Math.cos(a) - y * Math.sin(a), x * Math.sin(a) + y * Math.cos(a)];
  }

  const aPorcentaje = ([x, y]) => [50 + (x / g.ancho) * 100, 50 + (y / g.alto) * 100];
  // Último giro válido: si un clic llega a mitad de una inercia, el valor podría no ser numérico.
  let ultimoGiro = 0;
  function giroActual() {
    const giro = parseFloat(gsap.getProperty(asa, "rotation"));
    if (Number.isFinite(giro)) { ultimoGiro = giro; return giro; }
    gsap.set(asa, { clearProps: "transform" });
    gsap.set(asa, { rotation: ultimoGiro });
    return ultimoGiro;
  }
  const indiceArriba = (giro) => mod(Math.round(-giro / paso), n);

  /** Un arco paralelo a la elipse entre dos ramas, sin pisar las imágenes. */
  function arco(desde, hasta, d, centroA, centroB) {
    const libre = g.img / 2 + 9;
    const pts = [];
    for (let k = 0; k <= MUESTRAS; k++) {
      const p = punto(desde + ((hasta - desde) * k) / MUESTRAS, g.rx + d, g.ry + d);
      const lejosA = Math.hypot(p[0] - centroA[0], p[1] - centroA[1]) > libre;
      const lejosB = Math.hypot(p[0] - centroB[0], p[1] - centroB[1]) > libre;
      if (lejosA && lejosB) pts.push(p);
    }
    if (pts.length < 2) return "";
    return "M" + pts.map((p) => p[0].toFixed(2) + " " + p[1].toFixed(2)).join(" L");
  }

  function colocar() {
    const giro = giroActual();
    const fases = modulosEl.map((_, i) => i * paso + giro);
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
      for (const s of [-SEPARACION, SEPARACION]) {
        d += arco(fases[i], fases[i] + paso, s, centros[i], centros[j]) + " ";
      }
    }
    lineas.innerHTML = '<path d="' + d.trim() + '"/>';

    const i = indiceArriba(giro);
    if (i !== arriba) {
      arriba = i;
      modulosEl.forEach((el, j) => el.classList.toggle("arriba", j === i));
      cambiarNombre(datos[i].nombre);
    }
  }

  /** Coloca el nombre bajo la posición de arriba de la elipse. */
  function colocarNombre() {
    const [px, py] = punto(0, g.rx, g.ry);                      // centro de la rama de arriba
    const [x, y] = aPorcentaje([px, py + g.img / 2 + 8]);       // justo debajo de su imagen
    const c = Math.max(10, Math.min(90, x));
    // Centrado en c sin translate: se recorta el lado contrario.
    nombre.style.left = c < 50 ? "0" : 2 * c - 100 + "%";
    nombre.style.right = c < 50 ? 100 - 2 * c + "%" : "0";
    nombre.style.top = y + "%";
  }

  function cambiarNombre(texto) {
    if (sinMovimiento) { nombre.textContent = texto; return; }
    gsap.timeline()
      .to(nombre, { opacity: 0, y: -4, duration: 0.12, ease: "power1.in" })
      .add(() => { nombre.textContent = texto; })
      .fromTo(nombre, { opacity: 0, y: 4 }, { opacity: 1, y: 0, duration: 0.2, ease: "power2.out" });
  }

  /**
   * Corta cualquier giro en curso (inercia incluida) y deja el asa en el último giro válido.
   * Si se pulsa durante la inercia, Draggable deja la rotación como NaN: aquí se repara.
   */
  function detener() {
    gsap.killTweensOf(asa);
    const giro = parseFloat(gsap.getProperty(asa, "rotation"));
    if (!Number.isFinite(giro)) {
      // La caché de GSAP queda en NaN: se borra la transformación y se vuelve a poner.
      gsap.set(asa, { clearProps: "transform" });
      gsap.set(asa, { rotation: ultimoGiro });
    }
    if (arrastre) arrastre.update();
  }

  function girarA(objetivo, duracion, ease) {
    detener();
    gsap.to(asa, { rotation: objetivo, duration: sinMovimiento ? 0 : duracion, ease,
                   onUpdate: colocar, onComplete: colocar });
  }

  /** Gira por el camino más corto hasta dejar la rama i arriba. */
  function traerArriba(i) {
    detener();
    const actual = giroActual();
    let delta = mod(-i * paso - actual, 360);
    if (delta > 180) delta -= 360;
    girarA(actual + delta, 0.6, "power3.inOut");
  }

  function girarPasos(pasos) {
    detener();
    girarA(Math.round(giroActual() / paso) * paso + pasos * paso, 0.45, "power2.out");
  }

  let arrastre = null;
  [arrastre] = Draggable.create(asa, {
    type: "rotation",
    trigger: rueda,
    inertia: !sinMovimiento,
    dragClickables: false,
    clickableTest: (el) => !!el.closest(".copilot"),   // el chat no arrastra la rueda
    minimumMovement: 4,
    onPress() { if (gsap.isTweening(asa)) detener(); },
    snap: (valor) => Math.round(valor / paso) * paso,
    onDrag: colocar,
    onThrowUpdate: colocar,
    onThrowComplete: colocar,
    onDragEnd() { ultimoArrastre = Date.now(); colocar(); },
  });

  modulosEl.forEach((el, i) => {
    el.addEventListener("click", (e) => {
      e.preventDefault();                                // el doble clic abrirá la rama (siguiente paso)
      if (Date.now() - ultimoArrastre < 250) return;     // fue el final de un arrastre, no un clic
      traerArriba(i);
    });
  });

  rueda.addEventListener("keydown", (e) => {
    if (e.target.closest(".copilot")) return;            // no robar las flechas al campo de texto
    if (e.key === "ArrowRight" || e.key === "ArrowDown") { e.preventDefault(); girarPasos(-1); }
    if (e.key === "ArrowLeft" || e.key === "ArrowUp") { e.preventDefault(); girarPasos(1); }
  });

  function aplicarGeometria() {
    g = estrecha.matches ? circulo : ovalo;
    lineas.setAttribute("viewBox", `${-g.ancho / 2} ${-g.alto / 2} ${g.ancho} ${g.alto}`);
    colocarNombre();
    colocar();
  }
  estrecha.addEventListener("change", aplicarGeometria);

  // Entrada: las imágenes y las líneas aparecen una tras otra.
  aplicarGeometria();
  if (!sinMovimiento) {
    gsap.from(modulosEl, { opacity: 0, y: 14, duration: 0.6, stagger: 0.09, ease: "power2.out" });
    gsap.from(lineas, { opacity: 0, duration: 0.8, delay: 0.25 });
    gsap.from(".copilot", { opacity: 0, scale: 0.94, duration: 0.5, delay: 0.35, ease: "back.out(2)" });
  }
})();
