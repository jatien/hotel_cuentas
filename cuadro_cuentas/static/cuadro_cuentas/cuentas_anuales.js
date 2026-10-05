// Pestañas de los estados y desglose de cada partida en sus subcuentas
(() => {
  const pestanas = document.querySelectorAll("[data-pestana]");
  pestanas.forEach((b) => b.addEventListener("click", () => {
    pestanas.forEach((x) => x.setAttribute("aria-selected", String(x === b)));
    document.querySelectorAll("[data-panel]").forEach((p) => (p.hidden = p.dataset.panel !== b.dataset.pestana));
  }));

  document.addEventListener("click", (ev) => {
    const boton = ev.target.closest(".desplegar");
    if (!boton) return;
    const abierto = boton.getAttribute("aria-expanded") === "true";
    boton.setAttribute("aria-expanded", String(!abierto));
    document.querySelectorAll(`tr[data-de="${boton.dataset.ref}"]`).forEach((tr) => (tr.hidden = abierto));
  });

  // Al imprimir se muestran todos los estados, uno por página
  window.addEventListener("beforeprint", () => document.querySelectorAll("[data-panel]").forEach((p) => {
    p.dataset.oculto = p.hidden;
    p.hidden = false;
  }));
  window.addEventListener("afterprint", () => document.querySelectorAll("[data-panel]").forEach((p) => {
    p.hidden = p.dataset.oculto === "true";
  }));
})();