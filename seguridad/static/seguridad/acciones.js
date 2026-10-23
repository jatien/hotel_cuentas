/*
 * Comportamientos comunes declarados con atributos data-* en lugar de onclick/onchange/onsubmit.
 * Así ninguna plantilla necesita JavaScript en línea y la Content Security Policy puede
 * bloquearlo (ver docs/security.md).
 *
 *   <select data-autosubmit>                 envía su formulario al cambiar
 *   <form data-confirm="¿Seguro?">           pide confirmación antes de enviar
 *   <button data-confirm="¿Seguro?">         pide confirmación al pulsar
 *   <button data-print>                      imprime la página
 *   <form data-enviando="Consultando…">      desactiva el botón y cambia su texto al enviar
 *   <textarea data-enter-envia>              Enter envía el formulario; Mayús+Enter, salto de línea
 */
(function () {
  "use strict";

  document.addEventListener("change", function (e) {
    var el = e.target;
    if (el.matches && el.matches("[data-autosubmit]") && el.form) {
      el.form.submit();
    }
  });

  document.addEventListener("click", function (e) {
    var el = e.target.closest ? e.target.closest("[data-confirm]:not(form), [data-print]") : null;
    if (!el) return;
    if (el.hasAttribute("data-print")) {
      window.print();
      return;
    }
    if (!window.confirm(el.getAttribute("data-confirm"))) {
      e.preventDefault();
      e.stopImmediatePropagation();
    }
  }, true);

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (form.hasAttribute("data-confirm") && !window.confirm(form.getAttribute("data-confirm"))) {
      e.preventDefault();
      e.stopImmediatePropagation();
      return;
    }
    if (form.hasAttribute("data-enviando")) {
      var boton = form.querySelector("button");
      if (boton) {
        boton.disabled = true;
        boton.textContent = form.getAttribute("data-enviando");
      }
    }
  });

  document.addEventListener("keydown", function (e) {
    var el = e.target;
    if (el.matches && el.matches("textarea[data-enter-envia]") && e.key === "Enter" && !e.shiftKey && el.form) {
      e.preventDefault();
      el.form.requestSubmit();
    }
  });
})();
