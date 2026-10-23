# inicio

Página de inicio del PMS: una rueda ovalada y alargada (lado izquierdo más alto) con las
cuatro ramas (Gestión de reservas, Gestión de facturas, Contabilidad y Análisis) y el chat
del PMS Copilot en el centro. En móvil, un círculo.

- **Girar**: es un engranaje pesado. El arrastre ofrece resistencia y al soltar avanza como
  mucho una rama, con un golpe al encajar. Clic en una rama la trae arriba paso a paso.
- **Entrar** (doble clic o Intro): el icono vuela a la esquina superior izquierda, debajo cae
  el desplegable de secciones y a la derecha se abre la pantalla de la herramienta (la página
  de Django de esa sección, en un iframe). La flecha del icono, Esc o «atrás» vuelven.
  La URL es `/m/<rama>/?seccion=n`, así que recargar y compartir el enlace funcionan.
- **Tema**: Hotel Abrat (dorado, azul mar y crema; Arvo y Mulish). `?tema=azul` muestra el
  anterior para comparar.

## Piezas

| Fichero | Qué hace |
|---|---|
| `modulos.py` | Registro de las ramas y sus secciones. Única fuente de verdad del menú: la plantilla, el JS y la herramienta `modulos_pms` del copilot leen de aquí. |
| `views.py` | Geometría de la elipse, posición inicial de cada rama y la vista `/m/<rama>/` con la rama abierta. |
| `templates/inicio/home.html` | La rueda y el formulario del copilot. |
| `static/inicio/js/rueda.js` | Rueda (engranaje pesado), animación de entrada y salida de cada rama, desplegable, iframe e historial. |
| `templates/inicio/iconos/*.svg` | Ilustraciones propias, en línea para que tomen los colores del tema (sin licencias de terceros). |
| `static/inicio/vendor/gsap/` | GSAP 3.15.0 (`gsap`, `Draggable`), servido en local para que funcione en la red del hotel sin internet. |

Las pantallas de cada sección se muestran tal cual dentro del iframe (por eso
`X_FRAME_OPTIONS = 'SAMEORIGIN'` en settings); dentro del inicio se ocultan su cabecera
«← Cuentas» y el menú lateral propio de la contabilidad, cuyas entradas pasan al desplegable.

La página anterior de tarjetas sigue en `/clasico/`.

## Añadir una sección

Añadir un `Seccion("Nombre", "app:url_name", "descripción")` a la rama en `modulos.py`
(opcional: `grupo="…"` para agruparla y `principal=True` para que sea la que se abre al entrar).
Con `url_name=None` aparece como «próximamente». No hay que tocar la plantilla, el JS ni el copilot.

## Licencias

GSAP se distribuye con la licencia estándar «sin coste» de Webflow
(https://gsap.com/standard-license): uso gratuito, también comercial, en sitios y
aplicaciones web. No se pueden quitar los avisos de licencia de los ficheros `.min.js`.

## Pasos

1. Rueda: imágenes, líneas dobles, giro por arrastre, nombre de la rama de arriba, chat. ✔
   Ajustes: óvalo inclinado, factura saliendo por la ranura del ordenador, sin flechas ni texto inferior. ✔
2. Doble clic: el icono sube con su flecha de volver; desplegable de secciones debajo y la
   herramienta a la derecha. Engranaje pesado, óvalo alargado, tema Hotel Abrat, iconos nuevos. ✔
3. Chat del centro respondiendo en la propia página (API del copilot) en lugar de ir a `/copilot/`.
