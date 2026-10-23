# inicio

Página de inicio del PMS: una rueda con las cuatro ramas (Gestión de reservas, Gestión de
facturas, Contabilidad y Análisis) y el chat del PMS Copilot en el centro.

## Piezas

| Fichero | Qué hace |
|---|---|
| `modulos.py` | Registro de las ramas y sus secciones. Única fuente de verdad del menú: la plantilla, el JS y la herramienta `modulos_pms` del copilot leen de aquí. |
| `views.py` | Calcula la geometría de la rueda (arcos dobles entre imágenes y flechas de giro). |
| `templates/inicio/home.html` | La rueda y el formulario del copilot. |
| `static/inicio/js/rueda.js` | Giro por arrastre con inercia, encaje en 90°, clic para traer al frente, teclado. |
| `static/inicio/img/*.svg` | Ilustraciones propias de cada rama (sin licencias de terceros). |
| `static/inicio/vendor/gsap/` | GSAP 3.15.0 (`gsap`, `Draggable`, `InertiaPlugin`), servido en local para que funcione en la red del hotel sin internet. |

La página anterior de tarjetas sigue en `/clasico/`.

## Añadir una sección

Añadir un `Seccion("Nombre", "app:url_name", "descripción")` a la rama en `modulos.py`.
Con `url_name=None` aparece como «próximamente». No hay que tocar la plantilla, el JS ni el copilot.

## Licencias

GSAP se distribuye con la licencia estándar «sin coste» de Webflow
(https://gsap.com/standard-license): uso gratuito, también comercial, en sitios y
aplicaciones web. No se pueden quitar los avisos de licencia de los ficheros `.min.js`.

## Pasos

1. Rueda: imágenes, líneas dobles, giro por arrastre, nombre de la rama de arriba, chat. ✔
2. Doble clic: la imagen sube a la mitad superior con flecha de volver; debajo, panel con
   las secciones a la izquierda y la pantalla de la herramienta a la derecha.
3. Chat del centro respondiendo en la propia página (API del copilot) en lugar de ir a `/copilot/`.
