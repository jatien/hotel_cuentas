# Seguridad de `cuentas`

Diseño y guía de uso de la seguridad del proyecto. Cada capa dice qué está hecho, dónde está y qué
queda pendiente.

```
Navegador / app móvil
   │  LAN, o VPN para acceso remoto (nunca un puerto abierto a Internet)
   ▼
Caddy (proxy)            TLS, redirección HTTP→HTTPS, HSTS, límite de tamaño de peticiones
   ▼
Waitress / Gunicorn      solo escucha en 127.0.0.1
   ▼
Middleware de Django     cabeceras de seguridad, CSP, sesiones, CSRF
   ▼
Vista → servicio → PostgreSQL (rol propio de la app)
                 └→ herramientas del PMS Copilot (mismos permisos que las vistas)
```

| Capa | Estado |
|---|---|
| Ajustes por entorno y secretos fuera de git | ✅ este documento, §1 |
| Cabeceras de seguridad y CSP | ✅ §2 |
| Registro de eventos de seguridad | ✅ §3 |
| Servidor WSGI + proxy + HTTPS | ver §4 (PR aparte) |
| Cifrado en reposo y copias cifradas | ver §5 (PR aparte) |
| Llamadas a APIs externas y servidor del modelo | ver §6 (PR aparte) |
| Autenticación y autorización de usuarios | **pendiente** (se hará más adelante) |

---

## 1. Ajustes por entorno y secretos

`mysite/settings/` es un paquete:

| Fichero | Uso |
|---|---|
| `base.py` | Común: apps, middleware, cabeceras, CSP, logs, configuración de las apps |
| `dev.py` | Desarrollo: `DEBUG=True`, `runserver` |
| `prod.py` | Producción detrás de HTTPS: `DEBUG=False`, cookies seguras, HSTS, SMTP, estáticos con hash |

Se elige con `DJANGO_ENV=dev|prod` en el `.env`. `manage.py`, `wsgi.py` y `asgi.py` siguen apuntando a
`mysite.settings`, que carga el que toque.

**Ningún secreto vive en el código.** Todo sale del `.env` (plantilla en `.env.example`):
`SECRET_KEY`, `DB_PASSWORD`, `ALLOWED_HOSTS`, claves de los modelos de IA…

`prod.py` se niega a arrancar si:
- falta `SECRET_KEY`, es corta (< 50 caracteres) o es la de desarrollo;
- `ALLOWED_HOSTS` está vacío o contiene `*`;
- falta `DB_PASSWORD`.

Comprobación completa antes de poner en marcha el servidor:

```powershell
$env:DJANGO_ENV="prod"; python manage.py check --deploy
```

Solo debe quedar el aviso `security.W005` (HSTS de subdominios), que no aplica en la LAN.

### ⚠️ Secretos que han estado en git

Hasta este cambio, `mysite/settings.py` tenía la `SECRET_KEY` y la contraseña de PostgreSQL
(`postgres` / `1234`), y `db.sqlite3` estaba versionado. Siguen en el historial del repositorio, así
que hay que darlos por públicos:

1. Generar una `SECRET_KEY` nueva y ponerla en el `.env` de cada PC:
   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(64))"
   ```
   Al cambiarla se cierran las sesiones abiertas y caducan los enlaces firmados (normal).
2. Cambiar la contraseña de PostgreSQL y ponerla en `DB_PASSWORD`. Mejor aún, crear un rol propio
   para la app (ver §5) y dejar `postgres` solo para administración.
3. `db.sqlite3` se ha quitado del repositorio (el proyecto usa PostgreSQL); queda en `.gitignore`.

### Rotar la `SECRET_KEY` sin cerrar sesiones

```
SECRET_KEY=<nueva>
SECRET_KEY_FALLBACKS=<antigua>
```

Pasado un tiempo (cuando caduquen las sesiones), se vacía `SECRET_KEY_FALLBACKS`.

---

## 2. Cabeceras de seguridad y Content Security Policy

En todas las respuestas (`base.py`):

| Cabecera | Valor | Por qué |
|---|---|---|
| `X-Content-Type-Options` | `nosniff` | El navegador no adivina tipos (evita ejecutar un fichero subido como script) |
| `Referrer-Policy` | `same-origin` | Las URLs internas no salen hacia la CDN o Google Fonts |
| `Cross-Origin-Opener-Policy` | `same-origin` | Aísla la ventana de otras páginas |
| `X-Frame-Options` | `SAMEORIGIN` | Nadie de fuera puede meter la app en un iframe; los PDF propios sí |
| `Content-Security-Policy(-Report-Only)` | ver abajo | Lista blanca de orígenes de scripts y estilos |

En producción, además: cookies `Secure`, redirección a HTTPS y HSTS.

### CSP

La política (`CSP_POLICY` en `base.py`) permite scripts solo de la propia app, de
`cdn.jsdelivr.net` (Chart.js) y bloques `<script>` en línea que lleven el nonce de la petición.

- **Scripts en línea:** siempre con `<script nonce="{{ csp_nonce }}">`. Los bloques
  `<script type="application/json">` (datos con `json_script`) no lo necesitan.
- **Nada de `onclick`, `onchange`, `onsubmit`…** en las plantillas: se usan atributos que maneja
  `seguridad/static/seguridad/acciones.js`:

  | Antes | Ahora |
  |---|---|
  | `onchange="this.form.submit()"` | `data-autosubmit` |
  | `onsubmit="return confirm('…')"` (en `<form>`) | `data-confirm="…"` |
  | `onclick="return confirm('…')"` (en `<button>`) | `data-confirm="…"` |
  | `onclick="window.print()"` | `data-print` |

  Los tests de `seguridad` fallan si una plantilla nueva usa `on…=` o un `<script>` sin nonce.

**Despliegue en dos fases:**

1. `CSP_ENFORCE=False` (por defecto): *solo informe*. No bloquea nada; cada infracción aparece en la
   consola del navegador y en `logs/security.log` como `csp_violation`.
2. Cuando el log quede limpio usando la app con normalidad, `CSP_ENFORCE=True`.

---

## 3. Registro de eventos de seguridad

`logs/security.log` (rotativo, 10 × 5 MB; carpeta configurable con `LOG_DIR`) recibe:

- `django.security.*`: host no permitido, operaciones sospechosas, rechazos CSRF;
- `seguridad.csp`: infracciones de la CSP;
- `seguridad`: fallos del health check.

Cuando llegue la autenticación se añadirán aquí los inicios de sesión fallidos y los bloqueos.

`/seguridad/health/` responde `ok` si el proceso funciona y llega a la base de datos (lo usa el proxy).

---

## PMS Copilot

Cada componente nuevo se revisa por si encaja como herramienta del copilot (function calling):

| Componente | ¿Herramienta? | Motivo |
|---|---|---|
| Ajustes por entorno | No | Configuración interna, sin datos que consultar |
| CSP e informes | No | Por ahora. Cuando haya roles, un `eventos_seguridad` de solo lectura para administración podría resumir el log; con el login desactivado expondría IPs a cualquiera de la LAN |
| Health check | No | Lo consulta el proxy, no una persona |
