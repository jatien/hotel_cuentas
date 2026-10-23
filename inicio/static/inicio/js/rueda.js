/*
 * Rueda de la página de inicio.
 *
 * - Se gira arrastrando, como un engranaje (GSAP Draggable con inercia) y al soltar
 *   encaja con una rama arriba.
 * - Las imágenes se mantienen derechas: la rueda gira y cada cara gira lo contrario
 *   (variable CSS --giro).
 * - Clic en una imagen: la rueda gira hasta dejarla arriba. Flechas del teclado: giran.
 * - El nombre de la rama que está arriba aparece bajo ella.
 */
(function () {
  "use strict";

  const rueda = document.getElementById("rueda");
  const giro = document.getElementById("rueda-giro");
  const nombre = document.getElementById("rueda-nombre");
  const modulosEl = Array.from(giro.querySelectorAll(".modulo"));
  const datos = JSON.parse(document.getElementById("modulos-datos").textContent);

  if (!window.gsap || !window.Draggable) return; // sin GSAP la página sigue funcionando con enlaces

  gsap.registerPlugin(Draggable, InertiaPlugin);

  const n = modulosEl.length;
  const paso = 360 / n;
  const sinMovimiento = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let arriba = -1;
  let ultimoArrastre = 0;

  const rotacionActual = () => gsap.getProperty(giro, "rotation");
  const mod = (a, b) => ((a % b) + b) % b;

  /** Índice de la rama que queda arriba con una rotación dada. */
  const indiceArriba = (rot) => mod(Math.round(-rot / paso), n);

  function actualizar() {
    const rot = rotacionActual();
    giro.style.setProperty("--giro", rot + "deg");
    const i = indiceArriba(rot);
    if (i !== arriba) {
      arriba = i;
      modulosEl.forEach((el, j) => el.classList.toggle("arriba", j === i));
      cambiarNombre(datos[i].nombre);  // «Próximamente» ya lo indica la etiqueta de la imagen
    }
  }

  function cambiarNombre(texto) {
    if (sinMovimiento) { nombre.textContent = texto; return; }
    gsap.timeline()
      .to(nombre, { opacity: 0, y: -4, duration: 0.12, ease: "power1.in" })
      .add(() => { nombre.textContent = texto; })
      .fromTo(nombre, { opacity: 0, y: 4 }, { opacity: 1, y: 0, duration: 0.2, ease: "power2.out" });
  }

  /** Gira por el camino más corto hasta dejar la rama i arriba. */
  function traerArriba(i) {
    const actual = rotacionActual();
    let delta = mod(-i * paso - actual, 360);
    if (delta > 180) delta -= 360;
    gsap.to(giro, {
      rotation: actual + delta,
      duration: sinMovimiento ? 0 : 0.6,
      ease: "power3.inOut",
      onUpdate: actualizar,
      onComplete: actualizar,
    });
  }

  function girarPasos(pasos) {
    const objetivo = Math.round(rotacionActual() / paso) * paso + pasos * paso;
    gsap.to(giro, { rotation: objetivo, duration: sinMovimiento ? 0 : 0.45, ease: "power2.out",
                    onUpdate: actualizar, onComplete: actualizar });
  }

  Draggable.create(giro, {
    type: "rotation",
    inertia: !sinMovimiento,
    dragClickables: true,          // también se puede arrastrar cogiendo una imagen
    minimumMovement: 4,
    snap: (valor) => Math.round(valor / paso) * paso,
    onDrag: actualizar,
    onThrowUpdate: actualizar,
    onThrowComplete: actualizar,
    onDragEnd() { ultimoArrastre = Date.now(); actualizar(); },
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

  // Entrada: las imágenes y las líneas aparecen una tras otra.
  actualizar();
  if (!sinMovimiento) {
    gsap.from(modulosEl, { opacity: 0, y: 14, duration: 0.6, stagger: 0.09, ease: "power2.out" });
    gsap.from(".rueda-lineas path", { opacity: 0, duration: 0.8, stagger: 0.04, delay: 0.2 });
    gsap.from(".copilot", { opacity: 0, scale: 0.94, duration: 0.5, delay: 0.35, ease: "back.out(2)" });
  }
})();
